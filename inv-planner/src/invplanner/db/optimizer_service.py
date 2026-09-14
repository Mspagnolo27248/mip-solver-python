"""Running the optimizer and handing its answer back to the manual side."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..engine import simulate
from ..modelprep import build
from ..optimizer import greedy, v0, v1, v2, verify
from . import service as svc
from .models import (AuditEvent, OptimizerParams, OptimizerRun, Scenario,
                     ScheduleEntry, as_utc)


# ------------------------------------------------------------------ parameters
def active_params(db: Session) -> OptimizerParams:
    row = (db.query(OptimizerParams)
           .filter(OptimizerParams.is_active.is_(True))
           .order_by(OptimizerParams.id.desc()).first())
    if row is None:
        row = OptimizerParams(name="Default")
        db.add(row)
        db.commit()
    return row


FIELDS = ["crude_price_per_bbl", "downgrade_discount_per_gal",
          "lost_sale_margin_per_gal", "netback_diesel_per_gal",
          "netback_gasoline_per_gal", "switch_cost", "switch_cost_by_unit",
          "objective", "terminal_value_fraction", "safety_stock_days",
          "charge_floor_fraction", "must_run", "min_rate_fraction",
          "terminal_shortfall_per_gal", "model_version", "horizon_days",
          "time_limit_seconds", "mip_gap", "mip_gap_abs"]

LABELS = {
    "crude_price_per_bbl": ("Crude price", "$/bbl",
                            "Sets the netback on #6 oil and the cat cracker, "
                            "which are priced at crude less the discount."),
    "downgrade_discount_per_gal": ("Downgrade discount", "$/gal",
                                   "What material worth crude loses going to "
                                   "#6 oil or the cat cracker."),
    "lost_sale_margin_per_gal": ("Lost-sale margin", "$/gal",
                                 "Margin forgone when demand goes unserved. Its "
                                 "ratio to the downgrade discount decides "
                                 "whether the model downgrades or shorts."),
    "netback_diesel_per_gal": ("Diesel netback", "$/gal",
                               "What diesel fetches when it absorbs a "
                               "downgrade."),
    "netback_gasoline_per_gal": ("Gasoline netback", "$/gal",
                                 "What gasoline fetches when it absorbs a "
                                 "downgrade."),
    "switch_cost": ("Changeover cost", "$ each",
                    "Only for costs that lost time does not already capture - "
                    "labour, quality giveaway. Leave at zero to let the time "
                    "budget carry it alone. Applies to any unit without a "
                    "figure of its own."),
    "switch_cost_by_unit": (
        "Changeover cost by unit", "$ each, per unit",
        "Overrides the changeover cost for the units named, as "
        "{\"MEK\": 2000}. One figure cannot fit every unit - the campaigns it "
        "has to reproduce run from the hydrotreater's 1 day to ROSE's 40 - so "
        "raise it one unit at a time until campaign lengths match what the "
        "plant runs. Empty means every unit uses the single cost above."),
    "objective": (
        "Objective", "cost or margin",
        "\"cost\" minimises what the plan gives up - lost sales, downgrades, "
        "ending short. \"margin\" maximises what it earns, so producing is "
        "worth something and the model will want to run. Margin is what the "
        "plant is actually run for, but it depends on the end-of-window stock "
        "value below, which nobody has measured yet."),
    "terminal_value_fraction": (
        "End-of-window stock value", "fraction of netback",
        "Margin mode only. What a gallon still in the tank on the last day is "
        "worth, against what it fetches sold. It stands in for everything after "
        "the horizon: set it high and the plant hoards rather than ships, set "
        "it to zero and every tank has to be empty on the final day. It moves "
        "lost sales by a factor of five, so treat any single run as one point "
        "on a curve."),
    "safety_stock_days": (
        "Safety stock", "days of cover",
        "Days of demand each product keeps in tank. The only floor under "
        "inventory the model has - the workbook's lower control limits are not "
        "read by the optimizer - so at zero a tank may sit empty for weeks and "
        "break no rule. Leave it at zero unless you want it: measured at 0 "
        "through 7 days the schedule does not change, and it roughly doubles "
        "solve time for each 3 days of cover. It is derived from demand, not a "
        "figure set per product."),
    "charge_floor_fraction": ("Charge floor", "fraction of plan",
                              "How much of your own charge each unit must still "
                              "run. The cost objective never rewards production, "
                              "so with no floor the cheapest schedule is to stop "
                              "making oil. The feasible ceiling depends on the "
                              "horizon - 0.75 to 60 days, 0.50 to 160, and the "
                              "full year is infeasible at any floor because the "
                              "charge schedule ends 2026-12-31. 0.30 holds "
                              "everywhere, and raising it changes almost "
                              "nothing: at 100 days 0.30 and 0.50 lose the same "
                              "gallons."),
    "must_run": (
        "Units must run daily", "on or off",
        "On, a unit the model decides for has to be running every day it is not "
        "in a turnaround - which is what the plant does, and what stops the "
        "optimizer fixing an inventory problem by switching things off. Turn it "
        "off for a **cold start**: handed an empty MEK and extraction schedule "
        "the model has to let extraction stand while MEK builds its feeds, and "
        "with this on that solve is infeasible. Turn it back on before trusting "
        "the campaign shape."),
    "min_rate_fraction": (
        "Minimum rate", "fraction of the line's rate",
        "The least a unit the model decides for may charge on a day it runs. "
        "**Not the charge floor** - that one is a fraction of your own "
        "schedule and applies to the units you fixed; this is a fraction of the "
        "line's maximum rate and applies only to the freed ones. 0.60 sits just "
        "under the lowest rate the plant has held mid-campaign (0.64, median "
        "0.77), and it is what stops the model allocating a day and charging "
        "nothing through it. It is also what makes a cold start infeasible: "
        "extraction's stocked feeds last about three days at 60%, and 9305 "
        "cannot be built ahead in time. 0.30 solves it, at the cost of a "
        "schedule that runs softer than the plant ever has."),
    "terminal_shortfall_per_gal": (
        "End-of-window shortfall", "$/gal",
        "What ending below opening inventory costs. Material to replace, not "
        "margin lost, so it shares the downgrade's economics and defaults to "
        "the downgrade discount. Keep it well under the lost-sale margin: set "
        "level, the model cannot tell shorting a customer from drawing a tank "
        "down; set close, it shorts customers to hold stock."),
    "model_version": (
        "Model", "v0, v1, v2 or greedy",
        "v0 keeps your charge assignments and optimises only how much and where "
        "the overflow goes. v1 also decides what MEK and extraction charge each "
        "day. v2 adds the hydrotreater, solved in two stages because freeing all "
        "three at once does not finish - each stage is proved optimal, the pair "
        "is not a joint optimum. Over 100 days: v0 seconds, v1 a couple of "
        "minutes, v2 about five. greedy is not a solver at all - it builds a "
        "schedule by rule in under a second and cannot tell you how far from "
        "best it is, so read its verification and campaign shape rather than "
        "an objective."),
    # Named for what it does, not for how it is implemented. "Detailed
    # horizon" collided with the word four planning filters used for "how
    # many days am I looking at", which is a different question; the
    # scenario's own 366 days is a third. Decided by the user 2026-09-13.
    "horizon_days": ("Solve window", "days",
                     "How many days each run plans, counted from the plan's "
                     "first day. Firm orders cover about 57 days; beyond that "
                     "demand is pure forecast. A run stops earlier if the "
                     "charge schedule runs out before the window does."),
    "time_limit_seconds": ("Solver time limit", "seconds", ""),
    "mip_gap": ("Accepted gap, as a fraction", "fraction",
                "Stop when within this fraction of proven optimal. Leave at "
                "zero and use the gap in dollars instead: this objective measures "
                "value given up, so it shrinks as the plan improves and a "
                "percentage of it tightens on its own every time anything is "
                "fixed."),
    "mip_gap_abs": (
        "Accepted gap, in dollars", "$",
        "Stop when within this many dollars of proven optimal - the figure "
        "that means something, because it does not move when the objective "
        "does. A changeover costs about $2,000, so $10,000 is \"do not spend "
        "an hour proving something worth five changeovers\". Raising it does "
        "not buy a better schedule: at $40,000 the 42-day model proves in 43 s "
        "on a schedule worth 385,124, while $10,000 finds 379,196 and cannot "
        "prove it."),
}


#: Fields stored as booleans. They cannot go through the numeric coercion in
#: `update_params`: `type(False or 0.0)` is float, and a select posts the string
#: "false", which is truthy.
BOOL_FIELDS = {"must_run"}


#: Inclusive bounds for fields where a value outside them is not a preference but
#: a mistake. A fraction of -36.7 reached the solver as `chg >= negative`, which
#: is never binding - so the minimum-rate constraint was silently removed rather
#: than loosened, and the run came back with units nominally running and empty.
#: Nothing downstream could tell that from a deliberate setting.
RANGES = {
    "min_rate_fraction": (0.0, 1.0),
    "charge_floor_fraction": (0.0, 1.0),
    "terminal_value_fraction": (0.0, 1.0),
    "mip_gap": (0.0, 1.0),
    "safety_stock_days": (0.0, 60.0),
    "horizon_days": (1, 366),
    "time_limit_seconds": (1, 86400),
}


def params_dict(row: OptimizerParams) -> Dict[str, Any]:
    out = {f: getattr(row, f) for f in FIELDS}
    out["id"] = row.id
    out["name"] = row.name
    out["missing"] = row.missing()
    out["ready"] = not row.missing()
    return out


def params_schema() -> List[Dict[str, Any]]:
    return [{"field": f, "label": LABELS[f][0], "unit": LABELS[f][1],
             "help": LABELS[f][2]} for f in FIELDS]


def update_params(db: Session, values: Dict[str, Any],
                  actor: str = "planner") -> OptimizerParams:
    row = active_params(db)
    for f in FIELDS:
        if f in values:
            v = values[f]
            if f in BOOL_FIELDS:
                setattr(row, f, v if isinstance(v, bool)
                        else str(v).strip().lower() in ("1", "true", "yes", "on"))
                continue
            if f in RANGES and v is not None:
                lo, hi = RANGES[f]
                try:
                    n = float(v)
                except (TypeError, ValueError):
                    raise ValueError("{} must be a number, got {!r}".format(f, v))
                if not lo <= n <= hi:
                    raise ValueError(
                        "{} must be between {} and {}, got {}".format(f, lo, hi, n))
            setattr(row, f, None if v is None else type(getattr(row, f, 0.0) or 0.0)(v)
                    if isinstance(v, (int, float)) else v)
    db.add(AuditEvent(actor=actor, action="optimizer.params",
                      target=str(row.id), detail=values))
    db.commit()
    return row


# ------------------------------------------------------------------ running
def run(db: Session, base_scenario_id: int, actor: str = "planner",
        model_version: Optional[str] = None) -> OptimizerRun:
    """Solve a scenario with the active optimizer inputs.

    `model_version` overrides the Model input for this run only: Refine with v2
    (`refine`) runs v2 whatever the input is set to.
    """
    base = db.query(Scenario).get(base_scenario_id)
    if base is None:
        raise KeyError("no scenario {}".format(base_scenario_id))
    p = active_params(db)
    payload = params_dict(p)
    chosen = model_version or p.model_version
    payload["model_version"] = chosen

    run_row = OptimizerRun(base_scenario_id=base.id, created_by=actor,
                           params=payload, status="running")
    db.add(run_row)
    db.commit()

    try:
        ref = svc.engine_reference(db, base.reference_version_id)
        escn = svc.engine_scenario(db, base)
        sim = simulate(ref, escn, physical=True)
        dates = [svc._d(x) for x in base.inputs["dates"]][:p.horizon_days]
        downtime = svc.downtime_days(db, base.id)
        spec = build(ref, escn, sim, horizon=dates, downtime=downtime)

        solver_params = dict(payload)
        # netbacks arrive as what the material fetches; the model wants the
        # margin given up, which is the discount for the crude-linked outlets
        if p.netback_diesel_per_gal is not None:
            solver_params["netback_diesel_cost_per_gal"] = max(
                0.0, (p.crude_price_per_bbl / 42.0) - p.netback_diesel_per_gal)
        if p.netback_gasoline_per_gal is not None:
            solver_params["netback_gasoline_cost_per_gal"] = max(
                0.0, (p.crude_price_per_bbl / 42.0) - p.netback_gasoline_per_gal)

        # v2 is a two-stage solve, not a bigger one - see optimizer/v2.py for
        # why freeing all three units at once does not finish.
        # `.get`'s default silently runs v1 for an unrecognised name while the
        # run records itself under the name asked for, so anything selectable in
        # the UI has to be here too.
        model = {"v0": v0, "v1": v1, "v2": v2,
                 "greedy": greedy}.get(chosen or "v1", v1)
        result = model.solve(ref, escn, spec, solver_params, horizon=dates,
                             downtime=downtime)
        run_row.params = dict(payload, model_version=chosen)

        run_row.solver = result.solver
        run_row.solve_seconds = result.solve_seconds
        run_row.objective = result.objective
        run_row.status = result.status
        run_row.message = result.message

        # `feasible` is a time-limited run that found a schedule but could not
        # prove it best - the usual outcome on a 4-6 month horizon. It gets the
        # same treatment as `optimal`, including the simulator replay, because
        # the referee is what decides whether a schedule is usable; optimality
        # is a separate claim and `not_proved_optimal` keeps it visible.
        if result.status in ("optimal", "feasible"):
            proved = result.status == "optimal"
            # Not `dates`: the model may have solved fewer days than were asked
            # for. See `_solved_window`.
            scored = _solved_window(dates, result)
            baseline = _baseline_kpis(ref, escn, spec, scored)
            new_id = _write_result_scenario(db, base, result, actor, run_row.id,
                                            model_version=chosen)
            run_row.result_scenario_id = new_id

            # Replay the proposed schedule through the simulator. The optimizer's
            # own numbers come from its own variables, so they are exactly what
            # not to trust; the simulator is an independent implementation of the
            # same physics and is the referee.
            proposed = db.query(Scenario).get(new_id)
            check = verify.verify(ref, svc.engine_scenario(db, proposed),
                                  result.kpis, scored, spec, downtime=downtime)
            run_row.kpis = {"baseline": baseline, "optimized": result.kpis,
                            "verification": check, "proved_optimal": proved}
            if not check["verified"]:
                run_row.status = "unverified"
                run_row.message = check["note"]
            else:
                run_row.status = "done" if proved else "not_proved_optimal"
    except Exception as e:                       # surface, never swallow
        run_row.status = "error"
        run_row.message = "{}: {}".format(type(e).__name__, e)

    db.add(AuditEvent(actor=actor, action="optimizer.run", target=str(run_row.id),
                      detail={"status": run_row.status,
                              "objective": run_row.objective}))
    db.commit()
    return run_row


def _solved_window(dates, result) -> List[dt.date]:
    """The days the model actually planned, which is not always the days asked for.

    Both models cut the horizon back to the last day the planner has filled in a
    charge (`cfg.clamp_horizon`) and report what is left as `kpis["days"]`.
    Scoring the plan and refereeing the answer over the days *asked for* then
    measures two different windows against each other, and the longer window is
    always the baseline's - so the comparison flatters the optimizer by exactly
    the demand sitting in the days it never planned.

    On the seed plan at 180 days it cut to 162 and reported lost sales down 4.4%
    against an 18.8 M gal baseline. Over the 162 days both sides really covered,
    the baseline is 15.0 M and the run is 19.6% *worse*. The sign was wrong, in
    the direction that makes the tool look good, which is the one direction a
    referee must never fail in.

    Falls back to the full window when the model reported nothing usable. That is
    the old behaviour, and it is right here: a model that cannot say what it
    solved has not earned a narrower window being assumed on its behalf.
    """
    days = (result.kpis or {}).get("days")
    if isinstance(days, (int, float)) and 0 < int(days) <= len(dates):
        return dates[:int(days)]
    return dates


def _baseline_kpis(ref, escn, spec, dates) -> Dict[str, Any]:
    """The plan as it stands, scored exactly the way the optimized run is.

    This has to go through `verify` rather than summing the simulation directly.
    Scored the old way - raw workbook blocks, strict first-match lookups - the
    baseline carries 583,800 gal of demand booked against a retired product and
    the diesel pool's member-by-member clamping, neither of which the optimizer
    is asked to plan. Put side by side with a model-space result it overstated
    the improvement by seven points, which is the kind of error that only ever
    flatters the tool reporting it.
    """
    check = verify.verify(ref, escn, {"lost_sales_gal": 0.0, "downgrade_gal": 0.0},
                          dates, spec)
    actual = check["actual"]
    return {
        "lost_sales_gal": actual["lost_sales_gal"],
        # the plan routes nothing itself, so its downgrade is what the tanks
        # force: the residual, not a decision
        "downgrade_gal": actual["downgrade_required_gal"],
        "feed_shortfall_gal": actual["feed_shortfall_gal"],
        "excluded_lost_sales_gal": check["excluded_lost_sales_gal"],
        "scored_in_model_space": True,
        "days": len(dates),
    }


def _write_result_scenario(db: Session, base: Scenario, result, actor: str,
                           run_id: int, model_version: Optional[str] = None) -> int:
    """Land the answer as an Optimized Result, so the manual side can open it.

    Named like its label (`scenario_labels`): the run, the model and the
    Current Plan at the root, never the base's own name - a result refined
    from a result used to nest, "Optimizer run 21 (from Optimizer run 20
    (from ...))".
    """
    plan = scenario_labels(db).get(base.id, {}).get("plan_label", base.name)
    name = "Run {}{} \u00b7 from {}".format(
        run_id, " \u00b7 {}".format(model_version) if model_version else "", plan)
    scenario = svc.create_scenario(
        db, name, base.as_of, base.horizon_days, actor,
        note="Optimized Result of run {}. Open it on the planning side to "
             "review, edit or compare.".format(run_id),
        copy_schedule_from=base.id)

    # replace the copied charge levels with the optimized ones
    by_key = {}
    for e in db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id == scenario.id):
        by_key[(e.line_key, e.date.isoformat())] = e
    # Charge levels, and the downgrade decisions written onto the transfer lines.
    # Both are needed: writing only the charges would leave the planner's own
    # transfers in place, so the schedule the simulator replays would not be the
    # one the optimizer solved.
    for source in (result.charge, result.transfer_bbl):
        for line_key, series in source.items():
            for s, bbl in series.items():
                entry = by_key.get((line_key, s))
                if entry is None:
                    if bbl:
                        db.add(ScheduleEntry(scenario_id=scenario.id,
                                             line_key=line_key,
                                             date=svc._d(s), bbl=bbl))
                else:
                    entry.bbl = bbl
    # Outage days carry no charge variable, so the optimizer returns nothing for
    # them and the copy above leaves the planner's own charge standing. The
    # result then shows a unit charging on a day it is down - material the
    # optimizer never counted, which the verifier meets later as a feed shortfall
    # it cannot explain. Nothing may be produced on an outage day, so an
    # inherited value is never right.
    #
    # Zeroed rather than deleted: `engine_scenario` skips a zero, so it is inert
    # to the engine, and the grid keeps its shape on the planning side where a
    # missing cell and a zero read differently.
    down = svc.downtime_days(db, scenario.id)
    cleared = 0
    for e in db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id == scenario.id):
        if e.bbl and e.date in down.get(e.line_key.split("#")[0], ()):
            e.bbl = 0.0
            cleared += 1

    scenario.status = "proposed"
    scenario.schedule_version += 1
    db.commit()
    svc.invalidate(scenario.id)
    return scenario.id


#: Slack on top of twice a run's own time limit before a row still marked
#: `running` is taken to have died with the server. Runs observed here end
#: within seconds of their limit; the simulation and verification either side
#: of the solve are what the margin is for.
RUN_GRACE_SECONDS = 600


def in_progress(run: OptimizerRun, now: Optional[dt.datetime] = None) -> bool:
    """Whether a run marked `running` is really still solving.

    The row is committed as `running` before the solve starts and only updated
    when it ends, so a server stopped mid-solve leaves it `running` for ever.
    Without a cut-off, that one row would refuse every run after it.
    """
    if run.status != "running":
        return False
    limit = float((run.params or {}).get("time_limit_seconds") or 900)
    now = now or dt.datetime.utcnow()
    return now - run.created_at < dt.timedelta(
        seconds=2 * limit + RUN_GRACE_SECONDS)


def running_run(db: Session) -> Optional[OptimizerRun]:
    """The run still solving, if there is one.

    One at a time, whatever scenario it is on. Two v2 runs on the same plan
    were started 14 s apart - the button stayed clickable through a 15-minute
    solve - and both ran to the limit for the same answer.
    """
    for run in (db.query(OptimizerRun)
                .filter(OptimizerRun.status == "running")
                .order_by(OptimizerRun.id.desc())):
        if in_progress(run):
            return run
    return None


def rejected_result(db: Session, scenario_id: int) -> Optional[OptimizerRun]:
    """The run this scenario is the answer of, if the simulator rejected it.

    The scenario is kept so the failure can be inspected, but a schedule the
    simulator says cannot be executed must not reach the workbook.
    """
    run = (db.query(OptimizerRun)
           .filter(OptimizerRun.result_scenario_id == scenario_id)
           .order_by(OptimizerRun.id.desc()).first())
    return run if run is not None and run.status == "unverified" else None


# ------------------------------------------------------------------ refining
#: The model Refine with v2 runs on a greedy result.
REFINE_MODEL = "v2"


def refinable(run: OptimizerRun) -> Optional[str]:
    """Why a run's Optimized Result can't be refined with v2, or None if it can.

    Solving an Optimized Result again is refused everywhere else: the charge
    floor is a fraction of whatever the model is given, so pass after pass
    stacks a new minimum on every line-day and the model goes infeasible. One v2
    pass on a verified greedy result is the exception the user chose (UI
    convention 12). On scenario 48 at 42 days it lost 17.5% fewer gallons than
    v2 on the plan in the same time (README, "Greedy first, then the MIP").
    """
    if (run.params or {}).get("model_version") != "greedy":
        return "only a greedy run's Optimized Result can be refined with v2"
    if run.status not in ("done", "not_proved_optimal"):
        return ("run {} was not verified, so its result is not a starting point"
                .format(run.id))
    if run.result_scenario_id is None:
        return "run {} has no Optimized Result".format(run.id)
    return None


def refine(db: Session, run_id: int, actor: str = "planner") -> OptimizerRun:
    """Run v2 on a verified greedy run's Optimized Result."""
    source = db.query(OptimizerRun).get(run_id)
    if source is None:
        raise KeyError("no run {}".format(run_id))
    reason = refinable(source)
    if reason:
        raise ValueError(reason)
    return run(db, source.result_scenario_id, actor, model_version=REFINE_MODEL)


