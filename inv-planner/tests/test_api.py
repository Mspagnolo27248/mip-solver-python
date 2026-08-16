"""API and persistence tests.

Covers the three things Phase 1 has to get right: overrides survive a re-sync,
freezing a scenario reproduces the engine's numbers, and every endpoint the
front end calls actually exists.
"""
import datetime as dt
import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

SEED = os.path.join(ROOT, "data", "seed")
pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SEED, "reference.json")),
    reason="seed data not built; run scripts/seed.py")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """A throwaway database seeded exactly the way scripts/init_db.py does."""
    path = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["DATABASE_URL"] = "sqlite:///" + str(path)
    for mod in [m for m in list(sys.modules) if m.startswith("invplanner.db")]:
        del sys.modules[mod]

    from invplanner.db.session import SessionLocal, init_db
    from invplanner.db import service as svc

    init_db()
    session = SessionLocal()
    svc.load_reference_document(session, os.path.join(SEED, "reference.json"))
    session.commit()
    svc.sync_all(session, actor="test")
    yield session
    session.close()


def test_sync_populates_staging_from_source(db):
    from invplanner.db.models import Feed, StagingCell
    n = db.query(StagingCell).filter(StagingCell.feed == Feed.INVENTORY).count()
    assert n > 100


def test_override_survives_resync_and_is_flagged_when_source_moves(db):
    """The whole point of the staging layer: a re-sync must not clobber a
    planner's edit, and it must show when the source has moved underneath it."""
    from invplanner.db import service as svc
    from invplanner.db.connectors import default_connectors
    from invplanner.db.models import Feed, StagingCell

    cell = (db.query(StagingCell)
            .filter(StagingCell.feed == Feed.INVENTORY)
            .order_by(StagingCell.k1).first())
    original_source = cell.source_value

    svc.set_override(db, cell.id, 999999.0, actor="tester", reason="unit test")
    assert cell.effective_value == 999999.0
    assert not cell.is_stale

    conn = next(c for c in default_connectors(svc.SEED_DIR) if c.feed == Feed.INVENTORY)
    svc.sync_feed(db, conn, actor="test")
    db.refresh(cell)

    assert cell.override_value == 999999.0, "re-sync clobbered the override"
    assert cell.source_value == original_source
    assert cell.effective_value == 999999.0

    # Now move the source and confirm the override is reported as stale.
    cell.source_value = (original_source or 0.0) + 5000.0
    db.commit()
    db.refresh(cell)
    assert cell.is_stale

    svc.set_override(db, cell.id, None, actor="tester")
    db.refresh(cell)
    assert cell.override_value is None
    assert not cell.is_stale


def test_scenario_freeze_reproduces_engine_numbers(db):
    """A scenario built from staging must simulate identically to the JSON
    scenario the parity harness validated against the workbook."""
    import json

    from invplanner.db import service as svc
    from invplanner.engine import Reference, Scenario, simulate

    with open(os.path.join(SEED, "scenario.json"), encoding="utf-8") as f:
        seed_scn = json.load(f)
    as_of = seed_scn["meta"]["as_of"]

    scenario = svc.create_scenario(db, "test", svc._d(as_of), horizon_days=90,
                                   actor="test")
    db_sim = svc.simulate_scenario(db, scenario)

    ref = Reference.load(os.path.join(SEED, "reference.json"))
    json_sim = simulate(ref, Scenario.load(os.path.join(SEED, "scenario.json")))

    dates = [svc._d(x) for x in scenario.inputs["dates"]][:60]
    checked = 0
    for block in ref.blocks:
        a = db_sim.balances[block["id"]]["End Inventory"]
        b = json_sim.balances[block["id"]]["End Inventory"]
        for d in dates:
            assert a[d] == pytest.approx(b[d], rel=1e-9, abs=1e-6), \
                "{} diverges on {}".format(block["id"], d)
            checked += 1
    assert checked > 1000


