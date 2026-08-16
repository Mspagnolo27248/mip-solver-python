"""FastAPI application: feeds, scenarios, projections, schedule."""
from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import exporter, groups
from ..db import optimizer_service as optsvc
from ..db import reference_edit as refedit
from ..db import service as svc
from ..db.models import (CrudeDay, Feed, OptimizerRun, RawBatch, RefKind,
                         Scenario, ScheduleEntry, StagingCell)
from ..db.session import get_session, init_db
from ..layout import CRUDE_BLOCK_ID as L_CRUDE_BLOCK_ID

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", ".."))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Refinery Inventory Planner", version="0.1.0",
              lifespan=lifespan)


# ------------------------------------------------------------------- schemas
class OverrideIn(BaseModel):
    value: Optional[float] = None
    reason: str = ""
    actor: str = "planner"


class ScenarioIn(BaseModel):
    name: str
    as_of: Optional[dt.date] = None
    horizon_days: int = 366
    note: str = ""
    actor: str = "planner"
    copy_schedule_from: Optional[int] = None


class ScheduleEdit(BaseModel):
    line_key: str
    date: dt.date
    bbl: float
    actor: str = "planner"


class OptimizeRunIn(BaseModel):
    scenario_id: int
    actor: str = "planner"


class RefEditIn(BaseModel):
    k1: str
    k2: Optional[str] = None
    value: Optional[float] = None
    reason: str = ""
    actor: str = "planner"


class DowntimeIn(BaseModel):
    unit: str
    start: dt.date
    end: dt.date
    reason: str = ""
    actor: str = "planner"


class DowntimeDayIn(BaseModel):
    unit: str
    date: dt.date
    actor: str = "planner"


class CrudeEdit(BaseModel):
    date: dt.date
    bbl: Optional[float] = None
    mode: Optional[str] = None
    actor: str = "planner"


# -------------------------------------------------------------------- health
@app.get("/api/health")
def health(db: Session = Depends(get_session)) -> Dict[str, Any]:
    ref = svc.active_reference(db)
    return {
        "status": "ok",
        "reference_loaded": ref is not None,
        "reference_version": ref.id if ref else None,
        "blocks": len(ref.payload.get("blocks", [])) if ref else 0,
        "scenarios": db.query(Scenario).count(),
        "staging_cells": db.query(StagingCell).count(),
    }


# --------------------------------------------------------------------- feeds
@app.get("/api/feeds")
def list_feeds(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return svc.feed_summary(db)


@app.get("/api/feeds/{feed}/rows")
def feed_rows(feed: str, search: str = "", only: str = "all",
              limit: int = Query(500, le=5000), offset: int = 0,
              db: Session = Depends(get_session)) -> Dict[str, Any]:
    if feed not in Feed.ALL:
        raise HTTPException(404, "unknown feed {}".format(feed))
    return svc.staging_rows(db, feed, search, only, limit, offset)


@app.patch("/api/staging/{cell_id}")
def patch_cell(cell_id: int, body: OverrideIn,
               db: Session = Depends(get_session)) -> Dict[str, Any]:
    try:
        cell = svc.set_override(db, cell_id, body.value, body.actor, body.reason)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"id": cell.id, "effective_value": cell.effective_value,
            "has_override": cell.has_override, "is_stale": cell.is_stale}


@app.post("/api/feeds/{feed}/sync")
def sync_one(feed: str, actor: str = "planner",
             db: Session = Depends(get_session)) -> Dict[str, Any]:
    from ..db.connectors import default_connectors
    conn = next((c for c in default_connectors(svc.SEED_DIR) if c.feed == feed), None)
    if conn is None:
        raise HTTPException(404, "no connector for feed {}".format(feed))
    batch = svc.sync_feed(db, conn, actor)
    return {"batch": batch.id, "rows": batch.row_count, "meta": batch.meta}


@app.post("/api/feeds/sync-all")
def sync_everything(actor: str = "planner",
                    db: Session = Depends(get_session)) -> Dict[str, Any]:
    batches = svc.sync_all(db, actor)
    return {"batches": [{"feed": b.feed, "id": b.id, "rows": b.row_count}
                        for b in batches]}


