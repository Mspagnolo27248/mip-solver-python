"""greedy: the three units schedule themselves by rule, in one forward pass.

Not a MIP. No branch and bound, no CBC, no gap. It walks the horizon a day at a
time carrying a tank ledger, and on each day it decides what each freed unit
should be set up for and how hard to run it. A decision made on day 12 is never
revised, which is exactly why it is fast and exactly why it is not optimal.

**Why this exists.** Every other path to a schedule goes through CBC. v2 is the
best of them and costs about five minutes over 100 days; the joint model cannot
prove anything past seven days. CBC's own primal heuristics do not help - they
are already on by default, and what they produce *is* the incumbent v2 returns.
The bottleneck is proving optimality, not finding a schedule. So the thing that
was missing was not a better search. It was an answer in seconds.

**What it gives up, stated plainly.** There is no bound. `greedy` cannot tell you
how far from optimal it is, and unlike a time-limited MIP it has no gap to quote.
It returns `feasible`, never `optimal`, and the run surfaces as
`not_proved_optimal` - which is the honest label, not a consolation. Judge it the
way everything else here is judged: by replaying it through the simulator.

**The rule.** Service first. Each unit is scored on days of cover of what its
charge *makes* - a line is worth running because the thing it produces is short -
and margin breaks ties. That is how a planner works, and it is defensible line by
line in a way "the feasibility pump landed here" is not.

**Order matters, and the chain is not the obvious one.** Units are decided
upstream first, MEK then EXTRACT then HYDRO, so a downstream unit sees the feed
the upstream one has just committed to making; the balance permits same-day
consumption of same-day production, which is what makes one pass enough. Two
corrections to the tidy picture, both of which would bite a scheduler that
assumed a clean chain: extraction is not MEK's only outlet (`MEK#72` makes 9720,
which feeds `HYDRO#83` directly), and extraction is not HYDRO's only feed (it
also takes 9720 from MEK, 9711/9712/9703 straight from crude, and the 9713 pool).

**ROSE is not freed** even though it is a decision unit. v2 does not free it, so
there is no formulation to check a rule against, and its campaigns run about 40
days - "start one and hold it" is most of what a rule would say. Its charges are
taken as the planner left them, which also makes its output fixed supply to MEK.

**Measured, against the seed plan, cost objective.** All figures are the
simulator's after replay, not this module's own arithmetic:

    42 days          time    verified   lost sales      downgrade    charge bbl
      v0            ~3 s     yes           774,339      2,794,262       590,004
      v1            ~9 s     yes           774,339      2,655,686       595,950
      v2           ~60 s     yes           795,603      2,520,402       591,729
      greedy      0.013 s    yes           830,390      1,834,412       599,171

So at 42 days it is **4.4% behind v2 on service, 27% ahead on downgrade, and
about 4,600x faster**. Campaign shape is the part that surprised: 12 EXTRACT
campaigns at a median of 2.5 days and 14 on HYDRO at 3, against v2's 11 and 15 at
medians of 2 - the rules produce slightly *longer* campaigns than the MIP, which
is the direction a planner wants.

The standing acceptance bar from `MIP-FORMULATION.md` - beat 6,673,800 gal of
lost sales and 5,595,214 gal of downgrade over 42 days - is cleared on both.

**Where it stops working, stated rather than buried:**

    100 days     0.036 s    NO     2,543,811 lost, 272,306 gal left unrouted
    160 days     0.031 s    NO    16,837,555 lost, 10.5 M gal left unrouted

At 100 days it misses verification by 272,306 gal of residual downgrade - close,
but `fully_routed` is not a matter of degree and a run that fails it is not
verified. Past that it degrades badly. **The cause is not established.** The
obvious suspect is that the units this does *not* free run out of planner grid at
different dates - ROSE stops 2026-11-15, extraction 2026-12-18 - but 110 days
ends before any of those, so that is not the whole story and should be measured
rather than assumed. Until it is, **treat 42 days as the supported horizon** and
read the verification on anything longer.

Traps this module is written around, each verified in the code rather than
assumed:

  * `model_config` is keyed on `workbook_product`, not `product`. `MEK#72`
    charges 9116 but its spec product is 9718 - they share a tank. Config lookups
    take the workbook code; the ledger takes the model code.
  * Rates are keyed by **line**. `EXTRACT#90` is 2200 and `#91` is 1300, both on
    9305; keying on the product gives deep extraction 69% more than the unit has.
  * `changeover_loss_days` is product-keyed, so `EXTRACT#90 <-> #91` costs no
    *time* at all. Priced on time alone a scheduler flips that mode every day, so
    switches are priced with `switch_cost_for`, which does charge it.
  * `charge` must carry explicit zeros. `_write_result_scenario` starts from a
    copy of the planner's grid, so a `(line, day)` left out keeps the planner's
    barrels rather than being idle.
  * `transfer_bbl` must be dense - every sink line, every day - or transfers the
    planner entered survive into the result and the referee reports a downgrade
    that was never decided.
"""
from __future__ import annotations

