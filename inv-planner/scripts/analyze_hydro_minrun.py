"""What does giving the hydrotreater a minimum campaign do - to the schedule, and
to whether the model can be solved at all?

HYDRO is the only unit with no entry in `MIN_RUN_DAYS`. The reason recorded for
that is that the plan runs it in one-day campaigns, and the evidence cited for
*that* is "93 of its 98 changeovers are back to back" - which comes from the idle-gap
question in `switching-analysis.md` and says nothing about how long a campaign runs.
What the plan actually shows is a median of 1-2 days per charge, which is a fact
about the spreadsheet. Operations say the unit is not run that way.

The omission is expensive twice over. It is why a solved schedule fragments the
reactor - run 33 took 43 HYDRO campaigns at a median of 2 days - and it is why the
unit cannot be solved to a proven optimum: `v2.py` calls it the hard unit because
"there is nothing to prune the tree with", and a minimum campaign is exactly the
pruning that is missing.

So this measures both halves at once:

  * does a minimum campaign make the 42-day solve provable, and how fast
  * what it does to reactor crossings and to service

  python scripts/analyze_hydro_minrun.py [days] [seconds]

The settings are hard minimums, keyed per line the way EXTRACT already is in
`MIN_RUN_DAYS_BY_LINE`. A *soft* minimum - a penalty for a short campaign - was
considered and rejected: it leaves every short campaign feasible, so it prunes
nothing and makes the solve harder rather than easier. The model already has a soft
brake in `switch_cost`, and run 33 is the evidence that a cash penalty on this unit
gets bought out.
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

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 42
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 900
UNIT = "HYDRO"

#: The dedicated reactor's two lines. Everything else on the unit is R2.
R1_LINES = ["HYDRO#77", "HYDRO#84"]          # 9705 Kendex 0847, 9704 Kendex 0150
R2_LINES = ["HYDRO#76", "HYDRO#78", "HYDRO#79", "HYDRO#80",
            "HYDRO#81", "HYDRO#82", "HYDRO#83"]

#: (label, R1 days, R2 days). `1, 1` is the model as it ships - `min_run_days`
#: already falls back to 1 and `v0` skips the constraint at L <= 1, so it is the
#: honest name for "no minimum at all".
SETTINGS = [
    ("today (none)", 1, 1),
    ("R1 2, R2 1", 2, 1),
    ("R1 3, R2 2", 3, 2),
]

#: Run a subset by label substring, so the control does not have to be re-solved
#: every time: `python scripts/analyze_hydro_minrun.py 42 900 "R1 3"`.
if len(sys.argv) > 3:
    SETTINGS = [s for s in SETTINGS if sys.argv[3].lower() in s[0].lower()]

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


def reactor_series(res, spec, dates):
    """Which reactor the unit is set up for each day, held through idle days."""
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


def runs_of(series, want=None):
    out, cur = [], None
    for v in series:
        if cur is not None and v == cur[0]:
            cur[1] += 1
        else:
            cur = [v, 1]
            out.append(cur)
    return [n for v, n in out if want is None or v == want]


def median(xs):
    return sorted(xs)[len(xs) // 2] if xs else 0


def run_setting(label, r1, r2, ref, scn, spec, dates, down):
    """One minimum-run setting. The table is committed code - put it back."""
    was = cfg.MIN_RUN_DAYS_BY_LINE.get(UNIT)
    table = {}
    table.update({k: r1 for k in R1_LINES})
    table.update({k: r2 for k in R2_LINES})
    cfg.MIN_RUN_DAYS_BY_LINE[UNIT] = table
    try:
        res = v0.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=down,
                       free_units=[UNIT])
    finally:
        if was is None:
            cfg.MIN_RUN_DAYS_BY_LINE.pop(UNIT, None)
        else:
            cfg.MIN_RUN_DAYS_BY_LINE[UNIT] = was

    k = res.kpis or {}
    shape = (k.get("campaign_shape") or {}).get(UNIT, {})
    series = reactor_series(res, spec, dates)
    r1runs = runs_of(series, "R1")
    return {
        "label": label, "r1": r1, "r2": r2,
        "status": res.status, "proved": res.status == "optimal",
        "seconds": round(res.solve_seconds, 1),
        "objective": round(float(res.objective), 2) if res.objective else None,
        "crossings": baseline._reactor_shape(spec, res, dates)["startups"],
        "r1_campaigns": len(r1runs), "r1_median": median(r1runs),
        "switches": shape.get("switches", 0),
        "campaigns": shape.get("campaigns", 0),
        "median_campaign": shape.get("median_campaign_days", 0),
        "lost_sales_gal": round(float(k.get("lost_sales_gal") or 0.0)),
        "downgrade_gal": round(float(k.get("downgrade_gal") or 0.0)),
    }


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:DAYS]
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, sim, horizon=dates, downtime=down)

    print("Minimum campaign on {} over {} days ({} to {}), limit {}s\n".format(
        UNIT, DAYS, dates[0], dates[-1], LIMIT))
    print("  {:<14} {:>9} {:>9} {:>11} {:>9} {:>8} {:>12}  {}".format(
        "setting", "crossings", "R1 camps", "R1 median", "switches",
        "lost/1k", "solve", "status"))

    for label, r1, r2 in SETTINGS:
        row = run_setting(label, r1, r2, ref, scn, spec, dates, down)
        print("  {:<14} {:>9} {:>9} {:>11} {:>9} {:>8,} {:>11.1f}s  {}".format(
            row["label"], row["crossings"], row["r1_campaigns"],
            row["r1_median"], row["switches"],
            row["lost_sales_gal"] // 1000, row["seconds"], row["status"]))


if __name__ == "__main__":
    main()
