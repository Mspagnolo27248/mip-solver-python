"""Measure the planning problem, to ground the MIP formulation in what the plant
actually does rather than in what a textbook says it should do.

Writes data/reports/optimization-analysis.md.
"""
import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.engine import (GAL_PER_BBL, Reference, Scenario,  # noqa: E402
                               month_key, simulate)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")

PROCESS_UNITS = ["MEK", "HYDRO", "EXTRACT", "TOLLING", "ROSE", "PLATFORMER"]
TRANSFER_UNITS = ["TRANSFER_DIESEL", "TRANSFER_FINDSL", "TRANSFER_6OIL",
                  "TRANSFER_CAT", "SONNEBORN", "RAILCAR"]


def runs(flags):
    """[(value, start_index, length)] for consecutive equal non-None values."""
    out = []
    i = 0
    while i < len(flags):
        if flags[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < len(flags) and flags[j + 1] == flags[i]:
            j += 1
        out.append((flags[i], i, j - i + 1))
        i = j + 1
    return out


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn)
    dates = scn.dates
    L = []

    def w(s=""):
        L.append(s)

    w("# What the optimization model has to reproduce\n")
    w("Measured from the baseline scenario as the planner built it: "
      "{} days, {} to {}.\n".format(len(dates), dates[0], dates[-1]))

    # ---------------------------------------------------------------- 1. units
    w("## 1. What is actually decided\n")
    lines_by_unit = defaultdict(list)
    for line in ref.charge_lines:
        lines_by_unit[line["unit"]].append(line)

    w("| Unit | Charge options | Days running | Idle days | Days with >1 product |"
      " Distinct products used |")
    w("|---|---:|---:|---:|---:|---:|")
    split_day_total = 0
    for unit in PROCESS_UNITS:
        lines = lines_by_unit.get(unit, [])
        active = 0
        multi = 0
        used = set()
        for d in dates:
            on = [l for l in lines if scn.charge_bbl(l["key"], d) > 0]
            if on:
                active += 1
                used.update(l["code"] for l in on)
            if len(on) > 1:
                multi += 1
        split_day_total += multi
        w("| {} | {} | {} | {} | {} | {} |".format(
            unit, len(lines), active, len(dates) - active, multi, len(used)))

    crude_days = sum(1 for d in dates if scn.crude_bbl.get(d.isoformat(), 0) > 0)
    w("| CRUDE | 1 (rate + R/L mode) | {} | {} | n/a | n/a |".format(
        crude_days, len(dates) - crude_days))
    w("")
    w("**Split days: {}.** {}\n".format(
        split_day_total,
        "Units do charge more than one product on the same day, so the assignment "
        "variable cannot be a simple one-hot per unit-day."
        if split_day_total else
        "No unit ever charges two products on the same day, so a one-hot "
        "assignment per unit-day is a faithful model."))

    # ------------------------------------------------------- 2. rate adherence
    w("## 2. Are charge rates decisions or constants?\n")
    exact = off = 0
    ratios = []
    missing_rate = Counter()
    for unit in PROCESS_UNITS:
        for line in lines_by_unit.get(unit, []):
            rates = ref.run_rates.get(line["code"])
            for d in dates:
                bbl = scn.charge_bbl(line["key"], d)
                if bbl <= 0:
                    continue
                if not rates:
                    missing_rate[line["code"]] += 1
                    continue
                r = rates.get(month_key(d))
                if not r:
                    missing_rate[line["code"]] += 1
                    continue
                ratio = bbl / r
                ratios.append(ratio)
                if abs(ratio - 1.0) < 1e-6:
                    exact += 1
                else:
                    off += 1
    total = exact + off
    w("Of {:,} charged unit-days with a published run rate, **{:,} ({:.1f}%) are "
      "exactly the monthly run rate** and {:,} differ.\n".format(
          total, exact, 100.0 * exact / total if total else 0, off))
    if ratios:
        srt = sorted(ratios)
        w("Ratio of actual charge to run rate: min {:.2f}, median {:.2f}, "
          "max {:.2f}.\n".format(srt[0], srt[len(srt) // 2], srt[-1]))
    if missing_rate:
        w("Charged products with no run rate at all: {}.\n".format(
            ", ".join("{} ({} days)".format(k, v)
                      for k, v in missing_rate.most_common(8))))

    # ---------------------------------------------------------- 3. campaigns
    w("## 3. How the planner campaigns each unit\n")
    w("Consecutive days a unit stays on the same charge product. This is the "
      "structure a naive inventory-optimal model destroys.\n")
    w("| Unit | Campaigns | Median length | Mean | Longest | Switches | "
      "Switches/month |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    campaign_stats = {}
    for unit in PROCESS_UNITS:
        lines = lines_by_unit.get(unit, [])
        seq = []
        for d in dates:
            on = [l["code"] for l in lines if scn.charge_bbl(l["key"], d) > 0]
            seq.append("+".join(sorted(on)) if on else None)
        rr = runs(seq)
        if not rr:
            continue
        lengths = sorted(r[2] for r in rr)
        switches = max(0, len(rr) - 1)
        campaign_stats[unit] = lengths
        w("| {} | {} | {} | {:.1f} | {} | {} | {:.1f} |".format(
            unit, len(rr), lengths[len(lengths) // 2],
            sum(lengths) / len(lengths), lengths[-1], switches,
            switches / (len(dates) / 30.0)))

    modes = [scn.crude_mode.get(d.isoformat()) for d in dates]
    mode_runs = runs(modes)
    if mode_runs:
        ml = sorted(r[2] for r in mode_runs)
        w("| CRUDE mode R/L | {} | {} | {:.1f} | {} | {} | {:.1f} |".format(
            len(mode_runs), ml[len(ml) // 2], sum(ml) / len(ml), ml[-1],
            len(mode_runs) - 1, (len(mode_runs) - 1) / (len(dates) / 30.0)))
    w("")
    shortest = {u: l[0] for u, l in campaign_stats.items()}
    w("Shortest campaign observed per unit: {}. A minimum-run-length constraint "
      "should start from these, confirmed with operations.\n".format(
          ", ".join("{} {}d".format(u, v) for u, v in sorted(shortest.items()))))

    # ------------------------------------------------------- 4. material chain
    w("## 4. The material chain (why units cannot be optimized separately)\n")
    produced_by = defaultdict(set)
    consumed_by = defaultdict(set)
    for rule in ref.yield_rules:
        out = rule.get("out")
        if rule["kind"] == "crude" and out:
            produced_by[out].add("CRUDE")
        elif rule["kind"] == "unit":
            if out:
                produced_by[out].add(rule["unit"])
            consumed_by[rule["charge_code"]].add(rule["unit"])
        elif rule["kind"] == "diesel_yield_back" and out:
            produced_by[out].add("HYDRO")
    for line in ref.charge_lines:
        if line["unit"] in PROCESS_UNITS:
            consumed_by[line["code"]].add(line["unit"])

    intermediates = sorted(set(produced_by) & set(consumed_by))
    w("**{} products are both produced by one unit and charged to another.** "
      "Each is a hard coupling: charging it consumes inventory that an earlier "
      "unit had to make first.\n".format(len(intermediates)))
    w("| Product | Name | Made by | Charged to |")
    w("|---|---|---|---|")
    for code in intermediates:
        name = (ref.products.get(code, {}) or {}).get("name", "")[:34]
        w("| {} | {} | {} | {} |".format(
            code, name, ", ".join(sorted(produced_by[code])),
            ", ".join(sorted(consumed_by[code]))))
    w("")

    # -------------------------------------------------------- 5. baseline KPIs
    w("## 5. Where the planner's own baseline already breaks\n")
    w("The incumbent the optimizer must beat - and the reason commercial "
      "constraints have to be elastic rather than hard.\n")
    stockout_days = overflow_days = 0
    stockout_gal = overflow_gal = 0.0
    worst = []
    for b in ref.blocks:
        bal = sim.balances.get(b["id"], {})
        end = bal.get("End Inventory", {})
        cap = bal.get("Tank Capacity", {})
        sd = [d for d in dates if end.get(d, 0.0) < 0]
        od = [d for d in dates if cap.get(d, 0.0) > 0 and end.get(d, 0.0) > cap.get(d, 0.0)]
        sg = sum(-end.get(d, 0.0) for d in sd)
        og = sum(end.get(d, 0.0) - cap.get(d, 0.0) for d in od)
        stockout_days += len(sd)
        overflow_days += len(od)
        stockout_gal += sg
        overflow_gal += og
        if sd or od:
            code = b.get("charge_code")
            worst.append((sg + og, code,
                          (ref.products.get(code, {}) or {}).get("name", "")[:30],
                          len(sd), sg, len(od), og))
    n = len(ref.blocks) * len(dates)
    w("| Measure | Value |")
    w("|---|---:|")
    w("| product-days simulated | {:,} |".format(n))
    w("| product-days below zero inventory | {:,} ({:.1f}%) |".format(
        stockout_days, 100.0 * stockout_days / n))
    w("| product-days above tank capacity | {:,} ({:.1f}%) |".format(
        overflow_days, 100.0 * overflow_days / n))
    w("| total negative inventory (gal-days) | {:,.0f} |".format(stockout_gal))
    w("| total over-capacity (gal-days) | {:,.0f} |".format(overflow_gal))
    w("")
    worst.sort(reverse=True)
    w("Worst offenders:\n")
    w("| Product | Name | Dry days | Negative gal-days | Over-cap days | "
      "Over-cap gal-days |")
    w("|---|---|---:|---:|---:|---:|")
    for _, code, name, sd, sg, od, og in worst[:12]:
        w("| {} | {} | {} | {:,.0f} | {} | {:,.0f} |".format(
            code, name, sd, sg, od, og))
    w("")

    # ------------------------------------------------------ 6. demand structure
    w("## 6. Demand structure\n")
    order_dates = sorted({d for by in scn.open_orders.values() for d in by})
    covered = [d for d in dates if d.isoformat() in set(order_dates)]
    w("- Firm open orders exist for **{} products** across **{} calendar days** "
      "({} to {}).".format(len(scn.open_orders), len(order_dates),
                           order_dates[0] if order_dates else "-",
                           order_dates[-1] if order_dates else "-"))
    w("- That covers **{} of the {} days** in the horizon ({:.0f}%); beyond that, "
      "demand is entirely forecast.".format(
          len(covered), len(dates), 100.0 * len(covered) / len(dates)))
    w("- Forecast rates are published for {} products, blend component demand for "
      "{}.".format(len(scn.sales_forecast), len(scn.blend_component_demand)))
    w("")
    w("The near term is order-driven and the far term is forecast-driven. That "
      "argues directly for a detailed binary window over roughly the order "
      "horizon, and a coarse aggregate model beyond it.\n")

    # ---------------------------------------------------------- 7. data gaps
    w("## 7. Data gaps that would break a solver\n")
    no_cap = []
    no_band = 0
    for b in ref.blocks:
        code = b.get("charge_code")
        bal = sim.balances.get(b["id"], {})
        cap = bal.get("Tank Capacity", {})
        if not any(cap.get(d, 0.0) > 0 for d in dates):
            no_cap.append((code, (ref.products.get(code, {}) or {}).get("name", "")[:34]))
        t = ref.inventory_targets.get(code) or {}
        if t.get("lcl") is None and t.get("ucl") is None:
            no_band += 1
    w("- **{} product blocks have no tank capacity at all**, so any capacity "
      "constraint on them is vacuous: {}".format(
          len(no_cap), ", ".join("{} {}".format(c, n) for c, n in no_cap) or "none"))
    w("- {} of {} blocks have neither an LCL nor a UCL, so band penalties cannot "
      "steer them.".format(no_band, len(ref.blocks)))
    lcl_count = sum(1 for t in ref.inventory_targets.values()
                    if t.get("lcl") is not None)
    w("- Only {} products have a lower control limit; {} have an upper limit. "
      "A safety-stock objective needs LCLs that mostly do not exist yet.".format(
          lcl_count,
          sum(1 for t in ref.inventory_targets.values() if t.get("ucl") is not None)))
    dupes = defaultdict(list)
    for line in ref.charge_lines:
        if line.get("in_charge_lookup_range"):
            dupes[line["code"]].append(line["unit"])
    multi = {k: v for k, v in dupes.items() if len(v) > 1}
    w("- **{} products are charged at more than one line** ({}). The workbook only "
      "subtracts the first from inventory; the optimizer must subtract all of "
      "them or it will plan on feed that does not exist.".format(
          len(multi), ", ".join(sorted(multi))))
    w("")

    # ---------------------------------------------------------- 8. problem size
    w("## 8. Problem size\n")
    n_lines = sum(len(lines_by_unit.get(u, [])) for u in PROCESS_UNITS)
    n_blocks = len(ref.blocks)
    w("| Horizon | Assignment binaries | Inventory continuous | Notes |")
    w("|---|---:|---:|---|")
    for h in (14, 42, 90, 366):
        w("| {} days | {:,} | {:,} | {} |".format(
            h, (n_lines + 1) * h, n_blocks * h,
            "detailed window" if h <= 42 else
            ("hard for a MIP" if h <= 90 else "must be aggregated")))
    w("")
    w("{} process charge lines plus the crude mode; {} inventory blocks. "
      "A 42-day detailed window is ~{:,} binaries - comfortably solvable with "
      "campaign constraints and a warm start. A year at daily resolution is "
      "~{:,} and is not worth attempting directly.\n".format(
          n_lines, n_blocks, (n_lines + 1) * 42, (n_lines + 1) * 366))

    # ------------------------------------------------- 9. which blocks are real
    w("## 9. Which blocks are real tank accounts\n")
    w("A MIP that constrains every block equally is infeasible before it starts, "
      "because many blocks are not tanks. Classified by whether they carry "
      "capacity and whether the planner's own baseline keeps them anywhere near "
      "it.\n")
    tier_a, tier_b, tier_c = [], [], []
    for b in ref.blocks:
        code = b.get("charge_code")
        name = (ref.products.get(code, {}) or {}).get("name", "")[:32]
        bal = sim.balances.get(b["id"], {})
        end = bal.get("End Inventory", {})
        cap = bal.get("Tank Capacity", {})
        capmax = max((cap.get(d, 0.0) for d in dates), default=0.0)
        vals = [end.get(d, 0.0) for d in dates]
        lo, hi = min(vals), max(vals)
        if capmax <= 0:
            tier_c.append((code, name, lo, hi, capmax))
            continue
        # "plausible" = never more than 25% over the tank, never deeply negative
        if hi <= capmax * 1.25 and lo >= -0.05 * capmax:
            tier_a.append((code, name, lo, hi, capmax))
        else:
            tier_b.append((code, name, lo, hi, capmax))

    w("| Tier | Blocks | Meaning | Treatment in the MIP |")
    w("|---|---:|---|---|")
    w("| A - real, behaving | {} | Has capacity and the baseline respects it |"
      " Hard `0 <= I <= cap`, penalised band |".format(len(tier_a)))
    w("| B - has capacity, baseline violates it | {} | Either a genuine problem "
      "the plan already has, or the block is not really a tank | Elastic capacity "
      "with a penalty; review before trusting |".format(len(tier_b)))
    w("| C - no capacity at all | {} | Pool or bookkeeping row, not a vessel |"
      " No capacity constraint; balance only |".format(len(tier_c)))
    w("")
    for title, tier in (("Tier B - review these first", tier_b),
                        ("Tier C - no capacity", tier_c)):
        if not tier:
            continue
        w("**{}**\n".format(title))
        w("| Product | Name | Baseline low | Baseline high | Capacity |")
        w("|---|---|---:|---:|---:|")
        for code, name, lo, hi, capmax in sorted(tier, key=lambda r: r[2]):
            w("| {} | {} | {:,.0f} | {:,.0f} | {:,.0f} |".format(
                code, name, lo, hi, capmax))
        w("")

    # --------------------------------------------------- 10. horizon integrity
    w("## 10. How far out the baseline is meaningful\n")
    w("If violations grow with distance, the annual projection is extrapolation "
      "rather than a plan - which decides how far the optimizer should look and "
      "how far the baseline can be trusted as a warm start.\n")
    w("| Window | Product-days dry | Product-days over capacity | Blocks dry | "
      "Blocks over |")
    w("|---|---:|---:|---:|---:|")
    for name, a, b2 in (("days 1-14", 0, 14), ("days 15-42", 14, 42),
                        ("days 43-90", 42, 90), ("days 91-180", 90, 180),
                        ("days 181-366", 180, 366)):
        win = dates[a:b2]
        dry = over = tot = 0
        bd, bo = set(), set()
        for blk in ref.blocks:
            bal = sim.balances.get(blk["id"], {})
            end = bal.get("End Inventory", {})
            cap = bal.get("Tank Capacity", {})
            for d in win:
                tot += 1
                v, c = end.get(d, 0.0), cap.get(d, 0.0)
                if v < 0:
                    dry += 1
                    bd.add(blk["id"])
                elif c > 0 and v > c:
                    over += 1
                    bo.add(blk["id"])
        w("| {} | {:.1f}% | {:.1f}% | {} | {} |".format(
            name, 100.0 * dry / tot, 100.0 * over / tot, len(bd), len(bo)))
    w("")
    w("Per-product drift, day 14 against day 366:\n")
    w("| Product | Name | Capacity | Day 14 | Day 366 | Day 366 / capacity |")
    w("|---|---|---:|---:|---:|---:|")
    drift = []
    for blk in ref.blocks:
        bal = sim.balances.get(blk["id"], {})
        end = bal.get("End Inventory", {})
        cap = bal.get("Tank Capacity", {})
        code = blk.get("charge_code")
        c = cap.get(dates[13], 0.0)
        v14, v366 = end.get(dates[13], 0.0), end.get(dates[-1], 0.0)
        drift.append((abs(v366) - abs(v14), code,
                      (ref.products.get(code, {}) or {}).get("name", "")[:30],
                      c, v14, v366))
    drift.sort(reverse=True)
    for _, code, name, c, v14, v366 in drift[:10]:
        w("| {} | {} | {:,.0f} | {:,.0f} | {:,.0f} | {} |".format(
            code, name, c, v14, v366,
            "{:.0f}x".format(v366 / c) if c else "no capacity"))
    w("")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "optimization-analysis.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\n\nwritten to", path)


if __name__ == "__main__":
    main()
