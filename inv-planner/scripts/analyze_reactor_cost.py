"""What must a hydrotreater reactor crossing cost before the model stops doing it?

The plant groups 4315 and 4319 - the two products made on the dedicated reactor -
and will not come off it to run something on the other reactor in between. The
optimizer does exactly that: over the 100-day horizon it crosses 28 times against
the plan's 15.

The reactor model is already built. `UNIT_REACTORS` puts 9704 and 9705 on R1,
`changeover_loss_days` adds a quarter-day flush on a crossing, and v0 enforces one
reactor a day. What is missing is money: `switch_cost_for` resolves arc, then unit,
then the flat figure, and both committed tables are empty, so a crossing and an
ordinary changeover are both charged $2,000. The flush costs time and nothing else.

This sweeps the crossing arcs' cash cost and reports what the schedule does at each
price - crossings, how long the dedicated reactor holds, and what the discipline
costs in lost sales and downgrade. The point is the number to take to operations,
not to fill `SWITCH_COST_BY_ARC` here: every entry in that table is a claim about
what a changeover gives away, and nobody has measured this one yet.

  python scripts/analyze_reactor_cost.py [days] [seconds] [costs]

Only the hydrotreater is freed, so the sweep moves one unit and reads one unit.
Every point solves at `mip_gap` 0 and a point that cannot prove optimality is
reported and then dropped: at the planning gap the difference between two prices
here is smaller than the gap itself, which is how two earlier experiments on this
model produced confident numbers that turned out to be noise.

**The horizon has to be long enough to contain the behaviour.** Over 14 days the
plan crosses once and so does the model at every price, so the sweep reads flat -
not because the price does nothing but because there is nothing in the window for
it to act on. Start long enough to hold several R1 campaigns and shorten only if
points stop proving.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner import baseline, model_config as cfg        # noqa: E402
from invplanner.engine import Reference, Scenario, simulate  # noqa: E402
from invplanner.modelprep import build                       # noqa: E402
from invplanner.optimizer import v0                          # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
REPORT = os.path.join(ROOT, "data", "reports", "reactor-switch-cost.md")

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 42
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 300
UNIT = "HYDRO"

#: Cash on a crossing arc, on top of the flat $2,000 every changeover already
#: pays. 0 is the model as it ships and has to reproduce today's behaviour.
SWEEP = [0.0, 2_000.0, 5_000.0, 10_000.0, 20_000.0, 50_000.0, 100_000.0]
if len(sys.argv) > 3:
    SWEEP = [float(x) for x in sys.argv[3].split(",")]

PARAMS = {
    "objective": "cost",
    "lost_sale_margin_per_gal": 1.50,
    "downgrade_discount_per_gal": 0.50,
    "netback_diesel_cost_per_gal": 0.3667,
    "netback_gasoline_cost_per_gal": 0.22,
    "switch_cost": 2000.0,
    "charge_floor_fraction": 0.30,
    "time_limit_seconds": LIMIT,
    "mip_gap": 0.0,
    "horizon_days": DAYS,
}


def crossing_arcs(spec):
    """Every ordered pair of HYDRO charge products that changes reactor.

    Built from the spec's own lines rather than written out, so a line added to
    either reactor is priced without anyone remembering to edit a list here.
    """
    codes = sorted({l["workbook_product"] for l in spec.charge_lines
                    if l["unit"] == UNIT})
    return [(a, b) for a in codes for b in codes
            if cfg.reactor_of(UNIT, a) != cfg.reactor_of(UNIT, b)]


def reactor_series(res, spec, dates):
    """The reactor the unit is set up for each day, holding through idle days.

    A reactor keeps its last feed while the unit sits, so an idle day is not a
    reset - resuming the same reactor owes nothing and resuming the other still
    owes the flush. `res.schedule` says `None` on those days; carrying the last
    reactor forward is what makes a campaign that spans an idle day one campaign
    rather than two.
    """
    rof = {l["key"]: cfg.reactor_of(UNIT, l["workbook_product"])
           for l in spec.charge_lines if l["unit"] == UNIT}
    out, held = [], None
    for d in dates:
        line = (res.schedule.get(UNIT) or {}).get(d.isoformat())
        if line is not None:
            held = rof.get(line, held)
        if held is not None:
            out.append(held)
    return out


def campaigns_of(series, want=None):
    """Run lengths in a series, optionally only the runs of one value."""
    runs, cur = [], None
    for v in series:
        if cur is not None and v == cur[0]:
            cur[1] += 1
        else:
            cur = [v, 1]
            runs.append(cur)
    return [n for v, n in runs if want is None or v == want]


def median(xs):
    return sorted(xs)[len(xs) // 2] if xs else 0


def run_point(cost, ref, scn, spec, dates, down):
    """One price. The arc table is committed code, so put it back afterwards."""
    was = cfg.SWITCH_COST_BY_ARC.get(UNIT)
    if cost:
        cfg.SWITCH_COST_BY_ARC[UNIT] = {a: cost for a in crossing_arcs(spec)}
    try:
        res = v0.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=down,
                       free_units=[UNIT])
    finally:
        if was is None:
            cfg.SWITCH_COST_BY_ARC.pop(UNIT, None)
        else:
            cfg.SWITCH_COST_BY_ARC[UNIT] = was

    k = res.kpis or {}
    shape = (k.get("campaign_shape") or {}).get(UNIT, {})
    series = reactor_series(res, spec, dates)
    r1 = campaigns_of(series, "R1")
    return {
        "cost": cost,
        "status": res.status,
        "proved": res.status == "optimal",
        "seconds": round(res.solve_seconds, 1),
        # The whole objective moves with the price of the crossings it buys, so
        # a straight comparison across points reads the cost of the instrument
        # rather than the cost of the answer. Service is what to compare.
        "objective": round(float(res.objective), 2) if res.objective else None,
        "inherited": round(float(k.get("inherited_switch_cost") or 0.0), 2),
        "crossings": baseline._reactor_shape(spec, res, dates)["startups"],
        "both_reactor_days": baseline._reactor_shape(spec, res, dates)["both_reactor_days"],
        "r1_campaigns": len(r1),
        "r1_median_days": median(r1),
        "r1_days": sum(r1),
        "switches": shape.get("switches", 0),
        "campaigns": shape.get("campaigns", 0),
        "median_campaign_days": shape.get("median_campaign_days", 0),
        "lost_sales_gal": round(float(k.get("lost_sales_gal") or 0.0)),
        "downgrade_gal": round(float(k.get("downgrade_gal") or 0.0)),
    }


def plan_shape(scn, spec, dates):
    """The same measurements read off the planner's own schedule."""
    hy = [l for l in spec.charge_lines if l["unit"] == UNIT]
    rof = {l["key"]: cfg.reactor_of(UNIT, l["workbook_product"]) for l in hy}
    series, held = [], None
    for d in dates:
        on = [l["key"] for l in hy if scn.charge_bbl(l["key"], d) > 0]
        if on:
            held = rof.get(on[-1], held)
        if held is not None:
            series.append(held)
    r1 = campaigns_of(series, "R1")
    return {"crossings": sum(1 for a, b in zip(series, series[1:]) if a != b),
            "r1_campaigns": len(r1), "r1_median_days": median(r1),
            "r1_days": sum(r1)}


