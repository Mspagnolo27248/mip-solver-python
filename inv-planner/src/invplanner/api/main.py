"""FastAPI application: feeds, scenarios, projections, schedule."""
from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import exporter, feedsheets, groups
from .. import model_config as cfg
from ..db import connectors
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


class ScheduleFill(BaseModel):
    """Bulk edit: one rate over a range of days.

    `target` is CRUDE, a line key like ROSE#97, or a bare unit name - the last
    only to clear it, since a unit runs one line at a time.
    """
    target: str
    bbl: float = 0.0
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    mode: Optional[str] = None
    #: Outage days are left alone. The optimizer creates no variable on them, so
    #: anything typed there survives into a result unnoticed.
    skip_downtime: bool = True
    #: Off, only days the *unit* is idle are written - a day another of its
    #: lines is scheduled on is not a gap, and writing into it would clear that
    #: line. This is how the tail of a copied schedule is filled without
    #: disturbing the part it covers.
    overwrite: bool = True
    #: Clear the other lines on the same unit over the same days - only on units
    #: that run one feed at a time (`cfg.ONE_FEED_UNITS`).
    exclusive: bool = True
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
    #: Sent only when Undo puts a removed window back, so a detected window -
    #: inferred from the workbook, never confirmed - returns as detected.
    status: Optional[str] = None


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
    conn = next((c for c in connectors.default_connectors(svc.SEED_DIR)
                 if c.feed == feed), None)
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


# ------------------------------------------------------- uploaded feed sheets
#: A feed export is tens of kilobytes; the planning workbook is 6 MB. The cap is
#: generous enough for a bad export and small enough that nothing has to stream.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _upload_state() -> List[Dict[str, Any]]:
    out = []
    for kind in feedsheets.KINDS:
        path = connectors.upload_path(svc.SEED_DIR, kind)
        loaded = os.path.exists(path)
        out.append({
            "kind": kind,
            "title": feedsheets.KIND_TITLES[kind],
            "shape": feedsheets.KIND_SHAPES[kind],
            "feeds": list(feedsheets.KIND_FEEDS[kind]),
            "loaded": loaded,
            "uploaded_at": (dt.datetime.fromtimestamp(os.path.getmtime(path))
                            .isoformat() if loaded else None),
            "bytes": os.path.getsize(path) if loaded else None,
        })
    return out


@app.get("/api/feeds/uploads")
def list_feed_uploads() -> Dict[str, Any]:
    """Which feeds are reading from an uploaded sheet rather than the workbook."""
    return {"kinds": _upload_state(),
            "dir": connectors.upload_dir(svc.SEED_DIR)}