def scenario_labels(db: Session) -> Dict[int, Dict[str, Any]]:
    """What each scenario is called on screen, and which kind it is.

    A scenario a run wrote is an *Optimized Result*; every other one is a
    *Current Plan*. Results were named "Optimizer run N (from X)", and one
    refined from another nested four deep until the picker pushed New scenario
    off the screen. So a result is labelled from its run - the run, the model,
    and the Current Plan at the root of the chain - which gives the results made
    before this the short label too, without renaming anything stored.
    """
    names = {sid: name for sid, name in db.query(Scenario.id, Scenario.name)}
    made_by: Dict[int, OptimizerRun] = {}
    for run in (db.query(OptimizerRun)
                .filter(OptimizerRun.result_scenario_id.isnot(None))
                .order_by(OptimizerRun.id)):
        made_by[run.result_scenario_id] = run          # latest run wins

    def root(sid: int) -> int:
        seen = set()
        while sid in made_by and sid not in seen:
            seen.add(sid)
            sid = made_by[sid].base_scenario_id
        return sid

    out: Dict[int, Dict[str, Any]] = {}
    for sid, name in names.items():
        run = made_by.get(sid)
        if run is None:
            out[sid] = {"kind": "current_plan", "label": name,
                        "plan_id": sid, "plan_label": name}
            continue
        pid = root(sid)
        plan = names.get(pid, "scenario {}".format(pid))
        model = (run.params or {}).get("model_version") or "v0"
        out[sid] = {"kind": "optimized_result", "run_id": run.id,
                    "run_status": run.status, "model": model,
                    "label": "Run {} \u00b7 {} \u00b7 from {}".format(run.id, model, plan),
                    "plan_id": pid, "plan_label": plan}
    return out


