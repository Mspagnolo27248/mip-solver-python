"""Reconcile the optimizer's balance against the simulator's, term by term.

When the two disagree, patching the next suspicious term is guesswork. This finds
the product that accounts for most of the gap, then prints every term of both
balances for it, day by day, so the divergence has to show itself.

Usage:  python scripts/reconcile_v0.py [days] [product]
"""
import copy
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner import model_config as cfg                  # noqa: E402
from invplanner.engine import (GAL_PER_BBL, Reference,      # noqa: E402
                               Scenario, simulate)
from invplanner.modelprep import build                      # noqa: E402
from invplanner.optimizer import v0                         # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 14
FOCUS = sys.argv[2] if len(sys.argv) > 2 else None

PARAMS = {"lost_sale_margin_per_gal": 1.50, "downgrade_discount_per_gal": 0.50,
          "netback_diesel_cost_per_gal": 0.37,
          "netback_gasoline_cost_per_gal": 0.22,
          "horizon_days": DAYS, "time_limit_seconds": 120, "mip_gap": 0.0}


def apply_result(scn, result):
    """The base scenario with the optimizer's schedule written in."""
    data = copy.deepcopy(scn.raw)
    lines = data["charge_schedule"]["lines"]
    for source in (result.charge, result.transfer_bbl):
        for key, series in source.items():
            lines.setdefault(key, {})
            for s, bbl in series.items():
                if bbl:
                    lines[key][s] = bbl
                else:
                    lines[key].pop(s, None)
    return Scenario(data)


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    base_sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:DAYS]
    iso = [d.isoformat() for d in dates]
    downtime = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, base_sim, horizon=dates, downtime=downtime)

    res = v0.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=downtime)
    print("v0 solve: {}  objective {:,.0f}\n".format(res.status, res.objective or 0))
    if res.status != "optimal":
        return

    proposed = apply_result(scn, res)
    sim = simulate(ref, proposed, physical=True)

    # --------------------------------------------- per product, who disagrees
    opt_lost = {p: sum(v.values()) for p, v in res.lost_sales.items()}
    sim_lost = defaultdict(float)
    block_of = {}
    for b in ref.blocks:
        code = b.get("charge_code")
        target = spec.aliases.get(code, code)
        bal = sim.balances.get(b["id"], {})
        sim_lost[target] += sum(bal.get("Lost Sales", {}).get(d, 0.0) for d in dates)
        block_of.setdefault(target, b)

    rows = []
    for p in set(opt_lost) | set(sim_lost):
        o, s = opt_lost.get(p, 0.0), sim_lost.get(p, 0.0)
        if abs(o - s) > 1:
            rows.append((abs(o - s), p, o, s))
    rows.sort(reverse=True)

    print("Lost sales by product: optimizer vs simulator\n")
    print("  {:<8} {:>14} {:>14} {:>14}".format("product", "optimizer",
                                                "simulator", "delta"))
    for _, p, o, s in rows[:12]:
        print("  {:<8} {:>14,.0f} {:>14,.0f} {:>+14,.0f}".format(p, o, s, s - o))
    if not rows:
        print("  (they agree)")
        return
    print("\n  total gap {:,.0f} gal across {} products\n".format(
        sum(r[0] for r in rows), len(rows)))

    # --------------------------------------------------- drill into the worst
    target = FOCUS or rows[0][1]
    block = block_of.get(target)
    if block is None:
        print("no block for {}".format(target))
        return
    bal = sim.balances.get(block["id"], {})
    print("=" * 78)
    print("TERM BY TERM for {} (block {})".format(target, block["id"]))
    print("=" * 78)

    prod = spec.products.get(target, {})
    print("opening {:,.0f}   capacity {:,.0f}   tanked={}  sink={}\n".format(
        prod.get("opening", 0), prod.get("capacity", 0),
        prod.get("tanked"), prod.get("is_sink")))

    dem = defaultdict(float)
    for b in ref.blocks:
        code = b.get("charge_code")
        if spec.aliases.get(code, code) != target:
            continue
        for row, series in spec.demand_rows.get(b["id"], {}).items():
            for s, gal in series.items():
                dem[s] += gal

    print("  {:<12} {:>13} {:>13} {:>13} {:>13}".format(
        "date", "opt demand", "sim demand", "opt lost", "sim lost"))
    for d, s in zip(dates, iso):
        sim_dem = sum(bal.get(r, {}).get(d, 0.0)
                      for r in ("Sales", "Forecast", "Blends"))
        print("  {:<12} {:>13,.0f} {:>13,.0f} {:>13,.0f} {:>13,.0f}".format(
            s, dem.get(s, 0.0), sim_dem,
            res.lost_sales.get(target, {}).get(s, 0.0),
            bal.get("Lost Sales", {}).get(d, 0.0)))

    print("\n  {:<12} {:>13} {:>13} {:>13}".format(
        "date", "sim prod in", "sim chg out", "sim end inv"))
    for d, s in zip(dates, iso):
        print("  {:<12} {:>13,.0f} {:>13,.0f} {:>13,.0f}".format(
            s, bal.get("Production In", {}).get(d, 0.0),
            bal.get("Production Out", {}).get(d, 0.0)
            + bal.get("Out to Diesel", {}).get(d, 0.0),
            bal.get("End Inventory", {}).get(d, 0.0)))


if __name__ == "__main__":
    main()
