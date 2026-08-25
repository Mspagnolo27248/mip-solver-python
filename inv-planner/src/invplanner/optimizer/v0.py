"""v0: charge levels and downgrade routing, with the unit assignments fixed.

The first step of the build order in MIP-FORMULATION.md, and deliberately an LP -
no binaries at all. The planner's choice of *what* each unit charges is taken as
given; the optimizer chooses only *how much*, and where the resulting overflow
goes.

The point of starting here is diagnostic. If this is infeasible or produces
something silly, the problem is the data or the balance constraints, not MIP
hardness - and that distinction has been the hard one to make. It also exercises
the whole loop end to end: parameters in, solve, a schedule out that the manual
side can open.

What it decides:
    chg[line, day]        barrels charged, within the rate ceiling and the day's
                          remaining time after changeover losses
    dg[product, sink, d]  gallons pushed to a downgrade outlet
    lost[product, day]    demand that goes unserved

What it respects:
    0 <= inventory <= capacity, hard, every product every day
    the material balance, including every line that consumes a product
    a throughput floor: a unit charged on a non-outage day still runs at
        `charge_floor_fraction` of what the planner scheduled
    terminal inventory: the window ends no poorer than it began, elastic
"""
from __future__ import annotations

import datetime as dt
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .. import economics, model_config as cfg
from ..engine import GAL_PER_BBL, Reference, Scenario, month_key

try:
    import pulp
except ImportError:      # pragma: no cover - checked at call time
    pulp = None


#: How far a pinned charge may drift from the schedule handed in. See `pin_units`.
PIN_TOLERANCE = 1e-6


def line_rate(spec, unit: str, line_key: str, product: str):
    """The charge ceiling for a line, as the *spec* has it.

    Read from the spec rather than straight from `model_config`, because the
    spec is where a planner's edit has been folded in. Calling the config
    directly is what made the rate editor a silent no-op: the override was
    written to the reference payload, `modelprep` never applied it, and the
    solver asked the config for the original number every time.

    Keyed by line, so extraction's two ways of running 9305 keep their own
    ceilings. Falls back to the config for anything the spec does not carry.
    """
    info = ((spec.units.get(unit) or {}).get("max_rate_by_line") or {}).get(line_key)
    return info if info else cfg.max_rate_info(unit, product, line_key)


def spec_line_charges(spec, line_key: str) -> Optional[str]:
    """The product a charge line consumes."""
    for line in spec.charge_lines:
        if line["key"] == line_key:
            return line["product"]
    return None


class OptimizeResult:
    def __init__(self) -> None:
        self.status: str = "not_run"
        self.message: str = ""
        self.objective: Optional[float] = None
        self.solve_seconds: float = 0.0
        self.solver: str = ""
        self.charge: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.downgrade: Dict[str, Dict[str, Dict[str, float]]] = {}
        self.lost_sales: Dict[str, Dict[str, float]] = {}
        self.sink_offtake: Dict[str, Dict[str, float]] = {}
        #: Only populated when diagnosing: which constraint families needed
        #: relief, by how much, and where the worst of it is.
        self.binding: Dict[str, Any] = {}
        #: (product, sink) pairs with no schedule line to write them onto, so
        #: they cannot be part of the answer yet.
        self.unroutable: List[str] = []
        #: dg decisions mapped back onto charge lines, in barrels, ready to be
        #: written into the result scenario.
        self.transfer_bbl: Dict[str, Dict[str, float]] = {}
        #: unit -> {day: line the unit is set up for}, only for freed units.
        self.schedule: Dict[str, Dict[str, Optional[str]]] = {}
        #: product -> {day: gallons in tank at end of day}. Needed to hand a
        #: window's closing tanks to the next one, and the only way to see a
        #: tank run dry without replaying the whole schedule.
        self.inventory: Dict[str, Dict[str, float]] = {}
        self.kpis: Dict[str, Any] = {}


