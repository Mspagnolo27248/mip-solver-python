"""v1: the dewaxing and extraction units schedule themselves.

The second step of the build order in `MIP-FORMULATION.md`, specified in full in
`V1-MODEL.md`. v0 took the planner's choice of *what* each unit charges as given
and optimised only how much, and where the overflow went. v1 frees that choice on
the two units whose transition structure is known, and is therefore the first
model in the series with any binary variables at all.

    MEK      4 charge lines, 6 arcs of 12   strict viscosity ladder
    EXTRACT  4 charge lines, 9 arcs of 12   ladder plus a mode switch

**Why these two and not all three.** MEK's three dewaxed oils are extraction's
only feeds, so freeing MEK while extraction stays pinned bounds the answer by
what extraction was already scheduled to eat - the pair is the smallest increment
that can show a real gain. The hydrotreater waits for v2: 36 of its 42 possible
transitions already appear in the plan, so the arc formulation buys it no
tightening, and four of its seven rates are grossed up from partial days, so its
answers would be the least trustworthy part of the model.

**Why the setup state is the line, not the product.** Extraction runs 9305 two
ways - normal extraction to Argold, deep extraction to hydrotreater feed - at
different rates, and it changes between them without changing feed. At product
level that is 9305 -> 9305, a self-loop the arc formulation cannot represent.

Everything downstream of the assignment - the balance, the tanks, the downgrade
routing, the flow-through identities, the terminal condition - is shared verbatim
with v0. That is deliberate: every defect this model has had lived in the
balance, and a second copy of it would drift from the first at exactly the moment
nobody was looking.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from ..engine import Reference, Scenario
from . import v0

#: Units whose assignment v1 decides. The rest keep the planner's, as in v0.
FREE_UNITS: List[str] = ["MEK", "EXTRACT"]


def solve(ref: Reference, scn: Scenario, spec, params: Dict[str, Any],
          horizon: Optional[List[dt.date]] = None,
          downtime: Optional[Dict[str, set]] = None,
          elastic: bool = False,
          free_units: Optional[List[str]] = None,
          **kw) -> v0.OptimizeResult:
    """Solve v1. Signature matches `v0.solve` so callers can swap one for the other.

    `**kw` carries the window state a rolling horizon hands in - opening tanks,
    setup carry-in, whether this is the last window - straight through to v0.
    """
    return v0.solve(ref, scn, spec, params, horizon=horizon, downtime=downtime,
                    elastic=elastic,
                    free_units=list(FREE_UNITS if free_units is None
                                    else free_units), **kw)
