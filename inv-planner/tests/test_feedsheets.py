"""Reading standalone feed exports, and uploading them through the API.

The point of these sheets is that a planner produces them by hand, so the tests
care most about the ways a hand-made file goes wrong: a renamed column, a sign
flipped the other way, the same product on two rows, a month grid that stops
short. A wrong number that imports silently is worse than an import that fails.
"""
import datetime as dt
import os
import sys

import pytest
from openpyxl import Workbook

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from invplanner import feedsheets as fs           # noqa: E402

MONTHS12 = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar",
            "Apr", "May", "Jun", "Jul", "Aug", "Sep"]


def _write(tmp_path, name, rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    path = str(tmp_path / name)
    wb.save(path)
    return path


def _inventory(tmp_path, rows=None):
    return _write(tmp_path, "inventory.xlsx",
                  [["Code", "Product", "Net Inv"]] +
                  (rows if rows is not None else
                   [["4111", "4111-10 KENSOL 30", 1000.0],
                    ["9103", "9103-10 SOLVENT", 2500.5]]))


def _open_orders(tmp_path, rows=None, dates=("08/28/2026", "08/31/2026")):
    return _write(tmp_path, "open orders.xlsx",
                  [["Code", "Open Orders (Detail)"] + list(dates)] +
                  (rows if rows is not None else
                   [["4111", "4111-10 KENSOL 30", -19570, -440]]))


def _forecast(tmp_path, sales=None, blend=None):
    header = ["CODE"] + MONTHS12 + [None, "CODE"] + MONTHS12
    sales = sales if sales is not None else [["4111"] + [100.0] * 12]
    blend = blend if blend is not None else [["4115"] + [50.0] * 12]
    rows = [header]
    for i in range(max(len(sales), len(blend))):
        left = sales[i] if i < len(sales) else [None] * 13
        right = blend[i] if i < len(blend) else [None] * 13
        rows.append(list(left) + [None] + list(right))
    return _write(tmp_path, "forecast.xlsx", rows)


# ------------------------------------------------------------------ shapes

def test_inventory_reads_gallons_under_the_seed_connector_key(tmp_path):
    d = fs.read("inventory", _inventory(tmp_path))["inventory"]
    # "ALL" rather than a tank id, so an override made against workbook data
    # still points at the same staging cell after the switch to files.
    assert d.values == {"4111": {"ALL": 1000.0}, "9103": {"ALL": 2500.5}}
    assert d.labels["4111"] == "4111-10 KENSOL 30"


def test_inventory_adds_a_product_that_appears_twice(tmp_path):
    path = _inventory(tmp_path, [["4111", "KENSOL 30", 1000.0],
                                 ["4111", "KENSOL 30", 250.0]])
    d = fs.read("inventory", path)["inventory"]
    assert d.values["4111"]["ALL"] == 1250.0
    assert "rows 2 and 3" in d.notes[0]


def test_inventory_rejects_a_renamed_value_column(tmp_path):
    path = _write(tmp_path, "inventory.xlsx",
                  [["Code", "Product", "On Hand"], ["4111", "KENSOL", 10.0]])
    with pytest.raises(fs.FeedSheetError) as e:
        fs.read("inventory", path)
    assert "Net Inv" in str(e.value)


def test_open_orders_flips_the_sign_and_keys_by_date(tmp_path):
    d = fs.read("open_orders", _open_orders(tmp_path))["open_orders"]
    assert d.values == {"4111": {"2026-08-28": 19570.0, "2026-08-31": 440.0}}


def test_open_orders_reads_real_dates_as_well_as_text(tmp_path):
    path = _open_orders(tmp_path, dates=(dt.date(2026, 8, 28), dt.date(2026, 8, 31)))
    d = fs.read("open_orders", path)["open_orders"]
    assert sorted(d.values["4111"]) == ["2026-08-28", "2026-08-31"]


def test_open_orders_takes_a_positive_sheet_as_demand(tmp_path):
    path = _open_orders(tmp_path, [["4111", "KENSOL 30", 19570, 440]])
    d = fs.read("open_orders", path)["open_orders"]
    assert d.values["4111"]["2026-08-28"] == 19570.0
    assert d.notes and "positive" in d.notes[0]


def test_open_orders_refuses_a_sheet_that_mixes_signs(tmp_path):
    path = _open_orders(tmp_path, [["4111", "KENSOL 30", -19570, 440]])
    with pytest.raises(fs.FeedSheetError) as e:
        fs.read("open_orders", path)
    assert "mixes" in str(e.value)


def test_forecast_splits_the_two_grids(tmp_path):
    out = fs.read("forecast", _forecast(tmp_path))
    assert out["sales_forecast"].values == {"4111": dict.fromkeys(
        ["OCT", "NOV", "DEC", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL",
         "AUG", "SEP"], 100.0)}
    assert out["blend_component_demand"].values["4115"]["OCT"] == 50.0


def test_forecast_rejects_a_grid_missing_a_month(tmp_path):
    header = ["CODE"] + MONTHS12[:11] + [None, "CODE"] + MONTHS12
    path = _write(tmp_path, "forecast.xlsx", [header, ["4111"] + [100.0] * 11])
    with pytest.raises(fs.FeedSheetError) as e:
        fs.read("forecast", path)
    assert "12 month columns" in str(e.value)


def test_a_workbook_that_is_not_a_workbook_is_reported_not_raised_raw(tmp_path):
    path = str(tmp_path / "inventory.xlsx")
    with open(path, "wb") as fh:
        fh.write(b"this is not a zip archive")
    with pytest.raises(fs.FeedSheetError):
        fs.read("inventory", path)


@pytest.mark.parametrize("name,kind", [
    ("inventory.xlsx", "inventory"),
    ("open orders.xlsx", "open_orders"),
    ("Open-Orders.XLSX", "open_orders"),
    ("open_orders.xlsx", "open_orders"),
    ("forecast.xlsx", "forecast"),
    ("charge schedule.xlsx", None),
])
def test_filenames_map_to_sheet_kinds(name, kind):
    assert fs.kind_for_filename(name) == kind


# -------------------------------------------------------- through the stack

SEED = os.path.join(ROOT, "data", "seed")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The API on a throwaway database, with uploads pointed at tmp_path."""
    pytest.importorskip("fastapi")
    if not os.path.exists(os.path.join(SEED, "reference.json")):
        pytest.skip("seed data not built; run scripts/seed.py")

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("INVPLANNER_UPLOAD_DIR", str(uploads))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    for mod in [m for m in list(sys.modules) if m.startswith("invplanner.")]:
        del sys.modules[mod]

    from fastapi.testclient import TestClient
    from invplanner.api.main import app
    from invplanner.db import service as svc
    from invplanner.db.session import SessionLocal, init_db

    init_db()
    session = SessionLocal()
    svc.load_reference_document(session, os.path.join(SEED, "reference.json"))
    session.commit()
    svc.sync_all(session, actor="test")
    session.close()
    yield TestClient(app)


def _post(client, kind, path):
    with open(path, "rb") as fh:
        return client.post("/api/feeds/upload/{}".format(kind), content=fh.read())


def test_upload_switches_the_feed_over_and_can_switch_back(client, tmp_path):
    before = {f["feed"]: f for f in client.get("/api/feeds").json()}
    assert before["inventory"]["source"].startswith("workbook-seed:")

    r = _post(client, "inventory", _inventory(tmp_path))
    assert r.status_code == 200, r.text
    assert r.json()["feeds"][0]["rows"] == 2

    after = {f["feed"]: f for f in client.get("/api/feeds").json()}
    assert after["inventory"]["source"] == "upload:inventory.xlsx"
    # Everything the file did not carry is a full-refresh zero, not a leftover.
    assert after["inventory"]["rows"] == before["inventory"]["rows"]

    assert client.delete("/api/feeds/upload/inventory").status_code == 200
    back = {f["feed"]: f for f in client.get("/api/feeds").json()}
    assert back["inventory"]["source"].startswith("workbook-seed:")


def test_one_upload_leaves_the_other_feeds_alone(client, tmp_path):
    _post(client, "open_orders", _open_orders(tmp_path))
    sources = {f["feed"]: f["source"] for f in client.get("/api/feeds").json()}
    assert sources["open_orders"] == "upload:open_orders.xlsx"
    assert sources["inventory"].startswith("workbook-seed:")
    assert sources["sales_forecast"].startswith("workbook-seed:")


def test_forecast_upload_fills_both_of_its_feeds(client, tmp_path):
    r = _post(client, "forecast", _forecast(tmp_path))
    assert [f["feed"] for f in r.json()["feeds"]] == \
        ["sales_forecast", "blend_component_demand"]
    sources = {f["feed"]: f["source"] for f in client.get("/api/feeds").json()}
    assert sources["sales_forecast"] == "upload:forecast.xlsx"
    assert sources["blend_component_demand"] == "upload:forecast.xlsx"


def test_a_rejected_upload_leaves_the_working_feed_in_place(client, tmp_path):
    _post(client, "inventory", _inventory(tmp_path))
    good = client.get("/api/feeds/inventory/rows?limit=5").json()["total"]

    bad = _write(tmp_path, "inventory.xlsx",
                 [["Code", "Product", "On Hand"], ["4111", "KENSOL", 10.0]])
    r = _post(client, "inventory", bad)
    assert r.status_code == 400
    assert "Net Inv" in r.json()["detail"]

    still = client.get("/api/feeds").json()
    assert {f["feed"]: f["source"] for f in still}["inventory"] == \
        "upload:inventory.xlsx"
    assert client.get("/api/feeds/inventory/rows?limit=5").json()["total"] == good


def test_an_override_survives_the_switch_to_an_uploaded_sheet(client, tmp_path):
    rows = client.get("/api/feeds/inventory/rows?search=4111&limit=5").json()["rows"]
    cell = rows[0]
    client.patch("/api/staging/{}".format(cell["id"]),
                 json={"value": 777.0, "reason": "counted by hand"})

    _post(client, "inventory", _inventory(tmp_path))
    after = client.get("/api/feeds/inventory/rows?search=4111&limit=5").json()["rows"][0]
    assert after["source_value"] == 1000.0     # the upload moved the source
    assert after["effective_value"] == 777.0   # the correction still wins
    assert after["is_stale"] is True           # and is flagged as based on old data


def test_a_scenario_freezes_what_was_uploaded(client, tmp_path):
    _post(client, "inventory", _inventory(tmp_path))
    _post(client, "open_orders", _open_orders(tmp_path))
    s = client.post("/api/scenarios", json={"name": "uploaded",
                                            "as_of": "2026-08-28",
                                            "horizon_days": 30}).json()
    got = client.get("/api/scenarios/{}".format(s["id"])).json()
    assert got["as_of"] == "2026-08-28"
    detail = client.get("/api/feeds/inventory/rows?search=9103&limit=5").json()
    assert detail["rows"][0]["effective_value"] == 2500.5


def test_an_unknown_sheet_kind_is_a_404(client, tmp_path):
    r = _post(client, "charge_schedule", _inventory(tmp_path))
    assert r.status_code == 404
