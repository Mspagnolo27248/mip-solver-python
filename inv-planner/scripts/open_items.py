"""Everything still blocking the MIP, generated from the model's own state.

Derived rather than written down, so it stays true as inputs get filled in: each
check reads the live configuration and reports what is still missing. When this
prints nothing under "blocks", the model can be built.

Writes data/reports/open-items.md.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner import economics as ec                   # noqa: E402
from invplanner import model_config as cfg               # noqa: E402
from invplanner.engine import Reference, Scenario, simulate  # noqa: E402
from invplanner.modelprep import build                   # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")
WINDOW = 42

BLOCKS = "blocks"          # the model cannot be written without this
VERIFY = "verify"          # a placeholder is in use; confirm before trusting it
STRUCTURE = "structure"    # a different answer changes the formulation, not a number


def main():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    spec = build(ref, scn, sim, horizon=scn.dates[:WINDOW])
    econ = ec.DEFAULT
    items = []

    def add(kind, owner, title, detail):
        items.append({"kind": kind, "owner": owner, "title": title,
                      "detail": detail})

    # ------------------------------------------------------------- the objective
    if econ.default_lost_sale_margin_per_gal is None and \
            not econ.lost_sale_margin_per_gal:
        add(BLOCKS, "Finance",
            "Lost-sale margin ($/gal)",
            "The most influential number in the objective: its ratio to the "
            "$0.50/gal downgrade cost decides whether the model downgrades or "
            "shorts a customer. A single blended average is enough to start.")

    for key, outlet in ec.OUTLETS.items():
        if outlet.netback(econ.crude_price_per_gal) is None:
            add(BLOCKS, "Finance",
                "Netback for {}".format(outlet.title),
                "One of the four overflow valves has no price, so the optimizer "
                "cannot compare it with the others and would treat it as free.")

    if not econ.product_value_per_gal:
        add(VERIFY, "Finance",
            "Per-product values ($/gal)",
            "Downgrade cost currently runs in flat mode at $0.50/gal for every "
            "product, which is a floor. Real values make the model protect a "
            "specialty solvent more than a cheap stream. Five products carry 96% "
            "of the volume.")

    # ------------------------------------------------------------ unit constraints
    # Only units that schedule a charge need a rate ceiling. Transfer routes are
    # bounded by the sink's offtake and the platformer's stages follow from the
    # feed, so neither needs one.
    grossed = []
    missing = []
    for unit, u in spec.units.items():
        if not cfg.needs_max_rate(unit):
            continue
        for p, info in u.get("max_rate", {}).items():
            if info is None:
                missing.append("{} {}".format(unit, p))
            elif info["basis"] == "grossed up":
                grossed.append("{} {}".format(unit, p))
    if missing:
        add(BLOCKS, "Operations",
            "Maximum rate missing for {}".format(", ".join(missing)),
            "`charge = max_rate x time` cannot be written without one.")
    if grossed:
        add(VERIFY, "Operations",
            "Design maximum rate for {}".format(", ".join(grossed)),
            "These never run an undisturbed day in the plan, so their maxima "
            "were grossed up from a partial day. They are floors, not "
            "specifications - and they sit on the unit where the binding "
            "constraint lives.")

    if not cfg.MIN_RATE_FRACTION:
        add(VERIFY, "Operations",
            "Minimum stable rates",
            "Deliberately starting at zero: a minimum is a restriction, so v0 "
            "always has a feasible answer and adding real numbers later can only "
            "tighten the plan. The hook is wired through.")

    # -------------------------------------------------------------- product data
    no_cap = [p for p, d in spec.products.items()
              if d["tanked"] and not d["is_sink"] and d["capacity"] <= 0]
    if no_cap:
        add(BLOCKS, "Operations",
            "Tank capacity for {} products".format(len(no_cap)),
            "Every tank is physical, so a missing capacity is missing data, not "
            "an unlimited tank: {}".format(", ".join(sorted(no_cap)[:10])))

    lcl = [c for c, t in ref.inventory_targets.items() if t.get("lcl") is not None]
    if not lcl:
        add(VERIFY, "Operations",
            "Lower control limits (safety stock)",
            "No product has one anywhere in the workbook, so 'do not run dry' "
            "can only be written as inventory >= 0 - a stockout, not a service "
            "level.")

    dupes = defaultdict(list)
    for line in spec.charge_lines:
        if line["unit"].startswith(cfg.TRANSFER_UNIT_PREFIXES):
            continue
        dupes[line["product"]].append(line["unit"])
    multi = {p: u for p, u in dupes.items() if len(u) > 1}
    if multi:
        add(STRUCTURE, "Operations",
            "{} products charged at more than one unit".format(len(multi)),
            "The workbook subtracts only the first from inventory. The optimizer "
            "must subtract all of them or it plans on feed that does not exist: "
            "{}".format(", ".join("{} ({})".format(p, "+".join(u))
                                  for p, u in sorted(multi.items())[:6])))

    # ------------------------------------------------------------- structure
    if cfg.CRUDE_MODE_IS_DECISION:
        add(STRUCTURE, "Operations",
            "Is the crude R/L mode a decision or an input?",
            "Currently a decision, one binary a day. R makes 9117 which feeds "
            "the MEK; L makes 9118 for Sonneborn. Fix it if Sonneborn volumes "
            "are contractual.")

    if not cfg.RECYCLE_ROUTES:
        add(STRUCTURE, "Operations",
            "Recycle routes are unspecified",
            "Rare cases where product is recharged. Each needs a source product, "
            "a destination unit, and whether the volume is a decision or a fixed "
            "rate. Note the changeover interface is not one of these - it is "
            "already handled as lost time.")

    asym = []
    for unit, t in cfg.TRANSITIONS.items():
        if "arcs" not in t:
            continue
        arcs = set(t["arcs"])
        for a, b in list(arcs):
            if (b, a) not in arcs:
                asym.append("{} {}->{}".format(unit, a, b))
    if asym:
        add(VERIFY, "Operations",
            "One-way changeovers: {}".format(", ".join(asym)),
            "Observed in one direction only. If that was coincidence rather than "
            "a rule, the model is more constrained than the plant.")

    detected = [(u, w) for u, ws in cfg.TURNAROUNDS.items() for w in ws
                if w.get("status") == "detected"]
    if detected:
        add(VERIFY, "Operations",
            "{} turnaround windows are inferred, not confirmed".format(len(detected)),
            "Read from blank stretches in the workbook rather than a maintenance "
            "calendar. Planners can now set the real dates in the app.")

    proposed = [r for r in spec.sink_routes if r["status"] != "observed"]
    if proposed:
        add(VERIFY, "Operations",
            "{} downgrade routes added, not observed".format(len(proposed)),
            "The gasoline outlet does not exist in the workbook: {}".format(
                ", ".join(sorted({r["product"] for r in proposed}))))

    if cfg.UNEXPLAINED_IDLE_DAYS:
        n = sum(len(v) for v in cfg.UNEXPLAINED_IDLE_DAYS.values())
        add(VERIFY, "Operations",
            "{} idle days sit outside any declared outage".format(n),
            "Under must-run the model schedules through them, making it slightly "
            "tighter than the plan. Either one-day outages worth declaring, or "
            "slack.")

    add(STRUCTURE, "Modelling",
        "Blending is demand, not a decision",
        "The model cannot decline to make a blend when a component is short - it "
        "books a lost sale instead. If blends can be moved, that is a cheaper "
        "relief valve than downgrading and the model is blind to it.")

    add(STRUCTURE, "Integration",
        "Day-zero setup state must come from the live feed",
        "The setup carries across days, so each nightly re-solve needs what the "
        "unit is actually holding - not what the schedule wanted. Otherwise "
        "every run starts with a free or phantom changeover.")

    # ------------------------------------------------------------------ report
    L = []

    def w(s=""):
        L.append(s)

    counts = defaultdict(int)
    for it in items:
        counts[it["kind"]] += 1

    w("# Open items before the MIP\n")
    w("Generated from the model's live configuration, so it stays true as inputs "
      "are filled in. When nothing remains under **blocks**, the model can be "
      "built.\n")
    w("| | Count | Meaning |")
    w("|---|---:|---|")
    w("| Blocks | {} | The model cannot be written without it |".format(
        counts[BLOCKS]))
    w("| Verify | {} | A placeholder is in use; confirm before trusting it |".format(
        counts[VERIFY]))
    w("| Structure | {} | A different answer changes the formulation |".format(
        counts[STRUCTURE]))
    w("")

    for kind, title in ((BLOCKS, "Blocks the model"),
                        (STRUCTURE, "Changes the formulation"),
                        (VERIFY, "Placeholders to confirm")):
        rows = [i for i in items if i["kind"] == kind]
        if not rows:
            continue
        w("## {}\n".format(title))
        for it in rows:
            w("**{}** — _{}_  ".format(it["title"], it["owner"]))
            w(it["detail"] + "\n")

    if cfg.DECIDED_NOT_MODELLED:
        w("## Decided against, for now\n")
        w("Not open items - these have been answered, and the answer was \"not "
          "yet\". Listed so the absence is never read as an oversight, and so "
          "the cost of each simplification sits next to it.\n")
        for it in cfg.DECIDED_NOT_MODELLED:
            w("**{}**  ".format(it["what"]))
            w("_Decision._ {}  ".format(it["decision"]))
            w("_What it costs._ {}  ".format(it["cost"]))
            w("_When to revisit._ {}\n".format(it["when"]))

    w("## Solvers are ready\n")
    w("Nothing needs installing: PuLP 2.7 with CBC, and HiGHS through "
      "`scipy.optimize.milp`. A 90-binary toy of the same shape as the unit model "
      "(setup binaries, time variables, a day budget) solved in 1.5 s on CBC and "
      "0.02 s on HiGHS. Solve time is not the risk it was assumed to be.\n")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "open-items.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nwritten to", path)


if __name__ == "__main__":
    main()
