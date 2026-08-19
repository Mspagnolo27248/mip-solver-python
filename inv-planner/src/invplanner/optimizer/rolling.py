"""Solve a long horizon as a sequence of overlapping windows.

The reach problem and the freedom problem are the same problem. v1 solves 100 days
and returns nothing at 160; v2 costs eleven minutes at 100 and was never going to
reach the end of the data. Both run out at the same place, because both are paying
for binaries multiplied by days.

A window buys the multiplication back. Each solve sees `window` days, **commits**
the first `commit` of them, and hands its closing tanks and setup states to the
next. The tail of each window is lookahead - it is solved and thrown away, and it
exists so the committed days are not decided blind. Without it every window would
empty its tanks on the last committed day, because nothing downstream would be
asking for anything.

**What this gives up.** The result is optimal in each window and not over the
horizon: a decision on day 10 cannot be revised once day 30 is being solved. That is
the standard bargain, and the alternative here is not a better answer - it is no
answer, because the monolithic model does not finish.

**Two things must carry, and missing either makes the windows separate plans that
happen to be adjacent:**

  * **Tanks.** The next window opens where this one closed, not at the plan's
    opening inventory.
  * **Setup state.** Each freed unit starts the next window on the feed it ended
    this one on, so a changeover across a window boundary owes its flush like any
    other. Without it the seams are free changeovers, and the schedule quietly
    switches more the more windows you cut it into.

The terminal condition is applied only to the **last** window. "End no poorer than
you started" is a statement about the plan; applied per window it would force every
one of them to rebuild its tanks by its own final day - a constraint the plant never
faces, and one that would undo the point of carrying inventory forward.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from .. import model_config as cfg
from ..engine import Reference, Scenario
from . import v0

#: Days solved at once. Big enough to see past the commit, small enough to finish.
#:
#: 70/35 because shorter lookaheads are **infeasible**, not merely worse, and that
#: was the surprise. Over 160 days: 42/28 fails at window 2, 42/21 at window 4,
#: 56/28 at window 3, and 70/35 solves the whole horizon in four windows.
#:
#: The failure is always the same shape and it is worth knowing before tuning
#: these. A window with no reason to care about the future fills a tank because
#: storing is free to it - 4313, the ROSE c-stock, went to the brim - and the next
#: window opens over capacity and cannot drain fast enough, because ROSE is not a
#: freed unit and its charge floor keeps producing. The overflow landed one day
#: past the end of window 1: the lookahead was a single day short of seeing the
#: consequence of its own decision.
#:
#: So the lookahead has to outrun the slowest tank on the plant, not the fastest
#: unit. ROSE runs 40-day campaigns; half of 70 days is the smallest window
#: measured that survives it.
DEFAULT_WINDOW = 70

#: How far inside the tank a carried level is placed. See where `opening` is built.
CARRY_MARGIN = 1e-9

#: Days kept from each window. The rest is lookahead, solved and discarded.
DEFAULT_COMMIT = 35


def solve(ref: Reference, scn: Scenario, spec, params: Dict[str, Any],
          horizon: Optional[List[dt.date]] = None,
          downtime: Optional[Dict[str, set]] = None,
          elastic: bool = False,
          free_units: Optional[List[str]] = None,
          model=None) -> v0.OptimizeResult:
    """Roll `model` across the horizon in overlapping windows.

    `model` is any module with a `solve` matching v0's - so this composes with
    v1 and v2 rather than replacing them, and v2's own two-stage split happens
    inside each window.
    """
    model = model or v0
    dates = list(horizon or scn.dates[:params.get("horizon_days", 42)])
    dates = cfg.clamp_horizon(dates)
    window = int(params.get("window_days") or DEFAULT_WINDOW)
    commit = int(params.get("commit_days") or DEFAULT_COMMIT)
    if commit >= window:
        # A window with no lookahead decides its last committed day blind, and
        # that day is exactly where a tank gets emptied because nothing visible
        # wants the material.
        commit = max(1, window - 1)

    out = v0.OptimizeResult()
    out.solver = "CBC"
    out.status = "optimal"
    out.charge, out.inventory, out.schedule = {}, {}, {}
    out.lost_sales, out.sink_offtake, out.transfer_bbl = {}, {}, {}
    out.downgrade = {}
    opening: Dict[str, float] = {}
    setup: Dict[str, str] = {}
    windows: List[Dict[str, Any]] = []

    start = 0
    while start < len(dates):
        chunk = dates[start:start + window]
        last = start + len(chunk) >= len(dates)
        keep = len(chunk) if last else min(commit, len(chunk))
        # The boundary is the *committed* day, not the window's last - that is
        # the state actually handed on, and the only one worth constraining.
        # Committing through an outage instead was tried and backfired: it ate
        # the lookahead, so a window committed all 70 of its days, filled 9103
        # with no future to see, and handed 1.3 M gal of overflow to an 11-day
        # stump. Naming the target beats moving the seam.
        res = model.solve(
            ref, scn, spec, dict(params, horizon_days=len(chunk)),
            horizon=chunk, downtime=downtime, elastic=elastic,
            free_units=free_units, opening_override=opening or None,
            setup_override=setup or None, terminal_condition=last,
            boundary_day=None if last else chunk[keep - 1].isoformat())
        if res.status not in ("optimal", "feasible"):
            out.status = res.status
            out.message = ("window {} ({} to {}) did not solve: {}"
                           .format(len(windows) + 1, chunk[0], chunk[-1],
                                   res.message or res.status))
            return out

        kept = [d.isoformat() for d in chunk[:keep]]
        keptset = set(kept)
        for key, series in res.charge.items():
            out.charge.setdefault(key, {}).update(
                {s: v for s, v in series.items() if s in keptset})
        for key, series in res.transfer_bbl.items():
            out.transfer_bbl.setdefault(key, {}).update(
                {s: v for s, v in series.items() if s in keptset})
        for p, series in res.inventory.items():
            out.inventory.setdefault(p, {}).update(
                {s: v for s, v in series.items() if s in keptset})
        for p, series in res.lost_sales.items():
            out.lost_sales.setdefault(p, {}).update(
                {s: v for s, v in series.items() if s in keptset})
        for p, series in res.sink_offtake.items():
            out.sink_offtake.setdefault(p, {}).update(
                {s: v for s, v in series.items() if s in keptset})
        for p, sinks in res.downgrade.items():
            for sink, series in sinks.items():
                out.downgrade.setdefault(p, {}).setdefault(sink, {}).update(
                    {s: v for s, v in series.items() if s in keptset})
        for unit, sched in (res.schedule or {}).items():
            out.schedule.setdefault(unit, {}).update(
                {s: v for s, v in sched.items() if s in keptset})

        # Hand the boundary on: tanks as left, and what each unit is holding.
        #
        # Clamped a hair inside the tank, because a level carried at *exactly*
        # capacity is not reproducible. Window 2 left 4313 full to the last
        # gallon and window 3 came back infeasible with one violation of
        # magnitude **zero** - the same float knife-edge that an exact pin hits
        # in v2. A billionth of a tank is nothing physically and the difference
        # between a horizon that solves and one that stops halfway.
        opening = {}
        for prod, series in res.inventory.items():
            if kept[-1] not in series:
                continue
            level = series[kept[-1]]
            cap = (spec.products.get(prod) or {}).get("capacity")
            if cap:
                level = min(level, cap * (1.0 - CARRY_MARGIN))
            opening[prod] = max(0.0, level)
        setup = {unit: sched[kept[-1]] for unit, sched in (res.schedule or {}).items()
                 if sched.get(kept[-1])}
        out.solve_seconds += res.solve_seconds
        out.unroutable = res.unroutable
        windows.append({"from": chunk[0].isoformat(), "to": chunk[-1].isoformat(),
                        "committed": keep, "status": res.status,
                        "seconds": round(res.solve_seconds, 1)})
        if res.status == "feasible":
            out.status = "feasible"
        start += keep

    out.kpis = {
        "lost_sales_gal": sum(sum(v.values()) for v in out.lost_sales.values()),
        "downgrade_gal": sum(sum(sum(x.values()) for x in d.values())
                             for d in out.downgrade.values()),
        "sink_offtake_gal": sum(sum(v.values()) for v in out.sink_offtake.values()),
        "charge_bbl": sum(sum(v.values()) for v in out.charge.values()),
        "days": len(dates),
        "windows": windows,
        # Optimal per window, never across the horizon. Said out loud for the
        # same reason v2 says it: `optimal` on a decomposition promises less than
        # the word does on a single solve.
        "globally_optimal": False,
    }
    out.message = ("rolled {} window(s) of {} days committing {}; each solved on "
                   "its own, so the horizon is not a joint optimum"
                   .format(len(windows), window, commit))
    return out
