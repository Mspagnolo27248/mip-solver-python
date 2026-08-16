"""Replay an optimizer result through the simulator and check it holds up.

The optimizer reports its own numbers, computed from its own variables. That is
exactly the thing not to trust: a model with a wrong constraint will report a
wonderful answer with complete confidence. The simulator is an independent
implementation of the same physics, validated cell-by-cell against the workbook,
so replaying the proposed schedule through it is the only honest check that the
schedule does what the optimizer claims.

Any material disagreement means one of the two is wrong, and the run should not
be trusted until it is understood.

**The two sides must be compared in the same units, in the same product space,
and on the same quantity.** Getting that wrong makes the referee useless in the
worst way - it reports a disagreement where there is none, so a real one can hide
behind the noise. Three things are needed for that:

*Product space.* The simulator works in the workbook's 54 blocks; the optimizer
works in the model's 45 products, with the ten finished-diesel grades collapsed
into `DSL` and the obsolete 9202 and 4325 retired. Summing raw blocks counts
demand the optimizer correctly does not carry - 859,000 gal of it over 14 days,
which is most of what used to look like a failed verification. Every block is
therefore mapped through `spec.aliases` first, and whatever falls outside the
model is reported under `excluded` rather than silently dropped or silently
counted.

*Chosen versus residual downgrade.* The optimizer's `dg` is the downgrade it
**decided to do**; the simulator's `Downgrade Required` is what was **still
needed** after the schedule ran. They are not the same quantity and should never
be equal - a residual of zero is the success case. So the executed downgrade is
read off the transfer lines of the proposed schedule and compared with the
optimizer's claim, and the residual is checked separately against zero.

*Executability.* Feed shortfall must be zero either way.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from .. import model_config as cfg
from ..engine import GAL_PER_BBL, Reference, Scenario, simulate

#: Gallons of disagreement tolerated before a run is called unverified. Small
#: enough to catch a structural error, loose enough to ignore solver rounding.
TOLERANCE_GAL = 1000.0

#: Units whose charge lines are downgrade outlets. Volume written onto these is
#: the downgrade the schedule actually executes.
SINK_OUTLETS = frozenset(s["outlet"] for s in cfg.SINKS.values())


def verify(ref: Reference, proposed: Scenario, claimed: Dict[str, Any],
           dates: List[dt.date], spec: Optional[Any] = None) -> Dict[str, Any]:
    """Compare what the optimizer said against what the schedule actually does.

    `spec` is the `ModelSpec` the run was built from. Without it the comparison
    falls back to raw workbook blocks, which is the old behaviour and is wrong
    wherever the model aggregates or retires a product - so it is reported.
    """
    # `strict_workbook=False` on purpose. The workbook's first-match VLOOKUP
    # subtracts only one line when a product is charged at two, and the optimizer
    # subtracts all of them (MIP-FORMULATION section 8). Refereeing in strict
    # mode would score the optimizer against a known workbook defect and would
    # make the synthetic gasoline line invisible, since it is appended after the
    # line the first match resolves to.
    sim = simulate(ref, proposed, strict_workbook=False, physical=True)
    aliases = getattr(spec, "aliases", {}) or {}
    model_products = getattr(spec, "products", None)
    retired = cfg.RETIRED.get("products", {})

    actual = {"lost_sales_gal": 0.0, "downgrade_gal": 0.0,
              "downgrade_required_gal": 0.0, "feed_shortfall_gal": 0.0,
              "over_capacity_days": 0, "negative_days": 0}
    by_product: Dict[str, Dict[str, float]] = {}
    excluded: List[Dict[str, Any]] = []

    # An aggregate cannot be scored by adding up its members. Each member block
    # has already been clamped on its own, so the workbook's broken diesel split -
    # all the demand on 8175, which has no tank and no production, all the
    # production on 8105 and 8170, which have no demand - shows up as 670,000 gal
    # of lost sales next to 412,000 gal of overflow. In the pool they cancel. The
    # aggregate's balance is therefore re-derived once, from the members' summed
    # flows, following `Engine._compute_block` exactly.
    pooled = {}
    scored_pools = set()
    if model_products is not None:
        members = {}
        for block in ref.blocks:
            code = block.get("charge_code")
            target = aliases.get(code, code)
            if target in model_products:
                members.setdefault(target, []).append(block)
        for target, blocks in members.items():
            if len(blocks) > 1:
                pooled[target] = _repool(sim, blocks, dates,
                                         model_products[target])

    for block in ref.blocks:
        code = block.get("charge_code")
        target = aliases.get(code, code)
        bal = sim.balances.get(block["id"], {})
        if target in pooled:
            # counted once for the pool, on the first member we reach
            if target in scored_pools:
                continue
            scored_pools.add(target)
            lost, short, required = pooled[target]
        else:
            lost = sum(bal.get("Lost Sales", {}).get(d, 0.0) for d in dates)
            required = sum(bal.get("Downgrade Required", {}).get(d, 0.0)
                           for d in dates)
            short = sum(bal.get("Feed Shortfall", {}).get(d, 0.0) for d in dates)

        # Blocks the model does not carry are not evidence of disagreement: the
        # optimizer was never asked to plan them. They are still worth surfacing -
        # 9202 alone books 189,000 gal of demand over 14 days against a product
        # nothing can make, which is a data problem, not a scheduling one.
        if model_products is not None and target not in model_products:
            if lost or required or short:
                excluded.append({
                    "block": block["id"], "product": code,
                    "lost_sales_gal": lost, "downgrade_required_gal": required,
                    "feed_shortfall_gal": short,
                    "reason": retired.get(code, "not carried by the model"),
                })
            continue

        row = by_product.setdefault(
            target, {"lost_sales_gal": 0.0, "downgrade_required_gal": 0.0,
                     "feed_shortfall_gal": 0.0})
        row["lost_sales_gal"] += lost
        row["downgrade_required_gal"] += required
        row["feed_shortfall_gal"] += short

        actual["lost_sales_gal"] += lost
        actual["downgrade_required_gal"] += required
        actual["feed_shortfall_gal"] += short

        end = bal.get("End Inventory", {})
        cap = bal.get("Tank Capacity", {})
        actual["over_capacity_days"] += sum(
            1 for d in dates
            if cap.get(d, 0.0) > 0 and end.get(d, 0.0) > cap.get(d, 0.0))
        actual["negative_days"] += sum(1 for d in dates if end.get(d, 0.0) < 0)

    # The downgrade the schedule executes, read off the outlet lines rather than
    # inferred. This is the quantity the optimizer's `dg` claims.
    actual["downgrade_gal"] = sum(
        proposed.charge_bbl(line["key"], d) * GAL_PER_BBL
        for line in ref.charge_lines if line.get("unit") in SINK_OUTLETS
        for d in dates)

    worst = [dict(product=p, **v) for p, v in by_product.items()
             if v["lost_sales_gal"] or v["downgrade_required_gal"]
             or v["feed_shortfall_gal"]]
    worst.sort(key=lambda r: -(r["lost_sales_gal"] + r["downgrade_required_gal"]
                               + r["feed_shortfall_gal"]))

    diffs = {}
    for key in ("lost_sales_gal", "downgrade_gal"):
        said = float(claimed.get(key) or 0.0)
        did = float(actual.get(key) or 0.0)
        diffs[key] = {"optimizer": said, "simulator": did, "delta": did - said,
                      "agrees": abs(did - said) <= TOLERANCE_GAL}

    # Feed shortfall must be zero: the optimizer holds inventory at or above zero
    # by construction, so any shortfall on replay means the two models disagree
    # about what the schedule consumes.
    executable = actual["feed_shortfall_gal"] <= TOLERANCE_GAL
    # The optimizer claims it routed every overflow itself, so nothing should be
    # left for the simulator to force out.
    routed = actual["downgrade_required_gal"] <= TOLERANCE_GAL
    agrees = all(d["agrees"] for d in diffs.values())

    return {
        "verified": bool(agrees and executable and routed),
        "executable": executable,
        "fully_routed": routed,
        "scored_in_model_space": model_products is not None,
        "tolerance_gal": TOLERANCE_GAL,
        "claimed": {k: float(claimed.get(k) or 0.0)
                    for k in ("lost_sales_gal", "downgrade_gal")},
        "actual": actual,
        "comparison": diffs,
        "worst_products": worst[:12],
        "excluded": sorted(excluded,
                           key=lambda r: -r["lost_sales_gal"])[:12],
        "excluded_lost_sales_gal": sum(r["lost_sales_gal"] for r in excluded),
        "note": _explain(diffs, executable, routed, actual, excluded,
                         model_products is not None),
    }


def _repool(sim, blocks: List[Dict[str, Any]], dates: List[dt.date],
            product: Dict[str, Any]):
    """Re-derive one aggregate's clamped balance from its members' flows.

    The flow rows (production in, sales, forecast, blends, charged out) are
    sourced values and unaffected by clamping, so they can be summed; only the
    tank limits have to be re-applied, once, against the pool's own opening and
    capacity. Mirrors `Engine._compute_block`: demand is served before unit
    charge, then the tank ceiling bites.
    """
    lost = short = required = 0.0
    prev = float(product.get("opening") or 0.0)
    capacity = float(product.get("capacity") or 0.0)
    for d in dates:
        def total(label: str) -> float:
            return sum(sim.balances.get(b["id"], {}).get(label, {}).get(d, 0.0)
                       for b in blocks)
        net = (prev + total("Production In") - total("Sales")
               - total("Forecast") - total("Blends"))
        draw = (total("Production Out") + total("Out to Diesel")
                + total("Downgrade"))
        if net < 0:
            lost += -net
            net = 0.0
        end = net - draw
        if end < 0:
            short += -end
            end = 0.0
        if capacity > 0 and end > capacity:
            required += end - capacity
            end = capacity
        prev = end
    return lost, short, required


def _explain(diffs: Dict[str, Any], executable: bool, routed: bool,
             actual: Dict[str, Any], excluded: List[Dict[str, Any]],
             model_space: bool) -> str:
    if not model_space:
        return ("Scored against raw workbook blocks because no model spec was "
                "given, so any product the model aggregates or retires will "
                "read as a disagreement. Pass the spec to compare like with "
                "like.")
    if not executable:
        return ("The schedule asks units to charge feed the tanks cannot supply "
                "({:,.0f} gal). The optimizer believes it is executable and the "
                "simulator does not, so the two disagree about consumption - "
                "most likely a charge line missing from the optimizer's balance."
                .format(actual["feed_shortfall_gal"]))
    if not routed:
        return ("The schedule still needs {:,.0f} gal of downgrade the optimizer "
                "did not route. It believes every overflow has an outlet and the "
                "simulator found one that does not - most likely a product with "
                "no sink route, or a route the result cannot be written onto."
                .format(actual["downgrade_required_gal"]))
    bad = [k for k, v in diffs.items() if not v["agrees"]]
    if not bad:
        note = ("The simulator reproduces the optimizer's numbers, so the "
                "schedule does what the run claims.")
        if excluded:
            note += (" {:,.0f} gal of lost sales on {} product(s) outside the "
                     "model were excluded from the comparison - see `excluded`."
                     .format(sum(r["lost_sales_gal"] for r in excluded),
                             len(excluded)))
        return note
    parts = []
    for k in bad:
        d = diffs[k]
        parts.append("{}: optimizer {:,.0f}, simulator {:,.0f} ({:+,.0f})".format(
            k.replace("_gal", "").replace("_", " "), d["optimizer"],
            d["simulator"], d["delta"]))
    return ("The simulator does not reproduce the optimizer's numbers - "
            + "; ".join(parts)
            + ". One of the two models is wrong; do not act on this run until it "
              "is understood.")
