"""Domain services over the database: sync feeds, freeze scenarios, simulate."""
from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..engine import Reference as EngineReference
from ..engine import Scenario as EngineScenario
from ..engine import Simulation, simulate
from .connectors import FeedConnector, default_connectors
from .models import (AuditEvent, CrudeDay, Feed, PlannedDowntime, RawBatch,
                     RawRow, ReferenceVersion, Scenario, ScheduleEntry,
                     StagingCell)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", ".."))
SEED_DIR = os.path.join(ROOT, "data", "seed")


# ------------------------------------------------------------------ reference
def load_reference_document(db: Session, path: Optional[str] = None,
                            note: str = "") -> ReferenceVersion:
    path = path or os.path.join(SEED_DIR, "reference.json")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    # Scenario-shaped data that is planner-maintained rather than fed: manual
    # balance rows and the effective-dated capacity steps. Carried alongside the
    # reference so every new scenario starts from the same baseline.
    scenario_path = os.path.join(os.path.dirname(path), "scenario.json")
    if os.path.exists(scenario_path):
        with open(scenario_path, encoding="utf-8") as f:
            scn = json.load(f)
        payload["scenario_defaults"] = {
            "manual_block_rows": scn.get("manual_block_rows", {}),
            "capacity_series": scn.get("capacity_series", {}),
            "base_oil_transfer_columns": scn.get("base_oil_transfer_columns", {}),
            "charge_schedule": scn.get("charge_schedule", {}),
            "as_of": scn.get("meta", {}).get("as_of"),
            "dates": scn.get("dates", []),
            "crude_opening_bbl": scn.get("crude_opening_bbl", 0.0),
        }

    db.query(ReferenceVersion).update({ReferenceVersion.is_active: False})
    rv = ReferenceVersion(source=os.path.basename(path), note=note,
                          payload=payload, is_active=True)
    db.add(rv)
    db.flush()
    return rv


def active_reference(db: Session) -> Optional[ReferenceVersion]:
    return (db.query(ReferenceVersion)
            .filter(ReferenceVersion.is_active.is_(True))
            .order_by(ReferenceVersion.id.desc()).first())


# ----------------------------------------------------------------- feed sync
def sync_feed(db: Session, connector: FeedConnector, actor: str = "system"
              ) -> RawBatch:
    """Pull a feed, store it immutably, and merge into staging.

    Overrides are never touched: only `source_value` moves. Where a planner has
    an override and the source has since changed, the cell is left flagged as
    stale rather than silently resolved either way.
    """
    rows = connector.fetch()
    batch = RawBatch(feed=connector.feed, source=connector.source,
                     row_count=len(rows), status="ok",
                     meta={"full_refresh": connector.full_refresh})
    db.add(batch)
    db.flush()

    for r in rows:
        db.add(RawRow(batch_id=batch.id, feed=connector.feed, k1=r.k1, k2=r.k2,
                      k3=r.k3, value=r.value, extra=r.extra))

    existing = {(c.k1, c.k2, c.k3): c for c in
                db.query(StagingCell).filter(StagingCell.feed == connector.feed)}
    now = dt.datetime.utcnow()
    seen = set()
    created = updated = 0

    for r in rows:
        key = (r.k1, r.k2, r.k3)
        seen.add(key)
        cell = existing.get(key)
        if cell is None:
            cell = StagingCell(feed=connector.feed, k1=r.k1, k2=r.k2, k3=r.k3)
            db.add(cell)
            created += 1
        else:
            updated += 1
        cell.source_value = r.value
        cell.source_batch_id = batch.id
        cell.source_seen_at = now
        if r.label:
            cell.label = r.label

    zeroed = 0
    if connector.full_refresh:
        for key, cell in existing.items():
            if key not in seen and (cell.source_value or 0.0) != 0.0:
                cell.source_value = 0.0
                cell.source_batch_id = batch.id
                cell.source_seen_at = now
                zeroed += 1

    batch.meta = dict(batch.meta or {},
                      created=created, updated=updated, absent_zeroed=zeroed)
    db.add(AuditEvent(actor=actor, action="feed.sync", target=connector.feed,
                      detail={"batch": batch.id, "rows": len(rows),
                              "created": created, "updated": updated,
                              "absent_zeroed": zeroed}))
    db.commit()
    return batch


