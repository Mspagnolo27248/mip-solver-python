"""The hydrotreater's two reactors, and switch losses across every unit.

The hydrotreater is one unit with two reactors: 4315 and 4319 are made on one,
everything else on the other. Crossing between them costs a quarter of a day
while the reactor is flushed back to charge. Every unit also loses time on an
ordinary changeover, because the interface goes back to charge.

This measures which charge products sit on which reactor, how often the plan
crosses between them, and what the observed same-day combinations imply.

Writes data/reports/hydro-reactor-analysis.md.
"""
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.engine import Reference, Scenario, month_key  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")

#: Products made on the dedicated reactor.
REACTOR_1_OUTPUTS = {"4315", "4319"}


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    dates = scn.dates
    L = []

    def w(s=""):
        L.append(s)

    w("# The hydrotreater's two reactors\n")

    # ------------------------------------------- which charge feeds which output
    charge_to_out = defaultdict(set)
    for rule in ref.yield_rules:
        if rule["kind"] == "unit" and rule.get("unit") == "HYDRO":
            charge_to_out[rule["charge_code"]].add(rule["out"])

    hydro_lines = [l for l in ref.charge_lines if l["unit"] == "HYDRO"]
    reactor_of = {}
    w("## Which charge product runs on which reactor\n")
    w("Assigned by what the charge makes: anything producing 4315 or 4319 is on "
      "the dedicated reactor.\n")
    w("| Charge | Name | Produces | Reactor |")
    w("|---|---|---|---|")
    for line in hydro_lines:
        code = line["code"]
        outs = sorted(charge_to_out.get(code, []))
        r = 1 if REACTOR_1_OUTPUTS & set(outs) else 2
        reactor_of[code] = r
        name = (ref.products.get(code, {}) or {}).get("name", "")[:28]
        w("| {} | {} | {} | **R{}** |".format(
            code, name, ", ".join(outs) or "—", r))
    w("")
    r1 = sorted(c for c, r in reactor_of.items() if r == 1)
    r2 = sorted(c for c, r in reactor_of.items() if r == 2)
    w("- **Reactor 1**: {}".format(", ".join(r1)))
    w("- **Reactor 2**: {}".format(", ".join(r2)))
    w("")

    # ------------------------------------------------------- daily occupancy
    seq = []
    for d in dates:
        on = sorted(l["code"] for l in hydro_lines
                    if scn.charge_bbl(l["key"], d) > 0)
        seq.append(on)

    combos = Counter(tuple(s) for s in seq if len(s) > 1)
    w("## Same-day combinations\n")
    w("The hydrotreater runs more than one charge on {} days. If a day were a "
      "single indivisible slot this would be impossible - it is the clearest "
      "evidence that the model has to allocate *time within the day*, not whole "
      "days.\n".format(sum(1 for s in seq if len(s) > 1)))
    w("| Combination | Days | Reactors | Reading |")
    w("|---|---:|---|---|")
    for combo, n in combos.most_common():
        rs = sorted({reactor_of.get(c, 2) for c in combo})
        if len(rs) == 1:
            reading = "same reactor, shares the day"
        else:
            reading = "**crosses reactors — a flush day**"
        w("| {} | {} | {} | {} |".format(
            " + ".join(combo), n, ", ".join("R{}".format(x) for x in rs), reading))
    w("")

    # --------------------------------------------------------- reactor changes
    def reactors_on(day_products):
        return {reactor_of.get(c, 2) for c in day_products}

    changes = 0
    within = 0
    prev = None
    for s in seq:
        if not s:
            continue
        cur = reactors_on(s)
        if prev is not None:
            if cur != prev and not (cur & prev):
                changes += 1
            elif s and prev is not None:
                within += 1
        prev = cur

    running = [s for s in seq if s]
    w("## How often the plan crosses reactors\n")
    w("| Measure | Value |")
    w("|---|---:|")
    w("| Days the hydrotreater runs | {} |".format(len(running)))
    w("| Reactor changes | {} |".format(changes))
    w("| Reactor changes per month | {:.1f} |".format(
        changes / (len(dates) / 30.0)))
    w("| Time lost to reactor flushes, at 1/4 day | {:.1f} days |".format(
        changes * 0.25))
    w("")

    # ------------------------------------------------------ campaign grouping
    r1_days = [i for i, s in enumerate(seq) if s and reactors_on(s) == {1}]
    runs = []
    if r1_days:
        start = prev_i = r1_days[0]
        for i in r1_days[1:]:
            if i != prev_i + 1:
                runs.append(prev_i - start + 1)
                start = i
            prev_i = i
        runs.append(prev_i - start + 1)
    w("Reactor 1 runs in {} campaigns over the year, median {} days, longest {}. "
      "Grouping 4315 and 4319 together is exactly the switch-minimising "
      "behaviour the plant describes - and it is what the optimizer will "
      "reproduce on its own once a reactor change costs a quarter of a "
      "day.\n".format(len(runs), sorted(runs)[len(runs) // 2] if runs else 0,
                      max(runs) if runs else 0))

    # ------------------------------------------------------------ rate spread
    w("## Rate range by charge product\n")
    w("Charge as a multiple of the published monthly run rate. This is what the "
      "min/max rate bounds have to cover.\n")
    w("| Unit | Charge | Days run | Min | Median | Max |")
    w("|---|---|---:|---:|---:|---:|")
    for unit in ["HYDRO", "MEK", "EXTRACT", "ROSE"]:
        for line in [l for l in ref.charge_lines if l["unit"] == unit]:
            rates = ref.run_rates.get(line["code"])
            if not rates:
                continue
            ratios = []
            for d in dates:
                bbl = scn.charge_bbl(line["key"], d)
                if bbl <= 0:
                    continue
                r = rates.get(month_key(d))
                if r:
                    ratios.append(bbl / r)
            if not ratios:
                continue
            ratios.sort()
            w("| {} | {} | {} | {:.2f} | {:.2f} | {:.2f} |".format(
                unit, line["code"], len(ratios), ratios[0],
                ratios[len(ratios) // 2], ratios[-1]))
    w("")
    w("Rates are not fixed points and not free either: each product runs within a "
      "band. Modelling charge as `rate x time` with the rate bounded turns both "
      "the band and the switch loss into one linear constraint on the day's "
      "hours.\n")

    # -------------------------------------------- implied switch loss from data
    w("## Implied switch loss, measured\n")
    w("If a changeover costs time on the unit, the first day of a campaign should "
      "carry less charge than the days that follow. Comparing the two gives an "
      "estimate of the loss without anyone having to quote it.\n")
    w("| Unit | Charge | First-day mean (bbl) | Later-day mean (bbl) | "
      "Implied loss |")
    w("|---|---|---:|---:|---:|")
    est = {}
    for unit in ["MEK", "EXTRACT", "ROSE", "HYDRO"]:
        lines = [l for l in ref.charge_lines if l["unit"] == unit]
        firsts = defaultdict(list)
        laters = defaultdict(list)
        for line in lines:
            prev_on = False
            for d in dates:
                bbl = scn.charge_bbl(line["key"], d)
                on = bbl > 0
                if on:
                    (firsts if not prev_on else laters)[line["code"]].append(bbl)
                prev_on = on
        unit_first, unit_later = [], []
        for code in sorted(set(firsts) | set(laters)):
            f, l2 = firsts.get(code, []), laters.get(code, [])
            if len(f) < 3 or len(l2) < 3:
                continue
            mf, ml = sum(f) / len(f), sum(l2) / len(l2)
            unit_first.extend(f)
            unit_later.extend(l2)
            loss = 1.0 - (mf / ml) if ml else 0.0
            w("| {} | {} | {:,.0f} | {:,.0f} | {:.0%} |".format(
                unit, code, mf, ml, loss))
        if unit_first and unit_later:
            mf = sum(unit_first) / len(unit_first)
            ml = sum(unit_later) / len(unit_later)
            est[unit] = 1.0 - (mf / ml) if ml else 0.0
    w("")
    if est:
        w("| Unit | Implied switch loss, all products |")
        w("|---|---:|")
        for unit, loss in est.items():
            w("| {} | **{:.0%} of a day** |".format(unit, loss))
        w("")
    w("Treat these as a sanity check rather than a specification - the planner "
      "may simply enter round numbers. But if the plant quotes a switch loss and "
      "these disagree badly, one of the two is wrong and it is worth knowing "
      "which before the optimizer starts trading switches against inventory.\n")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "hydro-reactor-analysis.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
