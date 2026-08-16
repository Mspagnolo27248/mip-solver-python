"""Engine unit tests.

The balance arithmetic is asserted on hand-built fixtures so a regression shows up
as a failing rule rather than as a shifted number deep in a parity diff.
"""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.engine import Engine, Reference, Scenario, simulate  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")


def _ref(**over):
    data = {
        "meta": {}, "products": {}, "tank_capacity": {"9711": 100.0, "4105": 50.0},
        "inventory_targets": {}, "crude_yields": {"9711": {"JUL": 0.1}},
        "crude_loss_fraction": 0.0, "run_rates": {}, "other_rates": {},
        "crude_plan": {"charge_bbl_per_day": {}, "receipts_bbl_per_day": {"JUL": 5.0}},
        "blend_bom": [], "charge_lines": [], "yield_rules": [], "blocks": [],
    }
    data.update(over)
    return Reference(data)


def _scn(**over):
    data = {
        "meta": {"name": "t", "as_of": "2026-07-01"},
        "dates": ["2026-07-01", "2026-07-02", "2026-07-03"],
        "opening_inventory": {}, "open_orders": {}, "sales_forecast": {},
        "blend_component_demand": {}, "base_oil_transfer": {},
        "base_oil_transfer_columns": {},
        "charge_schedule": {"crude_bbl_per_day": {}, "crude_mode": {}, "lines": {}},
        "manual_block_rows": {}, "capacity_series": {},
    }
    data.update(over)
    return Scenario(data)


def _block(**over):
    rows = {
        "Begin Inventory": {"row": 1, "in": "9711", "out": "4105", "regions": None},
        "Production In": {"row": 2, "in": "9711", "out": None, "regions": [
            {"from": "1900-01-01", "manual": False,
             "spec": {"terms": [{"kind": "production_grid", "code": "9711"}]}}]},
        "Sales": {"row": 3, "in": "4105", "out": None, "regions": [
            {"from": "1900-01-01", "manual": False,
             "spec": {"terms": [{"kind": "open_orders", "code": "4105"}]}}]},
        "Blends": {"row": 4, "in": "4105", "out": None, "regions": None},
        "Forecast": {"row": 5, "in": "4105", "out": None, "regions": [
            {"from": "1900-01-01", "manual": False,
             "spec": {"terms": [{"kind": "forecast_net", "code": "4105"}]}}]},
        "Production Out": {"row": 6, "in": "9711", "out": "9711", "regions": None},
        "End Inventory": {"row": 7, "in": None, "out": None, "regions": None},
        "Tank Capacity": {"row": 8, "in": "9711", "out": None, "regions": None},
        "Excess Capacity": {"row": 9, "in": None, "out": None, "regions": None},
    }
    b = {
        "id": "T:1", "sheet": "T", "first_row": 1, "last_row": 9,
        "charge_code": "9711", "sold_code": "4105", "label": "t",
        "rows": rows, "row_order": list(rows),
        "capacity": {"kind": "codes", "codes": ["9711", "4105"]},
        "opening": {"kind": "codes", "codes": ["9711", "4105"]},
        "opening_row": 0, "prod_out_label": "Production Out",
        "prod_out_extra_transfer_row": None, "has_out_to_diesel": False,
        "blends_include_base_oil_transfer": False, "manual_rows": [],
    }
    b.update(over)
    return b


def test_opening_sums_the_codes_the_sheet_looks_up():
    ref = _ref(blocks=[_block()])
    scn = _scn(opening_inventory={"9711": 100.0, "4105": 25.0})
    sim = simulate(ref, scn)
    assert sim.balances["T:1"]["Begin Inventory"][dt.date(2026, 7, 1)] == 125.0


def test_opening_is_not_double_counted_when_codes_repeat():
    """A block whose charge and sold codes are the same product must not add its
    inventory twice - the sheet looks it up once."""
    b = _block(opening={"kind": "codes", "codes": ["9711"]})
    sim = simulate(_ref(blocks=[b]), _scn(opening_inventory={"9711": 100.0}))
    assert sim.balances["T:1"]["Begin Inventory"][dt.date(2026, 7, 1)] == 100.0