def sync_all(db: Session, actor: str = "system") -> List[RawBatch]:
    return [sync_feed(db, c, actor) for c in default_connectors(SEED_DIR)]


def feed_summary(db: Session) -> List[Dict[str, Any]]:
    out = []
    for feed in Feed.ALL:
        cells = db.query(StagingCell).filter(StagingCell.feed == feed).all()
        batch = (db.query(RawBatch).filter(RawBatch.feed == feed)
                 .order_by(RawBatch.id.desc()).first())
        overrides = [c for c in cells if c.has_override]
        meta = Feed.META.get(feed, {})
        out.append({
            "feed": feed,
            "title": meta.get("title", feed),
            "description": meta.get("description", ""),
            "key_labels": meta.get("key_labels", ["Key 1", "Key 2"]),
            "unit": meta.get("unit", ""),
            "rows": len(cells),
            "overrides": len(overrides),
            "stale_overrides": len([c for c in overrides if c.is_stale]),
            "last_sync": batch.fetched_at.isoformat() if batch else None,
            "source": batch.source if batch else None,
        })
    return out


def staging_rows(db: Session, feed: str, search: str = "", only: str = "all",
                 limit: int = 500, offset: int = 0) -> Dict[str, Any]:
    q = db.query(StagingCell).filter(StagingCell.feed == feed)
    if search:
        like = "%{}%".format(search.strip())
        q = q.filter((StagingCell.k1.ilike(like)) | (StagingCell.k2.ilike(like))
                     | (StagingCell.label.ilike(like)))
    rows = q.order_by(StagingCell.k1, StagingCell.k2).all()
    if only == "overrides":
        rows = [c for c in rows if c.has_override]
    elif only == "stale":
        rows = [c for c in rows if c.is_stale]
    total = len(rows)
    page = rows[offset:offset + limit]
    return {
        "feed": feed,
        "total": total,
        "rows": [{
            "id": c.id, "k1": c.k1, "k2": c.k2, "k3": c.k3, "label": c.label,
            "source_value": c.source_value,
            "override_value": c.override_value,
            "effective_value": c.effective_value,
            "has_override": c.has_override,
            "is_stale": c.is_stale,
            "override_by": c.override_by,
            "override_reason": c.override_reason,
            "override_at": c.override_at.isoformat() if c.override_at else None,
        } for c in page],
    }


def set_override(db: Session, cell_id: int, value: Optional[float],
                 actor: str, reason: str = "") -> StagingCell:
    cell = db.query(StagingCell).get(cell_id)
    if cell is None:
        raise KeyError("no staging cell {}".format(cell_id))
    if value is None:
        cell.override_value = None
        cell.override_by = None
        cell.override_at = None
        cell.override_reason = None
        cell.source_at_override = None
        action = "staging.override.clear"
    else:
        cell.override_value = float(value)
        cell.override_by = actor
        cell.override_at = dt.datetime.utcnow()
        cell.override_reason = reason
        cell.source_at_override = cell.source_value
        action = "staging.override.set"
    db.add(AuditEvent(actor=actor, action=action,
                      target="{}:{}".format(cell.feed, cell_id),
                      detail={"value": value, "reason": reason}))
    db.commit()
    return cell


def effective_map(db: Session, feed: str) -> Dict[Tuple[str, Optional[str]], float]:
    return {(c.k1, c.k2): c.effective_value
            for c in db.query(StagingCell).filter(StagingCell.feed == feed)}