ROW = ("| ${cost:>7,.0f} | {crossings:>9} | {r1_campaigns:>11} | "
       "{r1_median_days:>13} | {switches:>8} | {median_campaign_days:>14} | "
       "{lost_sales_gal:>15,} | {downgrade_gal:>13,} | {status} |")


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:DAYS]
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, sim, horizon=dates, downtime=down)

    arcs = crossing_arcs(spec)
    plan = plan_shape(scn, spec, dates)
    print("Pricing {} reactor crossings over {} days ({} to {})".format(
        UNIT, DAYS, dates[0], dates[-1]))
    print("{} crossing arcs; the plan crosses {} times in this window\n".format(
        len(arcs), plan["crossings"]))

    rows = []
    for cost in SWEEP:
        row = run_point(cost, ref, scn, spec, dates, down)
        rows.append(row)
        print("  ${:>7,.0f}  {:>2} crossings  R1 {:>2} campaigns of {:>2}d  "
              "{:>3} switches  lost {:>10,}  {}  ({}s)".format(
                  row["cost"], row["crossings"], row["r1_campaigns"],
                  row["r1_median_days"], row["switches"],
                  row["lost_sales_gal"], row["status"], row["seconds"]))

    write_report(rows, plan, arcs, dates)
    print("\nWrote {}".format(os.path.relpath(REPORT, ROOT)))

    unproved = [r for r in rows if not r["proved"]]
    if unproved:
        print("{} point(s) could not prove optimality and are marked in the "
              "report; shorten the horizon before reading them.".format(
                  len(unproved)))


