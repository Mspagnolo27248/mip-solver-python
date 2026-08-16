"""Running the optimizer and handing its answer back to the manual side."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..engine import simulate
from ..modelprep import build
from ..optimizer import v0, v1, verify
from . import service as svc
from .models import (AuditEvent, OptimizerParams, OptimizerRun, Scenario,
                     ScheduleEntry)


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
          "objective", "terminal_value_fraction", "charge_floor_fraction",
          "terminal_shortfall_per_gal", "model_version", "horizon_days",
          "time_limit_seconds", "mip_gap"]

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
    "charge_floor_fraction": ("Charge floor", "fraction of plan",
                              "How much of your own charge each unit must still "
                              "run. The objective only counts costs, so with no "
                              "floor the cheapest schedule is to stop making "
                              "oil. 1.0 pins charges to the plan and optimises "
                              "routing alone - but 1.0 is infeasible on the "
                              "current plan, which asks units to charge feed "
                              "that is not there."),
    "terminal_shortfall_per_gal": (
        "End-of-window shortfall", "$/gal",
        "What ending below opening inventory costs. Material to replace, not "
        "margin lost, so it shares the downgrade's economics and defaults to "
        "the downgrade discount. Keep it well under the lost-sale margin: set "
        "level, the model cannot tell shorting a customer from drawing a tank "
        "down; set close, it shorts customers to hold stock."),
    "model_version": (
        "Model", "v0 or v1",
        "v0 keeps your charge assignments and optimises only how much and where "
        "the overflow goes. v1 also decides what the MEK and extraction units "
        "charge each day, on the transitions those units are allowed. v0 solves "
        "in about a second, v1 in about ten."),
    "horizon_days": ("Detailed horizon", "days",
                     "Firm orders cover about 57 days; beyond that demand is "
                     "pure forecast."),
    "time_limit_seconds": ("Solver time limit", "seconds", ""),
    "mip_gap": ("Accepted gap", "fraction",
                "Stop when within this of proven optimal."),
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
            setattr(row, f, None if v is None else type(getattr(row, f, 0.0) or 0.0)(v)
                    if isinstance(v, (int, float)) else v)
    db.add(AuditEvent(actor=actor, action="optimizer.params",
                      target=str(row.id), detail=values))
    db.commit()
    return row


# ------------------------------------------------------------------ running
def run(db: Session, base_scenario_id: int, actor: str = "planner"
        ) -> OptimizerRun:
    base = db.query(Scenario).get(base_scenario_id)
    if base is None:
        raise KeyError("no scenario {}".format(base_scenario_id))
    p = active_params(db)
    payload = params_dict(p)

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

        model = v1 if (p.model_version or "v1") == "v1" else v0
        result = model.solve(ref, escn, spec, solver_params, horizon=dates,
                             downtime=downtime)
        run_row.params = dict(payload, model_version=p.model_version)

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
            baseline = _baseline_kpis(ref, escn, spec, dates)
            new_id = _write_result_scenario(db, base, result, actor, run_row.id)
            run_row.result_scenario_id = new_id

            # Replay the proposed schedule through the simulator. The optimizer's
            # own numbers come from its own variables, so they are exactly what
            # not to trust; the simulator is an independent implementation of the
            # same physics and is the referee.
            proposed = db.query(Scenario).get(new_id)
            check = verify.verify(ref, svc.engine_scenario(db, proposed),
                                  result.kpis, dates, spec)
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
                           run_id: int) -> int:
    """Land the answer as a scenario, so the manual side can open it."""
    scenario = svc.create_scenario(
        db, "Optimizer run {} (from {})".format(run_id, base.name),
        base.as_of, base.horizon_days, actor,
        note="Generated by optimizer run {}. Open it on the planning side to "
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
    scenario.status = "proposed"
    scenario.schedule_version += 1
    db.commit()
    svc.invalidate(scenario.id)
    return scenario.id


def list_runs(db: Session, limit: int = 25) -> List[Dict[str, Any]]:
    rows = (db.query(OptimizerRun).order_by(OptimizerRun.id.desc())
            .limit(limit).all())
    names = {s.id: s.name for s in db.query(Scenario)}
    return [{
        "id": r.id, "created_at": r.created_at.isoformat(),
        "created_by": r.created_by, "status": r.status, "message": r.message,
        "solver": r.solver, "solve_seconds": r.solve_seconds,
        "objective": r.objective, "kpis": r.kpis,
        "model_version": (r.params or {}).get("model_version") or "v0",
        # The run warm-starts from this scenario's own charge grid, so the
        # planner's schedule is both the starting point and the thing worth
        # holding the answer against.
        "base_scenario_id": r.base_scenario_id,
        "base_scenario_name": names.get(r.base_scenario_id),
        "result_scenario_id": r.result_scenario_id,
        "result_scenario_name": names.get(r.result_scenario_id),
    } for r in rows]


def pair_for(db: Session, scenario_id: int) -> Dict[str, Any]:
    """The other half of a warm-start / result pair, whichever half you hold.

    A planner looking at their own schedule wants the optimizer's beside it; a
    planner looking at a proposal wants their own. Both are the same question.
    """
    run = (db.query(OptimizerRun)
           .filter(OptimizerRun.result_scenario_id == scenario_id)
           .order_by(OptimizerRun.id.desc()).first())
    if run is not None:
        base = db.query(Scenario).get(run.base_scenario_id)
        return {"role": "result", "run_id": run.id,
                "other_id": run.base_scenario_id,
                "other_name": base.name if base else None,
                "other_role": "warm start"}
    run = (db.query(OptimizerRun)
           .filter(OptimizerRun.base_scenario_id == scenario_id,
                   OptimizerRun.result_scenario_id.isnot(None))
           .order_by(OptimizerRun.id.desc()).first())
    if run is not None:
        result = db.query(Scenario).get(run.result_scenario_id)
        return {"role": "warm start", "run_id": run.id,
                "other_id": run.result_scenario_id,
                "other_name": result.name if result else None,
                "other_role": "optimizer"}
    return {}