import datetime as dt
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .. import economics, model_config as cfg
from ..engine import GAL_PER_BBL, Reference, Scenario
from . import balance, v0

#: The units this decides. The same three v2 frees, for the same reason: they are
#: the ones whose transition structure is known well enough to write rules for.
FREE_UNITS: List[str] = ["MEK", "EXTRACT", "HYDRO"]

#: Days of cover a candidate must beat the incumbent by before the unit switches.
#: Without hysteresis a pure cover rule chases whatever is lowest and changes over
#: constantly; the minimum-campaign rules stop some of that churn but not all, and
#: what is left reads as noise to anyone who has to run the plan.
DEFAULT_SWITCH_THRESHOLD_DAYS = 1.5

#: Cover to report for a product with no demand at all. Such a product can never
#: be short, so it must never win the scoring - but it must not crash it either.
NO_DEMAND_COVER = 1e9


def solve(ref: Reference, scn: Scenario, spec, params: Dict[str, Any],
          horizon: Optional[List[dt.date]] = None,
          downtime: Optional[Dict[str, set]] = None,
          elastic: bool = False,
          free_units: Optional[List[str]] = None,
          opening_override: Optional[Dict[str, float]] = None,
          setup_override: Optional[Dict[str, str]] = None,
          terminal_condition: bool = True,
          boundary_day: Optional[str] = None,
          **kw) -> v0.OptimizeResult:
    """Build a schedule by rule. Signature matches `v0.solve` so callers swap freely."""
    res = v0.OptimizeResult()
    t0 = time.time()
    res.solver = "greedy"

    dates = list(horizon or scn.dates[:int(params.get("horizon_days", 42) or 42)])
    believable = cfg.valid_through(scn)
    asked = len(dates)
    dates = cfg.clamp_horizon(dates, scn)
    if not dates:
        res.status = "no_solution"
        res.message = ("the whole horizon falls past {}, the last day the "
                       "planner has filled in a charge and so the last day the "
                       "inputs can be believed".format(believable))
        return res
    truncated = asked - len(dates)
    iso = [d.isoformat() for d in dates]
    down = downtime if downtime is not None else {
        u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    free = [u for u in (FREE_UNITS if free_units is None else free_units)]

    # ------------------------------------------------------------- structure
    lines = [l for l in spec.charge_lines
             if not l["unit"].startswith(cfg.TRANSFER_UNIT_PREFIXES)]
    by_key = {l["key"]: l for l in lines}
    by_unit: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for l in lines:
        by_unit[l["unit"]].append(l)

    produces = balance.produces_map(ref, spec)
    consumes = balance.consumes_map(lines)
    lands_in = balance.lands_in_map(spec)

    #: line -> what it makes. The inverse of `produces`, which is the direction
    #: the scoring needs: given a candidate line, what does running it relieve?
    makes: Dict[str, List[tuple]] = defaultdict(list)
    for product, entries in produces.items():
        for key, per_bbl in entries:
            makes[key].append((product, per_bbl))

    tanked = {p: d for p, d in spec.products.items() if d["tanked"]}
    untanked = {p for p, d in spec.products.items()
                if not d["tanked"] and not d.get("is_sink")}

    # ------------------------------------------------------------- economics
    gp = economics.load_gross_profit()
    value_of: Dict[str, float] = {}
    for p in spec.products:
        v = economics.gross_profit(p, spec, gp)
        if v is not None:
            value_of[p] = v
    lost_margin = float(params.get("lost_sale_margin_per_gal", 1.50) or 1.50)
    switch_cost = float(params.get("switch_cost", 0.0) or 0.0)
    by_unit_cost = params.get("switch_cost_by_unit") or {}
    threshold = float(params.get("switch_threshold_days",
                                 DEFAULT_SWITCH_THRESHOLD_DAYS) or 0.0)

    # ------------------------------------------------------- supply & demand
    fixed_in: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    fixed_out: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for product, streams in spec.fixed_supply.items():
        for stream, series in streams.items():
            gate = stream[6:] if stream.startswith("_mode_") else None
            for s, gal in series.items():
                if gate and scn.crude_mode.get(s) != gate:
                    continue
                fixed_in[product][s] += gal

    # Lines that are not unit charges still move material. Identical treatment to
    # `v0`: railcars are receipts, Sonneborn and the non-sink transfers are real
    # outflows the planner decided, and the sink outlets are left alone because
    # the downgrade below is what writes them.
    outlets = {s["outlet"] for s in cfg.SINKS.values()}
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
                if lands:
                    fixed_in[lands][s] += gal

    demand: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for b in ref.blocks:
        code = b.get("charge_code")
        target = spec.aliases.get(code, code)
        if target not in tanked:
            continue
        for row in ("Sales", "Forecast", "Blends"):
            for s, gal in spec.demand_rows.get(b["id"], {}).get(row, {}).items():
                demand[target][s] += gal

    #: Average daily demand, for days of cover. The direct part is computed
    #: exactly as `v0`'s `safety_floor` does it, so the two cannot drift into
    #: different definitions of the same word.
    #:
    #: **The direct part alone is not enough, and assuming it was is what made
    #: the first version of this scheduler useless.** Only 15 of the 42 tanked
    #: products have a downgrade route, and the intermediates the freed units
    #: actually make - 9117, 9302, 9303, 9305, 9704 - carry *no customer demand
    #: at all*. Scored on direct demand every candidate line ties at infinite
    #: cover, the rule has nothing to choose on, and the unit sits on whatever it
    #: started on: extraction came back with two campaigns in 42 days.
    #:
    #: An intermediate's demand is the downstream unit drawing it. That draw is
    #: what the plant is *for*, so it is read off the planner's own grid - the
    #: same source every other figure here comes from - rather than invented from
    #: unit capacities, which would overstate it by whatever the plant does not
    #: run.
    draw: Dict[str, float] = defaultdict(float)
    for line in lines:
        product = line["product"]
        if product not in tanked:
            continue
        for d in dates:
            draw[product] += scn.charge_bbl(line["key"], d) * GAL_PER_BBL
    avg_demand = {p: (sum(demand[p].values()) + draw.get(p, 0.0))
                     / float(len(dates))
                  for p in tanked}

    # ----------------------------------------------------------- downgrading
    sink_lines = {}
    for line in spec.charge_lines:
        for sid, sink in cfg.SINKS.items():
            if sink.get("outlet") == line["unit"]:
                sink_lines[(line["product"], sid)] = line["key"]
    routes: Dict[str, List[str]] = defaultdict(list)
    unroutable = set()
    for r in spec.sink_routes:
        if (r["product"], r["sink"]) in sink_lines:
            routes[r["product"]].append(r["sink"])
        else:
            unroutable.add((r["product"], r["sink"]))
    res.unroutable = sorted("{}->{}".format(p, s) for p, s in unroutable)

    def capacity(p, s):
        dated = tanked[p].get("capacity_by_date") or {}
        return dated.get(s, tanked[p]["capacity"]) or None

    # -------------------------------------------------------- unit structure
    arcs: Dict[str, set] = {}
    for unit in free:
        allowed = cfg.line_arcs(unit, by_unit[unit]) or []
        arcs[unit] = set(allowed)

    def rate_of(line) -> Optional[float]:
        """The line's ceiling, through the spec so a planner's override applies."""
        info = v0.line_rate(spec, line["unit"], line["key"],
                            line["workbook_product"])
        return info["bbl"] if info else None

    for unit in free:
        for line in by_unit[unit]:
            if not rate_of(line):
                raise ValueError(
                    "{} is on a freed unit but has no maximum rate; charge = "
                    "rate x time cannot be written".format(line["key"]))

    # --------------------------------------------------------- opening state
    inv = {}
    for p, d in tanked.items():
        inv[p] = float((opening_override or {}).get(p, d["opening"]))

    setup: Dict[str, Optional[str]] = {}
    held: Dict[str, int] = {}
    reactor_held: Dict[str, int] = {}
    for unit in free:
        start = (setup_override or {}).get(unit)
        if start not in by_key:
            # Whatever the planner had running on the first day, else the line
            # they used most - a unit has to start set up for *something*, and
            # starting it on an arbitrary line buys a changeover nobody asked for.
            start = _opening_line(scn, by_unit[unit], dates)
        setup[unit] = start
        held[unit] = 0
        reactor_held[unit] = 0

    charge: Dict[str, Dict[str, float]] = defaultdict(dict)
    schedule: Dict[str, Dict[str, Optional[str]]] = {u: {} for u in free}
    inventory: Dict[str, Dict[str, float]] = {p: {} for p in tanked}
    lost_sales: Dict[str, Dict[str, float]] = defaultdict(dict)
    downgrade: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict))
    #: Dense, every sink line every day. A sparse one leaves the planner's own
    #: transfers standing in the result scenario.
    transfer_bbl: Dict[str, Dict[str, float]] = {
        key: {s: 0.0 for s in iso} for key in set(sink_lines.values())}

    switches: Dict[str, int] = {u: 0 for u in free}
    campaigns: Dict[str, List[int]] = {u: [] for u in free}

    # =========================================================== forward pass
    for i, (day, s) in enumerate(zip(dates, iso)):
        # What each product has to play with today, before anything is charged.
        # Demand is taken off up front on purpose: this is a service-first rule,
        # so a customer's gallons are not spent on feeding a unit.
        avail = {p: inv[p] + fixed_in[p].get(s, 0.0)
                    - fixed_out[p].get(s, 0.0) - demand[p].get(s, 0.0)
                 for p in tanked}
        made_today: Dict[str, float] = defaultdict(float)
        used_today: Dict[str, float] = defaultdict(float)

        def commit(key: str, bbl: float) -> None:
            """Run `key` at `bbl` and move the material it moves."""
            charge[key][s] = bbl
            if not bbl:
                return
            product = by_key[key]["product"]
            if product in avail:
                gal = bbl * GAL_PER_BBL
                avail[product] -= gal
                used_today[product] += gal
            for out, per_bbl in makes.get(key, []):
                gal = bbl * per_bbl
                made_today[out] += gal
                if out in avail:
                    avail[out] += gal

        def feed_allows(key: str) -> float:
            """Barrels of `key` the tanks can actually supply today."""
            product = by_key[key]["product"]
            if product not in avail:
                return float("inf")     # untanked or unmodelled: no buffer to check
            return max(0.0, avail[product] / GAL_PER_BBL)

        def headroom_cap(key: str, bbl: float) -> float:
            """Cut `bbl` back to what the receiving tanks can still take.

            A product with a downgrade outlet is not capped: overflow is routed
            below, which is what the outlet is for. A product *without* one is,
            and skipping this is what leaves the referee reporting a residual
            downgrade the schedule never routed - `fully_routed` is not advisory,
            a run that fails it is not verified.
            """
            for out, per_bbl in makes.get(key, []):
                if per_bbl <= 0 or out not in tanked or routes.get(out):
                    continue
                cap = capacity(out, s)
                if cap is None:
                    continue
                bbl = min(bbl, max(0.0, cap - avail.get(out, 0.0)) / per_bbl)
            return max(0.0, bbl)

        # --- units the planner still owns, including ROSE and the platformer.
        # Their charges are taken as given, clamped only by what the tanks can
        # supply: a charge the feed cannot cover is `feed_shortfall`, and a
        # schedule carrying any of it fails verification outright.
        for line in lines:
            unit = line["unit"]
            if unit in free:
                continue
            key = line["key"]
            if day in down.get(unit, set()):
                charge[key][s] = 0.0
                continue
            # A flow-through line runs on what the tanks hand it, not on what
            # the planner typed. The workbook has no row for the reformer, so
            # `PLATFORMER#104` sits at zero on all 366 days - taken literally
            # that leaves 4107 Kensol 17 with no drain at all, its tank fills,
            # and the headroom cap throttles the fractionator feeding it: the
            # platformer came back at 36,860 bbl against the MIP's 210,161, and
            # the whole cascade starved behind it. `HYDRO#76` is the same shape
            # on the diesel pool. v0 gives both a variable on every non-outage
            # day for this reason; the tank constraint is what makes them run.
            if key in cfg.FLOW_THROUGH_LINES:
                ceiling = rate_of(line) or 0.0
                commit(key, headroom_cap(key, min(ceiling, feed_allows(key))))
                continue
            want = scn.charge_bbl(key, day)
            commit(key, headroom_cap(key, min(want, feed_allows(key)))
                   if want > 0 else 0.0)

        # --- the units this module decides, upstream first
        for unit in _chain_order(free):
            picked, bbl = _decide(
                unit=unit, day=day, s=s, down=down, by_unit=by_unit,
                setup=setup, held=held, reactor_held=reactor_held, arcs=arcs,
                rate_of=rate_of, feed_allows=feed_allows,
                headroom_cap=headroom_cap, makes=makes,
                avail=avail, avg_demand=avg_demand, value_of=value_of,
                lost_margin=lost_margin, switch_cost=switch_cost,
                by_unit_cost=by_unit_cost, threshold=threshold)

            for line in by_unit[unit]:
                if line["key"] != picked:
                    charge[line["key"]][s] = 0.0      # explicit zero: see module doc
            if picked is not None:
                commit(picked, bbl)
            schedule[unit][s] = picked

        # --- route what will not fit
        for p in sorted(tanked):
            cap = capacity(p, s)
            if cap is None or avail[p] <= cap:
                continue
            over = avail[p] - cap
            for sink in routes.get(p, []):
                key = sink_lines.get((p, sink))
                if not key:
                    continue
                transfer_bbl[key][s] = over / GAL_PER_BBL
                downgrade[p][sink][s] = over
                avail[p] -= over
                landing = lands_in.get(key)
                if landing in avail:
                    avail[landing] += over
                break

        # --- close the day
        for p in tanked:
            level = avail[p]
            if level < 0:
                # Demand that the day could not cover. Capped at the demand that
                # was actually booked: a tank cannot lose more than was asked of
                # it, and without the cap a feed shortfall would be reported as a
                # lost sale and hide itself.
                short = min(-level, demand[p].get(s, 0.0))
                if short > 1e-6:
                    lost_sales[p][s] = short
                level = max(0.0, level + short)
            inv[p] = level
            inventory[p][s] = level

    # ================================================================ results
    # Campaign shape is read back off the schedule rather than tallied as the
    # pass runs. One reading, one definition - a counter incremented at the
    # decision site drifts from what the schedule actually says the moment a
    # branch forgets to touch it.
    for unit in free:
        run = 0
        prev = None
        for s in iso:
            cur = schedule[unit].get(s)
            if prev is not None and cur != prev:
                campaigns[unit].append(run)
                run = 0
                if cur is not None and prev is not None:
                    switches[unit] += 1
            run += 1
            prev = cur
        if run:
            campaigns[unit].append(run)

    res.charge = {k: dict(v) for k, v in charge.items()}
    res.transfer_bbl = transfer_bbl
    res.inventory = inventory
    res.schedule = schedule
    res.lost_sales = {p: v for p, v in lost_sales.items() if v}
    res.downgrade = {p: {k: dict(v) for k, v in d.items()}
                     for p, d in downgrade.items()}

    lost_gal = sum(v for d in lost_sales.values() for v in d.values())
    dg_bbl = sum(v for d in transfer_bbl.values() for v in d.values())
    res.kpis = {
        "lost_sales_gal": lost_gal,
        # Must equal the transfers written onto the schedule: `verify` reads the
        # executed downgrade off the result scenario, not off `downgrade`.
        "downgrade_gal": dg_bbl * GAL_PER_BBL,
        "charge_bbl": sum(v for d in charge.values() for v in d.values()),
        "days": len(dates),
        "horizon_truncated_days": truncated,
        "products": len(tanked),
        "campaign_shape": {
            u: {
                "campaigns": len(campaigns[u]),
                "median_campaign_days": _median(campaigns[u]),
                "longest_campaign_days": max(campaigns[u]) if campaigns[u] else 0,
                "switches": switches[u],
                "switches_per_month": round(switches[u] * 30.0 / len(dates), 2),
                "day_zero_setup": schedule[u].get(iso[0]),
                "days_available": sum(1 for d in dates
                                      if d not in down.get(u, set())),
            } for u in free},
        "switches": sum(switches.values()),
    }

    # Never "optimal". Nothing here proves anything, and the run surfaces as
    # `not_proved_optimal`, which is the accurate label rather than a demotion.
    res.status = "feasible"
    res.solve_seconds = time.time() - t0
    res.objective = None
    res.message = ("rule-based schedule over {} days in {:.2f}s; not a bound - "
                   "no optimality gap is available, so read the verification "
                   "and the campaign shape".format(len(dates), res.solve_seconds))
    if truncated:
        res.message = ("horizon cut by {} day(s) to end {}, the last day the "
                       "planner has filled in a charge. ".format(
                           truncated, believable) + res.message)
    return res