@app.post("/api/feeds/upload/{kind}")
async def upload_feed_sheet(kind: str, request: Request, actor: str = "planner",
                            db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Take one feed workbook as the raw request body and sync what it supplies.

    The body is the file itself rather than a multipart form: the browser sends
    it with `fetch(url, {method: 'POST', body: file})` in one line, and it keeps
    python-multipart off the dependency list.

    The upload is parsed before it replaces anything, so a sheet whose columns
    have moved is rejected while the feed that currently works keeps working.
    """
    if kind not in feedsheets.KIND_FEEDS:
        raise HTTPException(404, "unknown sheet kind {!r}; expected one of {}".format(
            kind, ", ".join(feedsheets.KINDS)))
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty upload")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "upload is {:.1f} MB; the limit is {} MB".format(
            len(body) / 1e6, MAX_UPLOAD_BYTES // (1024 * 1024)))

    dest = connectors.upload_path(svc.SEED_DIR, kind)
    # Held in a subfolder rather than under a ".part" suffix: openpyxl refuses a
    # file whose extension it does not know, and the name it is parsed under is
    # the name any complaint about the sheet will quote back to the planner.
    staged = os.path.join(os.path.dirname(dest), "incoming",
                          os.path.basename(dest))
    os.makedirs(os.path.dirname(staged), exist_ok=True)
    with open(staged, "wb") as fh:
        fh.write(body)
    try:
        parsed = feedsheets.read(kind, staged)
    except feedsheets.FeedSheetError as exc:
        os.remove(staged)
        raise HTTPException(400, str(exc))
    os.replace(staged, dest)

    conns = {c.feed: c for c in connectors.default_connectors(svc.SEED_DIR)}
    results = []
    for feed in feedsheets.KIND_FEEDS[kind]:
        batch = svc.sync_feed(db, conns[feed], actor)
        results.append({"feed": feed, "batch": batch.id,
                        "rows": batch.row_count, "meta": batch.meta,
                        "products": len(parsed[feed].values)})
    notes = sorted(set(n for d in parsed.values() for n in d.notes))
    return {"kind": kind, "bytes": len(body), "feeds": results, "notes": notes,
            "uploads": _upload_state()}


@app.delete("/api/feeds/upload/{kind}")
def clear_feed_upload(kind: str, actor: str = "planner",
                      db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Drop an uploaded sheet and put its feeds back on the workbook seed."""
    if kind not in feedsheets.KIND_FEEDS:
        raise HTTPException(404, "unknown sheet kind {!r}".format(kind))
    path = connectors.upload_path(svc.SEED_DIR, kind)
    if not os.path.exists(path):
        raise HTTPException(404, "no {} sheet is uploaded".format(kind))
    os.remove(path)

    conns = {c.feed: c for c in connectors.default_connectors(svc.SEED_DIR)}
    results = []
    for feed in feedsheets.KIND_FEEDS[kind]:
        batch = svc.sync_feed(db, conns[feed], actor)
        results.append({"feed": feed, "batch": batch.id, "rows": batch.row_count})
    return {"kind": kind, "feeds": results, "uploads": _upload_state()}


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
    try:
        row = optsvc.update_params(db, body, actor)
    except ValueError as e:
        # A value outside its range is a mistake, not a setting. Rejected here so
        # the field keeps its last good value rather than carrying nonsense into
        # a 15-minute solve that then fails for reasons that look like the model.
        raise HTTPException(400, str(e))
    return optsvc.params_dict(row)


@app.get("/api/optimizer/runs")
def get_optimizer_runs(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return optsvc.list_runs(db)


@app.post("/api/optimizer/run")
def post_optimizer_run(body: OptimizeRunIn,
                       db: Session = Depends(get_session)) -> Dict[str, Any]:
    busy = optsvc.running_run(db)
    if busy is not None:
        on = optsvc.scenario_labels(db).get(busy.base_scenario_id, {})
        raise HTTPException(409, "run {} is still solving on \"{}\" - wait for it "
                                 "to finish before starting another".format(
                                     busy.id, on.get("label", busy.base_scenario_id)))
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
    # Kind and on-screen label for each, so the picker can group Current Plans
    # apart from Optimized Results and mark a result the simulator rejected.
    labels = optsvc.scenario_labels(db)
    out = []
    for s in db.query(Scenario).order_by(Scenario.id.desc()).all():
        row = dict(_scenario_dict(s), run_status=None)
        row.update(labels.get(s.id, {}))
        out.append(row)
    return out


@app.post("/api/scenarios")
def create_scenario(body: ScenarioIn,
                    db: Session = Depends(get_session)) -> Dict[str, Any]:
    if not body.name.strip():
        # a blank name is a picker entry nobody can find again
        raise HTTPException(400, "a new plan needs a name")
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
    out = dict(_scenario_dict(_get_scenario(db, scenario_id)), run_status=None)
    out.update(optsvc.scenario_labels(db).get(scenario_id, {}))
    # The Current Plan / Optimized Result pair, so the schedule view can offer
    # the other half without the planner hunting for its id.
    out["pair"] = optsvc.pair_for(db, scenario_id)
    return out


@app.get("/api/scenarios/{scenario_id}/deletion")
def preview_deletion(scenario_id: int,
                     db: Session = Depends(get_session)) -> Dict[str, Any]:
    """What deleting this scenario would take with it, for the confirmation."""
    try:
        return optsvc.deletion(db, scenario_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int, actor: str = "planner",
                    db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Delete a Current Plan with its Optimized Results, or one Optimized Result
    with its run card (UI convention 13). Cannot be undone."""
    try:
        return optsvc.delete_scenario(db, scenario_id, actor)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/scenarios/{scenario_id}/products")
def scenario_products(scenario_id: int,
                      db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Every product block the projection can show."""
    s = _get_scenario(db, scenario_id)
    ref = svc.engine_reference(db, s.reference_version_id)
    products = ref.products
    targets = ref.inventory_targets
    out = []
    for b in cfg.live_blocks(ref.blocks):
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
    block = next((b for b in cfg.live_blocks(ref.blocks)
                  if b["id"] == block_id), None)
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
    for b in cfg.live_blocks(ref.blocks):
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
        blocks_spec = sorted((b for b in cfg.live_blocks(ref.blocks)
                              if b["sheet"] == sheet),
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

    specs = cfg.live_blocks(ref.blocks)
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
    for b in cfg.live_blocks(ref.blocks):
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
    for line in cfg.live_charge_lines(ref.charge_lines):
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
                                         if x in dset),
                      # whether Set a rate clears the unit's other lines
                      "one_feed": cfg.runs_one_feed(u)}
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
                          "name": optsvc.scenario_labels(db).get(
                              compare_with.id, {}).get("label", compare_with.name),
                          "cells_changed": moved}
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
    label = optsvc.scenario_labels(db).get(s.id, {})
    rejected = optsvc.rejected_result(db, s.id)
    if rejected is not None:
        raise HTTPException(409, "\"{}\" is the answer of run {}, which the "
                                 "simulator rejected, so it is not exported: {}"
                            .format(label.get("label", s.name), rejected.id,
                                    rejected.message or ""))
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
        cfg.live_charge_lines(ref.charge_lines), values, dates,
        title="Charge schedule - {}".format(label.get("label", s.name)),
        subtitle=("Optimized Result of run {} ({}), from \"{}\"."
                  .format(run.id, (run.params or {}).get("model_version", "v0"),
                          label.get("plan_label")) if run else
                  "Current Plan, built on the planning side."))
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
    # A bookmarked grid can still hold a line that has since been retired, and
    # the optimizer would drop whatever was typed into it without saying so.
    # Checked against the live list rather than `is_retired(line_key=...)`,
    # which only knows lines retired by name - MEK#70 and TOLLING#94 are
    # retired by their product and their unit instead.
    ref = svc.engine_reference(db, s.reference_version_id)
    live = {l["key"] for l in cfg.live_charge_lines(ref.charge_lines)}
    if body.line_key not in live:
        raise HTTPException(400, "{} is retired and is not scheduled"
                            .format(body.line_key))
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