@app.get("/api/feeds/{feed}/batches")
def feed_batches(feed: str, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    rows = (db.query(RawBatch).filter(RawBatch.feed == feed)
            .order_by(RawBatch.id.desc()).limit(25).all())
    return [{"id": b.id, "source": b.source, "fetched_at": b.fetched_at.isoformat(),
             "rows": b.row_count, "status": b.status, "meta": b.meta} for b in rows]


# ------------------------------------------------------------ reference data
@app.get("/api/reference")
def reference_summary(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """The model inputs a planner can adjust: yields, rates, capacities, limits."""
    rv = svc.active_reference(db)
    if rv is None:
        raise HTTPException(400, "no reference loaded")
    return {"version": rv.id, "source": rv.source,
            "edits": rv.overrides_version or 0,
            "groups": refedit.summary(db, rv)}


@app.get("/api/reference/{kind}")
def reference_values(kind: str, search: str = "", only_edited: bool = False,
                     db: Session = Depends(get_session)) -> Dict[str, Any]:
    if kind not in RefKind.ALL:
        raise HTTPException(404, "unknown reference kind {}".format(kind))
    rv = svc.active_reference(db)
    if rv is None:
        raise HTTPException(400, "no reference loaded")
    return refedit.list_values(db, rv, kind, search, only_edited)


@app.patch("/api/reference/{kind}")
def patch_reference(kind: str, body: RefEditIn,
                    db: Session = Depends(get_session)) -> Dict[str, Any]:
    if kind not in RefKind.ALL:
        raise HTTPException(404, "unknown reference kind {}".format(kind))
    rv = svc.active_reference(db)
    if rv is None:
        raise HTTPException(400, "no reference loaded")
    out = refedit.set_override(db, rv, kind, body.k1, body.k2, body.value,
                               body.actor, body.reason)
    # every projection rests on these, so drop the cached simulations
    for scenario in db.query(Scenario).all():
        svc.invalidate(scenario.id)
    return out


# ------------------------------------------------------------------ optimizer
@app.get("/api/optimizer/params")
def get_optimizer_params(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Inputs the optimizer needs and the manual planner does not."""
    row = optsvc.active_params(db)
    return {"params": optsvc.params_dict(row), "schema": optsvc.params_schema()}


@app.patch("/api/optimizer/params")
def patch_optimizer_params(body: Dict[str, Any],
                           db: Session = Depends(get_session)) -> Dict[str, Any]:
    actor = body.pop("actor", "planner")
    row = optsvc.update_params(db, body, actor)
    return optsvc.params_dict(row)


@app.get("/api/optimizer/runs")
def get_optimizer_runs(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return optsvc.list_runs(db)


@app.post("/api/optimizer/run")
def post_optimizer_run(body: OptimizeRunIn,
                       db: Session = Depends(get_session)) -> Dict[str, Any]:
    p = optsvc.active_params(db)
    if p.missing():
        raise HTTPException(400, "missing optimizer inputs: {}".format(
            ", ".join(p.missing())))
    try:
        run = optsvc.run(db, body.scenario_id, body.actor)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return optsvc.list_runs(db)[0] if run else {}


# ----------------------------------------------------------------- scenarios
def _scenario_dict(s: Scenario) -> Dict[str, Any]:
    return {"id": s.id, "name": s.name, "as_of": s.as_of.isoformat(),
            "horizon_days": s.horizon_days, "status": s.status,
            "created_at": s.created_at.isoformat(), "created_by": s.created_by,
            "note": s.note, "schedule_version": s.schedule_version,
            "reference_version_id": s.reference_version_id}


@app.get("/api/scenarios")
def list_scenarios(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return [_scenario_dict(s) for s in
            db.query(Scenario).order_by(Scenario.id.desc()).all()]


@app.post("/api/scenarios")
def create_scenario(body: ScenarioIn,
                    db: Session = Depends(get_session)) -> Dict[str, Any]:
    ref = svc.active_reference(db)
    if ref is None:
        raise HTTPException(400, "no reference loaded; run scripts/init_db.py")
    as_of = body.as_of
    if as_of is None:
        default = ref.payload.get("scenario_defaults", {}).get("as_of")
        as_of = svc._d(default) if default else dt.date.today()
    s = svc.create_scenario(db, body.name, as_of, body.horizon_days, body.actor,
                            body.note, body.copy_schedule_from)
    return _scenario_dict(s)


def _get_scenario(db: Session, scenario_id: int) -> Scenario:
    s = db.query(Scenario).get(scenario_id)
    if s is None:
        raise HTTPException(404, "no scenario {}".format(scenario_id))
    return s


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    out = _scenario_dict(_get_scenario(db, scenario_id))
    # The warm start / result pair, so the schedule view can offer the other half
    # without the planner hunting for its id.
    out["pair"] = optsvc.pair_for(db, scenario_id)
    return out


@app.get("/api/scenarios/{scenario_id}/products")
def scenario_products(scenario_id: int,
                      db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Every product block the projection can show."""
    s = _get_scenario(db, scenario_id)
    ref = svc.engine_reference(db, s.reference_version_id)
    products = ref.products
    targets = ref.inventory_targets
    out = []
    for b in ref.blocks:
        code = b.get("charge_code")
        name = (products.get(code, {}) or {}).get("name") or b.get("label") or code
        t = targets.get(code) or targets.get(b.get("sold_code") or "") or {}
        out.append({"block_id": b["id"], "sheet": b["sheet"], "code": code,
                    "sold_code": b.get("sold_code"), "name": name,
                    "lcl": t.get("lcl"), "ucl": t.get("ucl")})
    return out


@app.get("/api/scenarios/{scenario_id}/projection")
def projection(scenario_id: int, block_id: str,
               days: int = Query(120, le=400), offset: int = 0,
               db: Session = Depends(get_session)) -> Dict[str, Any]:
    s = _get_scenario(db, scenario_id)
    sim = svc.simulate_scenario(db, s)
    ref = svc.engine_reference(db, s.reference_version_id)
    block = next((b for b in ref.blocks if b["id"] == block_id), None)
    if block is None:
        raise HTTPException(404, "no block {}".format(block_id))
    bal = sim.balances.get(block_id, {})

    scn_dates = [svc._d(x) for x in s.inputs["dates"]]
    window = scn_dates[offset:offset + days]
    rows = []
    for d in window:
        rows.append({
            "date": d.isoformat(),
            "begin": bal.get("Begin Inventory", {}).get(d, 0.0),
            "production_in": bal.get("Production In", {}).get(d, 0.0),
            "sales": bal.get("Sales", {}).get(d, 0.0),
            "forecast": bal.get("Forecast", {}).get(d, 0.0),
            "blends": bal.get("Blends", {}).get(d, 0.0),
            "net_available": bal.get("Net Charge Available", {}).get(d, 0.0),
            "production_out": bal.get("Production Out", {}).get(d, 0.0),
            "out_to_diesel": bal.get("Out to Diesel", {}).get(d, 0.0),
            "downgrade": bal.get("Downgrade", {}).get(d, 0.0),
            "end": bal.get("End Inventory", {}).get(d, 0.0),
            "capacity": bal.get("Tank Capacity", {}).get(d, 0.0),
            "excess": bal.get("Excess Capacity", {}).get(d, 0.0),
        })

    code = block.get("charge_code")
    t = ref.inventory_targets.get(code) or {}
    name = (ref.products.get(code, {}) or {}).get("name") or block.get("label")
    return {"block_id": block_id, "code": code, "name": name,
            "sheet": block["sheet"], "lcl": t.get("lcl"), "ucl": t.get("ucl"),
            "rows": rows, "total_days": len(scn_dates)}


def _canonical_row(label: str) -> str:
    """Map a sheet's row label onto the engine's canonical measure name.

    Labels vary between blocks (LLN calls its production row
    "Production Out &DG DFO" because it folds the downgrade in), but the engine
    keys every block on the same set of measures.
    """
    for base in ("Production Out", "End Inventory", "Production In"):
        if label.startswith(base):
            return base
    return label


#: Rows a planner scans first when reading a stream sheet.
KEY_ROWS = ["Begin Inventory", "Production In", "Sales", "Forecast",
            "Production Out", "End Inventory", "Excess Capacity"]

CRUDE_ROWS = ["Begin Inventory", "Receipts", "Net Charge Available",
              "Production Out", "End Inventory", "Tank Capacity",
              "Excess Capacity"]


@app.get("/api/scenarios/{scenario_id}/streams")
def list_streams(scenario_id: int,
                 db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    s = _get_scenario(db, scenario_id)
    ref = svc.engine_reference(db, s.reference_version_id)
    counts: Dict[str, int] = defaultdict(int)
    for b in ref.blocks:
        counts[b["sheet"]] += 1
    out = [{"sheet": sheet, "products": n, "unit": "gal"}
           for sheet, n in counts.items()]
    out.append({"sheet": "CRUDE", "products": 1, "unit": "bbl"})
    return out


@app.get("/api/scenarios/{scenario_id}/stream")
def stream_sheet(scenario_id: int, sheet: str,
                 days: int = Query(21, le=120), offset: int = 0,
                 key_rows_only: bool = False,
                 db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Every product on one stream sheet, with its full balance block by date.

    This is the workbook's own layout: products stacked down the page, measures
    within each product, dates across. It is the view planners read the model in.
    """
    s = _get_scenario(db, scenario_id)
    sim = svc.simulate_scenario(db, s)
    ref = svc.engine_reference(db, s.reference_version_id)

    window = [svc._d(x) for x in s.inputs["dates"]][offset:offset + days]

    if sheet.upper() == "CRUDE":
        blocks_spec = [{"id": "CRUDE:14", "sheet": "CRUDE", "charge_code": "8000",
                        "sold_code": None, "label": "Crude oil",
                        "row_order": CRUDE_ROWS}]
        unit = "bbl"
    else:
        blocks_spec = sorted((b for b in ref.blocks if b["sheet"] == sheet),
                             key=lambda b: b["first_row"])
        unit = "gal"
    if not blocks_spec:
        raise HTTPException(404, "no stream {}".format(sheet))

    out_blocks = []
    for b in blocks_spec:
        bal = sim.balances.get(b["id"], {})
        code = b.get("charge_code")
        name = (ref.products.get(code, {}) or {}).get("name") or b.get("label") or code
        t = ref.inventory_targets.get(code) or {}

        labels = [lbl for lbl in b.get("row_order", []) if _canonical_row(lbl) in bal]
        if key_rows_only:
            labels = [lbl for lbl in labels if _canonical_row(lbl) in KEY_ROWS]

        rows = []
        for lbl in labels:
            series = bal.get(_canonical_row(lbl), {})
            rows.append({"label": lbl, "measure": _canonical_row(lbl),
                         "values": [series.get(d, 0.0) for d in window]})

        end = bal.get("End Inventory", {})
        cap = bal.get("Tank Capacity", {})
        out_blocks.append({
            "block_id": b["id"], "code": code, "sold_code": b.get("sold_code"),
            "name": name, "lcl": t.get("lcl"), "ucl": t.get("ucl"),
            "capacity": cap.get(window[0], 0.0) if window else 0.0,
            "rows": rows,
            "flags": [{"date": d.isoformat(),
                       "over": bool(cap.get(d, 0.0) > 0 and end.get(d, 0.0) > cap.get(d, 0.0)),
                       "dry": bool(end.get(d, 0.0) < 0)}
                      for d in window],
        })

    return {"sheet": sheet, "unit": unit,
            "dates": [d.isoformat() for d in window],
            "offset": offset, "total_days": len(s.inputs["dates"]),
            "blocks": out_blocks}


@app.get("/api/dashboards")
def dashboard_groups() -> List[Dict[str, Any]]:
    return [{"id": g["id"], "title": g["title"], "blurb": g.get("blurb", "")}
            for g in groups.all_groups()]


@app.get("/api/scenarios/{scenario_id}/dashboard")
def dashboard(scenario_id: int, group: str = "base_oil",
              days: int = Query(180, le=400), offset: int = 0,
              max_points: int = Query(180, ge=30, le=400),
              db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Chart series for every product in a family, grouped into stream sections.

    Series are downsampled to keep the payload small when a full year is
    requested, but the badges (overflow days, dry days, peak, trough) are computed
    over every day in the window so nothing is missed between sample points.
    """
    s = _get_scenario(db, scenario_id)
    sim = svc.simulate_scenario(db, s)
    ref = svc.engine_reference(db, s.reference_version_id)

    spec = groups.group_by_id(group)
    if spec is None:
        raise HTTPException(404, "no dashboard group {}".format(group))

    window = [svc._d(x) for x in s.inputs["dates"]][offset:offset + days]
    if not window:
        raise HTTPException(400, "empty date window")
    step = max(1, len(window) // max_points)
    sampled = window[::step]
    if sampled[-1] != window[-1]:
        sampled.append(window[-1])

    specs = list(ref.blocks)
    if group == "crude":
        specs = [{"id": L_CRUDE_BLOCK_ID, "sheet": "CRUDE", "charge_code": "8000",
                  "label": "Crude oil"}]

    sections: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in specs:
        code = b.get("charge_code")
        if groups.group_for(code, b["sheet"]) != group:
            continue
        bal = sim.balances.get(b["id"], {})
        end = bal.get("End Inventory", {})
        cap = bal.get("Tank Capacity", {})
        if not end:
            continue

        values = [end.get(d, 0.0) for d in window]
        caps = [cap.get(d, 0.0) for d in window]
        overflow = sum(1 for v, c in zip(values, caps) if c > 0 and v > c)
        dry = sum(1 for v in values if v < 0)
        t = ref.inventory_targets.get(code) or {}
        name = (ref.products.get(code, {}) or {}).get("name") or b.get("label") or code

        sections[b["sheet"]].append({
            "block_id": b["id"], "code": code, "name": name, "sheet": b["sheet"],
            "unit": "bbl" if b["sheet"].upper() == "CRUDE" else "gal",
            "dates": [d.isoformat() for d in sampled],
            "end": [end.get(d, 0.0) for d in sampled],
            "capacity": [cap.get(d, 0.0) for d in sampled],
            "lcl": t.get("lcl"), "ucl": t.get("ucl"),
            "peak": max(values), "trough": min(values),
            "overflow_days": overflow, "dry_days": dry,
        })

    out = [{"stream": sheet, "products": items}
           for sheet, items in sorted(sections.items(),
                                      key=lambda kv: groups.stream_sort_key(kv[0]))]
    return {"group": group, "title": spec["title"], "blurb": spec.get("blurb", ""),
            "days": len(window), "step": step,
            "from": window[0].isoformat(), "to": window[-1].isoformat(),
            "total_days": len(s.inputs["dates"]), "sections": out}


@app.get("/api/scenarios/{scenario_id}/alerts")
def alerts(scenario_id: int, days: int = Query(30, le=400),
           db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Where the plan runs out of tank or runs out of product.

    This is the signal the workbook exists to produce: which products need a
    downgrade because they will overflow, and which will run dry.
    """
    s = _get_scenario(db, scenario_id)
    sim = svc.simulate_scenario(db, s)
    ref = svc.engine_reference(db, s.reference_version_id)
    window = [svc._d(x) for x in s.inputs["dates"]][:days]

    out = []
    for b in ref.blocks:
        bal = sim.balances.get(b["id"], {})
        end = bal.get("End Inventory", {})
        cap = bal.get("Tank Capacity", {})
        code = b.get("charge_code")
        t = ref.inventory_targets.get(code) or {}
        lcl, ucl = t.get("lcl"), t.get("ucl")

        overflow = [d for d in window
                    if cap.get(d, 0.0) > 0 and end.get(d, 0.0) > cap.get(d, 0.0)]
        stockout = [d for d in window if end.get(d, 0.0) < 0]
        below = [d for d in window if lcl is not None and 0 <= end.get(d, 0.0) < lcl]
        above = [d for d in window
                 if ucl is not None and end.get(d, 0.0) > ucl] if ucl else []
        if not (overflow or stockout or below or above):
            continue
        name = (ref.products.get(code, {}) or {}).get("name") or b.get("label")
        peak = max((end.get(d, 0.0) for d in window), default=0.0)
        trough = min((end.get(d, 0.0) for d in window), default=0.0)
        out.append({
            "block_id": b["id"], "code": code, "name": name, "sheet": b["sheet"],
            "capacity": cap.get(window[0], 0.0) if window else 0.0,
            "peak": peak, "trough": trough,
            "overflow_days": len(overflow),
            "first_overflow": overflow[0].isoformat() if overflow else None,
            "stockout_days": len(stockout),
            "first_stockout": stockout[0].isoformat() if stockout else None,
            "below_lcl_days": len(below), "above_ucl_days": len(above),
        })

    severity = {"stockout": 0, "overflow": 1, "band": 2}

    def rank(a: Dict[str, Any]) -> tuple:
        if a["stockout_days"]:
            return (severity["stockout"], -a["stockout_days"])
        if a["overflow_days"]:
            return (severity["overflow"], -a["overflow_days"])
        return (severity["band"], -(a["below_lcl_days"] + a["above_ucl_days"]))

    out.sort(key=rank)
    return {"window_days": len(window), "alerts": out}


# ------------------------------------------------------------------ schedule
@app.get("/api/scenarios/{scenario_id}/schedule")
def get_schedule(scenario_id: int, days: int = Query(42, le=400), offset: int = 0,
                 compare: Optional[int] = None,
                 db: Session = Depends(get_session)) -> Dict[str, Any]:
    """The charge grid, optionally alongside another scenario's.

    `compare` returns a second value per cell so the planner can hold their own
    schedule against the optimizer's without leaving the grid - which is the only
    way to judge a proposed schedule, since the interesting part is never the
    total but which day it moved a campaign to.
    """
    s = _get_scenario(db, scenario_id)
    ref = svc.engine_reference(db, s.reference_version_id)
    dates = [svc._d(x) for x in s.inputs["dates"]][offset:offset + days]
    dset = set(dates)

    entries: Dict[str, Dict[str, float]] = defaultdict(dict)
    for e in db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id == s.id):
        if e.date in dset and e.bbl:
            entries[e.line_key][e.date.isoformat()] = e.bbl

    other: Dict[str, Dict[str, float]] = defaultdict(dict)
    compare_with = None
    if compare and compare != scenario_id:
        compare_with = db.query(Scenario).get(compare)
        if compare_with is not None:
            for e in db.query(ScheduleEntry).filter(
                    ScheduleEntry.scenario_id == compare):
                if e.date in dset and e.bbl:
                    other[e.line_key][e.date.isoformat()] = e.bbl

    crude = {}
    for c in db.query(CrudeDay).filter(CrudeDay.scenario_id == s.id):
        if c.date in dset:
            crude[c.date.isoformat()] = {"bbl": c.bbl, "mode": c.mode}

    units: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for line in ref.charge_lines:
        row = {
            "key": line["key"], "code": line["code"], "name": line["name"],
            "values": entries.get(line["key"], {}),
        }
        if compare_with is not None:
            row["compare"] = other.get(line["key"], {})
        units[line["unit"]].append(row)

    down = svc.downtime_days(db, s.id)
    out = {"dates": [d.isoformat() for d in dates],
           "crude": crude,
           "units": [{"unit": u, "lines": lines,
                      "downtime": sorted(x.isoformat() for x in down.get(u, set())
                                         if x in dset)}
                     for u, lines in units.items()],
           "downtime_windows": svc.list_downtime(db, s.id),
           "total_days": len(s.inputs["dates"])}
    if compare_with is not None:
        moved = 0
        for lines in units.values():
            for row in lines:
                keys = set(row["values"]) | set(row.get("compare", {}))
                moved += sum(
                    1 for k in keys
                    if abs(row["values"].get(k, 0.0)
                           - row.get("compare", {}).get(k, 0.0)) > 0.5)
        out["compare"] = {"scenario_id": compare_with.id,
                          "name": compare_with.name, "cells_changed": moved}
    return out


@app.get("/api/scenarios/{scenario_id}/schedule.xlsx")
def export_schedule(scenario_id: int, days: int = Query(42, le=400),
                    offset: int = 0,
                    db: Session = Depends(get_session)) -> Response:
    """The charge grid as blocks that paste into the planner's own workbook.

    One block per unit with its own target cell, because the rows between the
    unit blocks carry totals, date headers and notes that a single contiguous
    paste would flatten.
    """
    s = _get_scenario(db, scenario_id)
    ref = svc.engine_reference(db, s.reference_version_id)
    dates = [svc._d(x) for x in s.inputs["dates"]][offset:offset + days]
    if not dates:
        raise HTTPException(400, "no days in that window")
    dset = set(dates)

    values: Dict[str, Dict[Any, float]] = defaultdict(dict)
    for e in db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id == s.id):
        if e.date in dset and e.bbl:
            values[e.line_key][e.date] = e.bbl

    run = (db.query(OptimizerRun)
           .filter(OptimizerRun.result_scenario_id == s.id)
           .order_by(OptimizerRun.id.desc()).first())
    blob = exporter.schedule_workbook(
        ref.charge_lines, values, dates,
        title="Charge schedule - {}".format(s.name),
        subtitle=("Optimizer run {} ({}), warm started from scenario {}."
                  .format(run.id, (run.params or {}).get("model_version", "v0"),
                          run.base_scenario_id) if run else
                  "Built on the planning side."))
    name = "schedule-{}-{}-to-{}.xlsx".format(
        s.id, dates[0].isoformat(), dates[-1].isoformat())
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument."
                   "spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="{}"'.format(name)})


@app.patch("/api/scenarios/{scenario_id}/schedule")
def edit_schedule(scenario_id: int, body: ScheduleEdit,
                  db: Session = Depends(get_session)) -> Dict[str, Any]:
    s = _get_scenario(db, scenario_id)
    entry = (db.query(ScheduleEntry)
             .filter(ScheduleEntry.scenario_id == s.id,
                     ScheduleEntry.line_key == body.line_key,
                     ScheduleEntry.date == body.date).first())
    if entry is None:
        entry = ScheduleEntry(scenario_id=s.id, line_key=body.line_key,
                              date=body.date, bbl=body.bbl)
        db.add(entry)
    else:
        entry.bbl = body.bbl
    s.schedule_version += 1
    db.commit()
    svc.invalidate(s.id)
    return {"ok": True, "schedule_version": s.schedule_version}


@app.get("/api/scenarios/{scenario_id}/downtime")
def get_downtime(scenario_id: int,
                 db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Planned downtime, as ranges and as a per-day lookup for the grid."""
    _get_scenario(db, scenario_id)
    days = svc.downtime_days(db, scenario_id)
    return {"windows": svc.list_downtime(db, scenario_id),
            "days": {u: sorted(d.isoformat() for d in ds)
                     for u, ds in days.items()}}


@app.post("/api/scenarios/{scenario_id}/downtime")
def post_downtime(scenario_id: int, body: DowntimeIn,
                  db: Session = Depends(get_session)) -> Dict[str, Any]:
    _get_scenario(db, scenario_id)
    try:
        row = svc.add_downtime(db, scenario_id, body.unit, body.start, body.end,
                               body.reason, body.actor)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": row.id, "unit": row.unit, "days": row.days}


@app.delete("/api/scenarios/{scenario_id}/downtime/{downtime_id}")
def delete_downtime(scenario_id: int, downtime_id: int, actor: str = "planner",
                    db: Session = Depends(get_session)) -> Dict[str, Any]:
    try:
        svc.remove_downtime(db, downtime_id, actor)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.post("/api/scenarios/{scenario_id}/downtime/toggle")
def toggle_downtime(scenario_id: int, body: DowntimeDayIn,
                    db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Flip one unit-day. Clearing a day inside a window splits it."""
    _get_scenario(db, scenario_id)
    down = svc.toggle_downtime_day(db, scenario_id, body.unit, body.date,
                                   body.actor)
    return {"unit": body.unit, "date": body.date.isoformat(), "down": down}


@app.patch("/api/scenarios/{scenario_id}/crude")
def edit_crude(scenario_id: int, body: CrudeEdit,
               db: Session = Depends(get_session)) -> Dict[str, Any]:
    s = _get_scenario(db, scenario_id)
    day = (db.query(CrudeDay).filter(CrudeDay.scenario_id == s.id,
                                     CrudeDay.date == body.date).first())
    if day is None:
        day = CrudeDay(scenario_id=s.id, date=body.date, bbl=body.bbl or 0.0,
                       mode=body.mode)
        db.add(day)
    else:
        if body.bbl is not None:
            day.bbl = body.bbl
        if body.mode is not None:
            day.mode = body.mode
    s.schedule_version += 1
    db.commit()
    svc.invalidate(s.id)
    return {"ok": True, "schedule_version": s.schedule_version}


# ------------------------------------------------------------------- web app
if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(WEB_DIR, "index.html"))