# ------------------------------------------------------------------ scenarios
def create_scenario(db: Session, name: str, as_of: dt.date,
                    horizon_days: int = 366, actor: str = "planner",
                    note: str = "", copy_schedule_from: Optional[int] = None
                    ) -> Scenario:
    """Freeze current staging into a new scenario and seed its charge schedule."""
    ref = active_reference(db)
    if ref is None:
        raise RuntimeError("no reference version loaded")

    dates = [as_of + dt.timedelta(days=i) for i in range(horizon_days)]
    defaults = ref.payload.get("scenario_defaults", {})

    inputs = {
        "dates": [d.isoformat() for d in dates],
        "opening_inventory": _opening_inventory(db),
        "crude_opening_bbl": defaults.get("crude_opening_bbl", 0.0),
        "open_orders": _nested(db, Feed.OPEN_ORDERS),
        "sales_forecast": _nested(db, Feed.SALES_FORECAST),
        "blend_component_demand": _nested(db, Feed.BLEND_DEMAND),
        "base_oil_transfer": _nested(db, Feed.BASE_OIL_TRANSFER),
        "base_oil_transfer_columns": defaults.get("base_oil_transfer_columns", {}),
        "manual_block_rows": defaults.get("manual_block_rows", {}),
        "capacity_series": defaults.get("capacity_series", {}),
    }

    scenario = Scenario(name=name, as_of=as_of, horizon_days=horizon_days,
                        reference_version_id=ref.id, created_by=actor, note=note,
                        inputs=inputs, status="draft")
    db.add(scenario)
    db.flush()

    if copy_schedule_from:
        _copy_schedule(db, copy_schedule_from, scenario.id)
    else:
        _seed_schedule_from_defaults(db, scenario, defaults.get("charge_schedule", {}))
        _seed_downtime_from_config(db, scenario)

    db.add(AuditEvent(actor=actor, action="scenario.create",
                      target=str(scenario.id), detail={"name": name}))
    db.commit()
    return scenario


def _opening_inventory(db: Session) -> Dict[str, float]:
    """Net inventory per product, summed across tanks."""
    out: Dict[str, float] = defaultdict(float)
    for c in db.query(StagingCell).filter(StagingCell.feed == Feed.INVENTORY):
        out[c.k1] += c.effective_value
    return dict(out)


def _nested(db: Session, feed: str) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = defaultdict(dict)
    for c in db.query(StagingCell).filter(StagingCell.feed == feed):
        if c.k2 is None:
            continue
        out[c.k1][c.k2] = c.effective_value
    return {k: v for k, v in out.items()}


def _seed_schedule_from_defaults(db: Session, scenario: Scenario,
                                 schedule: Dict[str, Any]) -> None:
    for iso, bbl in (schedule.get("crude_bbl_per_day") or {}).items():
        db.add(CrudeDay(scenario_id=scenario.id, date=_d(iso), bbl=float(bbl),
                        mode=(schedule.get("crude_mode") or {}).get(iso)))
    for line_key, by_date in (schedule.get("lines") or {}).items():
        for iso, bbl in by_date.items():
            db.add(ScheduleEntry(scenario_id=scenario.id, line_key=line_key,
                                 date=_d(iso), bbl=float(bbl)))


def _copy_schedule(db: Session, src_id: int, dst_id: int) -> None:
    for e in db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id == src_id):
        db.add(ScheduleEntry(scenario_id=dst_id, line_key=e.line_key,
                             date=e.date, bbl=e.bbl))
    for c in db.query(CrudeDay).filter(CrudeDay.scenario_id == src_id):
        db.add(CrudeDay(scenario_id=dst_id, date=c.date, bbl=c.bbl, mode=c.mode))
    _copy_downtime(db, src_id, dst_id)