def test_schedule_edit_changes_the_projection(db):
    from invplanner.db import service as svc
    from invplanner.db.models import Scenario as ScenarioModel

    scenario = db.query(ScenarioModel).order_by(ScenarioModel.id.desc()).first()
    ref = svc.engine_reference(db, scenario.reference_version_id)
    block = next(b for b in ref.blocks if b["id"] == "MN:14")
    day = svc._d(scenario.inputs["dates"][3])

    before = svc.simulate_scenario(db, scenario).balances[block["id"]]["End Inventory"][day]

    line = next(l for l in ref.charge_lines if l["unit"] == "MEK")
    from invplanner.db.models import ScheduleEntry
    entry = (db.query(ScheduleEntry)
             .filter(ScheduleEntry.scenario_id == scenario.id,
                     ScheduleEntry.line_key == line["key"],
                     ScheduleEntry.date == day).first())
    if entry is None:
        entry = ScheduleEntry(scenario_id=scenario.id, line_key=line["key"],
                              date=day, bbl=0.0)
        db.add(entry)
    entry.bbl = (entry.bbl or 0.0) + 2500.0
    scenario.schedule_version += 1
    db.commit()
    svc.invalidate(scenario.id)

    after = svc.simulate_scenario(db, scenario).balances[block["id"]]["End Inventory"][day]
    assert after != before, "charging more feed left the projection unchanged"


def test_stream_sheet_returns_every_product_with_full_balance(db):
    """The stream view must reproduce the workbook tab: all products on the
    sheet, each with its balance rows, over a date window."""
    from fastapi.testclient import TestClient

    from invplanner.api.main import app
    from invplanner.db.models import Scenario as ScenarioModel
    from invplanner.db.session import get_session

    app.dependency_overrides[get_session] = lambda: db
    client = TestClient(app)
    sid = db.query(ScenarioModel).order_by(ScenarioModel.id).first().id

    streams = client.get("/api/scenarios/{}/streams".format(sid)).json()
    sheets = {s["sheet"] for s in streams}
    assert {"Solvents", "LLN", "Cstock", "MN", "HN", "Napthas", "CRUDE"} <= sheets

    r = client.get("/api/scenarios/{}/stream".format(sid),
                   params={"sheet": "Solvents", "days": 5})
    assert r.status_code == 200
    d = r.json()
    assert len(d["dates"]) == 5
    assert len(d["blocks"]) == 18

    block = d["blocks"][0]
    measures = {row["measure"] for row in block["rows"]}
    assert {"Begin Inventory", "Production In", "Sales", "Forecast",
            "End Inventory", "Excess Capacity"} <= measures
    for row in block["rows"]:
        assert len(row["values"]) == 5

    # The balance must actually balance on every day shown.
    by = {row["measure"]: row["values"] for row in block["rows"]}
    for i in range(5):
        expected_end = (by["Begin Inventory"][i] + by["Production In"][i]
                        - by["Sales"][i] - by["Forecast"][i] - by["Blends"][i]
                        - by["Production Out"][i] - by.get("Out to Diesel", [0] * 5)[i])
        assert by["End Inventory"][i] == pytest.approx(expected_end, abs=1e-6)

    # Crude is a different shape: barrels, and receipts instead of production in.
    c = client.get("/api/scenarios/{}/stream".format(sid),
                   params={"sheet": "CRUDE", "days": 3}).json()
    assert c["unit"] == "bbl"
    crude = {row["measure"]: row["values"] for row in c["blocks"][0]["rows"]}
    assert "Receipts" in crude
    assert crude["Begin Inventory"][0] > 0, "crude must open from the site summary"

    app.dependency_overrides.clear()


def test_dashboard_groups_cover_every_product_exactly_once(db):
    """No product may vanish from the dashboards, and none may be double-counted.

    The "other" group is the safety net: a newly added block lands there rather
    than disappearing, so this stays true as the product list changes.
    """
    from fastapi.testclient import TestClient

    from invplanner import groups
    from invplanner.api.main import app
    from invplanner.db import service as svc
    from invplanner.db.models import Scenario as ScenarioModel
    from invplanner.db.session import get_session

    app.dependency_overrides[get_session] = lambda: db
    client = TestClient(app)
    scenario = db.query(ScenarioModel).order_by(ScenarioModel.id).first()
    sid = scenario.id

    ref = svc.engine_reference(db, scenario.reference_version_id)
    expected = {b["id"] for b in ref.blocks} | {"CRUDE:14"}

    seen = []
    for g in client.get("/api/dashboards").json():
        d = client.get("/api/scenarios/{}/dashboard".format(sid),
                       params={"group": g["id"], "days": 60}).json()
        for section in d["sections"]:
            for p in section["products"]:
                seen.append(p["block_id"])

    assert len(seen) == len(set(seen)), "a product appears in more than one group"
    assert set(seen) == expected, "groups do not cover every product: {}".format(
        expected.symmetric_difference(seen))

    # Series must be downsampled but keep the true extremes and the last day.
    d = client.get("/api/scenarios/{}/dashboard".format(sid),
                   params={"group": "solvents", "days": 366, "max_points": 90}).json()
    p = d["sections"][0]["products"][0]
    assert len(p["end"]) <= 95
    assert p["dates"][-1] == d["to"]
    assert p["peak"] >= max(p["end"]) and p["trough"] <= min(p["end"])

    app.dependency_overrides.clear()