def test_forecast_is_netted_against_booked_orders():
    ref = _ref(blocks=[_block()])
    scn = _scn(opening_inventory={"9711": 100.0},
               sales_forecast={"4105": {"JUL": 30.0}},
               open_orders={"4105": {"2026-07-01": 10.0, "2026-07-02": 45.0}})
    bal = simulate(ref, scn).balances["T:1"]
    # orders below the forecast rate: forecast tops the day up to the rate
    assert bal["Forecast"][dt.date(2026, 7, 1)] == 20.0
    # orders above the rate: no incremental forecast, and never negative
    assert bal["Forecast"][dt.date(2026, 7, 2)] == 0.0


def test_balance_chains_end_to_begin():
    ref = _ref(blocks=[_block()])
    scn = _scn(opening_inventory={"9711": 100.0},
               open_orders={"4105": {"2026-07-01": 10.0}})
    bal = simulate(ref, scn).balances["T:1"]
    d1, d2 = dt.date(2026, 7, 1), dt.date(2026, 7, 2)
    assert bal["End Inventory"][d1] == 90.0
    assert bal["Begin Inventory"][d2] == bal["End Inventory"][d1]


def test_history_before_as_of_is_frozen():
    """Days before the as-of date carry no demand or production: the model only
    projects forward from the inventory snapshot."""
    ref = _ref(blocks=[_block()])
    scn = _scn(meta={"name": "t", "as_of": "2026-07-03"},
               opening_inventory={"9711": 100.0},
               open_orders={"4105": {"2026-07-01": 10.0}},
               sales_forecast={"4105": {"JUL": 30.0}})
    bal = simulate(ref, scn).balances["T:1"]
    assert bal["Sales"][dt.date(2026, 7, 1)] == 0.0
    assert bal["Forecast"][dt.date(2026, 7, 1)] == 0.0
    assert bal["End Inventory"][dt.date(2026, 7, 1)] == 100.0


def test_excess_capacity_is_clamped_to_capacity():
    """Negative inventory must not report more headroom than the tank has."""
    ref = _ref(blocks=[_block()])
    scn = _scn(opening_inventory={"9711": -500.0})
    bal = simulate(ref, scn).balances["T:1"]
    assert bal["Excess Capacity"][dt.date(2026, 7, 1)] == 150.0


def test_capacity_series_overrides_static_capacity():
    ref = _ref(blocks=[_block()])
    scn = _scn(opening_inventory={"9711": 0.0},
               capacity_series={"T:1": {"2026-07-02": 900.0}})
    bal = simulate(ref, scn).balances["T:1"]
    assert bal["Tank Capacity"][dt.date(2026, 7, 1)] == 150.0
    assert bal["Tank Capacity"][dt.date(2026, 7, 2)] == 900.0


def test_crude_yield_respects_the_unit_mode():
    """9117 is only made in regular mode, 9118 only in low-volatility mode."""
    rules = [
        {"kind": "crude", "po_row": 23, "out": "9117",
         "monthly_yield": {"JUL": 0.5}, "mode": "R"},
        {"kind": "crude", "po_row": 24, "out": "9118",
         "monthly_yield": {"JUL": 0.5}, "mode": "L"},
    ]
    ref = _ref(yield_rules=rules)
    scn = _scn(charge_schedule={"crude_bbl_per_day": {"2026-07-01": 100.0},
                                "crude_mode": {"2026-07-01": "R"}, "lines": {}})
    sim = simulate(ref, scn)
    d = dt.date(2026, 7, 1)
    assert sim.prod("9117", d) == pytest.approx(100 * 0.5 * 42)
    assert sim.prod("9118", d) == 0.0