def _seed_downtime_from_config(db: Session, scenario: Scenario) -> None:
    """Start a new scenario with the outages implicit in the workbook.

    These were inferred from blank stretches in the old plan, not read from a
    maintenance calendar, so they arrive marked `detected` - visible and editable
    rather than silently trusted.
    """
    from .. import model_config as cfg

    for unit, windows in cfg.TURNAROUNDS.items():
        for wdw in windows:
            db.add(PlannedDowntime(
                scenario_id=scenario.id, unit=unit,
                start_date=_d(wdw["start"]), end_date=_d(wdw["end"]),
                reason=wdw.get("note", "inferred from a gap in the workbook plan"),
                status=wdw.get("status", "detected"), created_by="seed"))


def _copy_downtime(db: Session, src_id: int, dst_id: int) -> None:
    for d in db.query(PlannedDowntime).filter(
            PlannedDowntime.scenario_id == src_id):
        db.add(PlannedDowntime(scenario_id=dst_id, unit=d.unit,
                               start_date=d.start_date, end_date=d.end_date,
                               reason=d.reason, status=d.status,
                               created_by=d.created_by))


# ------------------------------------------------------- charge schedule fill
def fill_schedule(db: Session, scenario_id: int, target: str,
                  bbl: float, start: Optional[dt.date] = None,
                  end: Optional[dt.date] = None, mode: Optional[str] = None,
                  skip_downtime: bool = True, overwrite: bool = True,
                  exclusive: bool = True, live_keys: Optional[set] = None,
                  actor: str = "planner") -> Dict[str, Any]:
    """Write one rate across a stretch of days.

    A cold start is set up by hand today: the planner types a rate into every
    cell of the crude, ROSE and Platformer rows and deletes every cell of the
    units the optimizer decides for. Over a 100-day window that is several
    hundred keystrokes per row, and the schedule the optimizer is handed is only
    as good as the last cell that got typed.

    `target` is one of:

      ``CRUDE``       the crude charge row, and its mode if one is given
      ``ROSE#97``     one charge line
      ``MEK``         every live line on that unit - **clearing only**

    Filling a whole unit with a *rate* is refused rather than obeyed. Most units
    carry several lines, and writing the same rate onto all of them does not run
    the unit at that rate: it runs every line at once, which is not a schedule
    the plant can execute and not what anyone meant to ask for. Clearing a whole
    unit is the operation that does make sense, and is the one a cold start
    needs.

    `exclusive` is the other half of that guard. A unit runs one feed at a time,
    so filling a line at a rate clears its siblings on the same days; without it
    a planner setting ROSE#97 over a window where ROSE#98 was already scheduled
    silently doubles the unit's charge.

    Days a unit is down are skipped, not written. The optimizer creates no
    variable on an outage day, so a charge typed there survives into results
    unnoticed - which is the defect the referee's downtime check now catches, and
    there is no reason to keep making it upstream of the referee.
    """
    scenario = db.query(Scenario).get(scenario_id)
    if scenario is None:
        raise KeyError("no scenario {}".format(scenario_id))

    days = [_d(x) for x in scenario.inputs["dates"]]
    if start is not None:
        days = [d for d in days if d >= start]
    if end is not None:
        days = [d for d in days if d <= end]
    if not days:
        raise ValueError("that date range does not overlap the scenario window "
                         "({} to {})".format(scenario.inputs["dates"][0],
                                             scenario.inputs["dates"][-1]))

    bbl = float(bbl or 0.0)
    if bbl < 0:
        raise ValueError("a charge cannot be negative")

    # ------------------------------------------------------------- crude row
    if target.upper() == "CRUDE":
        existing = {c.date: c for c in db.query(CrudeDay).filter(
            CrudeDay.scenario_id == scenario_id)}
        written = 0
        for d in days:
            row = existing.get(d)
            if row is None:
                if not bbl and mode is None:
                    continue                    # nothing there to clear
                db.add(CrudeDay(scenario_id=scenario_id, date=d, bbl=bbl,
                                mode=mode))
            else:
                if not overwrite and row.bbl:
                    continue
                row.bbl = bbl
                if mode is not None:
                    row.mode = mode
            written += 1
        return _fill_done(db, scenario, actor, target, bbl, days, written, 0)

    # -------------------------------------------------------- lines and units
    if "#" in target:
        keys = [target]
        unit = target.split("#")[0]
    else:
        unit = target
        if live_keys is None:
            raise ValueError("filling a whole unit needs the list of live lines")
        keys = sorted(k for k in live_keys if k.split("#")[0] == unit)
        if not keys:
            raise ValueError("{} has no charge lines".format(unit))
        if bbl:
            raise ValueError(
                "{} has {} charge lines, and writing {:,.0f} bbl onto all of "
                "them would run every one of them at once. Name a single line "
                "to set a rate, or set the rate to 0 to clear the unit."
                .format(unit, len(keys), bbl))
    if live_keys is not None:
        dead = [k for k in keys if k not in live_keys]
        if dead:
            raise ValueError("{} is retired and is not scheduled"
                             .format(", ".join(dead)))

    # Only a *rate* is kept out of an outage. Clearing one is the opposite of
    # the mistake this guard exists for: a charge already sitting on a day the
    # unit is down is invisible to the optimizer and survives into the result,
    # so a clear that skipped those days would leave behind exactly the cells
    # worth removing.
    down = (downtime_days(db, scenario_id).get(unit, set())
            if skip_downtime and bbl else set())
    targets = [d for d in days if d not in down]
    skipped = len(days) - len(targets)

    # The siblings this fill has to clear, so one unit does not end up charging
    # two feeds on the same day.
    family = sorted(k for k in (live_keys or ())
                    if k.split("#")[0] == unit) or list(keys)
    siblings: List[str] = []
    if exclusive and bbl:
        siblings = [k for k in family if k not in keys]

    entries = {}
    for e in db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id == scenario_id,
            ScheduleEntry.line_key.in_(family)):
        entries[(e.line_key, e.date)] = e

    # "Only days that are empty" has to mean the *unit* is idle that day, not
    # that this one line is. A unit runs one feed at a time, so a day where
    # another line is scheduled is a day this line is deliberately not running -
    # and writing into it would clear that line and destroy the schedule the
    # backfill was called to complete. This is the rolling case: a copied
    # schedule covers the near term and runs out, and filling the tail must not
    # touch the head.
    if not overwrite and bbl:
        occupied = {d for (k, d), e in entries.items() if e.bbl and k in family}
        targets = [d for d in targets if d not in occupied]

    written = 0
    #: Days the target line ends up carrying the rate - not the days this call
    #: changed. The siblings are cleared against *this*, so `overwrite=False`
    #: cannot clear a sibling on a day it declined to write: backfilling the gaps
    #: in a copied schedule would otherwise delete the schedule it was filling
    #: around, which is the opposite of what asking for empty days only means.
    set_days = set()
    for key in keys:
        for d in targets:
            row = entries.get((key, d))
            if row is None:
                if not bbl:
                    continue                    # already empty
                db.add(ScheduleEntry(scenario_id=scenario_id, line_key=key,
                                     date=d, bbl=bbl))
            elif not overwrite and row.bbl:
                continue                        # left as the planner had it
            elif row.bbl == bbl:
                set_days.add(d)                 # already at the rate
                continue
            else:
                row.bbl = bbl
            set_days.add(d)
            written += 1

    cleared = 0
    for key in siblings:
        for d in set_days:
            row = entries.get((key, d))
            if row is not None and row.bbl:
                row.bbl = 0.0
                cleared += 1

    return _fill_done(db, scenario, actor, target, bbl, days, written, skipped,
                      cleared=cleared, siblings=siblings)