def test_planned_downtime_round_trips_and_splits(db):
    """Planners set downtime before the optimizer runs. Clearing a day inside a
    window must split it, not delete the whole turnaround."""
    import datetime as dt

    from fastapi.testclient import TestClient

    from invplanner.api.main import app
    from invplanner.db import service as svc
    from invplanner.db.models import Scenario as ScenarioModel
    from invplanner.db.session import get_session

    app.dependency_overrides[get_session] = lambda: db
    client = TestClient(app)
    scenario = db.query(ScenarioModel).order_by(ScenarioModel.id).first()
    sid = scenario.id
    before_version = scenario.schedule_version

    r = client.post("/api/scenarios/{}/downtime".format(sid), json={
        "unit": "MEK", "start": "2026-09-10", "end": "2026-09-14",
        "reason": "test turnaround"})
    assert r.status_code == 200 and r.json()["days"] == 5

    days = svc.downtime_days(db, sid)["MEK"]
    for day in range(10, 15):
        assert dt.date(2026, 9, day) in days

    # editing downtime must invalidate the cached simulation
    db.refresh(scenario)
    assert scenario.schedule_version > before_version

    # clearing a day in the middle splits the window in two
    t = client.post("/api/scenarios/{}/downtime/toggle".format(sid),
                    json={"unit": "MEK", "date": "2026-09-12"}).json()
    assert t["down"] is False
    wins = [x for x in client.get("/api/scenarios/{}/downtime".format(sid)).json()
            ["windows"] if x["unit"] == "MEK" and x["start"] >= "2026-09-10"]
    assert sorted((x["start"], x["end"]) for x in wins) == [
        ("2026-09-10", "2026-09-11"), ("2026-09-13", "2026-09-14")]

    # the schedule grid reports the days so the UI can lock those cells
    sched = client.get("/api/scenarios/{}/schedule".format(sid),
                       params={"days": 60}).json()
    mek = next(u for u in sched["units"] if u["unit"] == "MEK")
    assert "2026-09-11" in mek["downtime"]
    assert "2026-09-12" not in mek["downtime"]

    for wnd in wins:
        client.delete("/api/scenarios/{}/downtime/{}".format(sid, wnd["id"]))

    app.dependency_overrides.clear()


def test_reference_data_is_editable_and_changes_the_projection(db):
    """Yields, rates and capacities drive every downstream number, so they must
    be adjustable - and an edit has to reach the projection, not just the table."""
    from fastapi.testclient import TestClient

    from invplanner.api.main import app
    from invplanner.db import service as svc
    from invplanner.db.models import Scenario as ScenarioModel
    from invplanner.db.session import get_session

    app.dependency_overrides[get_session] = lambda: db
    client = TestClient(app)
    scenario = db.query(ScenarioModel).order_by(ScenarioModel.id).first()
    if scenario is None:      # not dependent on another test having run first
        import datetime as dt
        scenario = svc.create_scenario(db, "reference-edit", dt.date(2026, 7, 23),
                                       horizon_days=42, actor="test")

    groups = {g["kind"]: g for g in client.get("/api/reference").json()["groups"]}
    assert groups["unit_yield"]["count"] > 0
    assert groups["crude_yield"]["count"] > 0

    rows = client.get("/api/reference/unit_yield").json()["rows"]

    # pick a yield that actually produces something in the window, so the edit
    # has an observable effect
    sim = svc.simulate_scenario(db, scenario)
    dates = [svc._d(x) for x in scenario.inputs["dates"]][:60]
    target = day = product = None
    for row in rows:
        _, _, out = row["k1"].split("|")
        hit = next((d for d in dates if sim.prod(out, d) > 0), None)
        if hit is not None:
            target, day, product = row, hit, out
            break
    assert target is not None, "no unit yield produces anything in the window"

    before = sim.prod(product, day)
    assert before > 0

    r = client.patch("/api/reference/unit_yield", json={
        "k1": target["k1"], "value": round(target["source_value"] * 0.5, 8),
        "reason": "test"})
    assert r.status_code == 200 and r.json()["edited"] is True

    db.refresh(scenario)
    after = svc.simulate_scenario(db, scenario).prod(product, day)
    assert after < before, "halving a yield must reduce what the unit makes"

    # the imported value is kept alongside the edit
    row = next(x for x in client.get("/api/reference/unit_yield").json()["rows"]
               if x["k1"] == target["k1"])
    assert row["source_value"] == pytest.approx(target["source_value"])
    assert row["edited"] is True

    # clearing it restores the imported value
    client.patch("/api/reference/unit_yield",
                 json={"k1": target["k1"], "value": None})
    db.refresh(scenario)
    restored = svc.simulate_scenario(db, scenario).prod(product, day)
    assert restored == pytest.approx(before, rel=1e-9)

    app.dependency_overrides.clear()


