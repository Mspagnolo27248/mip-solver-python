"""Build the optimization model's specification from the workbook reference.

Applies the simplifications in `model_config.py` and checks that nothing was lost
in the process: opening inventory, production and demand must all reconcile
against the workbook totals, aggregation by aggregation. If they do not, the
build fails rather than handing the optimizer a quietly wrong model.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any, Dict, List, Optional

from . import model_config as cfg
from .engine import GAL_PER_BBL, Reference, Scenario, Simulation, month_key

TOL = 1.0   # gallons


class ModelSpec:
    def __init__(self) -> None:
        self.products: Dict[str, Dict[str, Any]] = {}
        self.units: Dict[str, Dict[str, Any]] = {}
        self.charge_lines: List[Dict[str, Any]] = []
        self.yield_rules: List[Dict[str, Any]] = []
        self.reconciliation: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self.warnings: List[str] = []
        self.dropped: List[Dict[str, str]] = []
        self.never_charged: List[Dict[str, Any]] = []
        #: Charge lines the model had to create because the workbook has no row
        #: for them - today, the gasoline outlet.
        self.added_lines: List[Dict[str, Any]] = []
        #: Production booked under one code but landing in another product's
        #: block, so a yield of the first credits the second: {from: to}.
        self.production_alias: Dict[str, str] = {}
        #: workbook product code -> model product, including the sold-code
        #: aliases of pooled blocks
        self.aliases: Dict[str, str] = {}
        #: [{product, sink, status, line}] - where overflow may be pushed
        self.sink_routes: List[Dict[str, Any]] = []
        #: product -> {"fixed"|"_mode_R"|"_mode_L": {date: gallons}}
        #: Crude output, supplied to the model rather than decided by it.
        self.fixed_supply: Dict[str, Dict[str, Dict[str, float]]] = {}
        #: "supplied" when the app passed planner-set downtime, "inferred" when
        #: it fell back to the outages read out of the workbook.
        self.downtime_source: str = "inferred"
        #: block id -> row label -> {date: gallons}. The demand the optimizer has
        #: to serve, taken from the simulated rows so forecast is already netted
        #: against firm orders exactly as the workbook does it.
        self.demand_rows: Dict[str, Dict[str, Dict[str, float]]] = {}

    def summary(self) -> Dict[str, Any]:
        return {
            "products": len(self.products),
            "units": len(self.units),
            "charge_lines": len(self.charge_lines),
            "yield_rules": len(self.yield_rules),
            "dropped": len(self.dropped),
        }


def map_product(code: Optional[str]) -> Optional[str]:
    """Workbook product code -> model product code."""
    if code is None:
        return None
    if cfg.is_retired(code=code):
        return None
    return cfg.aggregate_of(code) or code


def _add_missing_sink_lines(ref: Reference) -> List[Dict[str, Any]]:
    """Give every downgrade route somewhere to be written.

    The gasoline outlet is confirmed with operations but the workbook never
    modelled it, so it has no charge line - and a route with no line cannot be
    part of an answer. v0 quietly dropped all three of its routes, which left
    Kensol 17 with 3.3 M gal of overflow and nowhere to put it: the model was
    infeasible the moment the plant was made to run at plan rate.

    The line is appended, never inserted, so the workbook's first-match VLOOKUP
    still resolves to the same row it always did and the parity harness is
    untouched. It only carries volume for a simulation run in physical mode,
    which sums every matching line - see `verify.py`.

    **This mutates `ref`, which the app caches and shares.** That is deliberate:
    the engine has to see the line to replay a schedule written onto it, and it
    reads `ref.charge_lines` directly. It is idempotent, and the lines carry no
    volume unless an optimizer result put it there, so the manual side is
    unaffected - but it is a global side effect of running the optimizer, and
    anything listing charge lines for a planner should filter on `synthetic`
    rather than assume every line came from the workbook.
    """
    # Keyed on (unit, product): a route needs a line for *its own* product, and
    # an outlet that exists for three products still has nothing to write a
    # fourth onto. Kendex D&N goes to #6 oil, an outlet the workbook has - but
    # not for that product, so the route was silently unroutable and 4579 simply
    # filled its tank until the model went infeasible.
    have = {(l["unit"], l["code"]) for l in ref.charge_lines}
    row = max((l["row"] for l in ref.charge_lines), default=0)
    added = []
    for sink_id, route in cfg.SINK_ROUTES.items():
        unit = cfg.SINKS[sink_id]["outlet"]
        for code in route["products"]:
            if (unit, code) in have:
                continue
            row += 1
            line = {"key": "{}#{}".format(unit, row), "unit": unit, "row": row,
                    "code": code, "name": ref.products.get(code, {}).get(
                        "name", "") if isinstance(ref.products.get(code), dict)
                    else "",
                    "index_in_block": len(added), "in_charge_lookup_range": True,
                    "synthetic": True}
            ref.charge_lines.append(line)
            added.append(line)
    return added


def _rated(overrides, unit, code, info):
    """A rate with the planner's edit applied, if they made one.

    The edit screen has always accepted these and the model has always ignored
    them: the override was written into the reference payload and read by
    nothing, so a planner could change a maximum charge rate, watch it save, and
    get the old number back in the schedule. A silent no-op is worse than a
    missing field - the missing field at least tells you it is missing.

    Keyed by unit then code, where code is a product or a line key, so a per-line
    ceiling can be edited without disturbing the product-level one. `basis` is
    rewritten because the imported basis describes where the *original* number
    came from, and it no longer does.
    """
    v = (overrides.get(unit) or {}).get(code)
    if v is None:
        return info
    out = dict(info or {})
    out["bbl"] = float(v)
    out["basis"] = "planner override"
    return out


def build(ref: Reference, scn: Scenario, sim: Simulation,
          horizon: Optional[List[dt.date]] = None,
          downtime: Optional[Dict[str, set]] = None) -> ModelSpec:
    """Build the optimization model specification.

    `downtime` is `{unit: {date, ...}}` of days a unit may not produce, normally
    supplied by the app so planners can set it before a run. Falls back to the
    outages inferred from the workbook when nothing is passed.
    """
    spec = ModelSpec()
    dates = horizon or scn.dates
    down = downtime if downtime is not None else {
        u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec.downtime_source = "supplied" if downtime is not None else "inferred"

    spec.added_lines = _add_missing_sink_lines(ref)

    # ------------------------------------------------------------- products
    # Every block becomes a model product, with aggregate members folded together
    # and pool blocks dropped (they are a derived sum, not a tank).
    pooled = {a["pool"] for a in cfg.AGGREGATIONS if a.get("pool")}
    agg_by_id = {a["id"]: a for a in cfg.AGGREGATIONS}
    contributions: Dict[str, List[str]] = defaultdict(list)

    for block in ref.blocks:
        code = block.get("charge_code")
        if code is None:
            continue
        if cfg.is_retired(code=code):
            spec.dropped.append({"kind": "product", "code": code,
                                 "why": cfg.RETIRED["products"][code]})
            continue
        if code in pooled:
            agg = next(a for a in cfg.AGGREGATIONS if a.get("pool") == code)
            spec.dropped.append({
                "kind": "pool", "code": code,
                "why": "derived sum of {}; kept as aggregate {}".format(
                    ", ".join(agg["members"]), agg["id"])})
            continue

        target = map_product(code)
        if target is None:
            continue
        contributions[target].append(code)

        bal = sim.balances.get(block["id"], {})
        opening = bal.get("Begin Inventory", {}).get(dates[0], 0.0)
        capacity = bal.get("Tank Capacity", {}).get(dates[0], 0.0)
        demand = sum(bal.get(r, {}).get(d, 0.0)
                     for r in ("Sales", "Forecast", "Blends") for d in dates)
        production = sum(bal.get("Production In", {}).get(d, 0.0) for d in dates)

        p = spec.products.setdefault(target, {
            "code": target, "members": [], "opening": 0.0, "capacity": 0.0,
            "demand": 0.0, "production": 0.0, "sheet": block["sheet"],
            "name": (ref.products.get(target, {}) or {}).get("name")
                    or agg_by_id.get(target, {}).get("title") or target,
        })
        p["members"].append(code)
        p["opening"] += opening
        p["capacity"] += capacity
        p["demand"] += demand
        p["production"] += production

        # A block pools a charge code with a sold code (9718 Waxy LLN Distillate
        # and 9116 Waxy Light Neutral share one tank), and other units charge
        # against the sold code. Both names must resolve to the same product or
        # the model will consume inventory it is not tracking.
        for alias in (block.get("charge_code"), block.get("sold_code")):
            if alias:
                spec.aliases.setdefault(alias, target)

    # aggregate capacity overrides, where the members' recorded tanks are known
    # to be incomplete
    for agg in cfg.AGGREGATIONS:
        p = spec.products.get(agg["id"])
        if not p:
            continue
        p["name"] = agg["title"]
        # A capacity typed against the aggregate wins. The config figure exists
        # because the members' recorded tanks do not add up - six of the ten have
        # none - so it is a stand-in, not a fact, and a planner correcting it
        # should not be overruled by it.
        edited = (ref.raw.get("tank_capacity") or {}).get(agg["id"])
        if edited:
            p["capacity_from_members"] = p["capacity"]
            p["capacity"] = float(edited)
            spec.notes.append(
                "{}: capacity set to {:,.0f} gal on the planning side."
                .format(agg["id"], float(edited)))
        elif agg.get("capacity_override") is not None:
            p["capacity_from_members"] = p["capacity"]
            p["capacity"] = agg["capacity_override"]
            spec.notes.append("{}: {}".format(agg["id"], agg["capacity_note"]))
        spec.notes.append("{}: {}".format(agg["id"], agg["why"]))

    # ---------------------------------------------------------- charge lines
    for line in ref.charge_lines:
        if cfg.is_retired(unit=line["unit"], line_key=line["key"]):
            spec.dropped.append({
                "kind": "charge_line", "code": line["key"],
                "why": (cfg.RETIRED["charge_lines"].get(line["key"])
                        or cfg.RETIRED["units"].get(line["unit"], ""))})
            continue
        target = map_product(line["code"])
        if target is None:
            spec.dropped.append({"kind": "charge_line", "code": line["key"],
                                 "why": "charges retired product {}".format(
                                     line["code"])})
            continue
        resolved = spec.aliases.get(line["code"], target)
        spec.charge_lines.append({
            "key": line["key"], "unit": line["unit"], "product": resolved,
            "workbook_product": line["code"], "row": line["row"],
            "via_alias": resolved != line["code"] and line["code"] not in
                         spec.products,
        })

    # ----------------------------------------------------------------- units
    by_unit: Dict[str, List[str]] = defaultdict(list)
    for line in spec.charge_lines:
        if line["product"] not in by_unit[line["unit"]]:
            by_unit[line["unit"]].append(line["product"])
    # Some charged products have no inventory block anywhere: the Platformer's
    # light straight run and isomerate are made and consumed inside the cascade
    # and never tanked. They still have to exist in the model, or the yield chain
    # breaks - but with no inventory variable and no capacity.
    for line in spec.charge_lines:
        if line["product"] in spec.products:
            continue
        if line["unit"].startswith(("TRANSFER", "SONNEBORN", "RAILCAR")):
            continue
        code = line["product"]
        spec.products[code] = {
            "code": code, "members": [code], "opening": 0.0, "capacity": 0.0,
            "demand": 0.0, "production": 0.0, "sheet": None, "tanked": False,
            "name": (ref.products.get(code, {}) or {}).get("name") or code,
        }
        spec.notes.append(
            "{} ({}) has no tank in the workbook: it is made and consumed inside "
            "the {} cascade. Modelled as a flow-through intermediate with no "
            "inventory constraint.".format(
                code, spec.products[code]["name"], line["unit"]))

    for p in spec.products.values():
        p.setdefault("tanked", True)

    # Downgrade sinks have unlimited *offtake*, not unlimited tankage. Diesel and
    # #6 oil are real products in real tanks that can still overfill; what makes
    # them overflow valves is that the market for them is effectively bottomless,
    # so the plant can always sell its way out at the netback. Modelling that as
    # an unbounded tank instead would let diesel inventory grow without limit and
    # quietly hide a genuine capacity problem.
    for code, sink_id in cfg.sink_products().items():
        p = spec.products.get(code)
        if p is None:
            continue
        p["is_sink"] = True
        p["sink"] = sink_id
        p["unlimited_offtake"] = True
        spec.notes.append(
            "{} ({}) is a downgrade sink: its tank and capacity stay real, but "
            "offtake is unlimited at the netback, so it can always be sold "
            "down.".format(code, cfg.SINKS[sink_id]["title"]))

    # Gasoline has no block in the workbook at all, so the sink has to be created.
    for sink_id, sink in cfg.SINKS.items():
        if sink.get("product") is not None:
            continue
        key = "SINK_{}".format(sink_id)
        # Gasoline has no tank here at all - it is purely an outlet.
        spec.products[key] = {
            "code": key, "members": [], "opening": 0.0, "capacity": 0.0,
            "demand": 0.0, "production": 0.0, "sheet": None, "tanked": False,
            "is_sink": True, "sink": sink_id, "unlimited_offtake": True,
            "name": sink["title"],
        }
        spec.warnings.append(
            "{} sink does not exist in the workbook and was created. {}".format(
                sink["title"], sink.get("note", "")))

    # Offtake a product has of its own, distinct from being a destination other
    # products are routed to. No transfer line, no receiving pool, no unit time -
    # the material is lifted as it is.
    for code, spec_ in cfg.UNLIMITED_OFFTAKE.items():
        p = spec.products.get(spec.aliases.get(code, code))
        if p is None:
            continue
        p["unlimited_offtake"] = True
        p["offtake_relative_to"] = spec_["relative_to"]
        p["offtake_discount_per_gal"] = spec_["discount_per_gal"]
        spec.notes.append("{} can be sold as-is without limit, at ${:.2f}/gal "
                          "under {}.".format(code, spec_["discount_per_gal"],
                                             spec_["relative_to"]))

    for p in spec.products.values():
        p.setdefault("is_sink", False)
        p.setdefault("unlimited_offtake", False)

    # Routes: which product may go to which sink. Observed routes come from the
    # workbook's own transfer lines; proposed ones do not exist there yet.
    for line in ref.charge_lines:
        sink_id = next((sid for sid, s in cfg.SINKS.items()
                        if s.get("outlet") == line["unit"]), None)
        if sink_id is None:
            continue
        if line.get("synthetic"):
            # The line exists so the decision has somewhere to be written, not
            # because the workbook has the route. It must never be able to pass
            # as observed - the route is still recorded as added, below.
            continue
        if cfg.is_retired(unit=line["unit"], line_key=line["key"]):
            # A retired line is not a route. This loop reads the raw reference
            # rather than the filtered spec, so without the check a line dropped
            # for being wrong came straight back as an observed route - which is
            # how Kensol 30 kept its outlet to the diesel charge pool after that
            # line was retired, and then had two outlets.
            continue
        product = spec.aliases.get(line["code"], map_product(line["code"]))
        if product is None or product not in spec.products:
            continue
        spec.sink_routes.append({"product": product, "sink": sink_id,
                                 "status": "observed", "line": line["key"]})

    for sink_id, route in cfg.SINK_ROUTES.items():
        for code in route["products"]:
            product = spec.aliases.get(code, map_product(code))
            if product is None or product not in spec.products:
                continue
            if any(r["product"] == product and r["sink"] == sink_id
                   for r in spec.sink_routes):
                continue
            spec.sink_routes.append({"product": product, "sink": sink_id,
                                     "status": route["status"], "line": None})

    def resolve(code: str) -> str:
        return spec.aliases.get(code, map_product(code) or code)

    # Workbook code -> model product, needed to express reactor membership and
    # rate bands (both configured in workbook codes) in model terms.
    def back_to_model(code: str) -> str:
        return spec.aliases.get(code, map_product(code) or code)

    for unit, products in by_unit.items():
        arcs = cfg.allowed_arcs(unit, products, resolve)
        # Planner edits to the charge rates, folded in by `effective_payload`.
        overrides = (ref.raw or {}).get("max_rate_overrides") or {}
        workbook_of = {}
        for line in spec.charge_lines:
            if line["unit"] == unit:
                workbook_of.setdefault(line["product"], line["workbook_product"])

        reactors = None
        rspec = cfg.UNIT_REACTORS.get(unit)
        if rspec:
            reactors = {"flush_days": rspec["flush_days"], "assignment": {}}
            for p in products:
                reactors["assignment"][p] = cfg.reactor_of(
                    unit, workbook_of.get(p, p))

        spec.units[unit] = {
            "unit": unit,
            "products": products,
            "allowed_arcs": arcs,
            "unrestricted": arcs is None,
            "min_run_days": {p: cfg.min_run_days(unit, p, resolve)
                             for p in products},
            "switch_loss_days": cfg.switch_loss_days(unit),
            "reactors": reactors,
            "max_rate": {p: _rated(overrides, unit, workbook_of.get(p, p),
                                   cfg.max_rate_info(unit, workbook_of.get(p, p)))
                         for p in products},
            # Keyed by product, `max_rate` cannot describe a unit that runs one
            # feed two ways - extraction's normal and deep modes both charge
            # 9305 at different ceilings. The per-line map is what the optimizer
            # reads; this one stays for the report and for units with one line
            # per feed.
            "max_rate_by_line": {
                l["key"]: _rated(
                    overrides, unit, l["key"],
                    _rated(overrides, unit, l["workbook_product"],
                           cfg.max_rate_info(unit, l["workbook_product"],
                                             l["key"])))
                for l in spec.charge_lines if l["unit"] == unit},
            "min_rate_bbl": {p: cfg.min_rate_bbl(unit, workbook_of.get(p, p))
                             for p in products},
            "max_products_per_day": cfg.MAX_PRODUCTS_PER_DAY,
            "max_changeovers_per_day": cfg.MAX_CHANGEOVERS_PER_DAY,
            "must_run": cfg.MUST_RUN_WHEN_AVAILABLE,
            # Days the unit may be idle. Everything else it must run, so this is
            # the only slack the optimizer gets on availability.
            "downtime_days": sorted(d.isoformat() for d in down.get(unit, set())
                                    if d in set(dates)),
            "must_run_days": sorted(d.isoformat() for d in dates
                                    if d not in down.get(unit, set())),
            # Time lost on every allowed changeover, including the reactor flush
            # where the arc crosses reactors.
            "changeover_loss_days": (
                {"{}->{}".format(a, b): cfg.changeover_loss_days(
                    unit, workbook_of.get(a, a), workbook_of.get(b, b))
                 for a, b in arcs} if arcs else None),
        }
        for p, info in spec.units[unit]["max_rate"].items():
            if info is None and not spec.products.get(p, {}).get("tanked", True):
                # Not a gap. An untanked intermediate has no buffer, so charge
                # equals what the upstream unit made, and the identity fixes the
                # level exactly - a ceiling would be a weaker restatement of it.
                spec.notes.append(
                    "{}: {} needs no maximum rate. It has no tank, so charge is "
                    "fixed by the flow-through identity - whatever is made is "
                    "charged the same day.".format(unit, p))
            elif info is None:
                spec.warnings.append(
                    "{}: {} has no maximum charge rate. `charge = max_rate x "
                    "time` cannot be written without one.".format(unit, p))
            elif info["basis"] == "grossed up":
                spec.warnings.append(
                    "{}: {} never runs an undisturbed day, so its maximum rate "
                    "({:,.0f} bbl/d) is the observed peak grossed up by one "
                    "changeover. It is a floor - confirm the design rate with "
                    "operations.".format(unit, p, info["bbl"]))

        if arcs is None:
            continue
        # A product with no arc into it can never be scheduled. That is almost
        # always a missing entry in the transition config rather than a real
        # constraint, and it fails silently - the solver simply never picks it.
        reachable = {b for _, b in arcs} | {a for a, _ in arcs}
        for p in products:
            if p not in reachable:
                spec.warnings.append(
                    "{}: product {} has no allowed transition, so the optimizer "
                    "can never schedule it. Either place it on the ladder or "
                    "retire it.".format(unit, p))

    # ----------------------------------------------------------- yield rules
    kept_lines = {l["key"] for l in spec.charge_lines}
    merged: Dict[tuple, Dict[str, Any]] = {}
    for rule in ref.yield_rules:
        out = map_product(rule.get("out"))
        if rule["kind"] == "unit":
            if rule["charge_line"] not in kept_lines or out is None:
                continue
            key = (rule["unit"], rule["charge_line"], out)
            entry = merged.setdefault(key, {
                "kind": "unit", "unit": rule["unit"],
                "charge_line": rule["charge_line"], "out": out, "yield": 0.0})
            # two workbook grades collapsing into one aggregate add their yields
            entry["yield"] += rule["yield"]
        elif rule["kind"] == "crude" and out is not None:
            merged[("crude", rule["po_row"], out)] = {
                "kind": "crude", "out": out,
                "monthly_yield": rule["monthly_yield"], "mode": rule.get("mode")}
        elif rule["kind"] == "diesel_yield_back" and out is not None:
            terms = [t for t in rule["terms"]]
            merged[("dyb", out)] = {"kind": "diesel_yield_back", "out": out,
                                    "terms": terms}
        elif rule["kind"] == "transfer" and out is not None:
            lines = [k for k in rule["charge_lines"] if k in kept_lines]
            if lines:
                merged[("transfer", rule["po_row"], out)] = {
                    "kind": "transfer", "out": out, "charge_lines": lines,
                    "divisor": rule.get("divisor", 1.0)}
    spec.yield_rules = list(merged.values())

    # --------------------------------------------------------- reconciliation
    for target, members in sorted(contributions.items()):
        if len(members) == 1 and target == members[0]:
            continue
        wb_open = wb_dem = wb_prod = 0.0
        for code in members:
            block = next((b for b in ref.blocks if b.get("charge_code") == code),
                         None)
            if not block:
                continue
            bal = sim.balances.get(block["id"], {})
            wb_open += bal.get("Begin Inventory", {}).get(dates[0], 0.0)
            wb_dem += sum(bal.get(r, {}).get(d, 0.0)
                          for r in ("Sales", "Forecast", "Blends") for d in dates)
            wb_prod += sum(bal.get("Production In", {}).get(d, 0.0) for d in dates)
        p = spec.products[target]
        spec.reconciliation.append({
            "product": target, "members": members,
            "opening": (p["opening"], wb_open),
            "demand": (p["demand"], wb_dem),
            "production": (p["production"], wb_prod),
            "ok": (abs(p["opening"] - wb_open) < TOL
                   and abs(p["demand"] - wb_dem) < TOL
                   and abs(p["production"] - wb_prod) < TOL),
        })

    # Charge lines the planner never uses are the same shape of problem as the
    # tolling route: a decision the model would offer that the plant does not
    # actually make. Surface them rather than silently carrying them.
    for line in spec.charge_lines:
        if not any(scn.charge_bbl(line["key"], d) > 0 for d in scn.dates):
            spec.never_charged.append({
                "key": line["key"], "unit": line["unit"],
                "product": line["product"],
                "name": (ref.products.get(line["workbook_product"], {}) or {})
                        .get("name", "")})

    # Crude is a fixed input, not a decision: its daily production becomes supply
    # the downstream schedule has to absorb. Computed here so the optimizer never
    # needs the crude yield rules at all.
    if cfg.CRUDE_RATE_IS_INPUT:
        for d in dates:
            iso = d.isoformat()
            crude_bbl = scn.crude_bbl.get(iso, 0.0)
            if not crude_bbl:
                continue
            mode = scn.crude_mode.get(iso)
            mk = month_key(d)
            for rule in ref.yield_rules:
                if rule["kind"] != "crude":
                    continue
                gate = rule.get("mode")
                if gate and cfg.CRUDE_MODE_IS_DECISION:
                    # mode still chosen by the optimizer: keep the two streams
                    # separate so it can pick between them
                    pass
                elif gate and mode != gate:
                    continue
                # Resolve through the alias, not just the retirement map. Crude
                # makes 9116 Waxy Light Neutral, but 9116's inventory lives in
                # the block keyed 9718 - the two share a tank. Keyed 9116 the
                # supply lands on a product that has no balance row, so 849,769
                # gal a window is created by the crude unit and never credited
                # to any tank. The optimizer then plans as if the MEK's lightest
                # feed barely exists, while the simulator - which reads the
                # workbook's own production grid - can see it.
                out = spec.aliases.get(rule.get("out")) or map_product(
                    rule.get("out"))
                if out is None or out not in spec.products:
                    continue
                gal = crude_bbl * rule["monthly_yield"].get(mk, 0.0) * GAL_PER_BBL
                if not gal:
                    continue
                entry = spec.fixed_supply.setdefault(out, {})
                if gate and cfg.CRUDE_MODE_IS_DECISION:
                    entry.setdefault("_mode_" + gate, {})[iso] = gal
                else:
                    entry.setdefault("fixed", {})[iso] = gal
        spec.notes.append(
            "Crude rate is a fixed input: {} days of crude production enter the "
            "model as supply, not as a decision.".format(
                len([d for d in dates if scn.crude_bbl.get(d.isoformat(), 0.0)])))

    # Tank capacity is not a constant. It steps mid-horizon when a tank project
    # lands - 4129 gains 300,000 gal on a `=CP23+300000` formula, 9703 loses
    # 80,000 - and flattening it to one number lets the optimizer plan to fill a
    # tank past what it holds on the days before the step. The engine has always
    # read it as an effective-dated series; only the model spec dropped the
    # dates.
    #
    # Aggregates keep their configured figure: the diesel pool's capacity is an
    # invented number already (six of its ten members have no tank recorded), so
    # a per-day series assembled from the members would be a false precision.
    for product, meta in spec.products.items():
        blocks = [b for b in ref.blocks
                  if spec.aliases.get(b.get("charge_code"),
                                      b.get("charge_code")) == product]
        if len(blocks) != 1:
            continue
        series = sim.balances.get(blocks[0]["id"], {}).get("Tank Capacity", {})
        by_date = {d.isoformat(): series[d] for d in dates
                   if series.get(d)}
        if not by_date or len(set(by_date.values())) == 1:
            continue
        meta["capacity_by_date"] = by_date
        spec.warnings.append(
            "{}: tank capacity changes during the window ({:,.0f} to {:,.0f}). "
            "Bounded per day; a single figure would let the model overfill it "
            "by {:,.0f} gal.".format(product, min(by_date.values()),
                                     max(by_date.values()),
                                     max(by_date.values()) - min(by_date.values())))

    for block in ref.blocks:
        bal = sim.balances.get(block["id"], {})
        rows = {}
        for label in ("Sales", "Forecast", "Blends"):
            series = {d.isoformat(): bal.get(label, {}).get(d, 0.0)
                      for d in dates if bal.get(label, {}).get(d, 0.0)}
            if series:
                rows[label] = series
        if rows:
            spec.demand_rows[block["id"]] = rows

    # A block whose "Production In" reads the production grid under *another*
    # code is being fed by that code's yield, not by its own. Isomerate (1128)
    # takes its production from 9501 Isomerate Run - the run is converted the
    # moment it is made, so the workbook books it straight into the finished
    # product's block. There is no yield rule with 1128 as its output anywhere,
    # so without this the optimizer credits isomerate with no production at all
    # and books 350,000 gal of lost sales it would never actually incur.
    for block in ref.blocks:
        code = block.get("charge_code")
        target = resolve(code) if code else None
        if target is None or target not in spec.products:
            continue
        for region in (block["rows"].get("Production In") or {}).get(
                "regions") or []:
            for term in region["spec"]["terms"]:
                if term.get("kind") != "production_grid":
                    continue
                src_code = term.get("code")
                if not src_code or src_code == code:
                    continue
                src = resolve(src_code)
                if src == target:
                    continue        # already handled as a tank alias (9116/9718)
                spec.production_alias[src] = target
                spec.notes.append(
                    "{} takes its production from {}'s yield: the workbook books "
                    "it into {}'s block directly, and no yield rule names {} as "
                    "an output.".format(target, src, target, target))

    # A product has exactly one downgrade outlet. Confirmed with operations: the
    # route is a physical line to one destination, not a choice between several.
    #
    # Enforced rather than observed. It happens to hold in the workbook's own
    # routes, so nothing checks it - and a second outlet added later would not
    # fail, it would quietly hand the optimizer a choice the plant cannot make,
    # and it would take that choice whenever the second outlet priced better.
    outlets = defaultdict(list)
    for route in spec.sink_routes:
        outlets[route["product"]].append(route["sink"])
    forked = {p: s for p, s in outlets.items() if len(set(s)) > 1}
    if forked:
        raise ValueError(
            "a product may downgrade to one outlet only, but {} - each route is "
            "a line to one destination, not a choice".format(
                "; ".join("{} goes to {}".format(p, " and ".join(sorted(set(s))))
                          for p, s in sorted(forked.items()))))

    bad = [r for r in spec.reconciliation if not r["ok"]]
    if bad:
        raise ValueError("aggregation lost material: {}".format(
            [r["product"] for r in bad]))
    return spec