def _fill_done(db: Session, scenario: Scenario, actor: str, target: str,
               bbl: float, days: List[dt.date], written: int, skipped: int,
               cleared: int = 0, siblings: Optional[List[str]] = None
               ) -> Dict[str, Any]:
    scenario.schedule_version += 1
    db.add(AuditEvent(actor=actor, action="schedule.fill",
                      target=str(scenario.id),
                      detail={"target": target, "bbl": bbl,
                              "start": days[0].isoformat(),
                              "end": days[-1].isoformat(),
                              "cells": written, "skipped_downtime": skipped,
                              "siblings_cleared": cleared}))
    db.commit()
    invalidate(scenario.id)
    return {"ok": True, "target": target, "bbl": bbl,
            "start": days[0].isoformat(), "end": days[-1].isoformat(),
            "days": len(days), "cells": written, "skipped_downtime": skipped,
            "siblings_cleared": cleared, "siblings": siblings or [],
            "schedule_version": scenario.schedule_version}


# ------------------------------------------------------------ planned downtime
def list_downtime(db: Session, scenario_id: int) -> List[Dict[str, Any]]:
    rows = (db.query(PlannedDowntime)
            .filter(PlannedDowntime.scenario_id == scenario_id)
            .order_by(PlannedDowntime.unit, PlannedDowntime.start_date).all())
    return [{"id": d.id, "unit": d.unit, "start": d.start_date.isoformat(),
             "end": d.end_date.isoformat(), "days": d.days, "reason": d.reason,
             "status": d.status, "created_by": d.created_by}
            for d in rows]


