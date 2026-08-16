"""What the current plan would actually cost if the tanks were respected.

The workbook lets inventory run negative and over capacity because it has no way
to represent the response. In reality neither happens: a full tank is relieved by
downgrading to a low-netback outlet, and unserved demand is a lost sale. Running
the same schedule in physical mode converts those silent numbers into the flows a
planner would have been forced to make - which is the baseline the optimizer has
to beat, in units finance can price.

Writes data/reports/physical-baseline.md.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.engine import Reference, Scenario, simulate  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")

WINDOWS = [("first 14 days", 14), ("first 42 days", 42), ("first 90 days", 90),
           ("full year", None)]


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    workbook = simulate(ref, scn)
    phys = simulate(ref, scn, physical=True)
    dates = scn.dates
    L = []

    def w(s=""):
        L.append(s)

    w("# The current plan, priced\n")
    w("The same schedule, simulated twice: once the way the workbook computes it, "
      "and once with the tanks actually enforced (`0 <= inventory <= capacity`), "
      "recording what it cost to stay inside them.\n")

    w("## Totals\n")
    w("| Window | Lost sales (gal) | Downgrade required (gal) | Feed shortfall (gal) |"
      " Products affected |")
    w("|---|---:|---:|---:|---:|")
    for label, n in WINDOWS:
        win = dates[:n] if n else dates
        lost = dg = short = 0.0
        affected = set()
        for b in ref.blocks:
            bal = phys.balances.get(b["id"], {})
            for d in win:
                a = bal.get("Lost Sales", {}).get(d, 0.0)
                c = bal.get("Downgrade Required", {}).get(d, 0.0)
                s = bal.get("Feed Shortfall", {}).get(d, 0.0)
                lost += a
                dg += c
                short += s
                if a or c or s:
                    affected.add(b["id"])
        w("| {} | {:,.0f} | {:,.0f} | {:,.0f} | {} |".format(
            label, lost, dg, short, len(affected)))
    w("")
    w("Feed shortfall is the one that matters most for the optimizer: it is volume "
      "the schedule asked a unit to charge that the tank could not supply. Unlike "
      "a lost sale or a downgrade it has no price - it means the plan was not "
      "executable as written.\n")

    # ------------------------------------------------------------- by product
    for label, n in (("first 42 days", 42), ("full year", None)):
        win = dates[:n] if n else dates
        rows = []
        for b in ref.blocks:
            bal = phys.balances.get(b["id"], {})
            code = b.get("charge_code")
            name = (ref.products.get(code, {}) or {}).get("name", "")[:30]
            lost = sum(bal.get("Lost Sales", {}).get(d, 0.0) for d in win)
            dg = sum(bal.get("Downgrade Required", {}).get(d, 0.0) for d in win)
            short = sum(bal.get("Feed Shortfall", {}).get(d, 0.0) for d in win)
            cap = bal.get("Tank Capacity", {}).get(win[0], 0.0)
            if lost or dg or short:
                rows.append((lost + dg + short, code, name, cap, lost, dg, short))
        rows.sort(reverse=True)
        w("## By product, {}\n".format(label))
        w("| Product | Name | Capacity | Lost sales | Downgrade required | "
          "Feed shortfall |")
        w("|---|---|---:|---:|---:|---:|")
        for _, code, name, cap, lost, dg, short in rows[:22]:
            w("| {} | {} | {} | {:,.0f} | {:,.0f} | {:,.0f} |".format(
                code, name, "{:,.0f}".format(cap) if cap else "**none**",
                lost, dg, short))
        w("")

    # ------------------------------------------- what enforcement changes
    w("## What enforcing the tanks changes\n")
    w("Peak inventory in the workbook against the physical run. Where the workbook "
      "climbs far above capacity it is not forecasting inventory, it is "
      "accumulating material the plan never disposed of.\n")
    w("| Product | Capacity | Workbook peak | Physical peak | Workbook / capacity |")
    w("|---|---:|---:|---:|---:|")
    comp = []
    for b in ref.blocks:
        wb = workbook.balances.get(b["id"], {}).get("End Inventory", {})
        ph = phys.balances.get(b["id"], {}).get("End Inventory", {})
        cap = phys.balances.get(b["id"], {}).get("Tank Capacity", {}).get(dates[0], 0.0)
        code = b.get("charge_code")
        wpk = max((wb.get(d, 0.0) for d in dates), default=0.0)
        ppk = max((ph.get(d, 0.0) for d in dates), default=0.0)
        if cap > 0 and wpk > cap * 1.2:
            comp.append((wpk / cap, code,
                         (ref.products.get(code, {}) or {}).get("name", "")[:30],
                         cap, wpk, ppk))
    comp.sort(reverse=True)
    for ratio, code, name, cap, wpk, ppk in comp[:12]:
        w("| {} {} | {:,.0f} | {:,.0f} | {:,.0f} | {:.0f}x |".format(
            code, name, cap, wpk, ppk, ratio))
    w("")

    # ------------------------------------------------- downgrade destinations
    w("## Where the downgrades would go\n")
    w("The plan already routes some volume to low-netback outlets explicitly. "
      "These are the existing routes the optimizer should use, rather than "
      "inventing new ones.\n")
    routed = defaultdict(float)
    for line in ref.charge_lines:
        if not line["unit"].startswith(("TRANSFER", "SONNEBORN")):
            continue
        total = sum(scn.charge_bbl(line["key"], d) for d in dates) * 42.0
        if total:
            routed[line["unit"]] += total
    w("| Route | Volume in the current plan (gal) |")
    w("|---|---:|")
    names = {"TRANSFER_DIESEL": "Solvents -> diesel charge",
             "TRANSFER_FINDSL": "K-30 -> finished diesel",
             "TRANSFER_6OIL": "Resins / wax -> #6 oil",
             "TRANSFER_CAT": "Slack wax -> cat cracker",
             "SONNEBORN": "Sonneborn sales"}
    for unit, gal in sorted(routed.items(), key=lambda kv: -kv[1]):
        w("| {} | {:,.0f} |".format(names.get(unit, unit), gal))
    w("")
    w("Total explicitly routed in the plan: {:,.0f} gal. Against a required "
      "downgrade of {:,.0f} gal over the same horizon, the gap is what the "
      "planner is doing by hand - or not doing at all.\n".format(
          sum(routed.values()),
          sum(phys.balances.get(b["id"], {}).get("Downgrade Required", {}).get(d, 0.0)
              for b in ref.blocks for d in dates)))

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "physical-baseline.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
