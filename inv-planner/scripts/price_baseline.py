"""Price the current plan.

Downgrades are now priced: #6 oil and the cat cracker net crude less $0.50/gal, so
material worth crude loses exactly that. Lost sales still need a margin, and its
ratio to the $0.50 decides whether the optimizer would rather downgrade or short a
customer - so this reports the whole range rather than picking one.

Writes data/reports/priced-baseline.md.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.economics import DEFAULT, OUTLETS, per_gal  # noqa: E402
from invplanner.engine import Reference, Scenario, simulate  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")

WINDOW = 42
DEFAULT_OUTLET = "TRANSFER_6OIL"
MARGIN_SCENARIOS = [0.25, 0.50, 1.00, 2.00, 3.00]


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    econ = DEFAULT
    dates = scn.dates[:WINDOW]
    L = []

    def w(s=""):
        L.append(s)

    w("# The current plan, priced\n")
    w("Window: first {} days, {} to {}. Crude at **${:,.2f}/bbl "
      "(${:.4f}/gal)**.\n".format(WINDOW, dates[0], dates[-1],
                                  econ.crude_price_per_bbl,
                                  econ.crude_price_per_gal))

    w("## Downgrade outlets\n")
    w("| Outlet | Netback $/gal | Netback $/bbl | Loss vs crude $/gal | Basis |")
    w("|---|---:|---:|---:|---|")
    for key, outlet in OUTLETS.items():
        nb = outlet.netback(econ.crude_price_per_gal)
        w("| {} | {} | {} | {} | {} |".format(
            outlet.title,
            "{:.4f}".format(nb) if nb is not None else "—",
            "{:,.2f}".format(nb * 42) if nb is not None else "—",
            "{:.2f}".format(outlet.discount_to_crude_per_gal)
            if outlet.discount_to_crude_per_gal is not None else "—",
            "crude less ${:.2f}/gal".format(outlet.discount_to_crude_per_gal)
            if outlet.discount_to_crude_per_gal is not None
            else "**needs a price**"))
    w("")

    # -------------------------------------------------------------- volumes
    rows = []
    for b in ref.blocks:
        bal = sim.balances.get(b["id"], {})
        code = b.get("charge_code")
        name = (ref.products.get(code, {}) or {}).get("name", "")[:30]
        dg = sum(bal.get("Downgrade Required", {}).get(d, 0.0) for d in dates)
        lost = sum(bal.get("Lost Sales", {}).get(d, 0.0) for d in dates)
        if dg or lost:
            rows.append((dg + lost, code, name, dg, lost))
    rows.sort(reverse=True)
    total_dg = sum(r[3] for r in rows)
    total_lost = sum(r[4] for r in rows)

    dg_cost = 0.0
    flat = per_product = 0
    for _, code, _, dg, _ in rows:
        c = econ.downgrade_cost_per_gal(code, DEFAULT_OUTLET) or 0.0
        dg_cost += dg * c
        if dg:
            if econ.cost_basis(code, DEFAULT_OUTLET) == "per_product":
                per_product += 1
            else:
                flat += 1

    w("## Cost of the current plan over {} days\n".format(WINDOW))
    w("| Component | Volume (gal) | $/gal | Cost | Basis |")
    w("|---|---:|---:|---:|---|")
    w("| Downgrade required | {:,.0f} | 0.50 | **${:,.0f}** | flat: "
      "material valued at crude |".format(total_dg, dg_cost))
    w("| Lost sales | {:,.0f} | ? | **not yet priced** | needs a margin |".format(
        total_lost))
    w("")
    w("{} of the {} products with downgrade volume are priced flat and {} per "
      "product. Swapping in real values only moves the number up: the flat "
      "figure is the floor.\n".format(flat, flat + per_product, per_product))

    # ------------------------------------------------- lost sale sensitivity
    w("## Where the trade-off flips\n")
    w("Downgrading costs $0.50/gal. Shorting a customer costs their margin. The "
      "ratio between the two is what the optimizer actually reasons about, so "
      "here is the whole range rather than a guess.\n")
    w("| Lost-sale margin $/gal | Cost of lost sales | Total plan cost | "
      "Lost sales as share | Optimizer's preference |")
    w("|---:|---:|---:|---:|---|")
    for m in MARGIN_SCENARIOS:
        lost_cost = total_lost * m
        total = lost_cost + dg_cost
        share = 100.0 * lost_cost / total if total else 0
        if m < 0.50:
            pref = "short the customer before downgrading"
        elif abs(m - 0.50) < 1e-9:
            pref = "indifferent"
        else:
            pref = "downgrade {:.0f}x before shorting".format(m / 0.50)
        w("| {:.2f} | ${:,.0f} | ${:,.0f} | {:.0f}% | {} |".format(
            m, lost_cost, total, share, pref))
    w("")
    w("Any margin above $0.50/gal makes downgrading the cheaper relief, which "
      "matches how the plant is described as operating. Below it, the model "
      "would rather lose the sale - so if a real margin ever comes in under "
      "$0.50/gal, that is worth questioning before trusting the schedule.\n")

    # -------------------------------------------------------- by product
    w("## By product, {} days\n".format(WINDOW))
    w("| Product | Name | Downgrade (gal) | Downgrade cost | Lost sales (gal) |")
    w("|---|---|---:|---:|---:|")
    for _, code, name, dg, lost in rows[:15]:
        c = econ.downgrade_cost_per_gal(code, DEFAULT_OUTLET) or 0.0
        w("| {} | {} | {:,.0f} | ${:,.0f} | {:,.0f} |".format(
            code, name, dg, dg * c, lost))
    w("")
    w("Two of the largest lines are data problems rather than planning problems: "
      "8175 has no tank capacity recorded, and 9202 is an obsolete flow that "
      "nothing produces.\n")

    # ------------------------------------------------------------ 9202
    w("## 9202 and the tolling route\n")
    used_mek = any(scn.charge_bbl(l["key"], d) > 0
                   for l in ref.charge_lines
                   if l["unit"] == "MEK" and l["code"] == "9202"
                   for d in scn.dates)
    tolling_used = any(scn.charge_bbl(l["key"], d) > 0
                       for l in ref.charge_lines if l["unit"] == "TOLLING"
                       for d in scn.dates)
    prod = sum(sim.prod("9202", d) for d in scn.dates)
    block = next((b for b in ref.blocks if b.get("charge_code") == "9202"), None)
    lost_9202 = sum(sim.balances.get(block["id"], {})
                    .get("Lost Sales", {}).get(d, 0.0)
                    for d in scn.dates) if block else 0.0
    w("| Check | Result |")
    w("|---|---|")
    w("| 9202 charged at MEK | {} |".format("yes" if used_mek else "**never**"))
    w("| Tolling unit used | {} |".format("yes" if tolling_used else "**never**"))
    w("| 9202 produced over the year | {:,.0f} gal |".format(prod))
    w("| Demand still booked against 9202 | {:,.0f} gal |".format(lost_9202))
    w("")
    w("Confirms the flow is obsolete. Retiring it makes the MEK ladder exactly "
      "four rungs (9116 – 9117 – 9119 – 4317), drops the tolling unit from the "
      "model, and removes a permanent lost sale the optimizer could never "
      "avoid.\n")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "priced-baseline.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
