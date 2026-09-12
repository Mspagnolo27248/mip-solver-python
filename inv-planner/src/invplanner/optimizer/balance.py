"""Who makes each product and who eats it, as data both solvers read.

This is lifted verbatim out of `v0.solve`, which is the only place it has ever
lived, because a second scheduler now needs the same two maps and copying them
would have been the wrong kind of cheap. Every defect this model has had lived in
the balance - the yield-back rule the optimizer could not see at all, the
coefficient that overstated the return by 18%, the production booked under one
code and landing in another tank - and each was found once and fixed once. A
second copy drifts from the first at exactly the moment nobody is looking at both.

The maps are the whole material chain. There is no explicit precedence graph in
this codebase and there does not need to be one: a product is made by every entry
in `produces[p]` and drained by every line in `consumes[p]`, and joining those two
is what reconstructs crude -> ROSE -> MEK -> EXTRACT -> HYDRO without anyone
having written it down.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

from ..engine import GAL_PER_BBL

#: product -> [(line_key, gallons produced per barrel charged on that line)]
ProducesMap = Dict[str, List[Tuple[str, float]]]
#: product -> [line_key, ...] that charge it
ConsumesMap = Dict[str, List[str]]


def produces_map(ref: Any, spec: Any) -> ProducesMap:
    """Gallons of each product made per barrel charged, by charge line.

    Two kinds of yield rule contribute, and missing the second one is the defect
    this function exists to keep fixed:

    `unit` rules are the ordinary case - a line charges a feed and a fraction of
    it comes out as something else. The output is credited through
    `production_alias`, because some production is booked under one code and
    lands in another product's tank (the isomerate run into isomerate). Credit it
    where the workbook puts it or the receiving product looks unproduceable.

    `diesel_yield_back` is the hydrotreater returning what it did not convert to
    the 9713 diesel charge pool. It is not a `unit` rule, so a loop that checks
    only for those walks straight past it and the model never sees the material -
    1,857,314 gal over 160 days into a 1,200,000 gal tank. Under 42 days the tank
    absorbed it and nothing looked wrong; past that the replay overflowed by
    exactly the amount nobody had been told about.

    **The coefficient is `1 - yield`, not `1/yield - 1`.** The engine applies its
    factor to the row's *output* (charge x yield), so the two compose to
    charge x (1 - yield). Applying `1/yield - 1` to the charge itself overstates
    the return by about 18% at a 0.85 yield, which is enough to schedule diesel
    the tank cannot supply.

    `diesel_yield_back` names production-out rows rather than charge lines, so
    they are matched back through `po_row` on the *reference's* own yield rules -
    `modelprep` drops `po_row` when it merges the unit rules, which is why `ref`
    is a parameter here and not just `spec`.
    """
    produces: ProducesMap = defaultdict(list)
    alias = getattr(spec, "production_alias", {}) or {}

    for rule in spec.yield_rules:
        if rule["kind"] == "unit":
            produces[alias.get(rule["out"], rule["out"])].append(
                (rule["charge_line"], rule["yield"] * GAL_PER_BBL))

    line_of_po_row = {r["po_row"]: r["charge_line"] for r in ref.yield_rules
                      if r.get("kind") == "unit" and r.get("po_row") is not None
                      and r.get("charge_line")}
    for rule in spec.yield_rules:
        if rule["kind"] != "diesel_yield_back":
            continue
        target = alias.get(rule["out"], rule["out"])
        for term in rule["terms"]:
            y = term.get("yield") or 0.0
            key = line_of_po_row.get(term.get("source_po_row"))
            if key and y > 0:
                produces[target].append((key, (1.0 - y) * GAL_PER_BBL))
    return produces


def consumes_map(lines: List[Dict[str, Any]]) -> ConsumesMap:
    """Which charge lines draw down each product.

    Takes the line list rather than the spec because callers work with a filtered
    one: `v0` drops the transfer prefixes before building this, since a transfer
    is a downgrade decision rather than a unit charging a feed, and a heuristic
    building a tank ledger needs the same filtering for the same reason.
    """
    consumes: ConsumesMap = defaultdict(list)
    for line in lines:
        consumes[line["product"]].append(line["key"])
    return consumes


def lands_in_map(spec: Any) -> Dict[str, str]:
    """Transfer line -> the product that receives what it moves.

    Downgraded material does not disappear. It becomes diesel charge, #6 oil or
    cat feed, and the receiving pool has to carry it or the balance quietly
    creates and destroys oil.

    Not every routable pair has an entry: `TRANSFER_6OIL#157` and `#158` (4579
    and 4586) are synthesised by `modelprep` and are absent from the 8201
    transfer rule, so material sent down them leaves one tank without arriving
    anywhere. That is a known defect in the model as built, recorded here so a
    second reader finds it rather than rediscovering it.
    """
    lands: Dict[str, str] = {}
    for rule in spec.yield_rules:
        if rule["kind"] != "transfer":
            continue
        for key in rule.get("charge_lines", []):
            lands[key] = rule["out"]
    return lands
