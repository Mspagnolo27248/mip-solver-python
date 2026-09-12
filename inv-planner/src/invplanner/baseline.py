"""A committed snapshot of what the model answers, diffed on every change.

Three defects shipped and ran for weeks before anyone noticed, and all three had
the same signature: a confident answer nobody could tell was wrong.

  * `Infeasible` was relabelled `feasible` and a schedule handed back. Every run
    at the shipped default charge floor was affected.
  * PuLP rewrites CBC's "stopped on time limit" into `Optimal`, so every v1 result
    ever called optimal was an unproven incumbent - one of them 20% worse than
    achievable.
  * A reactor-crossing constraint was written with no upper bound on its own
    indicator, so it cost binaries and did nothing at all.

None of those changed anything a person would look at. All three would have moved
a field in this file on the first run after they landed.

**Exactness is the point, and it is affordable.** Every comparison worth making has
to be at `mip_gap` 0, because at the 2% gap used for planning the differences a
modelling change makes are smaller than the gap itself - a safety-stock experiment
"recovered 43,705 gal" that turned out to be noise, and a reactor fix looked like a
3-crossing regression that was noise too. v0 solves 100 days exactly in about three
seconds, so the whole exact tier costs less than a slow test.

**v1 cannot join it.** At `mip_gap` 0 it hits a five-minute limit and returns
`feasible` even over 42 days, so its rows are recorded at the planning gap and
marked `exact: false`. They are still worth diffing - the solve is deterministic,
verified by three identical runs - but an objective delta on a v1 row is not
evidence that anything improved. Read those rows for *structure*: status, campaign
shape, how often a unit switches.

**A case may be one the model cannot answer.** `solves: False` says the expected
result is `infeasible`, and the row then carries which constraint families needed
relief instead of a schedule. `v0-42-blank` is the one: a limitation worth pinning
is still an answer, and the point of recording it is to see the day it changes.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from . import model_config as cfg
from .engine import Reference, Scenario, simulate
from .modelprep import build
from .optimizer import greedy, v0, v1, v2

#: Shared by every case, so a row's own entry says only what makes it different.
COMMON: Dict[str, Any] = {
    "lost_sale_margin_per_gal": 1.50,
    "downgrade_discount_per_gal": 0.50,
    "netback_diesel_cost_per_gal": 0.3667,
    "netback_gasoline_cost_per_gal": 0.22,
    "switch_cost": 2000.0,
    "charge_floor_fraction": 0.30,
    "time_limit_seconds": 300,
}

#: `exact` rows solve to a proven optimum and their objective is comparable.
#: Everything else is recorded for structure only - see the module docstring.
CASES: List[Dict[str, Any]] = [
    {"name": "v0-42-cost", "model": "v0", "days": 42, "exact": True,
     "params": {"objective": "cost"}},
    {"name": "v0-42-margin", "model": "v0", "days": 42, "exact": True,
     "params": {"objective": "margin"}},
    {"name": "v0-100-cost", "model": "v0", "days": 100, "exact": True,
     "params": {"objective": "cost"}},
    {"name": "v0-100-margin", "model": "v0", "days": 100, "exact": True,
     "params": {"objective": "margin"}},
    # v0 is an LP again with the flush off, which is the claim that keeps
    # "v0 has no binaries" checkable rather than merely asserted.
    {"name": "v0-100-cost-noflush", "model": "v0", "days": 100, "exact": True,
     "params": {"objective": "cost", "charge_reactor_flush": False}},
    # Safety stock ships off; this row is what proves turning it on still does
    # nothing, so the day it starts doing something is visible.
    {"name": "v0-100-cost-safety3", "model": "v0", "days": 100, "exact": True,
     "params": {"objective": "cost", "safety_stock_days": 3}},
    {"name": "v1-42-cost", "model": "v1", "days": 42, "exact": False,
     "params": {"objective": "cost"}},
    {"name": "v1-42-margin", "model": "v1", "days": 42, "exact": False,
     "params": {"objective": "margin"}},
    # v2 is two solves, so it is the slowest row here by far - one case only, at
    # the shorter horizon. It is worth the minute: v2 is the model with the most
    # moving parts and the weakest guarantee, so it is the one most likely to
    # drift without anyone noticing.
    {"name": "v2-42-cost", "model": "v2", "days": 42, "exact": False,
     "params": {"objective": "cost"}},
    # The blank sheet: every charge line emptied, crude left running because it
    # is an input to the model and not a decision. This is not a corner case, it
    # is what a planner opening a new window has, and the model cannot answer it
    # - a fixed-assignment line gets a charge variable only on days the schedule
    # already names one (`v0.py`, "assignment fixed: not scheduled"), so with
    # nothing typed in, the Platformer cannot run at all, crude keeps making
    # 144,072 gal/day of naphtha nobody can consume, and the tank is over
    # capacity on day 9. Freeing the Platformer instead is not available either:
    # two of its four lines carry no maximum rate.
    #
    # Recorded so the day that changes is visible. `infeasible` here is the
    # current answer, not the desired one - if this row ever solves, someone has
    # made the model able to plan from nothing, and that is worth reading the
    # diff for rather than discovering later.
    {"name": "v0-42-blank", "model": "v0", "days": 42, "exact": True,
     "blank_schedule": True, "solves": False, "params": {"objective": "cost"}},
]

#: Gallons and barrels are rounded to whole units and the objective to cents.
#: The solve is deterministic, so this is guarding against float formatting
#: rather than against drift - a real change moves these by far more.
def _g(x) -> float:
    return round(float(x or 0.0))


def _reactor_shape(spec, res, dates) -> Dict[str, int]:
    """Start-ups and both-reactor days on the hydrotreater.

    Start-ups rather than same-day overlaps: only 1 of the plan's 13 crossings
    has both reactors inside one day, so a same-day count would miss twelve.
    """
    hy = [l for l in spec.charge_lines if l["unit"] == "HYDRO"]
    rof = {l["key"]: cfg.reactor_of("HYDRO", l["workbook_product"]) for l in hy}
    was: Dict[str, bool] = {}
    ups = both = 0
    for d in dates:
        s = d.isoformat()
        on = {r: False for r in sorted({v for v in rof.values() if v})}
        for l in hy:
            if (res.charge.get(l["key"], {}).get(s, 0.0) or 0.0) > 1e-6:
                on[rof[l["key"]]] = True
        if not any(on.values()):
            continue                      # idle: the setup state persists
        if sum(1 for v in on.values() if v) > 1:
            both += 1
        for r, v in on.items():
            if v and was.get(r) is False:
                ups += 1
        was = dict(on)
    return {"startups": ups, "both_reactor_days": both}


def _schedule_gaps(scn, res, spec, dates) -> int:
    """Days the plan charges, the model owns, and the result says nothing about.

    A result is an instruction, and silence is not one. Everything that lands the
    answer somewhere - the app writing a result scenario, an export, a replay -
    starts from the planner's schedule and overwrites what the optimizer gives
    it. A missing entry is not read as "do not run", it is read as "leave the
    planner's number", so the stored schedule quietly becomes a mixture of the
    two.

    v2 did exactly this. Stage one idled MEK, the carry dropped the zero, stage
    two pinned a line with nothing scheduled and built no variable for it, and
    the result came back with a gap. The plan's 3,400 bbl went back onto 130 days
    the optimizer had chosen to idle, and the referee reported 7 M gal of feed
    the tanks could not supply - against a schedule the optimizer never produced.
    The exported workbook was right throughout, because it writes blanks.

    Both channels count: unit charges land in `charge`, downgrade decisions in
    `transfer_bbl`, and a line answered in either has been answered.

    Only lines the model owns. Retired lines - the Sonneborn lift into 9202 -
    keep the planner's numbers deliberately, because the model does not carry
    that material at all, and counting them would bury a real defect in noise.

    **This is a watch, not an assertion of zero.** Three gaps are standing - the
    Sonneborn lift on 9118 - and they were there before any of this and are the
    same under v0, v1 and v2. What matters is that the number does not move: v2's
    defect would have shown as 133 against a committed 3, on the first run after
    it landed.
    """
    gaps = 0
    for line in spec.charge_lines:
        key = line["key"]
        answered = res.charge.get(key, {})
        moved = res.transfer_bbl.get(key, {})
        for d in dates:
            if (scn.charge_bbl(key, d) or 0.0) <= 0.0:
                continue
            s = d.isoformat()
            if s not in answered and s not in moved:
                gaps += 1
    return gaps


def _campaigns(res) -> Dict[str, Any]:
    """Switch count and median campaign per freed unit. Empty for v0."""
    out = {}
    for unit, sched in (res.schedule or {}).items():
        days = sorted(sched)
        runs, start = [], 0
        for i in range(1, len(days) + 1):
            if i == len(days) or sched[days[i]] != sched[days[start]]:
                runs.append(i - start)
                start = i
        runs.sort()
        out[unit] = {"switches": sum(1 for a, b in zip(days, days[1:])
                                     if sched[a] != sched[b]),
                     "campaigns": len(runs),
                     "median_days": runs[len(runs) // 2] if runs else 0}
    return out


def _blank(scn: Scenario) -> Scenario:
    """The same scenario with nothing scheduled on any charge line.

    The crude rate is left alone deliberately. Crude is a fixed input rather
    than a decision, so zeroing it too asks a different and much emptier
    question: with no feed the plant has nothing to plan, and the run comes back
    "optimal" having idled everything except the two flow-through lines.
    """
    data = copy.deepcopy(scn.raw)
    data["charge_schedule"]["lines"] = {
        key: {} for key in data["charge_schedule"]["lines"]}
    return Scenario(data)


def _why_infeasible(ref, scn, spec, params, dates, down) -> Dict[str, Any]:
    """Which constraint families a failed run needed relief from, and how much.

    A bare `infeasible` is a status, not a reason, and this file exists to hold
    rows that explain themselves. One elastic v0 solve answers it: v1 and v2
    build on v0's constraint set, so the families are the same whichever tier
    asked, and diagnosing with v0 keeps this to a single extra solve rather than
    a second staged run.
    """
    res = v0.solve(ref, scn, spec, dict(params, mip_gap=0.0), horizon=dates,
                   downtime=down, elastic=True)
    out: Dict[str, Any] = {}
    for family, info in sorted((res.binding or {}).items()):
        worst = (info.get("worst") or [[None]])[0][0]
        out[family] = {"count": info["count"], "gal": _g(info["total"]),
                       # "9103_2026-09-02" -> the product, which is the half that
                       # names the problem; the date only says when it starts.
                       "worst": worst.rsplit("_", 1)[0] if worst else None}
    return out


def run_case(case: Dict[str, Any], ref, scn, sim, down) -> Dict[str, Any]:
    if case.get("blank_schedule"):
        scn = _blank(scn)
        sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:case["days"]]
    spec = build(ref, scn, sim, horizon=dates, downtime=down)
    params = dict(COMMON, horizon_days=case["days"],
                  mip_gap=0.0 if case["exact"] else 0.02, **case["params"])
    model = {"v0": v0, "v1": v1, "v2": v2, "greedy": greedy}[case["model"]]
    res = model.solve(ref, scn, spec, params, horizon=dates, downtime=down)

    row: Dict[str, Any] = {"status": res.status, "exact": case["exact"]}
    if res.status not in ("optimal", "feasible"):
        # Nothing below this has anything to read: an unsolved run carries no
        # charge, no KPIs and no schedule, so the row would be a page of zeros
        # with the one fact that matters buried in it.
        row["binding"] = _why_infeasible(ref, scn, spec, params, dates, down)
        return row
    if case["exact"]:
        # Only a proven optimum has an objective worth comparing. Recording one
        # for a time-limited incumbent invites exactly the mistake this file
        # exists to prevent.
        row["objective"] = round(float(res.objective), 2) if res.objective else None
    for k in ("lost_sales_gal", "downgrade_gal", "sink_offtake_gal", "charge_bbl",
              "terminal_shortfall_gal", "days", "horizon_truncated_days"):
        if k in (res.kpis or {}):
            row[k] = _g(res.kpis[k])
    row["lost_by_product"] = {p: _g(sum(v.values()))
                              for p, v in sorted(res.lost_sales.items())
                              if sum(v.values()) > 1.0}
    by_unit: Dict[str, float] = {}
    for line in spec.charge_lines:
        got = sum(res.charge.get(line["key"], {}).values())
        if got:
            by_unit[line["unit"]] = by_unit.get(line["unit"], 0.0) + got
    row["charge_by_unit"] = {u: _g(v) for u, v in sorted(by_unit.items())}
    row["hydro"] = _reactor_shape(spec, res, dates)
    #: Must stay 0. See `_schedule_gaps` - this is the check that would have
    #: caught the v2 write-back defect on the first run after it landed.
    row["schedule_gaps"] = _schedule_gaps(scn, res, spec, dates)
    shape = _campaigns(res)
    if shape:
        row["campaigns"] = shape
    return row


def snapshot(seed_dir: str) -> Dict[str, Any]:
    """Solve every case and return the whole picture, ready to be committed."""
    import os

    ref = Reference.load(os.path.join(seed_dir, "reference.json"))
    scn = Scenario.load(os.path.join(seed_dir, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    return {c["name"]: run_case(c, ref, scn, sim, down) for c in CASES}


def diff(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """Every field that moved, named so the message alone explains the change."""
    out: List[str] = []
    for name in sorted(set(old) | set(new)):
        if name not in old:
            out.append("{}: new case".format(name))
            continue
        if name not in new:
            out.append("{}: case removed".format(name))
            continue
        a, b = old[name], new[name]
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                out.append("{}.{}: {!r} -> {!r}".format(name, key, a.get(key),
                                                        b.get(key)))
    return out