def add_downtime(db: Session, scenario_id: int, unit: str, start: dt.date,
                 end: dt.date, reason: str = "", actor: str = "planner",
                 status: str = "confirmed") -> PlannedDowntime:
    if end < start:
        raise ValueError("end date is before start date")
    row = PlannedDowntime(scenario_id=scenario_id, unit=unit, start_date=start,
                          end_date=end, reason=reason, status=status,
                          created_by=actor)
    db.add(row)
    db.add(AuditEvent(actor=actor, action="downtime.add",
                      target="{}:{}".format(scenario_id, unit),
                      detail={"start": start.isoformat(), "end": end.isoformat(),
                              "reason": reason}))
    _bump(db, scenario_id)
    db.commit()
    return row


def remove_downtime(db: Session, downtime_id: int, actor: str = "planner") -> int:
    row = db.query(PlannedDowntime).get(downtime_id)
    if row is None:
        raise KeyError("no downtime {}".format(downtime_id))
    scenario_id = row.scenario_id
    db.add(AuditEvent(actor=actor, action="downtime.remove",
                      target="{}:{}".format(scenario_id, row.unit),
                      detail={"start": row.start_date.isoformat(),
                              "end": row.end_date.isoformat()}))
    db.delete(row)
    _bump(db, scenario_id)
    db.commit()
    return scenario_id


def toggle_downtime_day(db: Session, scenario_id: int, unit: str, day: dt.date,
                        actor: str = "planner") -> bool:
    """Flip a single day on or off. Returns True if the day is now down.

    Removing a day inside a longer window splits it, so a planner can clear one
    day of a turnaround without having to delete and re-enter the whole range.
    """
    rows = (db.query(PlannedDowntime)
            .filter(PlannedDowntime.scenario_id == scenario_id,
                    PlannedDowntime.unit == unit,
                    PlannedDowntime.start_date <= day,
                    PlannedDowntime.end_date >= day).all())
    if not rows:
        add_downtime(db, scenario_id, unit, day, day, reason="", actor=actor)
        return True

    for row in rows:
        start, end = row.start_date, row.end_date
        db.delete(row)
        if start < day:
            db.add(PlannedDowntime(scenario_id=scenario_id, unit=unit,
                                   start_date=start,
                                   end_date=day - dt.timedelta(days=1),
                                   reason=row.reason, status=row.status,
                                   created_by=row.created_by))
        if end > day:
            db.add(PlannedDowntime(scenario_id=scenario_id, unit=unit,
                                   start_date=day + dt.timedelta(days=1),
                                   end_date=end, reason=row.reason,
                                   status=row.status, created_by=row.created_by))
    db.add(AuditEvent(actor=actor, action="downtime.clear_day",
                      target="{}:{}".format(scenario_id, unit),
                      detail={"date": day.isoformat()}))
    _bump(db, scenario_id)
    db.commit()
    return False