def write_report(rows, plan, arcs, dates):
    proved = [r for r in rows if r["proved"]]
    base = next((r for r in rows if r["cost"] == 0.0), None)

    out = []
    out.append("# What a reactor crossing has to cost\n")
    out.append("Generated by `scripts/analyze_reactor_cost.py` over {} days "
               "({} to {}), v0 with the hydrotreater freed, every point at "
               "`mip_gap` 0.\n".format(len(dates), dates[0], dates[-1]))

    out.append("\n## The complaint\n")
    out.append("The plant groups 4315 and 4319 - Kendex 0150H and Argold, the two "
               "products made on the dedicated reactor - and does not come off R1 "
               "to run an R2 product in between. Sold raw those same streams are "
               "4305 and 4318, which is how the plan names them.\n")
    out.append("\nOver this window the plan crosses reactors **{} times**, holding "
               "R1 in {} campaigns of median {} days.\n".format(
                   plan["crossings"], plan["r1_campaigns"], plan["r1_median_days"]))

    out.append("\n## What a crossing costs today\n")
    out.append("Two charges, and only one of them is set.\n")
    out.append("\n| | within a reactor | crossing |\n|---|---:|---:|\n")
    out.append("| time lost | 0.125 day | **0.375 day** |\n")
    out.append("| cash | $2,000 | **$2,000** |\n")
    out.append("\n`changeover_loss_days` adds the quarter-day flush, so the time "
               "side is priced. `switch_cost_for` resolves arc, then unit, then "
               "the flat figure - and `SWITCH_COST_BY_ARC` is empty, so the cash "
               "side says a crossing and an ordinary changeover are the same "
               "thing. This sweep is what that entry would be worth.\n")

    out.append("\n## The sweep\n")
    out.append("{} crossing arcs priced; everything else stays at the flat "
               "$2,000.\n".format(len(arcs)))
    out.append("\n| crossing $ | crossings | R1 campaigns | R1 median days | "
               "switches | median campaign | lost sales (gal) | downgrade (gal) "
               "| status |\n")
    out.append("|---:|---:|---:|---:|---:|---:|---:|---:|---|\n")
    for r in rows:
        out.append(ROW.format(**r) + "\n")

    out.append("\nThe plan, measured the same way: **{} crossings**, R1 in {} "
               "campaigns of median {} days.\n".format(
                   plan["crossings"], plan["r1_campaigns"], plan["r1_median_days"]))

    if base and proved:
        hit = [r for r in proved
               if r["r1_median_days"] >= plan["r1_median_days"]
               and r["crossings"] <= plan["crossings"]]
        out.append("\n## Reading\n")
        if hit:
            t = hit[0]
            out.append("At **${:,.0f} a crossing** the model holds R1 for a "
                       "median {} days across {} campaigns and crosses {} times "
                       "- the plan's own discipline, reached by pricing rather "
                       "than by a grouping rule.\n".format(
                           t["cost"], t["r1_median_days"], t["r1_campaigns"],
                           t["crossings"]))
            out.append("\nWhat it costs to get there, against the same model "
                       "priced as it ships:\n")
            out.append("\n| | at $0 | at ${:,.0f} | change |\n|---|---:|---:|---:|\n"
                       .format(t["cost"]))
            for label, key in (("lost sales (gal)", "lost_sales_gal"),
                               ("downgrade (gal)", "downgrade_gal")):
                out.append("| {} | {:,} | {:,} | {:+,} |\n".format(
                    label, base[key], t[key], t[key] - base[key]))
            out.append("\nThat difference is the whole argument. If it is small, "
                       "the discipline the plant already keeps costs the plan "
                       "almost nothing and the model should keep it too.\n")
        else:
            out.append("No price in this range reproduces the plan's grouping. "
                       "Either the range is too low, or something other than the "
                       "changeover cost is driving R1 off the reactor - the "
                       "sweep says which by whether crossings move at all.\n")

    out.append("\n## The question for operations\n")
    out.append("Does coming off the reactor give away more than $X of material "
               "beyond the quarter-day it already loses?\n")
    out.append("\nThe plan's own numbers say something is missing. First-day "
               "charge on 9704 and 9705 runs **42-44% below** their later days, "
               "against ~13% for every other charge on the unit - the ordinary "
               "switch plus the flush, showing up in the data. What that costs in "
               "dollars is the part nobody has measured.\n")
    out.append("\nUntil someone does, `SWITCH_COST_BY_ARC` stays empty and the "
               "model prices exactly as it did before. That is the honest answer "
               "rather than an invented one.\n")

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("".join(out))


if __name__ == "__main__":
    main()