def test_diesel_yield_back_uses_produced_volume_not_charge():
    """The hydrotreater returns the volume the solvent stream loses, i.e.
    charge x (1 - yield); applying the factor to the charge inflates it by 1/yield."""
    rules = [
        {"kind": "unit", "po_row": 49, "unit": "HYDRO", "charge_line": "HYDRO#80",
         "charge_code": "9711", "out": "4115", "yield": 0.85},
        {"kind": "diesel_yield_back", "po_row": 54, "out": "9713",
         "terms": [{"source_po_row": 49, "yield": 0.85}]},
    ]
    ref = _ref(charge_lines=[{"key": "HYDRO#80", "unit": "HYDRO", "row": 80,
                              "code": "9711", "name": "", "index_in_block": 0,
                              "in_charge_lookup_range": True}],
               yield_rules=rules)
    scn = _scn(charge_schedule={"crude_bbl_per_day": {}, "crude_mode": {},
                                "lines": {"HYDRO#80": {"2026-07-01": 100.0}}})
    sim = simulate(ref, scn)
    d = dt.date(2026, 7, 1)
    assert sim.prod("4115", d) == pytest.approx(100 * 0.85 * 42)
    assert sim.prod("9713", d) == pytest.approx(100 * (1 - 0.85) * 42)


def test_charge_lookup_modes_differ_when_a_product_feeds_two_units():
    """The workbook's first-match VLOOKUP ignores the second line; loose mode sums
    both. The engine must be able to express each."""
    lines = [
        {"key": "EXTRACT#90", "unit": "EXTRACT", "row": 90, "code": "9305",
         "name": "", "index_in_block": 0, "in_charge_lookup_range": True},
        {"key": "EXTRACT#91", "unit": "EXTRACT", "row": 91, "code": "9305",
         "name": "", "index_in_block": 1, "in_charge_lookup_range": True},
    ]
    ref = _ref(charge_lines=lines)
    scn = _scn(charge_schedule={"crude_bbl_per_day": {}, "crude_mode": {},
                                "lines": {"EXTRACT#90": {"2026-07-01": 10.0},
                                          "EXTRACT#91": {"2026-07-01": 5.0}}})
    d = dt.date(2026, 7, 1)
    assert Engine(ref, scn, strict_workbook=True).charge_out("9305", d) == 10.0
    assert Engine(ref, scn, strict_workbook=False).charge_out("9305", d) == 15.0
    assert Engine(ref, scn).detect_lookup_conflicts()


def test_physical_mode_clamps_tanks_and_prices_the_response():
    """Tanks are real: inventory can neither go negative nor exceed capacity.
    Physical mode enforces that and records what it cost - lost sales when demand
    could not be served, downgrade when the tank would have overfilled."""
    ref = _ref(blocks=[_block()])
    scn = _scn(opening_inventory={"9711": 100.0},
               open_orders={"4105": {"2026-07-01": 500.0}})
    bal = simulate(ref, scn, physical=True).balances["T:1"]
    d1 = dt.date(2026, 7, 1)
    # 100 on hand against 500 of orders: 400 is a lost sale, not negative stock.
    assert bal["Lost Sales"][d1] == pytest.approx(400.0)
    assert bal["End Inventory"][d1] == 0.0

    # Overfilling is relieved by a downgrade, not by exceeding the tank.
    scn2 = _scn(opening_inventory={"9711": 100.0})
    ref2 = _ref(blocks=[_block()], tank_capacity={"9711": 120.0, "4105": 0.0})
    bal2 = simulate(ref2, scn2, physical=True).balances["T:1"]
    assert bal2["Downgrade Required"][d1] == pytest.approx(0.0)

    ref3 = _ref(blocks=[_block()], tank_capacity={"9711": 60.0, "4105": 0.0})
    bal3 = simulate(ref3, scn2, physical=True).balances["T:1"]
    assert bal3["End Inventory"][d1] == pytest.approx(60.0)
    assert bal3["Downgrade Required"][d1] == pytest.approx(40.0)


