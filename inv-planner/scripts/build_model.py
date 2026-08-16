"""Build the optimization model spec and report what changed from the workbook.

Writes data/reports/model-spec.md.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner import model_config as cfg          # noqa: E402
from invplanner.economics import DEFAULT as ECON    # noqa: E402
from invplanner.engine import Reference, Scenario, simulate  # noqa: E402
from invplanner.modelprep import build              # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")
WINDOW = 42


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:WINDOW]
    spec = build(ref, scn, sim, horizon=dates)
    L = []

    def w(s=""):
        L.append(s)

    w("# Optimization model specification\n")
    w("Built from the workbook reference over the first {} days. The engine still "
      "reproduces the workbook exactly; these simplifications apply only to what "
      "the optimizer sees.\n".format(WINDOW))

    s = spec.summary()
    w("| | Workbook | Model |")
    w("|---|---:|---:|")
    w("| Products | {} | {} |".format(len(ref.blocks), s["products"]))
    w("| Charge lines | {} | {} |".format(len(ref.charge_lines), s["charge_lines"]))
    w("| Units with a charge choice | {} | {} |".format(
        len({l['unit'] for l in ref.charge_lines}), s["units"]))
    w("| Yield rules | {} | {} |".format(len(ref.yield_rules), s["yield_rules"]))
    w("")

    w("## What was dropped, and why\n")
    w("| Kind | Item | Reason |")
    w("|---|---|---|")
    for d in spec.dropped:
        w("| {} | {} | {} |".format(d["kind"], d["code"], d["why"][:140]))
    w("")

    w("## Finished diesel, collapsed\n")
    agg = cfg.AGGREGATIONS[0]
    p = spec.products.get(agg["id"])
    w(agg["why"] + "\n")
    if p:
        w("| | Value |")
        w("|---|---:|")
        w("| Members merged | {} |".format(", ".join(p["members"])))
        w("| Opening inventory | {:,.0f} gal |".format(p["opening"]))
        w("| Capacity used | {:,.0f} gal |".format(p["capacity"]))
        w("| Capacity summed from members | {:,.0f} gal |".format(
            p.get("capacity_from_members", 0.0)))
        w("| Production over {} days | {:,.0f} gal |".format(WINDOW, p["production"]))
        w("| Demand over {} days | {:,.0f} gal |".format(WINDOW, p["demand"]))
        w("")
        w("_{}_\n".format(agg["capacity_note"]))

    w("## Reconciliation\n")
    w("Aggregation must not create or destroy material. Each aggregate is checked "
      "against the sum of the workbook blocks it replaces.\n")
    w("| Product | Members | Opening | Demand | Production | OK |")
    w("|---|---|---:|---:|---:|---|")
    for r in spec.reconciliation:
        w("| {} | {} | {:,.0f} | {:,.0f} | {:,.0f} | {} |".format(
            r["product"], len(r["members"]), r["opening"][0], r["demand"][0],
            r["production"][0], "yes" if r["ok"] else "**NO**"))
    w("")

    w("## Crude is an input, not a decision\n")
    w("Crude rate is set by refinery economics, not by downstream tank logistics — "
      "the plant runs it flat out and only turns it down in a poor-margin "
      "scenario. Leaving it as a variable would let the optimizer 'solve' every "
      "inventory problem by making less oil, the cheapest answer in the model and "
      "the most expensive one in reality.\n")
    supplied = sorted(spec.fixed_supply)
    w("| | Value |")
    w("|---|---|")
    w("| Crude rate | **fixed user input** |")
    w("| Crude R/L mode | {} |".format(
        "**still a decision**" if cfg.CRUDE_MODE_IS_DECISION else "fixed input"))
    w("| Products arriving as fixed supply | {} |".format(len(supplied)))
    w("")
    if cfg.CRUDE_MODE_IS_DECISION:
        w("Mode is kept as a decision because it is a product-mix choice with a "
          "direct downstream effect: **R makes 9117, which feeds the MEK "
          "dewaxer; L makes 9118, which goes to Sonneborn.** The plan runs R on "
          "313 days and L on 47, switching 38 times a year, so it is campaigned "
          "deliberately. It costs one binary a day and lets the model say 'run L "
          "a day earlier so the MEK does not run dry'. Set "
          "`CRUDE_MODE_IS_DECISION = False` if Sonneborn volumes are contractual "
          "and the mode calendar is really fixed.\n")

    w("## Turnarounds and the must-run rule\n")
    w("Units run every day unless a turnaround stops them. Inside the region the "
      "plan actually schedules, MEK runs 160 of 162 days, ROSE 153 of 162, "
      "extraction 151 of 162, the hydrotreater 151 of 160 — and the missing days "
      "cluster into outages rather than scattering. Without a must-run rule the "
      "optimizer could relieve any inventory problem by simply not running, which "
      "is never what the plant does.\n")
    w("Downtime source for this build: **{}**. Planners set it in the app "
      "(Charge schedule → Planned downtime) before the optimizer runs; where "
      "nothing has been set the build falls back to the outages inferred from "
      "the workbook.\n".format(
          "supplied by the app" if spec.downtime_source == "supplied"
          else "inferred from the workbook"))
    w("| Unit | Must run | Days down in window | Days it must run |")
    w("|---|---|---:|---:|")
    for unit in ("MEK", "HYDRO", "EXTRACT", "ROSE", "PLATFORMER"):
        u = spec.units.get(unit)
        if not u:
            continue
        w("| {} | {} | {} | {} |".format(
            unit, "yes" if u["must_run"] else "no",
            len(u["downtime_days"]), len(u["must_run_days"])))
    w("")
    w("Windows currently configured:\n")
    w("| Unit | From | To | Days |")
    w("|---|---|---|---:|")
    for unit, windows in cfg.TURNAROUNDS.items():
        for t in windows:
            w("| {} | {} | {} | {} |".format(unit, t["start"], t["end"],
                                             t.get("status", "")))
    w("")
    w("These were **detected from blank stretches in the workbook, not read from "
      "a maintenance calendar** — placeholders to check against the real dates. "
      "The October cluster (extraction 2–9, hydrotreater 11–15, ROSE 15–23) looks "
      "like one coordinated autumn turnaround.\n")
    if cfg.UNEXPLAINED_IDLE_DAYS:
        n = sum(len(v) for v in cfg.UNEXPLAINED_IDLE_DAYS.values())
        w("{} single idle days sit outside any outage ({}). Under a must-run rule "
          "the model schedules through them, making it slightly tighter than the "
          "plan — either they are one-day outages worth declaring, or they were "
          "slack.\n".format(n, ", ".join(
              "{} {}".format(k, len(v))
              for k, v in cfg.UNEXPLAINED_IDLE_DAYS.items())))

    w("## The hydrotreater's two reactors\n")
    hyd = spec.units.get("HYDRO")
    if hyd and hyd["reactors"]:
        r = hyd["reactors"]
        w(cfg.UNIT_REACTORS["HYDRO"]["why"] + "\n")
        groups = defaultdict(list)
        for p, reactor in r["assignment"].items():
            groups[reactor].append(p)
        w("| Reactor | Charges | Makes |")
        w("|---|---|---|")
        for reactor, ps in sorted(groups.items()):
            makes = cfg.UNIT_REACTORS["HYDRO"]["reactors"].get(
                reactor, {}).get("makes", [])
            w("| {} | {} | {} |".format(reactor, ", ".join(sorted(ps)),
                                        ", ".join(makes) or "everything else"))
        w("")
        w("Flush on crossing: **{:.2f} of a day**. Ordinary switch loss: "
          "**{:.3f} of a day**.\n".format(r["flush_days"],
                                          hyd["switch_loss_days"]))
        w("_{}_\n".format(cfg.UNIT_REACTORS["HYDRO"]["evidence"]))

    w("## Time lost to changeovers\n")
    w("A changeover returns the interface to charge, so no material is lost — "
      "but unit *time* is, which is why this belongs in the day's time budget "
      "rather than in the objective as cash.\n")
    w("| Unit | Switch loss | Reactor flush | Worst changeover | Days lost/yr at "
      "observed switch rate |")
    w("|---|---:|---:|---:|---:|")
    observed_switches = {"MEK": 56, "HYDRO": 98, "EXTRACT": 38, "ROSE": 5}
    for unit, u in sorted(spec.units.items()):
        if unit.startswith(("TRANSFER", "SONNEBORN", "RAILCAR")):
            continue
        flush = u["reactors"]["flush_days"] if u["reactors"] else 0.0
        worst = (max(u["changeover_loss_days"].values())
                 if u.get("changeover_loss_days")
                 else u["switch_loss_days"] + flush)
        n = observed_switches.get(unit)
        w("| {} | {:.3f} d | {} | {:.3f} d | {} |".format(
            unit, u["switch_loss_days"],
            "{:.2f} d".format(flush) if flush else "—", worst,
            "{:.1f}".format(n * u["switch_loss_days"]) if n else "—"))
    w("")
    w("The hydrotreater is the expensive one: 98 changeovers a year at 0.125 d is "
      "12.3 days of unit time, plus 20 reactor crossings at 0.25 d for another "
      "5.0 days. **Roughly 17 days a year of hydrotreater capacity goes to "
      "changeovers** — which is exactly the quantity the optimizer is trading "
      "when it decides whether to group 4315 and 4319.\n")

    w("## Maximum charge rates\n")
    w("`charge = max_rate x time_fraction`, so the parameter is an absolute "
      "barrels-per-day maximum. The workbook's monthly run rate is a planning "
      "figure rather than a ceiling — the plant runs above it routinely — so it "
      "cannot be used as the maximum.\n")
    w("| Unit | Product | Max bbl/day | Basis | Clean days behind it |")
    w("|---|---|---:|---|---:|")
    for unit in ("MEK", "HYDRO", "EXTRACT", "ROSE", "PLATFORMER"):
        u = spec.units.get(unit)
        if not u:
            continue
        for p in u["products"]:
            info = u["max_rate"][p]
            if info is None:
                w("| {} | {} | **missing** | — | — |".format(unit, p))
                continue
            basis = ("clean day" if info["basis"] == "clean day"
                     else "**grossed up — confirm**")
            w("| {} | {} | {:,.0f} | {} | {} |".format(
                unit, p, info["bbl"], basis, info["clean_days"]))
    w("")
    w("**Minimum rates start at zero.** Products do have real minimums, but a "
      "minimum is a restriction — starting without them means v0 always has a "
      "feasible answer, and adding the real numbers later can only tighten the "
      "plan rather than break it.\n")
    w("**At most {} products a day** on any unit, and at most {} changeovers. "
      "Confirmed across all 366 days: MEK and ROSE never exceed one product, the "
      "hydrotreater reaches two on 9 days and extraction on 1, and the platformer "
      "runs two every day — though there the pair is a parallel cascade rather "
      "than a changeover. Nothing anywhere runs three.\n".format(
          cfg.MAX_PRODUCTS_PER_DAY, cfg.MAX_CHANGEOVERS_PER_DAY))
    w("Every figure is a **floor on the true maximum**, never a specification. "
      "The hydrotreater is the problem case: its campaigns are median 1 day, so "
      "four of its charges never run a clean day in the whole year and their "
      "rates had to be grossed up from a partial day. Those are the numbers to "
      "confirm first, because that unit is where the binding constraint lives.\n")

    w("## Units and allowed changeovers\n")
    w("| Unit | Charge options | Transitions | Min run (days) |")
    w("|---|---|---|---|")
    for unit, u in sorted(spec.units.items()):
        if u["unrestricted"]:
            trans = "unrestricted"
        else:
            trans = ", ".join("{}->{}".format(a, b) for a, b in u["allowed_arcs"])
        w("| {} | {} | {} | {} |".format(
            unit, ", ".join(u["products"]), trans[:110],
            ", ".join("{}:{}".format(k, v) for k, v in u["min_run_days"].items())))
    w("")

    mek = spec.units.get("MEK")
    if mek and mek["allowed_arcs"] is not None:
        n = len(mek["products"])
        w("MEK has {} charge options, so {} transitions are conceivable; the "
          "ladder allows **{}**. The optimizer only ever gets variables for "
          "those.\n".format(n, n * (n - 1), len(mek["allowed_arcs"])))

    w("## Downgrade sinks\n")
    w("The overflow valves. What makes them sinks is **unlimited offtake, not "
      "unlimited tankage**: the market for them is effectively bottomless, so the "
      "plant can always sell its way out at the netback. Their tanks and "
      "capacities stay real — diesel can still overfill, and modelling it as an "
      "unbounded tank would hide that. Demand ceilings are relaxed; the only "
      "thing restraining their use is price.\n")
    w("| Sink | Model product | Tank | Capacity | In the workbook? | Netback |")
    w("|---|---|---|---:|---|---|")
    for sid, sink in cfg.SINKS.items():
        code = sink.get("product") or "SINK_" + sid
        p = spec.products.get(code, {})
        nb = ECON.outlet_netback_per_gal(sink["outlet"])
        w("| {} | {} | {} | {} | {} | {} |".format(
            sink["title"], code,
            "real" if p.get("tanked") else "outlet only",
            "{:,.0f}".format(p.get("capacity", 0.0)) if p.get("tanked") else "—",
            "yes" if sink.get("product") else "**no — added**",
            "${:.4f}/gal".format(nb) if nb is not None else "**needs a price**"))
    w("")

    observed = [r for r in spec.sink_routes if r["status"] == "observed"]
    added = [r for r in spec.sink_routes if r["status"] != "observed"]
    w("| Routes | Count |")
    w("|---|---:|")
    w("| Observed in the workbook's own transfer lines | {} |".format(len(observed)))
    w("| Added from operations | {} |".format(len(added)))
    w("")
    if added:
        w("**Routes added** — these do not exist in the spreadsheet:\n")
        w("| Product | Name | Sink | Status |")
        w("|---|---|---|---|")
        for r in added:
            nm = spec.products.get(r["product"], {}).get("name", "")
            w("| {} | {} | {} | {} |".format(
                r["product"], nm[:32], cfg.SINKS[r["sink"]]["title"],
                r["status"]))
        w("")
        for sink_id, route in cfg.SINK_ROUTES.items():
            if route.get("caution"):
                w("> **Caution ({}):** {}\n".format(
                    cfg.SINKS[sink_id]["title"], route["caution"]))

    if spec.warnings:
        w("## Warnings\n")
        for warn in spec.warnings:
            w("- **{}**".format(warn))
        w("")

    process = [n for n in spec.never_charged
               if not n["unit"].startswith(("TRANSFER", "SONNEBORN", "RAILCAR"))]
    routes = [n for n in spec.never_charged
              if n["unit"].startswith(("TRANSFER", "SONNEBORN", "RAILCAR"))]

    if process:
        w("## Process charge lines the planner never uses\n")
        w("Feeds the model would offer that the plant does not run — the same "
          "shape as the tolling route. Candidates for retirement, or for a "
          "missing entry in the transition config.\n")
        w("| Line | Unit | Product | Name |")
        w("|---|---|---|---|")
        for n in process:
            w("| {} | {} | {} | {} |".format(
                n["key"], n["unit"], n["product"], n["name"][:34]))
        w("")

    if routes:
        w("## Downgrade routes with no volume in the current plan\n")
        w("The opposite case: these are relief valves the plant has but the "
          "planner is not using. The optimizer will want them, so each needs a "
          "daily limit and a netback before it can be trusted to.\n")
        w("| Route | Unit | Product | Name |")
        w("|---|---|---|---|")
        for n in routes:
            w("| {} | {} | {} | {} |".format(
                n["key"], n["unit"], n["product"], n["name"][:34]))
        w("")

    w("## Notes carried from configuration\n")
    for note in spec.notes:
        w("- {}".format(note))
    w("")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "model-spec.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