def downtime_days(db: Session, scenario_id: int) -> Dict[str, set]:
    """{unit: {date, ...}} - every day each unit is unavailable."""
    out: Dict[str, set] = defaultdict(set)
    for d in db.query(PlannedDowntime).filter(
            PlannedDowntime.scenario_id == scenario_id):
        day = d.start_date
        while day <= d.end_date:
            out[d.unit].add(day)
            day += dt.timedelta(days=1)
    return dict(out)


def _bump(db: Session, scenario_id: int) -> None:
    """Downtime changes what the plan can do, so invalidate the simulation."""
    scenario = db.query(Scenario).get(scenario_id)
    if scenario is not None:
        scenario.schedule_version += 1
    invalidate(scenario_id)


def _d(iso: str) -> dt.date:
    y, m, d = (int(x) for x in iso.split("-"))
    return dt.date(y, m, d)


# ----------------------------------------------------------------- simulation
_sim_cache: Dict[Tuple[int, int, int], Simulation] = {}
_ref_cache: Dict[Tuple[int, int], EngineReference] = {}


def engine_reference(db: Session, reference_version_id: int) -> EngineReference:
    """The reference with planner edits applied.

    Keyed on the override counter as well as the version, so editing a yield
    invalidates the cache and the next projection picks it up.
    """
    from .reference_edit import apply_overrides

    rv = db.query(ReferenceVersion).get(reference_version_id)
    key = (reference_version_id, rv.overrides_version or 0)
    if key not in _ref_cache:
        _ref_cache.clear()
        _ref_cache[key] = EngineReference(apply_overrides(db, rv))
    return _ref_cache[key]


def engine_scenario(db: Session, scenario: Scenario) -> EngineScenario:
    lines: Dict[str, Dict[str, float]] = defaultdict(dict)
    for e in db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id == scenario.id):
        if e.bbl:
            lines[e.line_key][e.date.isoformat()] = e.bbl

    crude_bbl: Dict[str, float] = {}
    crude_mode: Dict[str, str] = {}
    for c in db.query(CrudeDay).filter(CrudeDay.scenario_id == scenario.id):
        if c.bbl:
            crude_bbl[c.date.isoformat()] = c.bbl
        if c.mode:
            crude_mode[c.date.isoformat()] = c.mode

    data = dict(scenario.inputs)
    data["meta"] = {"name": scenario.name, "as_of": scenario.as_of.isoformat()}
    data["charge_schedule"] = {"crude_bbl_per_day": crude_bbl,
                               "crude_mode": crude_mode,
                               "lines": {k: v for k, v in lines.items()}}
    return EngineScenario(data)


def simulate_scenario(db: Session, scenario: Scenario,
                      strict_workbook: bool = True) -> Simulation:
    rv = db.query(ReferenceVersion).get(scenario.reference_version_id)
    # Reference edits change every projection, so they belong in the cache key
    # alongside the schedule.
    key = (scenario.id, scenario.schedule_version, rv.overrides_version or 0)
    if key not in _sim_cache:
        _sim_cache.clear()   # single-scenario working set; keeps memory bounded
        ref = engine_reference(db, scenario.reference_version_id)
        _sim_cache[key] = simulate(ref, engine_scenario(db, scenario),
                                   strict_workbook=strict_workbook)
    return _sim_cache[key]


def invalidate(scenario_id: int) -> None:
    for key in list(_sim_cache):
        if key[0] == scenario_id:
            del _sim_cache[key]
