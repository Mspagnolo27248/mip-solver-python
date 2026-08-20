"""v2: the hydrotreater schedules itself too, by solving in two stages.

**Why not in one.** Freeing all three units at once does not solve. Measured, at
`mip_gap` 0.02 with a generous limit:

    horizon   all three freed            MEK+EXTRACT      HYDRO alone
      7 d     optimal, 116 s
     10 d     time limit, no proof
     14 d     time limit, no proof
     21 d     time limit, no proof       optimal,   9 s   optimal, 159 s
     42 d     time limit, no proof       optimal,  11 s   optimal,  47 s
    100 d                                optimal, 130 s   optimal, 170 s

Seven days is the largest window the joint model can prove, which is useless: it is
shorter than a single ROSE campaign, so a rolling horizon built on it would decide
a 40-day campaign seven days at a time.

**Why the hydrotreater is the hard one.** It offers 7 charge lines against MEK's
and extraction's 4, and 42 transitions against their 6 and 9 - and unlike them it
has no minimum campaign anywhere, because every one of its charges is observed
running for a single day. There is nothing to prune the tree with. The plant really
does switch it daily.

**What does not fix it**, all tried and measured:

  * Relaxing the arc variables to continuous. Correct - the setups sum to one and
    the arcs carry flow between two unit vectors, so a vertex is already integral,
    confirmed by 2,394 arcs coming back with none fractional. It is kept because it
    is free and it is right, but it does not make v2 provable.
  * A real changeover cost on the hydrotreater. It does what it is for - switches
    fall from 9 to 6 over 14 days - but the model still cannot close the gap.
  * A shorter horizon. Only 7 days proves, which is not a horizon.

**What does.** Solve the two units that are cheap, hold their answer, then solve the
hydrotreater against it. Both stages prove optimality, and together they cost about
five minutes over 100 days against a joint model that cannot finish ten.

The honest caveat: **this is not a global optimum.** Each stage is optimal given the
other's decisions, which is a real guarantee and a weaker one. The upper bound the
joint model would give is not available at any horizon worth planning, so the choice
is not between this and the true optimum - it is between this and no answer.
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Any, Dict, List, Optional

from ..engine import Reference, Scenario
from . import v0

#: Stage one: the units whose transition structure prunes their own search.
STAGE_ONE: List[str] = ["MEK", "EXTRACT"]

#: Stage two: the unit that has to be solved alone to be solved at all.
STAGE_TWO: List[str] = ["HYDRO"]

#: Share of the run's time budget stage one may take. Stage two is the harder
#: solve - it is the one that hits its limit - so the split favours it.
STAGE_ONE_SHARE = 0.4


def _carry(scn: Scenario, res: v0.OptimizeResult) -> Scenario:
    """The scenario with a stage's decisions written onto the charge lines.

    The same shape the referee uses to replay a schedule, and for the same
    reason: the next stage has to read the previous one's answer as though a
    planner had typed it in, or the two stages are solving different problems.
    """
    data = copy.deepcopy(scn.raw)
    lines = data["charge_schedule"]["lines"]
    for source in (res.charge, res.transfer_bbl):
        for key, series in source.items():
            lines.setdefault(key, {})
            for s, bbl in series.items():
                if bbl:
                    lines[key][s] = bbl
                else:
                    lines[key].pop(s, None)
    return Scenario(data)


def solve(ref: Reference, scn: Scenario, spec, params: Dict[str, Any],
          horizon: Optional[List[dt.date]] = None,
          downtime: Optional[Dict[str, set]] = None,
          elastic: bool = False,
          free_units: Optional[List[str]] = None,
          **kw) -> v0.OptimizeResult:
    """Solve v2. Signature matches `v0.solve` so callers can swap one for the other.

    `free_units` is accepted and ignored - which units go in which stage is the
    whole content of this model, not a caller's choice.
    """
    # `time_limit_seconds` is the budget for the *run*, not for each stage.
    # Handing the whole figure to both meant a 900 s setting produced an 1,813 s
    # solve - the caller asked for one budget and got two. Stage one is the
    # cheaper solve, so it takes the smaller share and hands on whatever it did
    # not spend; a stage one that finishes early buys stage two more time rather
    # than throwing it away.
    budget = float(params.get("time_limit_seconds") or 300.0)
    first_params = dict(params, time_limit_seconds=max(1.0, budget * STAGE_ONE_SHARE))

    first = v0.solve(ref, scn, spec, first_params, horizon=horizon,
                     downtime=downtime, elastic=elastic,
                     free_units=list(STAGE_ONE), **kw)
    if first.status not in ("optimal", "feasible"):
        first.message = ("stage 1 ({}) did not solve: {}"
                         .format("+".join(STAGE_ONE), first.message or first.status))
        return first

    # Stage two reads stage one's schedule as given, and is *pinned* to it rather
    # than floored: the charge floor would let this stage take back 70% of a
    # decision already made, and the two answers would not compose.
    staged = _carry(scn, first)
    second_params = dict(params, time_limit_seconds=max(
        1.0, budget - first.solve_seconds))
    second = v0.solve(ref, staged, spec, second_params, horizon=horizon,
                      downtime=downtime, elastic=elastic,
                      free_units=list(STAGE_TWO), pin_units=list(STAGE_ONE), **kw)
    if second.status not in ("optimal", "feasible"):
        second.message = ("stage 2 ({}) did not solve against the stage 1 "
                          "schedule: {}".format("+".join(STAGE_TWO),
                                                second.message or second.status))
        return second

    # Stage one's charges have to be carried onto the result explicitly, zeros
    # and all. Stage two pins the stage-one units, and a pinned line with no
    # charge that day gets *no variable* - so its answer comes back as a gap
    # rather than a zero, and anything that starts from the planner's schedule
    # and overwrites what it finds leaves the planner's number showing through.
    #
    # That is not cosmetic. It put the plan's 3,400 bbl of MEK back on 130 days
    # the optimizer had chosen to idle, and the referee - correctly - reported
    # 7 M gal of feed the tanks could not supply. The exported workbook was
    # right and the result scenario was wrong, which is the worst way round.
    for key, series in first.charge.items():
        merged = dict(series)
        merged.update(second.charge.get(key, {}))
        second.charge[key] = merged

    # The freed units are reported from the stage that actually decided them, so
    # a reader sees one schedule rather than two halves.
    second.schedule = dict(first.schedule, **second.schedule)
    second.solve_seconds = first.solve_seconds + second.solve_seconds
    for unit, shape in (first.kpis.get("campaign_shape") or {}).items():
        second.kpis.setdefault("campaign_shape", {})[unit] = shape
    # `switches` is computed per solve, so stage two's figure counted the
    # hydrotreater alone and reported roughly a third of the run's changeovers.
    # Recomputed from the merged shape rather than added up, so it cannot drift
    # from the table beside it.
    shape_all = second.kpis.get("campaign_shape") or {}
    if shape_all:
        second.kpis["switches"] = sum(c["switches"] for c in shape_all.values())
    # Inherited changeovers are counted per solve, so stage two called stage
    # one's units inherited - they are pinned there, not freed. They were still
    # *chosen*, by stage one, and they are already in `switches`. Listing them in
    # both places reports the same changeover twice to anyone adding up the two
    # numbers. Only a unit no stage ever freed is genuinely inherited.
    #
    # This is presentation only: stage two's objective is right either way,
    # because the pinned schedule really does cost those changeovers.
    freed_somewhere = set(STAGE_ONE) | set(STAGE_TWO)
    second.kpis["inherited_switches"] = {
        u: n for u, n in (second.kpis.get("inherited_switches") or {}).items()
        if u not in freed_somewhere}
    second.kpis["stages"] = [
        {"units": list(STAGE_ONE), "status": first.status,
         "seconds": round(first.solve_seconds, 1),
         "objective": first.objective},
        {"units": list(STAGE_TWO), "status": second.status,
         "seconds": round(second.solve_seconds - first.solve_seconds, 1),
         "objective": second.objective},
    ]
    # Neither stage saw the other's freedom, so the pair is optimal in each stage
    # and not jointly. Saying so on the result is cheaper than someone inferring
    # a guarantee from the word `optimal` that was never on offer.
    second.kpis["globally_optimal"] = False
    # Appended, never conditional. `if not second.message` dropped this caveat
    # whenever a stage stopped on its time limit and v0 had already written the
    # "not proved optimal" warning - which is the one run where a reader most
    # needs to be told the two stages never saw each other. The two warnings are
    # about different things and both have to survive.
    proved = all(st["status"] == "optimal" for st in second.kpis["stages"])
    note = ("two-stage: {} then {}, each {} in its own stage; the pair is not a "
            "joint optimum".format(
                "+".join(STAGE_ONE), "+".join(STAGE_TWO),
                "proved optimal" if proved else "solved"))
    second.message = "{}. {}".format(second.message, note) if second.message else note
    return second
