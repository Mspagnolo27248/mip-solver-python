"""Pre-implementation review: the assumptions still carrying weight untested.

Everything modelled so far has been checked against the plan. These are the
remaining load-bearing assumptions that had not been, and one of them changes how
the whole horizon should be read.

Writes data/reports/readiness-review.md.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner import layout as LX                       # noqa: E402
from invplanner import model_config as cfg                # noqa: E402
from invplanner.engine import Reference, Scenario, simulate  # noqa: E402
from invplanner.modelprep import build                    # noqa: E402
from invplanner.xlsx import is_num, load, norm_code, text  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")
WORKBOOK = os.path.abspath(os.path.join(ROOT, "..", LX.WORKBOOK_DEFAULT))
UNITS = ["MEK", "HYDRO", "EXTRACT", "ROSE", "PLATFORMER"]


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    spec = build(ref, scn, sim, horizon=scn.dates[:42])
    dates = scn.dates
    L = []

    def w(s=""):
        L.append(s)

    lines_by_unit = defaultdict(list)
    for line in ref.charge_lines:
        lines_by_unit[line["unit"]].append(line)

    def running_days(unit):
        return [d for d in dates
                if any(scn.charge_bbl(l["key"], d) > 0 for l in lines_by_unit[unit])]

    w("# Pre-implementation review\n")

    # ------------------------------------------ 1. how far the plan is populated
    w("## 1. The plan stops halfway, and that changes how to read everything\n")
    w("| Unit | First scheduled | Last scheduled | Days scheduled |")
    w("|---|---|---|---:|")
    for unit in UNITS:
        days = running_days(unit)
        w("| {} | {} | {} | {} |".format(
            unit, days[0] if days else "—", days[-1] if days else "—", len(days)))
    crude_days = [d for d in dates if scn.crude_bbl.get(d.isoformat(), 0) > 0]
    w("| CRUDE | {} | {} | {} |".format(
        crude_days[0], crude_days[-1], len(crude_days)))
    w("")
    w("**The processing units are scheduled only to 2026-12-31 — about five "
      "months — while the crude unit and platformer run the full year.** Crude "
      "keeps making side streams that nothing downstream consumes after New "
      "Year.\n")
    w("That is the real explanation for the inventory drift found earlier: Kensol "
      "17 ending the year at 100x its tank, Kensol 61 at 16x. It is not "
      "extrapolation gone wrong, it is a half-filled schedule. Consequences:\n")
    w("- **Full-year baseline figures are artefacts.** The 95 M gal of required "
      "downgrade over a year is mostly the unscheduled tail. The 42-day and "
      "90-day numbers are sound.")
    w("- **The optimizer's 42-day window sits safely inside the populated "
      "region** (to 2026-09-02), so the warm start and the comparison baseline "
      "are both valid.")
    w("- **The aggregate tail cannot be built from this plan.** Months 2-12 have "
      "no schedule to aggregate; the tail model has to be driven by demand and "
      "capacity, not by copying the workbook.\n")

    # ------------------------------------------------------- 2. shared tanks
    w("## 2. Do products share physical tanks? Swing tanks, one at a time\n")
    shared_any = shared_live = 0
    examples = []
    if os.path.exists(WORKBOOK):
        wv = load(WORKBOOK, data_only=True)
        ws = wv[LX.INVENTORY]
        by_tank = defaultdict(list)
        for r in range(LX.INV_FIRST_ROW, ws.max_row + 1):
            code = norm_code(ws.cell(r, LX.INV_CODE_COL).value)
            tank = text(ws.cell(r, LX.INV_TANK_COL).value)
            gal = ws.cell(r, LX.INV_GALLONS_COL).value
            if not code or not tank:
                continue
            by_tank[tank].append((code, float(gal) if is_num(gal) else 0.0))
        for tank, items in by_tank.items():
            if len({c for c, _ in items}) > 1:
                shared_any += 1
                examples.append((tank, sorted({c for c, _ in items})))
            if len([1 for _, g in items if g > 0]) > 1:
                shared_live += 1
        w("| Measure | Value |")
        w("|---|---:|")
        w("| Tanks in the inventory feed | {} |".format(len(by_tank)))
        w("| Tanks listed against more than one product | {} |".format(shared_any))
        w("| Tanks holding more than one product **at the same time** | "
          "**{}** |".format(shared_live))
        w("")
        w("**None.** The {} multi-product tanks are swing tanks: they take "
          "different products at different times, never two at once. So no joint "
          "capacity constraint is needed and per-product capacity is sound.\n"
          .format(shared_any))
        w("Two consequences worth carrying forward:\n")
        w("- Capacity is really *capacity given the current tank assignment*. "
          "Over a 42-day window that is stable enough to treat as fixed.")
        w("- **Swing tanks are flexibility the model cannot see.** Faced with an "
          "overflow the plant can reassign a tank; the optimizer can only "
          "downgrade. It will therefore over-state how much downgrading is "
          "needed, which is the safe direction to be wrong in, but worth "
          "knowing when a planner disputes a result.\n")
    else:
        w("_Workbook not found; skipped._\n")

    # ------------------------------------------------------- 3. the crude unit
    w("## 3. The crude unit drives everything and is barely modelled\n")
    charges = [scn.crude_bbl.get(d.isoformat(), 0.0) for d in dates]
    running = [c for c in charges if c > 0]
    modes = [scn.crude_mode.get(d.isoformat()) for d in dates]
    runs = []
    for m in modes:
        if m is None:
            continue
        if not runs or m != runs[-1][0]:
            runs.append([m, 1])
        else:
            runs[-1][1] += 1
    lens = sorted(r[1] for r in runs)
    w("| Measure | Value |")
    w("|---|---:|")
    w("| Days charging crude | {} of {} |".format(len(running), len(dates)))
    w("| Charge rate min / median / max | {:,.0f} / {:,.0f} / {:,.0f} bbl/d |".format(
        min(running), sorted(running)[len(running) // 2], max(running)))
    w("| Distinct charge rates used | {} |".format(len(set(running))))
    w("| R/L mode campaigns | {} |".format(len(runs)))
    w("| Mode campaign min / median / max | {} / {} / {} days |".format(
        lens[0], lens[len(lens) // 2], lens[-1]))
    w("| Mode switches per month | {:.1f} |".format(
        (len(runs) - 1) / (len(dates) / 30.0)))
    w("")
    w("Every downstream product starts here, yet the model treats crude charge as "
      "a given. Three questions:\n")
    w("- **Does an R/L mode switch cost time?** It is a cut change; every other "
      "unit's changeover costs an eighth of a day. At {} switches a year that "
      "would be real capacity, and it is not in the model.".format(len(runs) - 1))
    w("- **Is the charge rate a decision?** Five distinct rates appear, so the "
      "planner does vary it, but no minimum or maximum is recorded anywhere. If "
      "crude rate is a lever, it is the most powerful one in the plant.")
    w("- **Is crude supply ever binding?** Receipts are a monthly plan and the "
      "model assumes crude is always there.\n")

    # -------------------------------------------------------- 4. outages
    w("## 4. Outages, within the region that is actually scheduled\n")
    w("Idle stretches measured only up to each unit's last scheduled day, so the "
      "unpopulated tail does not masquerade as a turnaround.\n")
    w("| Unit | Longest idle inside the plan | Idle stretches over 5 days |")
    w("|---|---:|---:|")
    for unit in UNITS:
        days = running_days(unit)
        if not days:
            continue
        window = [d for d in dates if days[0] <= d <= days[-1]]
        idle = longest = long_runs = 0
        for d in window:
            on = any(scn.charge_bbl(l["key"], d) > 0 for l in lines_by_unit[unit])
            if on:
                if idle > 5:
                    long_runs += 1
                longest = max(longest, idle)
                idle = 0
            else:
                idle += 1
        longest = max(longest, idle)
        w("| {} | {} days | {} |".format(unit, longest, long_runs))
    w("")
    w("Nothing here looks like a turnaround - these are ordinary gaps between "
      "campaigns. **So this plan contains no maintenance outage at all**, which "
      "means the model has never been tested against one. Where does the "
      "maintenance calendar live, and what happens to the schedule around it?\n")

    # ---------------------------------------------------- 5. initial state
    w("## 5. What is each unit set up for on day zero?\n")
    w("The setup state persists across days, so the model needs to know what each "
      "unit holds when the horizon opens. Get it wrong and day one either gets a "
      "free changeover or pays one it does not owe.\n")
    w("| Unit | Charge on its first scheduled day | Reactor |")
    w("|---|---|---|")
    for unit in UNITS:
        days = running_days(unit)
        if not days:
            continue
        first = days[0]
        on = [l["code"] for l in lines_by_unit[unit]
              if scn.charge_bbl(l["key"], first) > 0]
        w("| {} | {} on {} | {} |".format(
            unit, ", ".join(on), first, cfg.reactor_of(unit, on[0]) or "—"))
    w("")
    w("These are what the schedule *wants* on day one, not necessarily what the "
      "unit is holding. **The live feed has to supply the true current setup**, "
      "the same way it supplies inventory - otherwise every nightly re-solve "
      "starts with a free or phantom changeover.\n")

    # ------------------------------------------------- 6. blends as demand
    w("## 6. Blending is demand, not a decision\n")
    bom = ref.blend_bom
    blends = {b["blend"] for b in bom}
    comps = {b["component"] for b in bom}
    tracked = set()
    for b in ref.blocks:
        for c in (b.get("charge_code"), b.get("sold_code")):
            if c:
                tracked.add(c)
    w("- {} blended products drawing on {} components.".format(
        len(blends), len(comps)))
    w("- Of those components, {} have an inventory block the model tracks; "
      "{} do not.".format(len(comps & tracked), len(comps - tracked)))
    w("")
    w("Blend demand arrives as a fixed monthly rate per component, so **the model "
      "cannot decide not to make a blend when a component is short** - it will "
      "book a lost sale on the component instead of rescheduling the blend. "
      "Whether that matters depends on how much freedom the blend plan really "
      "has. If blends can be moved, they are a cheaper relief valve than "
      "downgrading and the model is currently blind to it.\n")

    # ------------------------------------------------------ 7. demand shape
    w("## 7. Demand is flat within a month\n")
    order_days = sorted({d for by in scn.open_orders.values() for d in by})
    w("Firm orders are dated ({} distinct days). Everything beyond them is a flat "
      "gal/day rate applied to every day of the month.\n".format(len(order_days)))
    w("Over a 42-day window that shape matters: a tank overflows on the day it "
      "overflows, not on the monthly average. If real liftings are lumpy - "
      "month-end pulls, weekly liftings - the model will misplace overflow and "
      "stockout by days, and a schedule built on it will be tight in the wrong "
      "places. Worth asking whether the forecast can carry a shape.\n")

    # ------------------------------------------------------- 8. solver check
    w("## 8. Nothing has been solved yet\n")
    n_lines = sum(1 for l in spec.charge_lines
                  if not l["unit"].startswith(("TRANSFER", "SONNEBORN", "RAILCAR")))
    w("| | Count |")
    w("|---|---:|")
    w("| Model products | {} |".format(len(spec.products)))
    w("| Process charge lines | {} |".format(n_lines))
    w("| Units with a setup decision | {} |".format(
        len([u for u in spec.units if u in UNITS])))
    w("")
    w("A 42-day window is roughly {:,} setup binaries plus the changeover and "
      "time variables. That should solve comfortably, but **no model has been "
      "built or solved yet** - the estimate is arithmetic, not experience. The "
      "first thing v0 proves is whether the solve time is what we think it "
      "is.\n".format(n_lines * 42))

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "readiness-review.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