def solve(ref: Reference, scn: Scenario, spec, params: Dict[str, Any],
          horizon: Optional[List[dt.date]] = None,
          downtime: Optional[Dict[str, set]] = None,
          elastic: bool = False,
          free_units: Optional[List[str]] = None,
          pin_units: Optional[List[str]] = None,
          opening_override: Optional[Dict[str, float]] = None,
          setup_override: Optional[Dict[str, str]] = None,
          terminal_condition: bool = True,
          boundary_day: Optional[str] = None) -> OptimizeResult:
    """Solve v0, or v1 for whichever units are freed.

    `free_units` names the units whose *assignment* becomes a decision: they get
    setup binaries, changeover arcs on the allowed set only, and a day-time
    budget, in place of the charge floor that pins every other unit near the
    planner's own schedule. Empty is exactly v0.

    The balance, the tanks, the downgrade routing and the terminal condition are
    shared verbatim between the two - deliberately. Every defect this model has
    had lived in the balance, and a second copy of it for v1 would drift from the
    first at exactly the moment nobody was looking.

    `elastic=True` relaxes every hard constraint with a heavily penalised slack.
    The model then always solves, and whichever slacks come back non-zero name
    the constraint that was making it infeasible - a poor man's IIS, but one that
    also reports *how much* each constraint is short by, which is usually the
    more useful half.
    """
    if pulp is None:
        raise RuntimeError("pulp is not installed")

    res = OptimizeResult()
    dates = horizon or scn.dates[:params.get("horizon_days", 42)]
    # Every caller comes through here, so the boundary is enforced once. Asking
    # for days the workbook cannot answer for does not fail loudly - it returns a
    # confident schedule built on demand nobody stands behind, or an
    # infeasibility whose cause is three layers from the error.
    asked = len(dates)
    dates = cfg.clamp_horizon(dates)
    if not dates:
        res.status = "no_solution"
        res.message = ("the whole horizon falls past {}, the last day the "
                       "workbook's inputs can be believed"
                       .format(cfg.DATA_VALID_THROUGH))
        return res
    truncated = asked - len(dates)
    #: Units whose charges are held exactly at the schedule handed in, so a
    #: decomposition can settle one unit and pass it on untouched. See
    #: `optimizer/v2.py` for why that is the only way the hydrotreater solves.
    pinned = set(pin_units or [])
    #: What a window inherits from the one before it: tanks as they were left,
    #: and what each freed unit was set up for on the last committed day. Without
    #: both, consecutive windows are separate plans that happen to be adjacent -
    #: the second would restart every tank at the plan's opening and every unit
    #: on the plan's first feed, and owe no flush for doing it.
    opening_at = dict(opening_override or {})
    setup_at = dict(setup_override or {})
    down = downtime or {}
    iso = [d.isoformat() for d in dates]
    free = set(free_units or [])
    # The global default. Per-unit and per-arc figures live in `model_config`,
    # because what a changeover wastes is a property of the unit and the pair of
    # feeds, not of the scenario; `switch_cost_by_unit` lets a planner calibrating
    # against campaign lengths override one unit without editing code. A unit
    # named nowhere gets this scalar, which is how every unit behaved before.
    switch_cost = float(params.get("switch_cost") or 0.0)
    switch_cost_by_unit = params.get("switch_cost_by_unit") or {}

    # ------------------------------------------------------- objective mode
    #: "cost" minimises money given up; "margin" maximises money earned. Both are
    #: built from the same variables and the same balance - only the objective
    #: differs - so a scenario can be scored under each and the two compared.
    #: Kept side by side rather than replaced until the margin one has earned it;
    #: see MARGIN-AND-CAMPAIGNS.md for the order the change is meant to land in.
    margin = str(params.get("objective") or "cost").lower() == "margin"
    #: Guardrail: force one netback for every product and every sink. If revenue
    #: and the old penalties describe the same problem, a flat price should leave
    #: the schedule where it was - and it tests the *mechanism* without waiting
    #: for a single real netback, which is why it can run before the price list
    #: exists. Only meaningful with `charge_floor_fraction` at 1.0: below that,
    #: margin is free to run harder than cost ever would and the schedules differ
    #: for a reason that has nothing to do with the objective being wrong.
    flat_netback = params.get("flat_netback_per_gal")

    lost_margin = float(params.get("lost_sale_margin_per_gal") or 0.0)
    discount = float(params.get("downgrade_discount_per_gal") or 0.50)
    # How much of the planner's own charge each unit must still run. Without a
    # floor the objective counts only costs and never rewards production, so the
    # cheapest answer is to stop making oil: at zero this model shut the
    # Platformer off entirely and ran the plant at 34% of plan. At 1.0 the
    # charges are pinned to the schedule and v0 optimises routing alone, which
    # is the cleanest way to separate "is the routing right?" from "is the
    # charge optimisation right?".
    floor = float(params.get("charge_floor_fraction", 1.0))
    # What ending the window short costs, per gallon. Defaults to the downgrade
    # discount, because that is the economics it shares: ending short is material
    # you have to replace, not margin you have lost. Pricing it against the
    # lost-sale margin instead builds in the assumption that drawing a tank down
    # is nearly as bad as failing to ship a customer, and the plan showed what
    # that assumption costs - 310,736 gal of demand shorted to protect stock.
    # `or discount`, not a .get default: the key is always present once it is a
    # column, carrying None to mean "unset".
    terminal_cost = float(params.get("terminal_shortfall_per_gal") or discount)
    netbacks = {
        "SIX_OIL": discount, "CAT": discount,
        "DIESEL": params.get("netback_diesel_cost_per_gal", discount),
        "GASOLINE": params.get("netback_gasoline_cost_per_gal", discount),
    }

    # ------------------------------------------------------- measured margins
    # Operations' own gross profit per gallon, where it exists. Gross profit
    # already nets the crude cost, so it *is* the margin a lost sale gives up,
    # and the difference between two of them is what a downgrade gives up. No
    # conversion and no crude arithmetic.
    #
    # The flat assumptions this replaces were wrong in both directions and by a
    # long way: every lost sale was $1.50/gal against a real range of $0.50 to
    # $6.00, and every downgrade $0.50/gal when #6 oil and the cat cracker turn
    # out to be *negative* at -$0.32. Downgrading a $6.00 solvent to #6 oil costs
    # $6.32, not $0.50 - a factor of twelve.
    gp = economics.load_gross_profit()
    #: product -> $/gal it earns when sold as itself
    value_of: Dict[str, float] = {}
    for p in spec.products:
        v = economics.gross_profit(p, spec, gp)
        if v is not None:
            value_of[p] = v
    #: sink -> $/gal the material still earns once it lands there
    sink_value = {}
    for sink_id, sink in cfg.SINKS.items():
        landing = cfg.SINK_LANDS_IN.get(sink_id)
        v = value_of.get(landing) if landing else None
        if v is None:
            v = gp.get(sink_id)
        if v is not None:
            sink_value[sink_id] = v
    unpriced = sorted(p for p in spec.products
                      if p not in value_of and spec.products[p].get("tanked"))

    # The guardrail, applied once at the source. Every price the objective can
    # reach is derived from these three tables, so flattening them here flattens
    # `lost_cost`, `dg_cost`, `sell_cost`, `terminal_cost_for` and `netback_of`
    # together. Overriding at each use site instead leaves some prices flat and
    # others measured, which is not a controlled comparison - it is a fourth
    # objective nobody designed, and the first attempt at this shorted seven
    # million gallons because `lost_cost` had been left on the real numbers.
    #
    # Keep it above `downgrade_discount_per_gal`: `lost_cost` floors a lost sale
    # at the disposal cost, so a flat price below that floor is not flat.
    if flat_netback is not None:
        flat = float(flat_netback)
        value_of = {p: flat for p in spec.products}
        sink_value = {sink_id: flat for sink_id in cfg.SINKS}
        netbacks = {k: flat for k in netbacks}

    prob = pulp.LpProblem("v0_charge_and_downgrade",
                          pulp.LpMaximize if margin else pulp.LpMinimize)
    PENALTY = 1e6
    slacks: Dict[str, Dict[str, Any]] = defaultdict(dict)

    def slack(family: str, name: str):
        """A penalised relief variable, only when diagnosing."""
        if not elastic:
            return None
        v = pulp.LpVariable("slk_{}_{}".format(family, name), 0)
        slacks[family][name] = v
        return v

    # ------------------------------------------------------------- variables
    lines = [l for l in spec.charge_lines
             if not l["unit"].startswith(cfg.TRANSFER_UNIT_PREFIXES)]

    # Intermediates with no tank anywhere in the plant - the light straight run
    # and the isomerate run, inside the platformer cascade. They cannot
    # accumulate, so whatever is made of them must be charged the same day. That
    # makes the downstream unit's charge a *consequence* of the upstream unit's,
    # not a decision with a rate of its own: the plan runs the isomerisation unit
    # at exactly 0.16194 x the fractionator on all 305 days it runs, without a
    # single deviation, because the planner has been computing this identity by
    # hand. Modelled as an equality below rather than a ceiling.
    untanked = {p for p, d in spec.products.items()
                if not d["tanked"] and not d.get("is_sink")}
    planned = {}      # (line, iso) -> barrels the planner scheduled
    for line in lines:
        for d, s in zip(dates, iso):
            planned[(line["key"], s)] = scn.charge_bbl(line["key"], d)

    chg = {}
    for line in lines:
        unit = line["unit"]
        info = line_rate(spec, unit, line["key"], line["workbook_product"])
        ceiling = info["bbl"] if info else None
        flows = line["key"] in cfg.FLOW_THROUGH_LINES
        # Charging an untanked intermediate: the identity fixes the level, so no
        # rate and no floor apply. A ceiling here would be a second, weaker
        # statement of the same thing, and could contradict it.
        determined = line["product"] in untanked
        freed = unit in free
        if freed and not ceiling:
            raise ValueError(
                "{} is on a freed unit but has no maximum rate; charge = rate x "
                "time cannot be written".format(line["key"]))
        for d, s in zip(dates, iso):
            was = planned[(line["key"], s)]
            if d in down.get(unit, set()):
                continue                       # outage: nothing runs
            if was <= 0 and not (flows or determined or freed):
                continue                       # assignment fixed: not scheduled
            if freed:
                # The assignment is a decision now. The level is bounded by the
                # setup and time variables below, not by what the planner ran,
                # and the floor does not apply - must-run and the day budget
                # take its place.
                hi, lo = ceiling, 0.0
            elif determined:
                hi, lo = None, 0.0
            elif flows:
                # Runs every day whatever the schedule says. Free between zero
                # and the rate ceiling: the material has nowhere else to go, so
                # the tank constraint is what makes the unit run, not a floor.
                if not ceiling:
                    raise ValueError(
                        "{} is flow-through but has no maximum rate; "
                        "charge = rate x time cannot be written".format(
                            line["key"]))
                hi, lo = ceiling, 0.0
            else:
                # Most recorded maxima are *floors* - the largest charge ever
                # observed, which is evidence of what the unit has done and not
                # of what it can do. The plan exceeds several of them, so raising
                # the ceiling to the planner's own charge keeps the schedule
                # admissible rather than declaring the plant infeasible.
                #
                # A rate confirmed with operations is the opposite: a real limit,
                # and one the plan violates on 3 of 151 hydrotreater days. Let
                # those through and the optimizer inherits a day the unit cannot
                # run - so a confirmed ceiling binds even against the plan.
                # `RATE_BASIS_IS_FLOOR` is what tells the two apart.
                is_floor = cfg.RATE_BASIS_IS_FLOOR.get(
                    (info or {}).get("basis"), True)
                if ceiling and not is_floor:
                    hi = ceiling
                else:
                    hi = max(ceiling if ceiling else was, was)
                # Clamp: on a day the plan overran a confirmed ceiling the floor
                # would otherwise sit above it and the model would be infeasible.
                lo = min(was * floor, hi)
            if unit in pinned:
                # Held where it is, not merely floored. A stage that has already
                # decided a unit's schedule has to hand it on untouched; leaving
                # the floor to do it would let the next stage take 70% of the
                # decision back, and the two stages would no longer add up to one
                # schedule.
                #
                # A hair of tolerance rather than a flat equality, because an
                # exact pin is infeasible on a knife edge. Stage one drains 9302
                # - a dewaxed oil MEK makes and extraction eats - to precisely
                # zero on 2026-08-02, and pinning producer and consumer to the
                # same float leaves the balance no room to reproduce it: the
                # elastic diagnostic returns one violation of magnitude *zero*.
                # A part per million of 5,000 bbl is five thousandths of a
                # barrel, which is nothing physically and everything numerically.
                hi = was * (1.0 + PIN_TOLERANCE)
                lo = was * (1.0 - PIN_TOLERANCE)
            chg[(line["key"], s)] = pulp.LpVariable(
                "chg_{}_{}".format(line["key"].replace("#", "_"), s), lo, hi)

    # Downgrade decisions replace whatever the planner entered on the transfer
    # lines, so the result has to be writable back onto those lines - otherwise
    # the schedule the simulator replays is not the one the optimizer solved.
    # Gasoline has no line in the workbook, so it cannot be expressed yet and is
    # excluded here rather than being silently unrepresentable.
    sink_lines = {}
    for line in spec.charge_lines:
        for sid, sink in cfg.SINKS.items():
            if sink.get("outlet") == line["unit"]:
                sink_lines[(line["product"], sid)] = line["key"]

    routes = defaultdict(list)
    unroutable = set()
    for r in spec.sink_routes:
        if (r["product"], r["sink"]) in sink_lines:
            routes[r["product"]].append(r["sink"])
        else:
            unroutable.add((r["product"], r["sink"]))
    res.unroutable = sorted("{}->{}".format(p, s) for p, s in unroutable)

    dg = {}
    for product, sinks in routes.items():
        for sink in set(sinks):
            for s in iso:
                dg[(product, sink, s)] = pulp.LpVariable(
                    "dg_{}_{}_{}".format(product, sink, s), 0)

    tanked = {p: d for p, d in spec.products.items() if d["tanked"]}
    # When diagnosing, capacity becomes an explicit constraint with a slack so the
    # solver can report by how much it is short; otherwise it is a variable bound.
    def cap_on(p, s):
        """The tank's capacity on one day, which is not always the same day to day."""
        dated = tanked[p].get("capacity_by_date") or {}
        return dated.get(s, tanked[p]["capacity"]) or None

    inv = {(p, s): pulp.LpVariable(
        "inv_{}_{}".format(p, s), 0,
        None if elastic else cap_on(p, s))
        for p in tanked for s in iso}
    lost = {(p, s): pulp.LpVariable("lost_{}_{}".format(p, s), 0)
            for p in tanked for s in iso}

    # A sink has unlimited offtake but a real tank: diesel can always be sold
    # down at the netback, but it can still overfill if nothing sells it. Without
    # this the sinks accumulate whatever they absorb and the model is infeasible
    # the moment one of them reaches capacity.
    sell = {(p, s): pulp.LpVariable("sell_{}_{}".format(p, s), 0)
            for p, d in tanked.items() if d.get("unlimited_offtake")
            for s in iso}

    # What a gallon sold as-is gives up. A sink sold beyond its own forecast is
    # priced flat; a product with offtake of its own is priced against whatever
    # it would otherwise have become, which is how the diesel charge outlet can
    # be costed today without waiting for an absolute netback: selling it raw
    # gives up the diesel downgrade cost plus the $0.60/gal discount.
    #
    # With measured gross profit the differential retires: 9713 has a price of
    # its own, so lifting a gallon as-is gives up what processing it would have
    # earned instead - 0.98 gal of finished diesel - less what the raw sale
    # fetches. That is the trade stated directly rather than inferred.
    sell_cost = {}
    for p, d in tanked.items():
        if not d.get("unlimited_offtake"):
            continue
        # Same reasoning as `dg_cost`: a credit for what the sale fetches, with
        # the opportunity left to the balance. Lifting a gallon of diesel charge
        # as-is earns $0.35; whether that was better than hydrotreating it into
        # finished diesel is a comparison the balance makes, not this line.
        raw = value_of.get(p)
        processed = None
        for rule in spec.yield_rules:
            if rule["kind"] == "unit" and rule.get("charge_line")                     and spec_line_charges(spec, rule["charge_line"]) == p:
                made = value_of.get(rule["out"])
                if made is not None:
                    processed = max(processed or 0.0, made * rule["yield"])
        if raw is not None and processed is not None:
            sell_cost[p] = max(0.0, processed - raw)
            continue
        rel = d.get("offtake_relative_to")
        if rel:
            sell_cost[p] = (netbacks.get(rel, discount)
                            + float(d.get("offtake_discount_per_gal") or 0.0))
        else:
            sell_cost[p] = discount

    # ------------------------------------------------------------- balance
    produces = defaultdict(list)     # product -> [(line_key, gal per bbl charged)]
    alias = getattr(spec, "production_alias", {}) or {}
    for rule in spec.yield_rules:
        if rule["kind"] == "unit":
            # Some production is booked under one code and lands in another
            # product's tank - isomerate run into isomerate. Credit it where the
            # workbook puts it, or the receiving product looks unproduceable.
            produces[alias.get(rule["out"], rule["out"])].append(
                (rule["charge_line"], rule["yield"] * GAL_PER_BBL))

    # The hydrotreater returns what it did not convert to the diesel charge pool:
    # charge x (1/yield - 1) on every R2 line. It is a `diesel_yield_back` rule
    # rather than a `unit` one, so the loop above walked straight past it and the
    # optimizer never saw the material at all - 1,857,314 gal over 160 days into
    # a 1,200,000 gal tank. Under 42 days the tank absorbed it and nothing looked
    # wrong; past that the replay overflowed by exactly the amount the optimizer
    # had not been told about.
    #
    # The coefficient is `1 - yield`, not `1/yield - 1`. The engine applies its
    # factor to the row's *output* (charge x yield), so the two compose to
    # charge x (1 - yield); applying `1/yield - 1` to the charge itself
    # overstates the return by about 18% at a 0.85 yield, which is enough to
    # make the optimizer charge diesel the tank cannot supply.
    #
    # The terms name production-out rows, so they are matched back to their
    # charge lines through `po_row` on the reference's own yield rules.
    line_of_po_row = {r["po_row"]: r["charge_line"] for r in ref.yield_rules
                      if r.get("kind") == "unit" and r.get("po_row") is not None
                      and r.get("charge_line")}
    for rule in spec.yield_rules:
        if rule["kind"] != "diesel_yield_back":
            continue
        target = alias.get(rule["out"], rule["out"])
        for term in rule["terms"]:
            y = term.get("yield") or 0.0
            key = line_of_po_row.get(term.get("source_po_row"))
            if key and y > 0:
                produces[target].append((key, (1.0 - y) * GAL_PER_BBL))
    consumes = defaultdict(list)     # product -> [line_key]
    for line in lines:
        consumes[line["product"]].append(line["key"])

    # Where a transfer lands. Downgraded material does not disappear: it becomes
    # diesel charge, #6 oil or cat feed, and the receiving pool has to carry it
    # or the balance quietly creates and destroys oil.
    lands_in = {}                    # line_key -> product that receives it
    for rule in spec.yield_rules:
        if rule["kind"] != "transfer":
            continue
        for key in rule.get("charge_lines", []):
            lands_in[key] = rule["out"]

    # Crude supply. The spec keeps the mode-gated streams apart so a later
    # version can choose between them, but v0 takes the assignments as given -
    # including the mode - so only the stream the plan actually runs counts.
    # Summing both would supply 9117 *and* 9118 at full rate every day, as if the
    # crude unit ran two modes at once.
    fixed_in = defaultdict(lambda: defaultdict(float))
    for product, streams in spec.fixed_supply.items():
        for stream, series in streams.items():
            gate = stream[6:] if stream.startswith("_mode_") else None
            for s, gal in series.items():
                if gate and scn.crude_mode.get(s) != gate:
                    continue
                fixed_in[product][s] += gal

    # Lines that are not unit charges still move material, and leaving them out
    # was what made v0 infeasible: 9118 is made in L-mode and sold to Sonneborn,
    # but the workbook booked that sale against 9202. Retiring 9202 removed the
    # demand and left the production with nowhere to go.
    #
    #   SONNEBORN  a sale - fixed demand on the product itself
    #   RAILCAR    a receipt - fixed supply
    #   TRANSFER_* to a sink outlet: a downgrade the optimizer decides through
    #              `dg`, so deliberately not fixed here
    #   TRANSFER_* to anything else: a real outflow with no decision attached.
    #              `TRANSFER_FINDSL#135` moves Kensol 30 to finished diesel and
    #              is the only one - it has no sink, so `dg` never covered it and
    #              the volume simply vanished from the optimizer's balance. That
    #              let 4111 look 81,000 gal richer than the simulator found it.
    #              Held at the planner's value, which is also what gets replayed,
    #              since `_write_result_scenario` only resets sink lines.
    outlets = {s["outlet"] for s in cfg.SINKS.values()}
    fixed_out = defaultdict(lambda: defaultdict(float))
    for line in spec.charge_lines:
        unit = line["unit"]
        if unit in outlets:
            continue
        if unit.startswith("RAILCAR"):
            for d, s in zip(dates, iso):
                gal = scn.charge_bbl(line["key"], d) * GAL_PER_BBL
                if gal:
                    fixed_in[line["product"]][s] += gal
        elif unit.startswith(cfg.TRANSFER_UNIT_PREFIXES):
            lands = lands_in.get(line["key"])
            for d, s in zip(dates, iso):
                gal = scn.charge_bbl(line["key"], d) * GAL_PER_BBL
                if not gal:
                    continue
                fixed_out[line["product"]][s] += gal
                # material moved, not destroyed: the receiving pool carries it
                if lands:
                    fixed_in[lands][s] += gal

    # Demand comes off the simulated balance rows, which already net forecast
    # against firm orders exactly as the workbook does.
    demand = defaultdict(lambda: defaultdict(float))
    for b in ref.blocks:
        code = b.get("charge_code")
        target = spec.aliases.get(code, code)
        if target not in tanked:
            continue
        for row in ("Sales", "Forecast", "Blends"):
            series = spec.demand_rows.get(b["id"], {}).get(row, {})
            for s, gal in series.items():
                demand[target][s] += gal

    for p in tanked:
        opening = opening_at.get(p, tanked[p]["opening"])
        for i, s in enumerate(iso):
            prev = inv[(p, iso[i - 1])] if i else opening
            made = pulp.lpSum(
                chg[(k, s)] * factor for k, factor in produces.get(p, [])
                if (k, s) in chg)
            used = pulp.lpSum(chg[(k, s)] * GAL_PER_BBL
                              for k in consumes.get(p, []) if (k, s) in chg)
            out = pulp.lpSum(dg[(p, sink, s)] for sink in set(routes.get(p, [])))
            # material arriving from someone else's downgrade
            arrives = pulp.lpSum(
                dg[(src, sink, s)]
                for (src, sink), key in sink_lines.items()
                if lands_in.get(key) == p and (src, sink, s) in dg)
            offtake = sell[(p, s)] if (p, s) in sell else 0
            # relief on the capacity ceiling, and on the floor of zero
            over = slack("capacity", "{}_{}".format(p, s))
            under = slack("negative_inventory", "{}_{}".format(p, s))
            relief = under if under is not None else 0
            prob += (inv[(p, s)] == prev + made + fixed_in[p].get(s, 0.0)
                     + arrives - used - demand[p].get(s, 0.0)
                     - fixed_out[p].get(s, 0.0)
                     + lost[(p, s)] - out - offtake + relief), \
                "bal_{}_{}".format(p, s)
            cap = cap_on(p, s)
            if elastic and cap and over is not None:
                prob += inv[(p, s)] <= cap + over, "cap_{}_{}".format(p, s)
            prob += lost[(p, s)] <= demand[p].get(s, 0.0), \
                "lostcap_{}_{}".format(p, s)

    # -------------------------------------------------- assignment, where freed
    # The setup-carryover formulation from MIP-FORMULATION section 2, keyed on
    # the charge *line* rather than the product. A unit that runs one feed two
    # ways has two setup states on one product, and a product-keyed arc set
    # cannot say so - extraction's normal-to-deep switch is 9305 -> 9305 at
    # product level, a self-loop the formulation cannot represent at all.
    #
    # Forbidden transitions are not constrained away: they get no variable, so
    # the solver cannot reach for them and the relaxation is not weakened by
    # excluding them.
    setup, arc, tfrac = {}, {}, {}
    #: arc -> $ it costs to take it. Priced once here rather than in the
    #: objective, because this is the only scope that knows which unit an arc
    #: belongs to and what feed sits at each end of it.
    arc_cost: Dict[tuple, float] = {}
    day_zero: Dict[str, str] = {}
    #: line -> the reactor it runs on, and unit -> the reactors that name a
    #: minimum visit. Both empty for a unit with one reactor or none, which is
    #: what keeps the visit constraint off every unit that has not asked for it.
    reactor_of_line: Dict[str, Optional[str]] = {}
    visit_reactors: Dict[str, List[str]] = {}
    for unit in sorted(free):
        ulines = [l for l in lines if l["unit"] == unit]
        keys = [l["key"] for l in ulines]
        arcs = cfg.line_arcs(unit, ulines) or []
        rate = {l["key"]: line_rate(spec, unit, l["key"],
                                   l["workbook_product"])["bbl"]
                for l in ulines}
        minrun = {l["key"]: cfg.min_run_days_for_line(
            unit, l["key"], l["workbook_product"]) for l in ulines}
        reactor_of_line.update({l["key"]: cfg.reactor_of(unit,
                                                         l["workbook_product"])
                                for l in ulines})
        # Only a unit that actually has two reactors here can cross between
        # them, so only that unit can owe a minimum visit.
        on_unit = sorted({r for r in (reactor_of_line[l["key"]] for l in ulines)
                          if r})
        visit_reactors[unit] = [r for r in on_unit
                                if len(on_unit) > 1
                                and cfg.min_reactor_visit_days(unit, r) > 1]
        minrate = {l["key"]: rate[l["key"]] * cfg.min_rate_fraction(
            unit, l["workbook_product"]) for l in ulines}
        feed = {l["key"]: l["workbook_product"] for l in ulines}
        loss = {(a, b): cfg.changeover_loss_days(unit, feed[a], feed[b])
                for a, b in arcs}
        # The cash the day budget does not already charge for. Keyed on the arc
        # rather than the unit so a reactor crossing can cost more than an
        # ordinary switch, the way its lost time already does.
        arc_cost.update({
            (a, b): cfg.switch_cost_for(unit, feed[a], feed[b], switch_cost,
                                        switch_cost_by_unit)
            for a, b in arcs})

        # `t` is continuous and needs no indicator of its own. An earlier draft
        # carried a binary "this line receives time today", but `t <= s[d-1] +
        # s[d]` says the same thing against variables that are already binary,
        # and "at most two products a day" follows from one changeover a day
        # rather than needing its own constraint. Dropping it removed a third of
        # the binaries for nothing.
        for k in keys:
            for s in iso:
                setup[(k, s)] = pulp.LpVariable(
                    "su_{}_{}".format(k.replace("#", "_"), s), cat="Binary")
                if (k, s) in chg:
                    tfrac[(k, s)] = pulp.LpVariable(
                        "t_{}_{}".format(k.replace("#", "_"), s), 0, 1)
        # **The arcs do not need to be binary, and making them so is what puts
        # the hydrotreater out of reach.** With `one_setup` forcing the setups to
        # sum to one and the carry equation below linking consecutive days, the
        # arcs carry flow between two unit vectors - a transshipment structure,
        # totally unimodular, so a vertex of the relaxation is already integral.
        # The cap of one changeover a day and a positive cost keep them there.
        #
        # It is the difference between a model that solves and one that does not.
        # The hydrotreater offers 42 arcs against 7 lines, so at 21 days binary
        # arcs cost 882 binaries against the setups' 147 - six times as many, on
        # the one unit with no minimum campaign to prune the search. MEK and
        # extraction have 6 and 9 arcs and never felt it.
        #
        # `integral_arcs` restores the binaries for anyone who wants to check the
        # claim rather than take it: the answers must not move.
        arc_cat = "Binary" if params.get("integral_arcs") else "Continuous"
        for a in arcs:
            for s in iso:
                arc[(a, s)] = pulp.LpVariable(
                    "z_{}_{}_{}".format(a[0].replace("#", "_"),
                                        a[1].replace("#", "_"), s),
                    0, 1, cat=arc_cat)

        # What the unit is holding when the horizon opens. The live feed should
        # supply this; until it does, the last thing the plan ran before the
        # window is the best available proxy, and a wrong answer costs day one a
        # free or phantom changeover.
        opening_key = None
        for d in reversed([x for x in scn.dates if x < dates[0]]):
            hit = [k for k in keys if scn.charge_bbl(k, d) > 0]
            if hit:
                opening_key = hit[0]
                break
        if opening_key is None:
            for d in dates:
                hit = [k for k in keys if scn.charge_bbl(k, d) > 0]
                if hit:
                    opening_key = hit[0]
                    break
        day_zero[unit] = setup_at.get(unit) or opening_key or keys[0]

        # ------------------------------------------------------- warm start
        # The planner's own schedule is a feasible assignment, so hand it to the
        # solver as an incumbent rather than making branch-and-bound discover one.
        # Only the binaries are seeded: the continuous levels are cheap to
        # recover, and the plan violates a rate ceiling on EXTRACT#89 anyway
        # (1,800 recorded, 2,100 run), so seeding those would hand CBC a start it
        # has to reject.
        held, plan_setup = day_zero[unit], {}
        for d, s in zip(dates, iso):
            on = [k for k in keys if scn.charge_bbl(k, d) > 0]
            if on:
                held = on[-1]
            plan_setup[s] = held
        for k in keys:
            for s in iso:
                setup[(k, s)].setInitialValue(1 if plan_setup[s] == k else 0)
        was_held = day_zero[unit]
        for s in iso:
            now = plan_setup[s]
            for a in arcs:
                arc[(a, s)].setInitialValue(
                    1 if (a == (was_held, now) and was_held != now) else 0)
            was_held = now

        for i, s in enumerate(iso):
            prev = iso[i - 1] if i else None
            # exactly one setup at all times, idle or not
            prob += pulp.lpSum(setup[(k, s)] for k in keys) == 1, \
                "one_setup_{}_{}".format(unit, s)
            for k in keys:
                before = setup[(k, prev)] if prev else (
                    1 if k == day_zero[unit] else 0)
                prob += (setup[(k, s)] == before
                         + pulp.lpSum(arc[((p, k), s)] for p in keys
                                      if (p, k) in arcs)
                         - pulp.lpSum(arc[((k, q), s)] for q in keys
                                      if (k, q) in arcs)), \
                    "carry_{}_{}".format(k.replace("#", "_"), s)
                # Time only on what the unit is set up for, at either end of the
                # day - which is what lets a changeover day carry both charges.
                if (k, s) in tfrac:
                    prob += tfrac[(k, s)] <= before + setup[(k, s)], \
                        "tcap_{}_{}".format(k.replace("#", "_"), s)
                    prob += chg[(k, s)] <= rate[k] * tfrac[(k, s)], \
                        "rate_{}_{}".format(k.replace("#", "_"), s)
                    # A line that gets time must actually be run. Without this,
                    # must-run binds the day and nothing binds the volume, so the
                    # unit comes back nominally running and empty. Scaled by the
                    # time it gets, so a changeover day is held to the fraction
                    # of the day it actually had.
                    if minrate[k] > 0:
                        prob += chg[(k, s)] >= minrate[k] * tfrac[(k, s)], \
                            "minrate_{}_{}".format(k.replace("#", "_"), s)

            # One changeover a day.
            #
            # The plan contains two days with two changeovers, out of 366, so
            # this is the approximation the switching analysis already identified
            # and judged worth taking. It buys two things beyond speed. It makes
            # the arc restriction **hard**: with a single arc a day the setup
            # moves exactly one allowed step, where two let the model reach an
            # unobserved pair through an intermediate it never ran - which it
            # duly did, EXTRACT#87 -> #90 via #89. And it makes the minimum-run
            # constraint unnecessary at L = 1, which was the previous way of
            # closing that hole and cost a constraint per line per day.
            prob += pulp.lpSum(arc[(a, s)] for a in arcs) <= 1, \
                "swcap_{}_{}".format(unit, s)
            # The day's time is shared between charging and changing over.
            budget = (pulp.lpSum(tfrac[(k, s)] for k in keys if (k, s) in tfrac)
                      + pulp.lpSum(loss[a] * arc[(a, s)] for a in arcs))
            prob += budget <= 1.0, "budget_{}_{}".format(unit, s)

            d = dates[i]
            if d in down.get(unit, set()):
                pass          # no charge variables exist on an outage day
            elif cfg.MUST_RUN_WHEN_AVAILABLE:
                # Idling is not a free choice. Note this binds *time*: a unit
                # notionally on and charging nothing is exactly the non-answer
                # must-run exists to forbid, so the day has to be fully allocated
                # between charging and changing over.
                #
                # It still does not bound *volume* - charge is free in
                # [0, rate x t] while minimum rates are zero. Until those are
                # filled in, or the objective rewards production rather than
                # only penalising its absence, a freed unit can allocate its day
                # and charge very little. See MIN_RATE_FRACTION.
                prob += budget >= 1.0, "mustrun_{}_{}".format(unit, s)

            # A stock that is started stays on the unit for its minimum campaign.
            #
            # Written one day at a time rather than as a single sum over the
            # window. The two say the same thing about integer solutions, but the
            # disaggregated form is the tighter relaxation - the aggregated
            # version lets a fractional setup spread itself across the window and
            # satisfy the sum without ever committing to a day, which is exactly
            # the slack a branch-and-bound search then has to close by hand.
            #
            # L = 1 needs nothing: one changeover a day already means a stock
            # switched into is still there at end of day.
            for k in keys:
                L = minrun[k]
                if L <= 1 or i + L > len(iso):
                    continue
                entering = pulp.lpSum(arc[((p, k), s)] for p in keys
                                      if (p, k) in arcs)
                for w in iso[i:i + L]:
                    prob += setup[(k, w)] >= entering, \
                        "minrun_{}_{}_{}".format(k.replace("#", "_"), s, w)

            # And the same thing one level up: a unit that goes to a reactor
            # stays on it.
            #
            # The line-level minimum above cannot express this. It says how long
            # a *charge* runs, so it lengthens a visit while leaving the model
            # free to leave R1 for a Kensol and come straight back - measured at
            # R1 3 / R2 2 over 42 days, that is exactly what it did, taking 6
            # crossings against 3 with no minimum at all. The plant's rule is
            # about the reactor, so the constraint has to be too.
            #
            # A reactor's state is not a variable here: the freed formulation
            # carries the flush in the arc costs and builds no `ron` binary, the
            # way the fixed-assignment branch does. It does not need one - the
            # setups sum to one a day, so the setups of a reactor's lines sum to
            # exactly the indicator "the unit is on this reactor today", and
            # entering the reactor is the crossing arcs into it. Both are already
            # built, so this adds constraints and no variables.
            # Clamped to the window rather than skipped near its end, which is
            # where this parts company with `minrun` above. Skipping lets a visit
            # begun inside the last L days escape the rule entirely, and the
            # model takes that: at 18 days it went to R1 on day 16, ran one day
            # and left for R2 - a one-day visit with two clear days after it,
            # which is the exact behaviour the constraint exists to forbid. A
            # visit that cannot be proven to complete must still not be
            # abandoned, so it is held to the end of the window instead.
            for r in visit_reactors.get(unit, ()):
                L = cfg.min_reactor_visit_days(unit, r)
                if L <= 1:
                    continue
                mine = [k for k in keys if reactor_of_line[k] == r]
                entering = pulp.lpSum(
                    arc[((p, k), s)] for (p, k) in arcs
                    if k in mine and reactor_of_line.get(p) != r)
                for w in iso[i:i + L]:
                    prob += pulp.lpSum(setup[(k, w)] for k in mine) >= entering, \
                        "minvisit_{}_{}_{}_{}".format(unit, r, s, w)

    # ------------------------------------------------- flow-through identities
    # No tank means no buffer: an untanked intermediate is made and consumed in
    # the same instant, so production equals consumption exactly, every day.
    #
    # This is stronger than a rate ceiling and replaces it. The isomerisation
    # unit has no recorded maximum anywhere, and it does not need one - it takes
    # the light straight run the fractionator hands it and nothing else. The real
    # limit downstream is the fractionator outrunning the reformer and filling
    # the Kensol 17 tank; the isomerisation unit is never the binding constraint.
    #
    # Without this the model tracked neither side: untanked products get no
    # balance row, so the optimizer could charge more light straight run than the
    # fractionator made, or less, with no consequence either way.
    for p in sorted(untanked):
        makers = produces.get(p, [])
        users = consumes.get(p, [])
        if not makers or not users:
            continue          # nothing to tie together (9501 is booked into 1128)
        for s in iso:
            made = pulp.lpSum(chg[(k, s)] * factor for k, factor in makers
                              if (k, s) in chg)
            used = pulp.lpSum(chg[(k, s)] * GAL_PER_BBL for k in users
                              if (k, s) in chg)
            prob += made == used, "flow_{}_{}".format(p, s)

    # ------------------------------------------------------ terminal inventory
    # The window has to hand the next one a plant in the state it found it. Left
    # out, the cheapest schedule drains every tank to serve demand inside the
    # horizon and books the consequences after the last day, where nothing
    # scores them - a large free win that evaporates the moment the horizon
    # rolls forward.
    #
    # Elastic, per the formulation: a hard floor makes the window infeasible
    # whenever demand genuinely exceeds what the plant can supply, which is a
    # real situation the planner needs an answer for rather than a solver error.
    #
    # Priced *well* below the lost-sale margin, and the gap matters twice over.
    #
    # Priced level, the two are interchangeable and the solver picks between them
    # arbitrarily - it booked 98,746 gal of Argold lost sales it did not have to,
    # purely because the tie had to break somewhere.
    #
    # Priced merely a little below, it still outranks service in aggregate: at
    # $1.35/gal against a $1.50/gal margin the plan shorted 310,736 gal more
    # demand than at $0.50, to hold stock it did not need. A lost sale is margin
    # gone for good; ending short is material to replace. They are not the same
    # order of cost, and the parameter should not be expressed as a fraction of
    # the margin, which quietly asserts that they are.
    #
    # The plan is insensitive anywhere in $0.45-$0.75/gal - identical schedule -
    # so this sits on a plateau rather than on a knife edge, which is what you
    # want from a number nobody has measured yet. What would replace it is a real
    # safety stock per product; no product has a lower control limit anywhere in
    # the workbook, which is why opening inventory is the only anchor available.
    # Priced per product, and strictly below that product's own lost sale.
    #
    # A flat figure worked while every lost sale was worth the same $1.50. With
    # measured margins it stopped: isomerate is worth $0.50/gal, exactly the flat
    # terminal price, so shorting a customer on day one to hold stock for day
    # forty-two became cost-neutral and the solver did it - 50,000 gal of lost
    # sales with 307,208 gal sitting in the tank. Tying the terminal price to the
    # product's own margin keeps the ordering right at every price level.
    def terminal_cost_for(p):
        # Tied to the product's own margin where one is known. The flat cap has
        # to go once downgrades became credits: at $0.50 it sat below the $0.35 a
        # sink pays, so the model could earn more by dumping a tank than it lost
        # by ending short - and it would have drained the plant into the diesel
        # pool for a profit. Ninety percent of the margin keeps it below a lost
        # sale, which is what stops it shorting customers to hold stock.
        return min(terminal_cost, 0.9 * lost_cost(p))

    # Off for every window but the last. "End no poorer than you started" is a
    # statement about the *plan*, not about an arbitrary internal boundary -
    # applied to each window it would force every one of them to rebuild its
    # tanks by its own final day, which is a constraint the plant never faces and
    # would quietly undo the point of carrying inventory forward at all.
    term_short = {}
    if terminal_condition:
        for p in tanked:
            v = pulp.LpVariable("term_{}".format(p), 0)
            term_short[p] = v
            prob += inv[(p, iso[-1])] + v >= tanked[p]["opening"],                 "term_{}".format(p)

    # ------------------------------------------------- what a window leaves behind
    # A window hands its tanks to the next one, and the next one cannot see what
    # is coming for them. That is survivable for most products and fatal for a
    # few, and which few is not a matter of taste - it is arithmetic.
    #
    # 4313 is the case that taught this. Crude makes it at a fixed 10.7% yield
    # whether anyone wants it or not, ROSE is its **only** exit, it carries 10 k
    # gal of demand over 160 days and has no downgrade route at all. Its tank is
    # 440,000 gal against an inflow near 48,700 gal/day - about nine days of
    # buffer. ROSE then has a nine-day turnaround, which delivers ~438,000 gal
    # into a 440,000 gal tank: **the outage inflow is the whole tank.** So 4313
    # has to be nearly empty when ROSE goes down, and ROSE can only out-draw the
    # inflow by about a third, so the drawdown takes roughly four weeks.
    #
    # A window that ends four weeks before that outage plans the drawdown in its
    # lookahead and then throws it away, and the next window inherits a full tank
    # with a fortnight to empty it. It cannot, and the run dies on a capacity
    # violation dated the outage's last day.
    #
    # A headroom fraction cannot express this. It was tried at 0.85 and 0.70 and
    # failed both times, for two reasons worth keeping: it is soft, so a window
    # pays it and fills the tank anyway; and a *fraction of capacity* is the wrong
    # unit for a tank measured in days of an inflow nobody controls.
    #
    # So the target is computed rather than chosen: leave room for exactly the
    # inflow the next outage will deliver. Priced high enough to dominate the
    # ordinary trade-offs, and still finite - an infeasible window kills the run,
    # which is the failure this exists to prevent.
    DRAIN_PENALTY = 100.0
    drain = {}
    edge = boundary_day or (iso[-1] if not terminal_condition else None)
    if edge and edge in iso:
        edge_date = dates[iso.index(edge)]
        for p in tanked:
            cap = cap_on(p, edge)
            if not cap:
                continue
            # Whoever eats this product is who has to be running for it to leave.
            eaters = {l["unit"] for l in lines if l["product"] == p}
            need = 0.0
            for unit in sorted(eaters):
                # `down` is the scenario's own downtime, set on the planning
                # side and handed to the solver per run - so a turnaround moved
                # in the app moves the drawdown that protects the tank with it.
                out_days = cfg.next_outage(down.get(unit, ()), edge_date)
                if not out_days:
                    continue
                # Only the fixed arrivals count. What the units make is a decision
                # the next window still has; what crude delivers is not.
                need = max(need, sum(fixed_in[p].get(d.isoformat(), 0.0)
                                     for d in out_days))
            if need <= 0:
                continue
            v = pulp.LpVariable("drain_{}".format(p), 0)
            drain[p] = v
            prob += inv[(p, edge)] - v <= max(0.0, cap - need),                 "drain_{}".format(p)

    # ------------------------------------------------------------ safety stock
    # The terminal condition pins the last day against the first and says nothing
    # about the 98 in between, so a tank could sit at zero for weeks and satisfy
    # every constraint in the model. It did: isomerate spent 41 of 100 days empty
    # and gave up 244,981 gal of demand the planner's own schedule serves in full,
    # because at $0.50/gal it is the cheapest thing in the book to short.
    #
    # Elastic, and that is not a preference. 9511 has no production at all in this
    # data - the reformer row was never carried - so a hard floor on it would make
    # the model infeasible for a reason that has nothing to do with the schedule.
    #
    # Days of cover rather than a tank level, because it is the honest shape of
    # what is known: nobody has set a lower control limit, but demand is measured.
    # `SAFETY_STOCK_GAL` overrides per product as real figures arrive.
    # **Off by default**, and deliberately, for the same reason `SWITCH_COST_BY_UNIT`
    # and `MIN_RATE_FRACTION` ship empty: days of cover is a placeholder for a
    # number nobody has measured, and a placeholder that changes every answer is
    # worse than one that waits. It also costs real solve time - at 42 days it is
    # the difference between v1 proving optimality inside two minutes and not.
    #
    # Solved exactly (pure LP, `mip_gap` 0), cover of 0 through 7 days gives a
    # bit-identical answer at both 42 and 100 days - except 7 days at 42, which
    # *creates* 16,233 gal of lost isomerate, because stock held is stock not
    # shipped. As priced the floor is soft and sits far below a lost sale, so the
    # model absorbs the penalty rather than rearranging the plan.
    #
    # Which is the useful result: the tanks are not empty for want of a buffer.
    # Isomerate is short because production earns nothing under the cost
    # objective, so the cheapest gallon in the book goes first, and no floor can
    # conjure material the model declined to make. Anything measured at the 2%
    # gap in normal use is smaller than the gap - compare at gap 0 or not at all.
    safety_days = float(params.get("safety_stock_days", 0.0) or 0.0)

    def safety_floor(p):
        override = cfg.SAFETY_STOCK_GAL.get(p)
        if override is not None:
            return float(override)
        # Sinks are disposal outlets, not products anyone protects: #6 oil and
        # the cat cracker carry "demand" that is somewhere to put material.
        if not safety_days or tanked[p].get("unlimited_offtake"):
            return 0.0
        served = sum(demand[p].values())
        if served <= 0:
            return 0.0
        return safety_days * served / float(len(dates))

    def safety_price(p):
        """What a gallon-day below the floor costs.

        Spread across the horizon and tied to the product's own lost sale, so a
        gallon held below the floor for the *whole* window costs a fixed fraction
        of shorting a customer once. That ordering has to hold at every price
        level, which a flat figure cannot promise: the margins here run from
        $0.50 to $6.00, and a penalty that outranks a lost sale would have the
        model protecting stock by refusing orders - the exact failure
        `terminal_shortfall_per_gal` was tuned away from.
        """
        return safety_fraction * lost_cost(p) / float(len(dates))

    safety_fraction = float(params.get("safety_stock_penalty_fraction", 0.5) or 0.0)
    below = {}
    for p in tanked:
        floor_gal = safety_floor(p)
        if floor_gal <= 0:
            continue
        for s in iso:
            v = pulp.LpVariable("below_{}_{}".format(p, s), 0)
            below[(p, s)] = v
            prob += inv[(p, s)] + v >= floor_gal, "safe_{}_{}".format(p, s)

    # --------------------------------------------------------- day capacity
    # One time budget per unit, because a unit that changes over can only do one
    # thing at a time. A cascade cannot: the splitter, the reformer and the
    # isomerisation unit are separate vessels running simultaneously, which is
    # why the Platformer charges two or more lines on 305 of 366 days. Giving it
    # a shared day would force the plant to choose between stages it runs
    # together - and once the reformer had a rate, that alone made the model
    # infeasible. Each vessel is still bounded by its own maximum rate.
    # A unit with two reactors owes a flush every time it starts one up, and the
    # flush comes out of the same day. Nothing charged it before: the flush lives
    # in the arc formulation, which only freed units get, so on a unit whose
    # assignment is fixed **crossing reactors was free**. The hydrotreater duly
    # crossed 17 times against the plan's 14, and ran both reactors on 5 days
    # against the plan's 1 - because `HYDRO#76`, the diesel draw, is flow-through
    # on R2 and may run on any day at no cost, including days R1 is running.
    #
    # Counting *start-ups* rather than same-day overlaps is what makes this
    # faithful: only 1 of the plan's 14 crossings has both reactors inside one
    # day, so the rest happen at a day boundary and a same-day test would miss
    # thirteen of them.
    #
    # The setup state persists across idle days on purpose - a reactor still
    # holds its last feed while the unit sits, so resuming the *same* reactor
    # owes nothing and resuming the other still owes the flush. That is why an
    # idle or outage day does not reset `was_on`.
    reactor_flush = bool(params.get("charge_reactor_flush", True))
    for unit in sorted({l["unit"] for l in lines}):
        if unit in cfg.CASCADE_UNITS:
            continue
        if unit in free:
            continue          # has a real time budget, above, with the arc loss
        ulines = [l for l in lines if l["unit"] == unit]
        rspec = cfg.UNIT_REACTORS.get(unit)
        reactors = sorted({r for r in (cfg.reactor_of(unit, l["workbook_product"])
                                       for l in ulines) if r}) if rspec else []
        # One reactor cannot be crossed, and this is the only thing that adds
        # binaries to v0 - so a unit that does not need them does not get them.
        crossing = reactor_flush and rspec and len(reactors) > 1
        flush_days = float(rspec["flush_days"]) if crossing else 0.0
        was_on = {}
        for d, s in zip(dates, iso):
            if d in down.get(unit, set()):
                continue
            terms = []
            by_reactor = defaultdict(list)
            for line in ulines:
                if (line["key"], s) not in chg:
                    continue
                info = line_rate(spec, unit, line["key"], line["workbook_product"])
                if info:
                    t = chg[(line["key"], s)] * (1.0 / info["bbl"])
                    terms.append(t)
                    by_reactor[cfg.reactor_of(unit, line["workbook_product"])].append(t)
            if not terms:
                continue
            flush = []
            if crossing:
                # The unit is set up for exactly one reactor a day, and may only
                # produce on the one it is set up for. The `<=` link alone is not
                # enough and the first draft of this shipped with only that: an
                # indicator bounded below by production and above by nothing can
                # be switched on for free, so the model held both reactors "on"
                # permanently, never registered a change, and paid no flush. It
                # read as a 3-crossing regression that was really solver noise
                # inside the 2% gap.
                #
                # Summing to one is what gives it teeth, and it is also the
                # physics: the reactors are sequential on one train - the plan
                # shows both on 1 running day in 91, which is a crossing caught
                # mid-day rather than concurrency.
                now = {r: pulp.LpVariable("ron_{}_{}_{}".format(unit, r, s),
                                          cat="Binary") for r in reactors}
                prob += pulp.lpSum(now.values()) == 1, \
                    "ronly_{}_{}".format(unit, s)
                for r in reactors:
                    rt = by_reactor.get(r)
                    if rt:
                        # Time is already a fraction of a day, so 1 is the
                        # tightest valid big-M and no looser one is needed.
                        prob += pulp.lpSum(rt) <= now[r], \
                            "ron_{}_{}_{}".format(unit, r, s)
                if was_on:
                    # One flush a day at most: with the setups summing to one, a
                    # change turns exactly one reactor on and one off, so
                    # charging per reactor would bill the same crossing twice.
                    up = pulp.LpVariable("flush_{}_{}".format(unit, s), 0, 1)
                    for r in reactors:
                        prob += up >= now[r] - was_on[r], \
                            "flush_{}_{}_{}".format(unit, r, s)
                    flush.append(up)
                was_on = now
            extra = slack("day_time", "{}_{}".format(unit, s))
            prob += pulp.lpSum(terms) + flush_days * pulp.lpSum(flush) <= 1.0 + (
                extra if extra is not None else 0), \
                "day_{}_{}".format(unit, s)

    # --------------------------------------------------- products per day
    # How many *different* feeds a unit may charge in one day. The time budget
    # above does not imply this: three products at a third of a day each fit the
    # day perfectly well, and the hydrotreater duly ran three on 3 days of a
    # 160-day schedule against a plant limit of two. The spec has carried
    # `max_products_per_day` since the build was written and nothing had ever
    # read it - the constraint existed everywhere except in the model.
    #
    # This costs a binary per line per day on any unit with more room than the
    # limit, so it is written only where it can actually bind. Freed units get
    # theirs from the setup variables instead, which already carry it.
    for unit in sorted({l["unit"] for l in lines}):
        if unit in free or unit in cfg.CASCADE_UNITS:
            continue
        cap = (spec.units.get(unit) or {}).get("max_products_per_day")
        ulines = [l for l in lines if l["unit"] == unit]
        if not cap or len(ulines) <= cap:
            continue                  # cannot be violated: nothing to write
        for d, s in zip(dates, iso):
            if d in down.get(unit, set()):
                continue
            on = []
            for line in ulines:
                var = chg.get((line["key"], s))
                if var is None:
                    continue
                # `upBound` is this line's own ceiling for the day, so it is the
                # tightest valid big-M. A loose one would leave the relaxation
                # weak and the branch-and-bound slow for no gain.
                big_m = var.upBound
                if not big_m:
                    continue
                y = pulp.LpVariable(
                    "on_{}_{}".format(line["key"].replace("#", "_"), s),
                    cat="Binary")
                prob += var <= big_m * y, "on_{}_{}".format(
                    line["key"].replace("#", "_"), s)
                on.append(y)
            if len(on) > cap:
                prob += pulp.lpSum(on) <= cap, "nprod_{}_{}".format(unit, s)

    # Changeovers a *fixed* unit inherits from the plan.
    #
    # The arcs above exist only for freed units, so with nothing freed the
    # objective's changeover term summed over nothing and v0 was charged $0 for
    # switching while silently inheriting every changeover the planner made -
    # 119 of them over 100 days, $238,000 at the default price. That made the
    # objective mean a different thing in every model version and inverted the
    # comparison: v0 read 8.6% better than v2 at 100 days, and on equal terms
    # v2 is 20.9% *better* at 42.
    #
    # Priced as a constant, deliberately. A fixed unit cannot avoid these - it
    # does not choose its assignments - so a constant cannot move its argmin,
    # and v0's schedule must come back bit-identical. What changes is only that
    # the number on the end means the same thing as v2's.
    #
    # Cascade units are excluded because they run several lines at once, so
    # "what the unit is set up for" is not a question about them. Transfers
    # never reach here: `lines` has already dropped them.
    inherited_switches: Dict[str, int] = {}
    inherited_cost = 0.0
    for unit in sorted({l["unit"] for l in lines}):
        if unit in free or unit in cfg.CASCADE_UNITS:
            continue
        ulines = [l for l in lines if l["unit"] == unit]
        feed = {l["key"]: l["workbook_product"] for l in ulines}
        held, n = None, 0
        for d in dates:
            on = [l["key"] for l in ulines if scn.charge_bbl(l["key"], d) > 0]
            if not on:
                continue                  # idle: the unit keeps its last feed
            now = on[-1]
            if held is not None and now != held:
                n += 1
                inherited_cost += cfg.switch_cost_for(
                    unit, feed[held], feed[now], switch_cost, switch_cost_by_unit)
            held = now
        if n:
            inherited_switches[unit] = n

    # ------------------------------------------------------------ objective
    # A lost sale gives up the product's own margin; a downgrade gives up the
    # difference between where it was going and where it ends up. Both fall back
    # to the flat assumptions for anything operations has not priced yet, and
    # `unpriced` says which those are so a report can never present a floor as a
    # measurement.
    def lost_cost(p):
        v = value_of.get(p)
        if v is None:
            return lost_margin
        # Gasoline is a sale, not disposal: not reforming leaves naphtha
        # unreformed, not platformate sitting in a tank. So the floor below does
        # not apply and the margin given up is the gasoline margin itself.
        if not cfg.is_disposal(p):
            return max(0.0, v)
        # Never below the cost of getting rid of the material. #6 oil carries a
        # gross profit of -$0.32/gal, and taken literally that makes failing to
        # serve its demand *profitable* - the model duly booked 25,000 gal of
        # lost sales to avoid selling at a loss. But the demand is disposal, not
        # a sale: declining it does not make the oil vanish, it leaves it in the
        # tank to be got rid of some other way. So the floor is the disposal
        # cost, and a lost sale is never a reward.
        return max(v, discount)

    def dg_cost(p, sink):
        """Margin given up by routing a gallon of `p` to `sink`.

        Known to over-count, and kept anyway until the objective is rebuilt
        properly. The balance already charges the opportunity - remove a gallon
        and either its own demand goes unserved or the unit downstream makes less
        - so charging the difference here counts it a second time. It also
        assumes the alternative was selling `p` as itself, when a gallon of 9711
        is worth $1.40 sold and $1.29 as the 4115 the hydrotreater makes from it.

        Making the transfer free and crediting the sink instead was tried and is
        worse: it drove lost sales from 774 k to 2.2 M gal and v1's solve from
        three minutes to nearly four hours, because a cost-minimising objective
        with no revenue for *serving* demand cannot have revenue for dumping it.
        Half a margin objective is worse than none. The real fix is the whole one
        - see MARGIN-OBJECTIVE.md - where every gallon earns exactly once,
        wherever it ends up, and this function disappears.
        """
        landed = sink_value.get(sink)
        if p in value_of and landed is not None:
            # **Not the margin gap.** A downgraded gallon does not forgo a sale:
            # if it displaced one, demand goes unserved and the `lost` term
            # charges the whole netback for it, so charging `value - landed`
            # here as well bills the same gallon twice. And if demand was
            # already met there was no sale to forgo at all, which made the
            # charge pure fiction - $5,585,893 of a $7,370,310 objective over
            # 100 days, on four products with zero unserved demand between them.
            #
            # That fiction is what backed the tanks up. Surplus Kensol 30 costs
            # $2.30/gal to move to diesel and nothing to leave where it is, so
            # the model left it, filled its tank, and then had to throttle the
            # platformer - which backed naphtha up to its own ceiling. The
            # plant just ships it.
            #
            # What a downgrade really gives up is the chance to sell the gallon
            # *after* the window, which is exactly what terminal value prices.
            # So the residual is holding value less what the sink pays. For
            # Kensol 30 that is zero; for Kensol 61 it is three cents.
            return max(0.0, terminal_value_of(p) - landed)
        return netbacks.get(sink, discount)

    # ------------------------------------------------------ margin objective
    #: What a gallon still owned at the end is worth, as a fraction of what it
    #: fetches sold. Below 1 on purpose: stock in the tank on the last day is not
    #: cash, it is a gallon that still has to be sold.
    #:
    #: **This is the most sensitive number in the margin objective, and nobody
    #: has measured it.** It is not really a price - it is standing in for
    #: everything after the last day, which the model cannot see. Set it high and
    #: holding stock rivals selling it, so the plant hoards; set it to zero and
    #: every tank must be emptied by the final day, so it dumps.
    #:
    #: Measured over 42 days at a 0.75 charge floor - the highest floor that is
    #: feasible - every run proven optimal. The cost objective on the same
    #: scenario loses 1,172,658 gal and charges 596,047 bbl.
    #:
    #:     f      lost sales     downgrade      offtake    charge bbl
    #:     0.00      618,868     6,086,900    3,962,985       634,044
    #:     0.25      854,224     5,152,079    3,501,021       657,571
    #:     0.50    1,626,964     3,720,088    2,514,814       682,890
    #:     0.75    2,921,754     1,284,689    1,510,003       647,543
    #:     0.90    3,024,625     1,284,689    1,527,799       649,537
    #:
    #: Monotone, and it moves lost sales by a factor of five while throughput
    #: barely responds - so this decides where material *goes*, not how much is
    #: made. Above 0.5 the model shorts more demand than the cost objective did,
    #: to hold stock.
    #:
    #: **0.25 because operations said so, not because it is the middle.** The
    #: rule given is: run to the finished-good tanks rather than hold upstream
    #: when there is spare capacity, but never at the expense of real demand. A
    #: low fraction is exactly that rule in a price - holding earns little, so
    #: converting and shipping win. At 0.25 the plan loses 27% fewer gallons than
    #: the cost objective *and* charges 10.3% more; both halves of the rule are
    #: satisfied at once, which is not true anywhere above 0.5.
    #:
    #: The real fix is still not a better fraction. It is a rolling horizon, or a
    #: safety stock per product - the thing `terminal_shortfall_per_gal` has been
    #: standing in for all along, because the window ends where the data ends and
    #: not where the plant stops caring.
    TERMINAL_VALUE_FRACTION = float(
        params.get("terminal_value_fraction", 0.25) or 0.0)

    def netback_of(p):
        """$/gal `p` earns when it leaves the system as itself.

        Reads `value_of`, which the guardrail has already flattened if it is
        running - there is one place prices are set, and this is not it.
        """
        v = value_of.get(p)
        # No measured margin: fall back to the same flat assumption the cost
        # objective uses for an unpriced lost sale, so neither mode is quietly
        # reasoning from a number the other cannot see.
        return lost_margin if v is None else v

    def terminal_value_of(p):
        """What a gallon of `p` left in the tank on the last day is worth.

        **Zero for anything unpriced**, and that is not a detail. The unpriced
        products are the intermediates - MEK's waxy neutrals 9117/9118/9119, the
        dewaxed oils 9302/9303/9305 that extraction eats, and deep extract 9705 -
        and `netback_of` falls back to the lost-sale margin, $1.50/gal. Finished
        diesel is worth $0.80 and diesel charge $0.35, so that fallback prices
        work in progress at nearly double the finished product it becomes, and
        the model books a profit for *not* converting it. 1.36 M gal of opening
        inventory sits on those products.

        Confirmed with operations, and it is stronger than "unmeasured": **the
        dewaxed and waxy feeds cannot be sold at all.** Waxy medium neutral does
        sell, but there is no additional outlet for it - so an extra gallon in
        that tank cannot be turned into money either. Zero is the right price for
        every one of them, not a conservative stand-in for a number still coming.

        This is also what makes the objective prefer the plant's own rule -
        *push to the finished-good tanks rather than hold upstream when there is
        spare capacity, but never at the expense of real demand*. Holding
        intermediate earns nothing, converting it earns the finished product's
        terminal value, and serving actual demand earns the full netback, which
        is strictly more than either. The ordering falls out of the prices; it
        needs no rule of its own.
        """
        if p not in value_of:
            return 0.0
        # Never negative. Stock you own cannot be worth less than leaving it
        # where it is, and #6 oil's measured -$0.32/gal would otherwise pay the
        # model to empty a tank it has nowhere to empty into.
        return max(0.0, TERMINAL_VALUE_FRACTION * netback_of(p))

    if margin:
        # Every gallon earns exactly once, wherever it ends up.
        #
        # **Downgrades carry no term at all**, and that is the whole reason
        # `dg_cost` disappears rather than being rewritten. Every sink lands in a
        # product that is itself tanked with unlimited offtake, so a downgrade
        # moves material between tanks and does not leave the system - it earns
        # later, when the landing product is sold or serves its own demand.
        # Crediting the sink here as well would pay for the same gallon twice.
        # The cost of downgrading emerges instead of being priced: the landing
        # product's netback is lower, so the gallon simply earns less.
        #
        # Served demand is `demand - lost`, and demand is a constant, so the only
        # variable part is the revenue a lost sale does not collect. `lost_cost`
        # rather than `netback_of` on purpose - it carries the disposal floor
        # that stops shorting a negative-margin product from being a reward.
        prob += (
            - pulp.lpSum(v * lost_cost(p) for (p, s), v in lost.items())
            + pulp.lpSum(v * netback_of(p) for (p, s), v in sell.items())
            + pulp.lpSum(inv[(p, iso[-1])] * terminal_value_of(p) for p in tanked)
            # Falling below safety stock is value given up under either
            # objective, so it is subtracted here and added there - the same
            # term, not a second policy.
            - pulp.lpSum(v * safety_price(p) for (p, s), v in below.items())
            - DRAIN_PENALTY * pulp.lpSum(drain.values())
            - pulp.lpSum(v * arc_cost.get(a, switch_cost)
                         for (a, s), v in arc.items())
            - inherited_cost
            - pulp.lpSum(v for fam in slacks.values()
                         for v in fam.values()) * PENALTY
        )
    else:
        prob += (
            pulp.lpSum(v * lost_cost(p) for (p, s), v in lost.items())
            + pulp.lpSum(dg[(p, sink, s)] * dg_cost(p, sink)
                         for (p, sink, s) in dg)
            # Selling a sink down beyond its forecast is still value given up, so
            # it is costed - otherwise the model would dump everything into
            # diesel.
            + pulp.lpSum(v * sell_cost.get(p, discount)
                         for (p, s), v in sell.items())
            + pulp.lpSum(v * terminal_cost_for(p)
                         for p, v in term_short.items())
            + pulp.lpSum(v * safety_price(p) for (p, s), v in below.items())
            + DRAIN_PENALTY * pulp.lpSum(drain.values())
            # Only what lost time does not already capture - labour, quality
            # giveaway, the interface that really is slopped off. The time itself
            # is paid for in the day budget, by the production it displaces.
            #
            # Priced per arc. A single figure across every unit cannot be
            # calibrated: the campaign lengths it has to reproduce run from
            # HYDRO's 1 day to ROSE's 40, so the number that gives one unit its
            # campaigns freezes another. `arc_cost` falls back to the global
            # scalar for any unit nobody has calibrated, which is every unit
            # until someone does.
            + pulp.lpSum(v * arc_cost.get(a, switch_cost)
                         for (a, s), v in arc.items())
            # ...and the ones a fixed unit inherits, so both are counted
            + inherited_cost
            + pulp.lpSum(v for fam in slacks.values()
                         for v in fam.values()) * PENALTY
        )

    t0 = time.time()
    # `warmStart` only bites when there are binaries to seed. Proving optimality
    # on this model is expensive and not worth paying for: the objective is
    # nearly flat across many schedules, so the last fraction of a percent costs
    # far more search than it is worth to a planner. Take the gap.
    #
    # **The absolute gap is the one that means anything here, and it is the one
    # to set.** A cost objective measures value *given up*, so it shrinks toward
    # zero as the model gets better - and a relative gap therefore tightens
    # every time anything is fixed, with no decision behind it. Removing the
    # phantom downgrade charge cut the objective roughly fourfold and the same
    # 2% went from tens of thousands of dollars of slack to about twelve, at
    # which point nothing proved inside its time limit and eleven tests that
    # never mentioned optimality failed together.
    #
    # In dollars the question has an answer a planner can give: a changeover
    # costs $2,000, so $10,000 is "do not spend an hour proving something worth
    # five changeovers". That number stays put whatever the objective does.
    #
    # `gapRel` is kept and defaults to zero. CBC stops at whichever bound it
    # reaches first, so leaving it set would silently reintroduce the treadmill
    # on the day someone raised it.
    gap_abs = params.get("mip_gap_abs")
    if gap_abs is None:
        gap_abs = cfg.DEFAULT_GAP_ABS
    solver = pulp.PULP_CBC_CMD(msg=0,
                               timeLimit=params.get("time_limit_seconds", 300),
                               gapRel=params.get("mip_gap") or 0.0,
                               gapAbs=float(gap_abs),
                               warmStart=bool(free))
    status = prob.solve(solver)
    res.solve_seconds = time.time() - t0
    res.solver = "CBC"
    res.objective = pulp.value(prob.objective)

    # Read `sol_status`, not `status`. PuLP's CBC reader rewrites the status when
    # CBC stops on a time limit with an incumbent in hand:
    #
    #     if status == LpStatusNotSolved and len(statusstrs) >= 5:
    #         if statusstrs[4] == "objective":
    #             status = LpStatusOptimal                     # <- overwritten
    #             sol_status = LpSolutionIntegerFeasible       # <- the truth
    #
    # so `LpStatus[...]` reads "Optimal" for a run that proved nothing, and the
    # real answer is only in the second field, which `solve()` does not return.
    #
    # This is not a corner case on this model. At 100 days v1 reports "Optimal"
    # at every time limit and `sol_status` is *never* "Optimal Solution Found" -
    # and the incumbent at a 20 s limit scored 10,095,452 against 8,114,383 at
    # 200 s, so the earlier run was 20% worse than achievable while claiming to
    # be the best there is. Every v1 number this project has called "optimal" was
    # a time-limited incumbent.
    #
    # The five outcomes are distinguishable here and were not before:
    #   Optimal Solution Found  proved best            -> optimal
    #   Solution Found          stopped early, usable  -> feasible
    #   No Solution Found       stopped with nothing   -> unusable
    #   No Solution Exists      infeasible             -> infeasible
    #   Solution is Unbounded   unbounded              -> unbounded
    sol = getattr(prob, "sol_status", None)
    res.status = {
        pulp.constants.LpSolutionOptimal: "optimal",
        pulp.constants.LpSolutionIntegerFeasible: "feasible",
        pulp.constants.LpSolutionInfeasible: "infeasible",
        pulp.constants.LpSolutionUnbounded: "unbounded",
        pulp.constants.LpSolutionNoSolutionFound: "no_solution",
    }.get(sol, pulp.LpStatus[status].lower())

    if elastic:
        used = {}
        for family, entries in slacks.items():
            hits = {n: float(v.value() or 0.0) for n, v in entries.items()
                    if (v.value() or 0.0) > 1e-6}
            if hits:
                used[family] = {"count": len(hits), "total": sum(hits.values()),
                                "worst": sorted(hits.items(),
                                                key=lambda kv: -kv[1])[:12]}
        res.binding = used

    # `feasible` is a real answer and worth keeping: on a 4-6 month plan the
    # binaries do not close the gap in any patience a planner has, and throwing
    # the incumbent away turns every long run into a failure with nothing to
    # show for it. Anything else has no schedule behind it.
    #
    # Nothing here inspects whether the variables carry values any more. They
    # always do - CBC hands back the last relaxation it held, so an infeasible
    # model and a stopped-with-nothing model are both fully populated, and both
    # used to be relabelled `feasible` and returned as a schedule. `sol_status`
    # answers the question that seeding never could.
    if res.status not in ("optimal", "feasible"):
        res.message = "solver returned {}".format(res.status)
        return res
    if truncated:
        res.message = ("horizon cut by {} day(s) to end {}, the last day the "
                       "workbook's inputs can be believed; solving {} days. "
                       .format(truncated, cfg.DATA_VALID_THROUGH, len(dates)))
    if res.status == "feasible":
        res.message += ("stopped after {:.0f}s with a usable schedule that is "
                       "NOT proved optimal - the gap is unknown, and a longer "
                       "run has scored 20% better on this model"
                       .format(res.solve_seconds))

    for (key, s), var in chg.items():
        res.charge[key][s] = float(var.value() or 0.0)
    routed = defaultdict(lambda: defaultdict(dict))
    for (p, sink, s), var in dg.items():
        v = float(var.value() or 0.0)
        if v > 1e-6:
            routed[p][sink][s] = v
    res.downgrade = {p: {k: dict(v) for k, v in d.items()}
                     for p, d in routed.items()}

    # Map the decisions back onto the transfer lines so the result scenario
    # carries the whole plan. Every sink line is reset, so a line the planner
    # used and the optimizer did not is cleared rather than left behind.
    transfer = {key: {s: 0.0 for s in iso} for key in sink_lines.values()}
    for (p, sink, s), var in dg.items():
        v = float(var.value() or 0.0)
        key = sink_lines.get((p, sink))
        if key and v > 1e-6:
            transfer[key][s] = v / GAL_PER_BBL
    res.transfer_bbl = transfer
    res.inventory = {p: {s: float(inv[(p, s)].value() or 0.0) for s in iso}
                     for p in tanked}
    res.lost_sales = {p: {s: float(lost[(p, s)].value() or 0.0) for s in iso
                          if (lost[(p, s)].value() or 0) > 1e-6}
                      for p in tanked}
    res.lost_sales = {p: v for p, v in res.lost_sales.items() if v}

    res.sink_offtake = {p: v for p, v in (
        (p, {s: float(sell[(p, s)].value() or 0.0) for s in iso
             if (sell[(p, s)].value() or 0) > 1e-6})
        for p in {k[0] for k in sell}) if v}

    res.kpis = {
        "lost_sales_gal": sum(sum(v.values()) for v in res.lost_sales.values()),
        "downgrade_gal": sum(sum(sum(x.values()) for x in d.values())
                             for d in res.downgrade.values()),
        "sink_offtake_gal": sum(sum(v.values())
                                for v in res.sink_offtake.values()),
        "charge_bbl": sum(sum(v.values()) for v in res.charge.values()),
        # How much the window ends short of where it started. Non-zero is not an
        # error - it says the plant genuinely could not both serve demand and
        # hold its tanks - but it is the plan borrowing from the next window, so
        # it belongs on the report rather than buried in the objective.
        "terminal_shortfall_gal": sum(float(v.value() or 0.0)
                                      for v in term_short.values()),
        "charge_floor_fraction": floor,
        "days": len(dates),
        #: Days cut off the end because they fall past `DATA_VALID_THROUGH`.
        #: Non-zero means the answer covers less than was asked for, which a
        #: report must say rather than quietly show a shorter plan.
        "horizon_truncated_days": truncated,
        "products": len(tanked),
    }

    if free:
        # What the freed units decided. Campaign shape is how a planner judges a
        # schedule, and it is emergent here: the formulation imposes adjacency
        # and a minimum run, never the sweep up and down the ladder that the
        # plant actually runs. If the sweep does not appear on its own, the
        # transition data is wrong rather than the solver.
        res.schedule = {}
        shape = {}
        for unit in sorted(free):
            keys = [l["key"] for l in lines if l["unit"] == unit]
            picked = []
            for s in iso:
                on = [k for k in keys
                      if (setup[(k, s)].value() or 0) > 0.5]
                picked.append(on[0] if on else None)
            res.schedule[unit] = dict(zip(iso, picked))
            runs, cur = [], 1
            for a, b in zip(picked, picked[1:]):
                if a == b:
                    cur += 1
                else:
                    runs.append(cur)
                    cur = 1
            runs.append(cur)
            switches = sum(round(v.value() or 0.0)
                           for (a, s), v in arc.items() if a[0] in keys)
            # Days the unit holds a setup and is allocated time, but charges
            # essentially nothing. Must-run binds *time*; nothing bounds volume
            # while minimum rates are zero, so the model can keep a unit
            # nominally running and put almost nothing through it. Reported
            # rather than hidden - it is the measure of what minimum rates would
            # buy, and a planner would reject these days on sight.
            live = [s for s, d in zip(iso, dates)
                    if d not in down.get(unit, set())]
            idle = sum(
                1 for s in live
                if sum(res.charge.get(k, {}).get(s, 0.0) for k in keys) < 1.0)
            shape[unit] = {
                "campaigns": len(runs),
                "median_campaign_days": sorted(runs)[len(runs) // 2],
                "longest_campaign_days": max(runs),
                "switches": int(switches),
                "switches_per_month": round(30.0 * switches / len(dates), 1),
                "day_zero_setup": day_zero[unit],
                "days_allocated_without_charge": idle,
                "days_available": len(live),
            }
        res.kpis["campaign_shape"] = shape
        res.kpis["switches"] = sum(s["switches"] for s in shape.values())
    # What the plan's own assignments cost, on the units this run did not free.
    # Reported next to the price so a reader can see what is being charged for
    # rather than inferring it from a total that moved.
    res.kpis["inherited_switches"] = inherited_switches
    res.kpis["inherited_switch_cost"] = inherited_cost
    return res