# --------------------------------------------------------------------- rules

def _chain_order(free: List[str]) -> List[str]:
    """Upstream before downstream, so a unit sees the feed just committed to it."""
    order = {"ROSE": 0, "MEK": 1, "EXTRACT": 2, "HYDRO": 3}
    return sorted(free, key=lambda u: (order.get(u, 99), u))


def _median(xs: List[int]) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    mid = len(ys) // 2
    return float(ys[mid] if len(ys) % 2 else (ys[mid - 1] + ys[mid]) / 2.0)


def _opening_line(scn: Scenario, unit_lines: List[Dict[str, Any]],
                  dates: List[dt.date]) -> Optional[str]:
    """What the unit is already set up for on day zero.

    The planner's own first charged day if there is one, else the line they ran
    most over the window. A unit has to start set up for *something*; starting it
    somewhere arbitrary buys a changeover on day one that nobody asked for.
    """
    for d in dates:
        for line in unit_lines:
            if scn.charge_bbl(line["key"], d) > 0:
                return line["key"]
    best, best_bbl = None, -1.0
    for line in unit_lines:
        total = sum(scn.charge_bbl(line["key"], d) for d in dates)
        if total > best_bbl:
            best, best_bbl = line["key"], total
    return best


def _cover(product: Optional[str], avail: Dict[str, float],
           avg_demand: Dict[str, float]) -> float:
    """Days of cover: what is on hand divided by what a day typically takes.

    A product nobody buys can never be short, so it is pushed to the back of the
    queue rather than dividing by zero.
    """
    if product is None or product not in avg_demand:
        return NO_DEMAND_COVER
    rate = avg_demand.get(product, 0.0)
    if rate <= 0:
        return NO_DEMAND_COVER
    return max(0.0, avail.get(product, 0.0)) / rate