def list_runs(db: Session, limit: int = 25) -> List[Dict[str, Any]]:
    rows = (db.query(OptimizerRun).order_by(OptimizerRun.id.desc())
            .limit(limit).all())
    labels = scenario_labels(db)
    now = dt.datetime.utcnow()
    # The runs already started from each listed run's result, so a card can say
    # it has been refined before another 15-minute refinement is started.
    refined_by: Dict[int, List[int]] = {}
    results = [r.result_scenario_id for r in rows if r.result_scenario_id]
    if results:
        for child in (db.query(OptimizerRun)
                      .filter(OptimizerRun.base_scenario_id.in_(results))
                      .order_by(OptimizerRun.id)):
            refined_by.setdefault(child.base_scenario_id, []).append(child.id)
    return [{
        "id": r.id, "created_at": as_utc(r.created_at),
        "created_by": r.created_by, "message": r.message,
        # A row left `running` by a stopped server reads as what happened to it.
        "status": ("interrupted" if r.status == "running"
                   and not in_progress(r, now) else r.status),
        "in_progress": in_progress(r, now),
        "time_limit_seconds": (r.params or {}).get("time_limit_seconds"),
        # What the run was solved with. The inputs are global and save the
        # moment a box loses focus, so the screen only ever shows what the
        # *next* run will use.
        "settings": [{"field": f, "label": LABELS[f][0], "unit": LABELS[f][1],
                      "value": (r.params or {}).get(f)}
                     for f in FIELDS if f in (r.params or {})],
        "solver": r.solver, "solve_seconds": r.solve_seconds,
        "objective": r.objective, "kpis": r.kpis,
        "model_version": (r.params or {}).get("model_version") or "v0",
        # The run starts from this scenario's own charge grid, so it is both the
        # starting point and the thing worth holding the answer against.
        "base_scenario_id": r.base_scenario_id,
        "base_scenario_label": labels.get(r.base_scenario_id, {}).get("label"),
        "base_kind": labels.get(r.base_scenario_id, {}).get("kind"),
        "result_scenario_id": r.result_scenario_id,
        "result_scenario_label": labels.get(r.result_scenario_id, {}).get("label"),
        "refinable": refinable(r) is None,
        "refined_by": refined_by.get(r.result_scenario_id, []),
    } for r in rows]


