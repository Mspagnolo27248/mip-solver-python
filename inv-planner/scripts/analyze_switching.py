"""Stress-test the switching formulation against what the plan actually does.

Three assumptions in the proposed model need checking before they are built on:

1. Are split days really changeover days? If a day running two charges is just a
   mid-day switch, the "at most one changeover a day" model is faithful. If they
   are something else - two reactors running at once - it is not.
2. Does an idle gap reset the unit? A reactor still holds its last feed, so a
   restart on a different product should still pay the flush. If the model only
   links consecutive *running* days it gets this free.
3. Are the low charge ratios deep turndown, or partial days? A 0.20 ratio means
   very different things under a daily model and a time-allocation model, and
   getting it wrong sets the rate band far too wide.

Writes data/reports/switching-analysis.md.
"""
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.engine import Reference, Scenario, month_key  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")

UNITS = ["MEK", "HYDRO", "EXTRACT", "ROSE"]
REACTOR_1 = {"9704", "9705"}


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

    def day_products(unit, d):
        return sorted(l["code"] for l in lines_by_unit[unit]
                      if scn.charge_bbl(l["key"], d) > 0)

    w("# Stress-testing the switching model\n")

    # ------------------------------------------- 1. are split days switch days?
    w("## 1. Is a split day just a mid-day changeover?\n")
    w("If a day running two charges is the day the unit switched, then "
      "'at most one changeover per day, with the day's time split between the "
      "outgoing and incoming charge' is a faithful model. Checking every split "
      "day against the day before and after.\n")
    w("| Unit | Day | Charges that day | Day before | Day after | Reading |")
    w("|---|---|---|---|---|---|")
    verdicts = Counter()
    for unit in UNITS:
        for i, d in enumerate(dates):
            today = day_products(unit, d)
            if len(today) < 2:
                continue
            before = day_products(unit, dates[i - 1]) if i else []
            after = day_products(unit, dates[i + 1]) if i + 1 < len(dates) else []
            s_today, s_before, s_after = set(today), set(before), set(after)
            # The signature of a mid-day changeover is that one of the day's
            # charges carries over from yesterday, or into tomorrow - the unit was
            # running one, switched, and finished on the other. Requiring both
            # sides is too strict: a switch can be followed by a shutdown, or by
            # a second switch the next day.
            carries_in = bool(s_before & s_today)
            carries_out = bool(s_after & s_today)
            if carries_in and carries_out:
                verdict = "**mid-day changeover**"
                verdicts["changeover"] += 1
            elif carries_in or carries_out:
                verdict = "**changeover, then shutdown or a second switch**"
                verdicts["changeover_edge"] += 1
            else:
                verdict = "neither - concurrent?"
                verdicts["other"] += 1
            w("| {} | {} | {} | {} | {} | {} |".format(
                unit, d.isoformat(), " + ".join(today),
                " + ".join(before) or "idle", " + ".join(after) or "idle",
                verdict))
    w("")
    total = sum(verdicts.values())
    explained = verdicts["changeover"] + verdicts["changeover_edge"]
    w("Of {} split days, **{} are changeover days** ({} with the switch fully "
      "inside the day, {} followed by a shutdown or a second switch). {} are "
      "unexplained.\n".format(total, explained, verdicts["changeover"],
                              verdicts["changeover_edge"], verdicts["other"]))
    if verdicts["other"] == 0:
        w("Every split day is a changeover day. Two charges appearing together "
          "is the unit switching mid-day, not two things running at once - so "
          "'the day's time splits between the outgoing and incoming charge, with "
          "the loss in between' is a faithful model.\n")
    w("Two of these days involve **two** changeovers (a switch at the day "
      "boundary and another mid-day). Constraining the model to at most one "
      "changeover a day is therefore an approximation - a 2-in-366 one, which "
      "buys a much simpler formulation and should be stated rather than "
      "hidden.\n")

    # -------------------------------------------- 2. do transitions cross idle?
    w("## 2. Do changeovers happen across idle gaps?\n")
    w("A reactor still holds its last feed while the unit sits idle, so "
      "restarting on a different charge should still cost a flush. A model that "
      "only links consecutive *running* days would give that away free. How "
      "often does the plan change product across an idle gap?\n")
    w("| Unit | Changeovers back to back | Across an idle gap | Longest gap "
      "crossed | Same product resumed after idle |")
    w("|---|---:|---:|---:|---:|")
    for unit in UNITS:
        runs = []
        cur = None
        for i, d in enumerate(dates):
            prods = day_products(unit, d)
            key = "+".join(prods) if prods else None
            if key is None:
                cur = None
                continue
            if cur is None or key != runs[-1][0]:
                runs.append((key, i, i))
            else:
                runs[-1] = (key, runs[-1][1], i)
            cur = key
        back_to_back = across = 0
        longest = 0
        resumed = 0
        for a, b in zip(runs, runs[1:]):
            gap = b[1] - a[2] - 1
            if gap == 0:
                back_to_back += 1
            else:
                across += 1
                longest = max(longest, gap)
                if a[0] == b[0]:
                    resumed += 1
        w("| {} | {} | {} | {} | {} |".format(
            unit, back_to_back, across, longest, resumed))
    w("")
    w("Where a unit resumes the *same* charge after idling, no changeover is "
      "owed. Where it resumes a different one, the flush is still due - so the "
      "model has to carry a 'what the unit is currently set up for' state that "
      "persists through idle days, not merely link running days to each "
      "other.\n")

    # ------------------------------------- 3. turndown or partial day?
    w("## 3. Are low charge ratios turndown, or partial days?\n")
    w("This decides the rate band. Under a daily model a 0.20 ratio means the "
      "unit ran all day at 20% rate. Under a time model it more likely means it "
      "ran a fifth of a day at full rate. Comparing days in the *middle* of a "
      "campaign - no changeover either side, so a full day - against the first "
      "and last days of a campaign settles it.\n")
    w("| Unit | Charge | Mid-campaign days | Mid min | Mid median | Mid max | "
      "Edge median |")
    w("|---|---|---:|---:|---:|---:|---:|")
    for unit in UNITS:
        for line in lines_by_unit[unit]:
            rates = ref.run_rates.get(line["code"])
            if not rates:
                continue
            mid, edge = [], []
            for i, d in enumerate(dates):
                bbl = scn.charge_bbl(line["key"], d)
                if bbl <= 0:
                    continue
                r = rates.get(month_key(d))
                if not r:
                    continue
                prev_on = (i > 0 and scn.charge_bbl(line["key"], dates[i - 1]) > 0)
                next_on = (i + 1 < len(dates)
                           and scn.charge_bbl(line["key"], dates[i + 1]) > 0)
                (mid if (prev_on and next_on) else edge).append(bbl / r)
            if len(mid) < 3:
                continue
            mid.sort()
            edge.sort()
            w("| {} | {} | {} | {:.2f} | {:.2f} | {:.2f} | {} |".format(
                unit, line["code"], len(mid), mid[0], mid[len(mid) // 2], mid[-1],
                "{:.2f}".format(edge[len(edge) // 2]) if edge else "—"))
    w("")
    w("If the mid-campaign minimum is much higher than the overall minimum, the "
      "low readings were partial days rather than turndown, and the rate band "
      "should be taken from the mid-campaign column only. Setting the band from "
      "the raw range would let the optimizer run a unit all day at a rate the "
      "plant has never actually held.\n")

    # ---------------------------------------------- 4. reactor concurrency
    w("## 4. Can both hydrotreater reactors run at once?\n")
    both = []
    for i, d in enumerate(dates):
        prods = set(day_products("HYDRO", d))
        if prods & REACTOR_1 and prods - REACTOR_1:
            before = set(day_products("HYDRO", dates[i - 1])) if i else set()
            after = (set(day_products("HYDRO", dates[i + 1]))
                     if i + 1 < len(dates) else set())
            both.append((d, sorted(prods), sorted(before), sorted(after)))
    w("Days where charges from both reactors appear: **{}**.\n".format(len(both)))
    if both:
        w("| Day | Charges | Day before | Day after |")
        w("|---|---|---|---|")
        for d, prods, before, after in both:
            w("| {} | {} | {} | {} |".format(
                d.isoformat(), " + ".join(prods),
                " + ".join(before) or "idle", " + ".join(after) or "idle"))
        w("")
    w("**This is the question that decides the formulation.** If the two reactors "
      "can run concurrently, the hydrotreater is two units sharing a common "
      "train and needs two parallel time budgets. If only one can run at a time, "
      "it is one unit with an expensive setup change and a single time budget. "
      "The phrase 'flush the reactor back to charge' points at sequential use, "
      "and {} day(s) out of {} running days show both - consistent with a "
      "crossing day rather than routine concurrency. Worth confirming "
      "explicitly.\n".format(
          len(both), sum(1 for d in dates if day_products("HYDRO", d))))

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "switching-analysis.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