def test_feed_shortfall_is_separated_from_lost_sales():
    """Demand the tank cannot cover is a lost sale; charge the tank cannot cover
    means the schedule was not executable. They must not be conflated."""
    b = _block()
    b["rows"]["Production Out"]["regions"] = [
        {"from": "1900-01-01", "manual": False,
         "spec": {"terms": [{"kind": "charge_first_match", "code": "9711"}]}}]
    ref = _ref(blocks=[b], charge_lines=[
        {"key": "MEK#69", "unit": "MEK", "row": 69, "code": "9711", "name": "",
         "index_in_block": 0, "in_charge_lookup_range": True}])
    scn = _scn(opening_inventory={"9711": 100.0},
               charge_schedule={"crude_bbl_per_day": {}, "crude_mode": {},
                                "lines": {"MEK#69": {"2026-07-01": 10.0}}})
    bal = simulate(ref, scn, physical=True).balances["T:1"]
    d1 = dt.date(2026, 7, 1)
    # 10 bbl = 420 gal of charge against 100 gal on hand.
    assert bal["Lost Sales"][d1] == 0.0
    assert bal["Feed Shortfall"][d1] == pytest.approx(320.0)
    assert bal["End Inventory"][d1] == 0.0


def test_downgrade_pricing_falls_back_to_the_flat_discount():
    """Until a product is valued, a downgrade costs the outlet's discount to
    crude - the loss on material worth only crude, which is the floor on the true
    cost, never an overstatement."""
    from invplanner.economics import Economics

    econ = Economics(crude_price_per_bbl=70.0)
    assert econ.crude_price_per_gal == pytest.approx(70.0 / 42)
    assert econ.outlet_netback_per_gal("TRANSFER_6OIL") == pytest.approx(
        70.0 / 42 - 0.50)
    assert econ.downgrade_cost_per_gal("4107", "TRANSFER_6OIL") == pytest.approx(0.50)
    assert econ.cost_basis("4107", "TRANSFER_6OIL") == "flat"


def test_downgrade_pricing_uses_the_product_value_once_it_exists():
    """Dropping in a real value must take precedence, with no code change - and
    a specialty product must then cost more to downgrade than a cheap one."""
    from invplanner.economics import Economics

    econ = Economics(crude_price_per_bbl=70.0,
                     product_value_per_gal={"4107": 3.00, "8105": 1.80})
    netback = 70.0 / 42 - 0.50
    assert econ.downgrade_cost_per_gal("4107", "TRANSFER_6OIL") == pytest.approx(
        3.00 - netback)
    assert econ.cost_basis("4107", "TRANSFER_6OIL") == "per_product"
    # the higher-value product must be the more expensive one to downgrade
    assert (econ.downgrade_cost_per_gal("4107", "TRANSFER_6OIL")
            > econ.downgrade_cost_per_gal("8105", "TRANSFER_6OIL"))
    # and an unpriced product still falls back rather than costing zero
    assert econ.downgrade_cost_per_gal("9999", "TRANSFER_6OIL") == pytest.approx(0.50)


def test_unpriced_outlet_reports_rather_than_costing_zero():
    """A route with no price must not silently look free, or the optimizer would
    send everything down it."""
    from invplanner.economics import Economics

    econ = Economics()
    assert econ.outlet_netback_per_gal("TRANSFER_DIESEL") is None
    assert econ.downgrade_cost_per_gal("4107", "TRANSFER_DIESEL") is None
    assert econ.cost_basis("4107", "TRANSFER_DIESEL") == "unpriced"


@pytest.mark.skipif(not os.path.exists(os.path.join(SEED, "reference.json")),
                    reason="seed data not built; run scripts/seed.py")
def test_physical_mode_never_leaves_the_tank(seeded=None):
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    for block in ref.blocks:
        bal = sim.balances[block["id"]]
        for d in scn.dates:
            v = bal["End Inventory"][d]
            cap = bal["Tank Capacity"][d]
            assert v >= -1e-6, "{} negative on {}".format(block["id"], d)
            if cap > 0:
                assert v <= cap + 1e-6, "{} over capacity on {}".format(block["id"], d)


@pytest.mark.skipif(not os.path.exists(os.path.join(SEED, "reference.json")),
                    reason="seed data not built; run scripts/seed.py")
def test_seeded_scenario_simulates():
    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn)
    assert len(sim.balances) >= len(ref.blocks)
    for block in ref.blocks:
        bal = sim.balances[block["id"]]
        for d in scn.dates[1:]:
            prev = scn.dates[scn.dates.index(d) - 1]
            if d > scn.as_of:
                assert bal["Begin Inventory"][d] == pytest.approx(
                    bal["End Inventory"][prev]), block["id"]