def _solved_days(run: OptimizerRun) -> Optional[int]:
    """Days the run covered, or `None` when it never got far enough to say.

    Read off the result rather than the run's own parameters: a run whose
    horizon was clamped back to the last day the planner had filled in solved
    fewer days than it was asked for, and the clamped figure is the one that
    describes the schedule sitting in the scenario.
    """
    opt = ((run.kpis or {}).get("optimized") or {})
    days = opt.get("days")
    return int(days) if isinstance(days, (int, float)) and days > 0 else None


def pair_for(db: Session, scenario_id: int) -> Dict[str, Any]:
    """The other half of a Current Plan / Optimized Result pair, whichever half
    you hold.

    A planner holding their Current Plan wants its Optimized Result beside it;
    one holding the result wants the plan back. Same question, both ways. Roles
    are `current_plan` and `optimized_result` - the words on screen - except
    that a result refined from a result has a result as its other half.
    """
    labels = scenario_labels(db)
    run = (db.query(OptimizerRun)
           .filter(OptimizerRun.result_scenario_id == scenario_id)
           .order_by(OptimizerRun.id.desc()).first())
    if run is not None:
        other = labels.get(run.base_scenario_id, {})
        return {"role": "optimized_result", "run_id": run.id,
                "run_status": run.status, "other_id": run.base_scenario_id,
                "other_role": other.get("kind", "current_plan"),
                "other_label": other.get("label"),
                # How many days this run actually solved, counted from the
                # scenario's `as_of`. The result carries the whole grid - the
                # days past this one are the copied plan, which the optimizer
                # never touched and the referee never scored - so the planning
                # side needs the real number rather than the live input, which
                # may have been changed since the run.
                "solved_days": _solved_days(run)}
    run = (db.query(OptimizerRun)
           .filter(OptimizerRun.base_scenario_id == scenario_id,
                   OptimizerRun.result_scenario_id.isnot(None))
           .order_by(OptimizerRun.id.desc()).first())
    if run is not None:
        other = labels.get(run.result_scenario_id, {})
        return {"role": "current_plan", "run_id": run.id,
                "run_status": run.status, "other_id": run.result_scenario_id,
                "other_role": "optimized_result",
                "other_label": other.get("label")}
    return {}