def test_every_endpoint_the_ui_calls_exists():
    """Guards against the front end drifting away from the API."""
    from invplanner.api.main import app

    js_path = os.path.join(ROOT, "src", "invplanner", "web", "app.js")
    with open(js_path, encoding="utf-8") as f:
        js = f.read()

    called = set(re.findall(r"""api\(\s*[`'"]([^`'"]+)""", js))
    assert called, "no api() calls found - did the front end change shape?"

    patterns = []
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        patterns.append(re.compile(
            "^" + re.sub(r"\{[^}]+\}", "[^/]+", route.path) + "$"))

    unresolved = []
    for call in sorted(called):
        path = call.split("?")[0]
        path = re.sub(r"\$\{[^}]*\}", "X", path)
        if not any(p.match(path) for p in patterns):
            unresolved.append(call)
    assert not unresolved, "front end calls endpoints that do not exist: {}".format(
        unresolved)


def test_schedule_exports_as_paste_blocks(db):
    """The export hands over blocks to paste, not a rewritten workbook.

    One block per unit with its own target cell, because the rows between the
    unit blocks carry a total, the date headers and the "enter BBLs in yellow
    cells" note - a single contiguous paste would flatten all of them.
    """
    import io

    from fastapi.testclient import TestClient
    from openpyxl import load_workbook

    from invplanner.api.main import app
    from invplanner.db.session import get_session

    app.dependency_overrides[get_session] = lambda: db
    client = TestClient(app)
    scenarios = client.get("/api/scenarios").json()
    sid = scenarios[0]["id"]
    r = client.get("/api/scenarios/{}/schedule.xlsx?days=14".format(sid))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]

    ws = load_workbook(io.BytesIO(r.content))["Paste blocks"]
    headers = [ws.cell(row, 1).value for row in range(1, ws.max_row + 1)
               if "paste at" in str(ws.cell(row, 1).value or "")]
    assert headers, "no paste blocks in the export"
    # every block names a cell, and no two units share one
    targets = [h.split("paste at ")[1] for h in headers]
    assert len(targets) == len(set(targets))
    app.dependency_overrides.clear()


def test_export_targets_the_column_the_date_really_sits_in(db):
    """The paste target is computed from a layout anchor, so the anchor has to be
    right. Checked against the workbook when it is present - a wrong column is
    the failure that looks exactly like success."""
    import datetime as dt
    import os

    from invplanner import exporter, layout

    y, m, d = (int(x) for x in layout.CS_GRID_FIRST_DATE.split("-"))
    assert exporter.grid_column(dt.date(y, m, d)) == layout.CS_FIRST_COL

    book = os.path.join(ROOT, "..", layout.WORKBOOK_DEFAULT)
    if not os.path.exists(book):
        pytest.skip("workbook not present")
    from openpyxl import load_workbook
    ws = load_workbook(book, data_only=True, read_only=True)["Charge Schedule"]
    row = next(ws.iter_rows(min_row=layout.CS_DATE_ROW_BBL,
                            max_row=layout.CS_DATE_ROW_BBL,
                            min_col=1, max_col=200, values_only=True))
    for col, cell in enumerate(row, start=1):
        if isinstance(cell, dt.datetime):
            assert exporter.grid_column(cell.date()) == col, (
                "{} sits in column {}, the export would target {}".format(
                    cell.date(), col, exporter.grid_column(cell.date())))
            break
