"""Why is v0 infeasible? Relax every constraint and see which relief it uses.

Solves the same model with a penalised slack on each hard constraint. The model
then always solves, and the slacks that come back non-zero name the binding
constraint and say by how much - more useful than a bare 'infeasible'.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner import model_config as cfg                # noqa: E402
from invplanner.engine import Reference, Scenario, simulate  # noqa: E402
from invplanner.modelprep import build                    # noqa: E402
from invplanner.optimizer import v0                       # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 14

PARAMS = {"lost_sale_margin_per_gal": 1.50, "downgrade_discount_per_gal": 0.50,
          "netback_diesel_cost_per_gal": 0.37,
          "netback_gasoline_cost_per_gal": 0.22,
          "horizon_days": DAYS, "time_limit_seconds": 120, "mip_gap": 0.0}


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:DAYS]
    downtime = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, sim, horizon=dates, downtime=downtime)

    print("Diagnosing v0 over {} days ({} to {})\n".format(
        DAYS, dates[0], dates[-1]))

    strict = v0.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=downtime)
    print("strict solve  : {}".format(strict.status))

    res = v0.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=downtime,
                   elastic=True)
    print("elastic solve : {}  ({:.2f}s)\n".format(res.status, res.solve_seconds))

    if not res.binding:
        print("No constraint needed relief - the strict model should solve.")
        return

    print("Constraints that needed relief, worst first:\n")
    for family, info in sorted(res.binding.items(),
                               key=lambda kv: -kv[1]["total"]):
        print("  {}".format(family.upper()))
        print("    {} cells, total relief {:,.0f}".format(
            info["count"], info["total"]))
        for name, value in info["worst"][:8]:
            print("      {:<34} {:>14,.2f}".format(name, value))
        print()

    print("Reading:")
    if "day_time" in res.binding:
        print("  day_time  - the planner's own charge exceeds what the recorded")
        print("              maximum rate allows in a day. Those rates are floors")
        print("              grossed up from partial days, so the plan is telling")
        print("              us the real maximum is higher.")
    if "capacity" in res.binding:
        print("  capacity  - a tank overfills with no route to relieve it.")
    if "negative_inventory" in res.binding:
        print("  negative_inventory - a product is consumed faster than it can be")
        print("              made or bought, and lost sales do not cover it "
              "(a charge, not a sale, is drawing it down).")


if __name__ == "__main__":
    main()