# ------------------------------------------------------------------ deleting
def _deletion_set(db: Session, scenario_id: int):
    """The scenarios and runs a delete removes, and the results it leaves.

    Decided by the user on 2026-09-12 and 2026-09-13 (UI convention 13):

    - A Current Plan goes with every Optimized Result made from it, however
      many refinements deep, and with every run started from any of them.
    - An Optimized Result goes with its own run card. Results made *from* it
      stay; `delete_scenario` points their runs at the scenario it came from.
    """
    labels = scenario_labels(db)
    me = labels.get(scenario_id)
    if me is None:
        raise KeyError("no scenario {}".format(scenario_id))
    if me["kind"] == "current_plan":
        doomed = {sid for sid, l in labels.items() if l["plan_id"] == scenario_id}
        kept: set = set()
    else:
        doomed = {scenario_id}
        kept = {r.result_scenario_id for r in db.query(OptimizerRun).filter(
            OptimizerRun.base_scenario_id == scenario_id,
            OptimizerRun.result_scenario_id.isnot(None))}
    runs = [r for r in db.query(OptimizerRun).filter(
                OptimizerRun.result_scenario_id.in_(doomed)
                | OptimizerRun.base_scenario_id.in_(doomed))
            # a kept result's own run survives, pointed elsewhere
            if r.result_scenario_id not in kept]
    return me, labels, doomed, kept, runs