def _score(key: str, makes, avail, avg_demand, value_of, lost_margin):
    """(worst cover of what this line makes, negative margin per barrel).

    Sorted ascending, so the shortest downstream product wins and margin breaks
    the tie. Scoring the *output* rather than the feed is the point: a line earns
    its campaign because the thing it produces is running out.
    """
    outs = makes.get(key, [])
    if not outs:
        return (NO_DEMAND_COVER, 0.0)
    cover = min(_cover(p, avail, avg_demand) for p, _ in outs)
    margin = sum(per_bbl * value_of.get(p, lost_margin) for p, per_bbl in outs)
    return (cover, -margin)


def _decide(unit, day, s, down, by_unit, setup, held, reactor_held, arcs,
            rate_of, feed_allows, headroom_cap, makes, avail, avg_demand,
            value_of, lost_margin, switch_cost, by_unit_cost, threshold):
    """Choose this unit's line and rate for one day.

    Returns `(line_key or None, barrels)`. `None` means the unit is down.
    """
    if day in down.get(unit, set()):
        held[unit] = 0
        return None, 0.0

    current = setup[unit]
    cur_line = next((l for l in by_unit[unit] if l["key"] == current), None)

    # --- may it move at all?
    # The minimum campaign holds the unit on the *line*. The reactor minimum is a
    # separate rule and is applied per candidate below, because it forbids only
    # the moves that leave the reactor - a unit part-way through an R1 visit is
    # still free to go from 9704 to 9705, which is the grouping the plant
    # describes and the whole reason the two rules are not one.
    movable = True
    if cur_line is not None:
        min_run = cfg.min_run_days_for_line(unit, current,
                                            cur_line["workbook_product"])
        movable = held[unit] >= min_run

    candidates = []
    for line in by_unit[unit]:
        key = line["key"]
        if key == current:
            continue
        if not movable:
            continue
        if current is not None and arcs.get(unit) and (current, key) not in arcs[unit]:
            continue                      # not a changeover this plant makes
        if cur_line is not None:
            reactor = cfg.reactor_of(unit, cur_line["workbook_product"])
            target = cfg.reactor_of(unit, line["workbook_product"])
            if (reactor is not None and target is not None and reactor != target
                    and reactor_held[unit] < cfg.min_reactor_visit_days(unit, reactor)):
                continue                  # reactor visit not served yet
        candidates.append(line)

    def runnable(key: str) -> bool:
        """Is there enough feed in the tanks to run this line at its floor?"""
        line = next((l for l in by_unit[unit] if l["key"] == key), None)
        if line is None:
            return False
        floor = cfg.min_rate_bbl(unit, line["workbook_product"])
        return feed_allows(key) >= max(floor, 1e-6)

    incumbent = _score(current, makes, avail, avg_demand, value_of,
                       lost_margin) if current else (NO_DEMAND_COVER, 0.0)
    best, best_score = None, None
    for line in candidates:
        sc = _score(line["key"], makes, avail, avg_demand, value_of, lost_margin)
        if best_score is None or sc < best_score:
            best, best_score = line, sc

    picked = current
    switched = False

    # **A unit must not sit on a feed it does not have.** The score measures what
    # a line *makes*, so a unit whose own feed tank has run dry sees no reason to
    # move: every candidate looks equally well covered, the gain never clears the
    # toll, and the unit stays put charging nothing. The hydrotreater did exactly
    # that - one campaign of 80 days over a 160-day horizon, and its total charge
    # stopped moving after about day 60. Being unable to run is not a preference
    # to be weighed against cover; it overrides the hysteresis outright.
    if current is not None and not runnable(current):
        stuck = [l for l in candidates if runnable(l["key"])]
        if stuck:
            best = min(stuck, key=lambda l: _score(l["key"], makes, avail,
                                                   avg_demand, value_of,
                                                   lost_margin))
            setup[unit] = best["key"]
            held[unit] = 0
            prev_r = cfg.reactor_of(unit, _wp(by_unit, unit, current))
            now_r = cfg.reactor_of(unit, best["workbook_product"])
            reactor_held[unit] = 0 if prev_r != now_r else reactor_held[unit] + 1
            return _rate_for(unit, best, current, by_unit, feed_allows,
                             headroom_cap, rate_of, switched=True)

    if best is not None and best_score is not None:
        # Hysteresis, measured in days of cover: the candidate has to be shorter
        # by a real margin, not by a rounding error, or the unit buys a
        # changeover for nothing.
        #
        # The price is what makes the *time-free* switches cost something.
        # `changeover_loss_days` is product-keyed and returns zero for
        # `EXTRACT#90 <-> #91`, since both charge 9305 - so a rule pricing
        # switches by lost time alone would flip that mode every single day.
        # Converted to days of cover at the product's own daily demand, so the
        # two halves of the comparison are in the same unit.
        gain = incumbent[0] - best_score[0]
        price = cfg.switch_cost_for(unit, _wp(by_unit, unit, current),
                                    _wp(by_unit, unit, best["key"]),
                                    default=switch_cost, by_unit=by_unit_cost)
        # The price has to be divided by a rate in $/day, not by a margin in
        # $/bbl - getting that wrong turns a $2,000 changeover into a toll of
        # fifty days of cover and the unit never moves again.
        per_day = abs(best_score[1]) * max(1.0, rate_of(best) or 1.0)
        toll = threshold + (price / per_day if price and per_day > 0 else 0.0)
        if gain > toll:
            picked = best["key"]
            switched = True

    if picked is None:
        return None, 0.0

    line = next(l for l in by_unit[unit] if l["key"] == picked)
    if switched:
        held[unit] = 0
        prev = cfg.reactor_of(unit, _wp(by_unit, unit, current))
        now = cfg.reactor_of(unit, line["workbook_product"])
        reactor_held[unit] = 0 if prev != now else reactor_held[unit] + 1
    else:
        held[unit] += 1
        reactor_held[unit] += 1
    setup[unit] = picked
    return _rate_for(unit, line, current, by_unit, feed_allows, headroom_cap,
                     rate_of, switched)


