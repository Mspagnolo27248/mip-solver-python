"""Maximum charge rate per unit and product, in barrels per day.

The model is `charge = max_rate x time_fraction`, so the parameter that matters is
an absolute rate, not a multiple of the workbook's monthly planning rate. Those
two are not the same thing: products routinely run above the planning rate, so
using it as a ceiling would cap the optimizer below what the plant does.

The best estimate available from the plan is the largest daily charge ever
recorded. That is a **lower bound** on the true maximum, because a day carrying a
changeover was only a partial day - a product that never ran a clean day has a
true rate higher than anything observed.

Writes data/reports/rate-analysis.md.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner import model_config as cfg               # noqa: E402
from invplanner.engine import Reference, Scenario, month_key  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")

UNITS = ["MEK", "HYDRO", "EXTRACT", "ROSE", "PLATFORMER"]


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    dates = scn.dates
    L = []

    def w(s=""):
        L.append(s)

    lines_by_unit = defaultdict(list)
    for line in ref.charge_lines:
        lines_by_unit[line["unit"]].append(line)

    w("# Maximum charge rates\n")
    w("`charge = max_rate x time_fraction`, so what the model needs is an "
      "absolute barrels-per-day maximum. The workbook's monthly run rate is a "
      "*planning* figure, not a ceiling - products run above it routinely, so "
      "using it as the maximum would cap the optimizer below what the plant "
      "already does.\n")

    w("## Observed maxima against the planning rate\n")
    w("`clean max` is the largest charge on a day with no changeover either side "
      "- a genuinely full day. `any max` includes partial days, so it "
      "understates. Where there is no clean day, the true maximum is higher than "
      "anything here.\n")
    w("| Unit | Charge | Name | Planning rate | Clean max | Any max | "
      "Clean/planning | Recommended max |")
    w("|---|---|---|---:|---:|---:|---:|---:|")
    rows = []
    for unit in UNITS:
        for line in lines_by_unit[unit]:
            code = line["code"]
            rates = ref.run_rates.get(code) or {}
            clean, anyd = [], []
            for i, d in enumerate(dates):
                bbl = scn.charge_bbl(line["key"], d)
                if bbl <= 0:
                    continue
                anyd.append(bbl)
                prev_on = i > 0 and scn.charge_bbl(line["key"], dates[i - 1]) > 0
                next_on = (i + 1 < len(dates)
                           and scn.charge_bbl(line["key"], dates[i + 1]) > 0)
                if prev_on and next_on:
                    clean.append(bbl)
            if not anyd:
                continue
            planning = max(rates.values()) if rates else None
            clean_max = max(clean) if clean else None
            any_max = max(anyd)
            # With no clean day, gross the observed maximum up by the time a
            # single changeover would have taken.
            if clean_max:
                rec = clean_max
                basis = "clean day"
            else:
                loss = cfg.switch_loss_days(unit)
                rec = any_max / (1.0 - loss) if loss < 1 else any_max
                basis = "grossed up"
            rows.append((unit, code, rec, basis, len(clean)))
            name = (ref.products.get(code, {}) or {}).get("name", "")[:22]
            w("| {} | {} | {} | {} | {} | {:,.0f} | {} | **{:,.0f}** |".format(
                unit, code, name,
                "{:,.0f}".format(planning) if planning else "—",
                "{:,.0f}".format(clean_max) if clean_max else "**none**",
                any_max,
                "{:.2f}".format(clean_max / planning) if clean_max and planning else "—",
                rec))
    w("")
    w("Where `clean max` is blank the recommendation is the observed maximum "
      "grossed up by one changeover's time loss, since the day it was set was "
      "necessarily partial. It remains a floor, not a specification.\n")

    w("## Config snippet\n")
    w("```python")
    w("MAX_RATE_BBL_PER_DAY = {")
    by_unit = defaultdict(list)
    for unit, code, rec, basis, n in rows:
        by_unit[unit].append((code, rec, basis, n))
    for unit, items in by_unit.items():
        w('    "{}": {{'.format(unit))
        for code, rec, basis, n in items:
            w('        "{}": {{"bbl": {:.0f}, "basis": "{}", "clean_days": {}}},'
              .format(code, rec, basis, n))
        w("    },")
    w("}")
    w("```")
    w("")

    w("## Worked example of the day budget\n")
    w("Two charges sharing a day, taken from the plant's own description.\n")
    w("```")
    w("  diesel   max 5,000 bbl/day")
    w("  4139     max 3,000 bbl/day")
    w("")
    w("  no changeover loss, day split 50/50:")
    w("      t[diesel] = 0.5  ->  5,000 x 0.5 = 2,500 bbl")
    w("      t[4139]   = 0.5  ->  3,000 x 0.5 = 1,500 bbl")
    w("      sum of time = 1.0                            OK")
    w("")
    w("  same split, but the two are on different reactors:")
    w("      interface loss 0.125 + reactor flush 0.250 = 0.375 of the day gone")
    w("      time left to share = 0.625, say 0.3125 each")
    w("      t[diesel] = 0.3125 -> 5,000 x 0.3125 = 1,563 bbl")
    w("      t[4139]   = 0.3125 -> 3,000 x 0.3125 =   938 bbl")
    w("```")
    w("")
    w("The second case is the whole point: crossing reactors costs 37.5% of the "
      "day's production on that unit, which is why the plant groups 4315 and "
      "4319 - and why the optimizer will too, without being told.\n")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "rate-analysis.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