def deletion(db: Session, scenario_id: int) -> Dict[str, Any]:
    """What deleting a scenario takes with it, for the confirmation to name."""
    me, labels, doomed, kept, runs = _deletion_set(db, scenario_id)
    results = sorted((s for s in doomed
                      if labels[s]["kind"] == "optimized_result"), reverse=True)
    return {
        "scenario_id": scenario_id, "kind": me["kind"], "label": me["label"],
        "plan_label": me["plan_label"],
        "scenario_ids": sorted(doomed),
        "result_ids": results,
        "results": [labels[s]["label"] for s in results],
        "runs": sorted(r.id for r in runs),
        "kept": [labels[k]["label"] for k in sorted(kept) if k in labels],
        "busy": sorted(r.id for r in runs if in_progress(r)),
    }


def delete_scenario(db: Session, scenario_id: int,
                    actor: str = "planner") -> Dict[str, Any]:
    """Delete a scenario and what goes with it; see `_deletion_set`.

    A result made from the deleted one keeps a valid reference and still names
    its Current Plan: its run is pointed at the scenario the deleted result came
    from, and its KPIs note the result it really started from, since its
    comparison numbers were scored against that one. Runs go before scenarios,
    because they refer to them.
    """
    summary = deletion(db, scenario_id)
    if summary["busy"]:
        raise RuntimeError("run {} is still solving on this - wait for it to finish "
                           "before deleting".format(summary["busy"][0]))
    me, labels, doomed, kept, runs = _deletion_set(db, scenario_id)
    if kept:
        made = (db.query(OptimizerRun)
                .filter(OptimizerRun.result_scenario_id == scenario_id)
                .order_by(OptimizerRun.id.desc()).first())
        for child in db.query(OptimizerRun).filter(
                OptimizerRun.base_scenario_id == scenario_id,
                OptimizerRun.result_scenario_id.in_(kept)):
            child.base_scenario_id = made.base_scenario_id
            child.kpis = dict(child.kpis or {}, started_from_deleted={
                "scenario_id": scenario_id, "label": me["label"]})
    for run in runs:
        db.delete(run)
    db.flush()
    for sid in doomed:
        scenario = db.query(Scenario).get(sid)
        if scenario is not None:
            db.delete(scenario)      # its grid, crude days and downtime cascade
    db.add(AuditEvent(actor=actor, action="scenario.delete", target=str(scenario_id),
                      detail={"label": me["label"], "scenarios": sorted(doomed),
                              "runs": summary["runs"], "kept": sorted(kept)}))
    db.commit()
    for sid in doomed:
        svc.invalidate(sid)
    return summary