def _rate_for(unit, line, previous, by_unit, feed_allows, headroom_cap,
              rate_of, switched):
    """Barrels to run `line` at today, once the setup is settled.

    Three things cut the ceiling down, in order: the time lost to a changeover,
    the feed the tanks can actually supply, and the room left in whatever the
    line makes. Skipping the second is a feed shortfall and skipping the third is
    an unrouted downgrade - either one fails verification outright.
    """
    key = line["key"]
    loss = cfg.changeover_loss_days(unit, _wp(by_unit, unit, previous),
                                    line["workbook_product"]) if switched else 0.0
    fraction = max(0.0, 1.0 - loss)
    ceiling = cfg.net_rate_bbl(unit, line["workbook_product"], fraction)
    if ceiling is None:
        ceiling = (rate_of(line) or 0.0) * fraction

    bbl = headroom_cap(key, max(0.0, min(ceiling, feed_allows(key))))

    floor = cfg.min_rate_bbl(unit, line["workbook_product"]) * fraction
    if 0 < bbl < floor:
        # Below the minimum a unit is not really running. Either it makes the
        # floor or it should not be started - a unit that comes back nominally
        # running and empty is the failure `MIN_RATE_FRACTION` exists to stop.
        bbl = 0.0
    return key, bbl


def _wp(by_unit, unit, key) -> Optional[str]:
    """The workbook code for a line key - what `model_config` is keyed on."""
    if key is None:
        return None
    for line in by_unit[unit]:
        if line["key"] == key:
            return line["workbook_product"]
    return None