@app.post("/api/scenarios/{scenario_id}/schedule/fill")
def fill_schedule(scenario_id: int, body: ScheduleFill,
                  db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Set one rate across a date range, or clear a unit over it.

    The cell-at-a-time PATCH above is the right shape for correcting a schedule
    and the wrong one for building one. Setting up a cold start means writing the
    same crude, ROSE and Platformer rate onto every day of the window and
    emptying the units the optimizer is meant to decide - hundreds of cells, all
    of which have to be right or the run is planning against a schedule nobody
    typed on purpose.
    """
    s = _get_scenario(db, scenario_id)
    ref = svc.engine_reference(db, s.reference_version_id)
    live = {l["key"] for l in cfg.live_charge_lines(ref.charge_lines)}
    try:
        return svc.fill_schedule(
            db, s.id, body.target, body.bbl, body.start, body.end,
            mode=body.mode, skip_downtime=body.skip_downtime,
            overwrite=body.overwrite, exclusive=body.exclusive,
            live_keys=live, actor=body.actor)
    except ValueError as e:
        raise HTTPException(400, str(e))


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
        if body.status not in (None, "detected", "confirmed"):
            raise ValueError("status must be detected or confirmed")
        row = svc.add_downtime(db, scenario_id, body.unit, body.start, body.end,
                               body.reason, body.actor,
                               status=body.status or "confirmed")
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
#: Sent with the page and its files, so the browser checks they are current before
#: using them. With no cache header it decides for itself how long a copy stays
#: fresh, and it kept an old index.html beside a new app.js: the script looked for
#: a form the old page did not have, stopped halfway through loading, and the app
#: never started. `no-cache` still uses the stored copy - the server answers 304
#: when nothing changed - but only after asking. Set where the files are served
#: rather than in middleware, which on this Python warned once per request, API
#: calls included.
REVALIDATE = {"Cache-Control": "no-cache"}


class _RevalidatedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = REVALIDATE["Cache-Control"]
        return response


if os.path.isdir(WEB_DIR):
    app.mount("/static", _RevalidatedStaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(WEB_DIR, "index.html"), headers=REVALIDATE)
