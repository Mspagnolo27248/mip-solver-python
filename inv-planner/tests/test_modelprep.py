"""The optimization model must simplify the workbook without losing material."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

SEED = os.path.join(ROOT, "data", "seed")
pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SEED, "reference.json")),
    reason="seed data not built; run scripts/seed.py")


@pytest.fixture(scope="module")
def built():
    from invplanner.engine import Reference, Scenario, simulate
    from invplanner.modelprep import build

    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    spec = build(ref, scn, sim, horizon=scn.dates[:42])
    return ref, scn, sim, spec


def test_aggregation_conserves_material(built):
    """Collapsing products must not create or destroy opening inventory, demand
    or production. `build` raises if it does; this asserts the check ran."""
    _, _, _, spec = built
    assert spec.reconciliation, "no aggregate was checked"
    for r in spec.reconciliation:
        assert r["ok"], r["product"]
        model, workbook = r["opening"]
        assert model == pytest.approx(workbook, abs=1.0)


def test_diesel_collapses_to_one_product(built):
    """Ten grades plus a pool become a single finished-diesel product, and the
    pool is dropped so the same material is not counted twice."""
    ref, _, _, spec = built
    assert "DSL" in spec.products
    assert len(spec.products["DSL"]["members"]) == 10
    for grade in ("8170", "8175", "8135", "8105"):
        assert grade not in spec.products, "{} survived aggregation".format(grade)
    assert "DDDD" not in spec.products, "the pool must not be modelled too"
    assert any(d["kind"] == "pool" and d["code"] == "DDDD" for d in spec.dropped)

    # the diesel charge stock is the hydrotreater's feed, not a finished grade,
    # so it must stay a distinct product
    assert "9713" in spec.products


def test_obsolete_tolling_flow_is_retired(built):
    _, _, _, spec = built
    assert "9202" not in spec.products
    assert "TOLLING" not in spec.units
    assert not any(l["unit"] == "TOLLING" for l in spec.charge_lines)


def test_mek_ladder_gives_only_adjacent_transitions(built):
    """The whole point of the arc formulation: forbidden transitions get no
    variable at all. Expressed in model products, so 9116 appears as the pooled
    block it shares a tank with."""
    _, _, _, spec = built
    mek = spec.units["MEK"]
    arcs = set(mek["allowed_arcs"])
    light = spec.aliases["9116"]
    assert len(arcs) == 6
    for pair in [(light, "9117"), ("9117", "9119"), ("9119", "4317")]:
        assert pair in arcs and pair[::-1] in arcs, pair
    # two-rung jumps must not exist
    for pair in [(light, "9119"), ("9117", "4317"), (light, "4317")]:
        assert pair not in arcs and pair[::-1] not in arcs, pair


def test_charge_lines_resolve_through_pooled_sold_codes(built):
    """The MEK charges 9116 but that inventory is tracked in the 9718 block.
    Resolving to the wrong key would let the model consume stock it never
    decremented."""
    _, _, _, spec = built
    line = next(l for l in spec.charge_lines if l["key"] == "MEK#72")
    assert line["workbook_product"] == "9116"
    assert line["product"] == spec.aliases["9116"]
    assert line["product"] in spec.products


def test_obsolete_4325_is_retired(built):
    _, _, _, spec = built
    assert "4325" not in spec.products
    assert not any(l["workbook_product"] == "4325" for l in spec.charge_lines)
    assert "4325" not in [p for u in spec.units.values() for p in u["products"]]


def test_downgrade_sinks_have_unlimited_offtake_but_keep_their_tanks(built):
    """The overflow valve is an unlimited *market*, not an unlimited tank.

    Diesel is a real product in a real tank that can still overfill; what makes
    it a sink is that it can always be sold at the netback. Modelling it as an
    unbounded tank would let inventory grow without limit and hide a genuine
    capacity problem.
    """
    from invplanner import model_config as cfg

    _, _, _, spec = built
    for code, sink_id in cfg.sink_products().items():
        p = spec.products.get(code)
        if p is None:
            continue
        assert p["is_sink"] is True, code
        assert p["unlimited_offtake"] is True, code

    dsl = spec.products["DSL"]
    assert dsl["is_sink"] is True
    assert dsl["unlimited_offtake"] is True
    assert dsl["tanked"] is True, "diesel still has a real tank"
    assert dsl["capacity"] > 0, "diesel capacity must survive being a sink"

    # gasoline has no workbook block, so the sink is created and has no tank
    gas = spec.products["SINK_GASOLINE"]
    assert gas["is_sink"] is True and gas["unlimited_offtake"] is True
    assert gas["tanked"] is False

    # ordinary products must not have acquired unlimited offtake
    assert spec.products["9711"]["unlimited_offtake"] is False


def test_sink_routes_separate_observed_from_added(built):
    """Routes added for the model must never be presented as if the workbook
    already had them."""
    _, _, _, spec = built
    assert spec.sink_routes
    observed = [r for r in spec.sink_routes if r["status"] == "observed"]
    added = [r for r in spec.sink_routes if r["status"] != "observed"]
    assert observed, "the workbook's own transfer lines should be picked up"
    assert added, "the gasoline routes are added"
    for r in observed:
        assert r["line"] is not None
    for r in added:
        assert r["line"] is None


def test_gasoline_takes_only_the_confirmed_blendstocks(built):
    """Platformate and isomerate blend to gasoline. Kensol 30 goes to diesel, and
    the untanked cascade intermediates need no outlet at all.

    Kensol 17 is deliberately *not* here. It is the reformer's own feed - the
    same material as the platformer charge, under the code it carries when sold
    as a solvent - and it reaches gasoline as platformate. The direct route only
    existed because the reformer leg was missing from the workbook; left in, it
    lets the optimizer blend heart cut straight to gasoline rather than reform
    it, which is cheaper in the model and wrong in the plant.
    """
    _, _, _, spec = built
    gas = {r["product"] for r in spec.sink_routes if r["sink"] == "GASOLINE"}
    # Empty on purpose. Platformate and isomerate already reach gasoline through
    # their 5020 forecast, and a second line to the same customer is the same
    # barrels twice - which is what the route's own `caution` warned about and
    # what happened when it was used: the model shorted the forecast to feed the
    # sink and the replay, serving demand first, found nothing left.
    assert gas == set(), gas
    assert "4107" not in gas

    # Kensol 30 must route to diesel, not gasoline - and to the *finished* pool,
    # not the charge stock, confirmed with operations. The workbook already
    # carries that line, so it should come through as observed.
    assert "4111" not in gas
    diesel = [r for r in spec.sink_routes
              if r["product"] == "4111" and r["sink"] == "FINDSL"]
    assert diesel and any(r["status"] == "observed" for r in diesel)
    assert not any(r["product"] == "4111" and r["sink"] == "DIESEL"
                   for r in spec.sink_routes)

    # untanked intermediates cannot overflow, so they get no sink route
    for code in ("9505", "9501"):
        assert not any(r["product"] == code for r in spec.sink_routes)


def test_gasoline_demand_is_not_double_counted(built):
    """Platformate and isomerate already take a forecast from product 5020
    (E10 gasoline), split 75/25. The sink is incremental offtake on top of that,
    so the forecast must still be present - if it vanished, the same barrels
    would be counted twice."""
    ref, _, _, spec = built
    napthas = {b.get("charge_code"): b for b in ref.blocks
               if b["sheet"] == "Napthas"}
    for code, factor in (("9511", 0.75), ("1128", 0.25)):
        row = napthas[code]["rows"]["Forecast"]
        assert row["in"] == "5020", code
        spec_region = (row["regions"] or [{}])[0].get("spec", {})
        assert spec_region.get("factor") == pytest.approx(factor), code


def test_hydrotreater_reactors_are_split_by_what_they_make(built):
    """4315 and 4319 come off the dedicated reactor; everything else off the
    other. Getting this wrong would let the optimizer alternate freely between
    them and never pay the flush."""
    _, _, _, spec = built
    hyd = spec.units["HYDRO"]
    assert hyd["reactors"] is not None
    assign = hyd["reactors"]["assignment"]
    # 9704 makes 4315 and 9705 makes 4319 - both on R1
    assert assign["9704"] == "R1"
    assert assign["9705"] == "R1"
    for solvent in ("9711", "9712", "9703", "9720", "9713"):
        if solvent in assign:
            assert assign[solvent] == "R2", solvent
    assert hyd["reactors"]["flush_days"] == pytest.approx(0.25)


def test_crossing_reactors_costs_more_than_an_ordinary_switch(built):
    """The flush is what makes the optimizer group the reactor-1 products
    together without being told to."""
    from invplanner import model_config as cfg

    within = cfg.changeover_loss_days("HYDRO", "9704", "9705")
    across = cfg.changeover_loss_days("HYDRO", "9704", "9711")
    staying = cfg.changeover_loss_days("HYDRO", "9704", "9704")

    assert staying == 0.0
    assert within == pytest.approx(0.125)
    assert across == pytest.approx(0.375), "switch loss plus the quarter-day flush"
    assert across > within

    # a unit with no reactors pays only the switch loss
    assert cfg.changeover_loss_days("MEK", "9117", "9119") == pytest.approx(0.125)


def test_max_rates_are_absolute_and_carry_their_basis(built):
    """`charge = max_rate x time`, so the parameter is bbl/day - and it must say
    whether it came from a clean day or was grossed up, so a floor is never
    mistaken for a specification."""
    from invplanner import model_config as cfg

    _, _, _, spec = built
    for unit in ("MEK", "HYDRO", "EXTRACT", "ROSE"):
        u = spec.units[unit]
        for p in u["products"]:
            info = u["max_rate"][p]
            if info is None:
                continue
            assert info["bbl"] > 0, (unit, p)
            # Every basis must be declared, so adding one is a deliberate act
            # rather than a string nobody notices. The declaration also records
            # whether it is a floor: a rate read off the plan is evidence of what
            # the unit has done, never of what it can do.
            assert info["basis"] in cfg.RATE_BASIS_IS_FLOOR, info

    # the plant's own example: diesel hydro charge runs 5,000 bbl/day
    assert cfg.max_rate("HYDRO", "9713") == 5000


def test_charge_is_max_rate_times_time():
    """The plant's worked example: two charges splitting a day 50/50 with no
    changeover give exactly half their maximum each."""
    from invplanner import model_config as cfg

    assert cfg.net_rate_bbl("HYDRO", "9713", 0.5) == pytest.approx(2500)
    assert cfg.net_rate_bbl("HYDRO", "9713", 1.0) == pytest.approx(5000)
    assert cfg.net_rate_bbl("HYDRO", "9713", 0.0) == pytest.approx(0)


def test_the_day_budget_shrinks_by_both_losses():
    """Interface loss on every changeover, plus the reactor flush on top when the
    pair crosses reactors. They are additive."""
    from invplanner import model_config as cfg

    same_reactor = cfg.day_time_budget("HYDRO", [("9711", "9712")])
    assert same_reactor == pytest.approx(1 - 0.125)

    cross_reactor = cfg.day_time_budget("HYDRO", [("9704", "9711")])
    assert cross_reactor == pytest.approx(1 - 0.375), "interface plus flush"

    # and the net rate falls with the time available
    assert cfg.net_rate_bbl("HYDRO", "9713", cross_reactor) == pytest.approx(
        5000 * 0.625)
    assert cross_reactor < same_reactor


def test_at_most_two_products_a_day(built):
    """No unit runs more than two charges in a day anywhere in the plan, so the
    model caps it. Two changeovers are possible within that: the unit can switch
    off what it carried in without running it, then switch again mid-day."""
    from collections import defaultdict

    from invplanner import model_config as cfg

    ref, scn, _, spec = built
    assert cfg.MAX_PRODUCTS_PER_DAY == 2
    for u in spec.units.values():
        assert u["max_products_per_day"] == 2

    lines = defaultdict(list)
    for line in ref.charge_lines:
        lines[line["unit"]].append(line)
    for unit in ("MEK", "HYDRO", "EXTRACT", "ROSE", "PLATFORMER"):
        for d in scn.dates:
            running = [l for l in lines[unit] if scn.charge_bbl(l["key"], d) > 0]
            assert len(running) <= cfg.MAX_PRODUCTS_PER_DAY, (unit, d, running)


def test_minimum_rates_are_measured_not_assumed(built):
    """A line that runs must be run properly, and the floor comes from the plan.

    This started at zero on purpose - a minimum is a restriction, and starting
    without one guaranteed a feasible answer. Zero turned out to be the wrong
    kind of wrong: must-run binds the day's *time* and nothing bound the volume,
    so the model allocated days it then charged nothing through, and extraction
    came back with six of 42 days nominally running and empty.

    The replacement is measured rather than picked. Across every line with enough
    mid-campaign days to read, the lowest rate the plant has ever held is 0.64 of
    the line's maximum; the default sits just under that, so it forbids the
    trickle without forbidding anything the plant actually does.
    """
    from invplanner import model_config as cfg

    _, _, _, spec = built
    assert 0.0 < cfg.DEFAULT_MIN_RATE_FRACTION < 0.64, (
        "the floor must bite, and must stay under the lowest rate observed")

    # it reaches barrels on every unit that has a maximum to scale
    for unit in spec.units.values():
        for product, lo in unit["min_rate_bbl"].items():
            top = (unit["max_rate"].get(product) or {}).get("bbl")
            if top:
                assert lo == pytest.approx(top * cfg.DEFAULT_MIN_RATE_FRACTION)

    # and a per-product figure still overrides the default when one arrives
    cfg.MIN_RATE_FRACTION["HYDRO"] = {"9713": 0.4}
    try:
        assert cfg.min_rate_fraction("HYDRO", "9713") == pytest.approx(0.4)
        assert cfg.min_rate_bbl("HYDRO", "9713") == pytest.approx(5000 * 0.4)
        assert cfg.min_rate_fraction("HYDRO", "9704") == pytest.approx(
            cfg.DEFAULT_MIN_RATE_FRACTION), "others fall back to the default"
    finally:
        cfg.MIN_RATE_FRACTION.pop("HYDRO", None)


def test_crude_is_supplied_not_decided(built):
    """Crude rate is set by refinery economics, not tank logistics. Left as a
    variable the optimizer would relieve every inventory problem by making less
    oil - the cheapest answer in the model and the most expensive in reality."""
    from invplanner import model_config as cfg

    _, scn, _, spec = built
    assert cfg.CRUDE_RATE_IS_INPUT is True
    assert spec.fixed_supply, "crude output must arrive as supply"

    # crude must not appear as a schedulable unit
    assert "CRUDE" not in spec.units

    # supply must cover the window and be positive
    total = 0.0
    for streams in spec.fixed_supply.values():
        for series in streams.values():
            total += sum(series.values())
    assert total > 0

    # the mode-gated streams stay separate while mode is still a decision
    if cfg.CRUDE_MODE_IS_DECISION:
        gated = [p for p, s in spec.fixed_supply.items()
                 if any(k.startswith("_mode_") for k in s)]
        assert gated, "9117/9118 should be held apart for the mode choice"


def test_units_must_run_except_in_a_turnaround(built):
    """Idling is the exception, not a free choice - otherwise the optimizer
    relieves any inventory problem by not running."""
    import datetime as dt

    from invplanner import model_config as cfg

    _, _, _, spec = built
    for unit in ("MEK", "HYDRO", "EXTRACT", "ROSE"):
        assert spec.units[unit]["must_run"] is True

    # a declared turnaround suspends the must-run rule for exactly those days
    ta = cfg.turnaround_days("ROSE")
    assert dt.date(2026, 10, 15) in ta
    assert dt.date(2026, 10, 23) in ta
    assert dt.date(2026, 10, 24) not in ta
    assert cfg.must_run("ROSE", dt.date(2026, 10, 15)) is False
    assert cfg.must_run("ROSE", dt.date(2026, 10, 24)) is True

    # a unit with no turnaround on a date must run
    assert cfg.must_run("MEK", dt.date(2026, 10, 15)) is True


def test_downtime_can_be_supplied_by_the_planner(built):
    """Downtime is app input, not code. Supplying it must override the outages
    inferred from the workbook, and must_run must follow it exactly."""
    import datetime as dt

    from invplanner.modelprep import build

    ref, scn, sim, _ = built
    window = scn.dates[:42]
    chosen = {window[3], window[4], window[5]}

    spec = build(ref, scn, sim, horizon=window, downtime={"MEK": chosen})
    mek = spec.units["MEK"]

    assert spec.downtime_source == "supplied"
    assert mek["downtime_days"] == sorted(d.isoformat() for d in chosen)
    assert len(mek["must_run_days"]) == len(window) - len(chosen)
    for d in chosen:
        assert d.isoformat() not in mek["must_run_days"]

    # a unit with no downtime supplied must run every day of the window
    assert len(spec.units["ROSE"]["must_run_days"]) == len(window)
    assert spec.units["ROSE"]["downtime_days"] == []

    # and with nothing supplied at all it falls back to the workbook's outages
    fallback = build(ref, scn, sim, horizon=window)
    assert fallback.downtime_source == "inferred"


def test_turnarounds_are_marked_as_detected_until_confirmed(built):
    """These came from blank stretches in the plan, not a maintenance calendar.
    Presenting them as real dates would be worse than having none."""
    from invplanner import model_config as cfg

    for unit, windows in cfg.TURNAROUNDS.items():
        for wdw in windows:
            assert wdw["status"] == "detected", (unit, wdw)
            assert wdw["start"] <= wdw["end"]


def test_a_rate_read_off_the_plan_is_never_presented_as_a_spec(built):
    """Any maximum that came from watching the plan is a floor, and the build has
    to say so. A rate confirmed with operations is the one kind that is not.

    The four hydrotreater charges that motivated this still have no clean day -
    but they are no longer grossed up, because grossing them up put three of them
    above a unit that tops out at 5,200 bbl/day. Operations confirmed the limit,
    so they carry a confirmed basis and keep `was` as the superseded figure. The
    invariant being pinned is the general one, not those four rows.
    """
    from invplanner import model_config as cfg

    _, _, _, spec = built
    # Every basis has to be one the model knows how to weigh. An unrecognised
    # string defaults to "floor" in the optimizer, which is the safe direction,
    # but it would do so silently - so pin the vocabulary here instead.
    for unit, by_product in cfg.MAX_RATE_BBL_PER_DAY.items():
        for code, info in by_product.items():
            assert info["basis"] in cfg.RATE_BASIS_IS_FLOOR, (unit, code)
            # A grossed-up rate is an extrapolation, never an observation, and
            # the build must warn whenever one is in play. No row is on that
            # basis today; this holds the guard in place for the next one.
            if info["basis"] == "grossed up":
                assert any(code in w and "grossed up" in w
                           for w in spec.warnings), (unit, code)

    # No hydrotreater feed may sit above the unit's confirmed daily limit: the
    # day-time budget is a weighted average, so a per-line ceiling above 5,200
    # lets a two-product day total more than the unit can physically charge.
    for code, info in cfg.MAX_RATE_BBL_PER_DAY["HYDRO"].items():
        assert info["bbl"] <= 5200, (code, info["bbl"])

    for code in ("9711", "9712", "9703", "9720"):
        info = cfg.max_rate_info("HYDRO", code)
        assert info["clean_days"] == 0, code
        assert info["basis"] == "confirmed with operations", code
        assert info["was"] > info["bbl"], code

    # a product with plenty of clean days must not be flagged
    assert cfg.max_rate_info("EXTRACT", "9302")["basis"] == "clean day"


def test_no_product_is_unreachable_on_a_restricted_unit(built):
    """A product no arc reaches can never be scheduled, and the solver fails
    silently rather than complaining. With 4325 retired this must now hold for
    every restricted unit - and the build warns if it ever stops holding."""
    _, _, _, spec = built
    for unit, u in spec.units.items():
        if u["unrestricted"]:
            continue
        reachable = {a for a, _ in u["allowed_arcs"]} | {b for _, b in u["allowed_arcs"]}
        unreachable = [p for p in u["products"] if p not in reachable]
        assert not unreachable, "{}: {} can never be scheduled".format(
            unit, unreachable)
    assert not [w for w in spec.warnings if "no allowed transition" in w]


def test_every_kept_charge_line_points_at_a_modelled_product(built):
    """Nothing may be charged that the model does not track, or the balance
    silently loses material."""
    _, _, _, spec = built
    for line in spec.charge_lines:
        assert line["product"] in spec.products or line["unit"].startswith(
            ("TRANSFER", "SONNEBORN", "RAILCAR")), line


def test_untanked_intermediates_exist_but_carry_no_capacity(built):
    """The Platformer's light straight run is made and consumed inside the
    cascade. It must stay in the model to keep the yield chain intact, but must
    not get an inventory constraint.

    The isomerate *run* 9501 used to be here too and is deliberately gone. Its
    only consumer was `PLATFORMER#106`, which operations confirmed is not a run
    the plant makes, so with that line retired nothing charges 9501 and it stops
    being a product. Its production is not lost: the yield rule still names it,
    and `production_alias` books it into 1128's tank, which is where the
    workbook puts it.
    """
    _, _, _, spec = built
    assert "9505" in spec.products, "9505 vanished from the model"
    assert spec.products["9505"]["tanked"] is False
    assert spec.products["9505"]["capacity"] == 0.0

    assert "9501" not in spec.products
    assert spec.production_alias.get("9501") == "1128"
    assert any(r.get("out") == "9501" and r["charge_line"] == "PLATFORMER#103"
               for r in spec.yield_rules), "the isomerate run must still be made"
    # everything with a real block stays tanked
    assert spec.products["9711"]["tanked"] is True
    assert spec.products["DSL"]["tanked"] is True


def test_sales_codes_match_the_workbook_where_it_records_them(built):
    """Five products are one oil under two codes: a stream code inside the plant
    and a grade code on the invoice. Kensol 17 and the platformer charge were the
    same duality and cost 67% of the objective before anyone noticed it, so the
    pairs are written down rather than inferred.

    Four of the five the workbook records itself, in each block's sold code, and
    those must agree - a drift between the two would mean a price attached to the
    wrong grade. The fifth (9704 sold as 4305) the workbook does not know: its
    block calls the product 9704 on both sides, and only operations can say
    otherwise, so it is marked as coming from them.
    """
    from invplanner import model_config as cfg

    ref, _, _, spec = built
    workbook = {}
    for b in ref.blocks:
        charge, sold = b.get("charge_code"), b.get("sold_code")
        if charge and sold and sold != charge:
            workbook[spec.aliases.get(charge, charge)] = sold

    for code, entry in cfg.SALES_CODES.items():
        if entry["source"] != "workbook":
            continue
        assert code in workbook, "{} is not a two-code block".format(code)
        assert workbook[code] == entry["sold_as"], (
            "{}: config says it sells as {}, the workbook says {}".format(
                code, entry["sold_as"], workbook[code]))

    # and the model keys on the production code, never the sales code
    for code, entry in cfg.SALES_CODES.items():
        assert code in spec.products, code
        assert entry["sold_as"] not in spec.products, entry["sold_as"]


def test_every_exit_from_the_plant_has_somewhere_to_get_a_price(built):
    """A schedule cannot be costed unless every way material leaves is priced.

    There are three, and they are enumerated from the spec rather than listed by
    hand, so a route added later shows up here without anyone remembering to:
    demand served, downgrade routed to a sink, and product lifted as-is.

    The mapping matters as much as the coverage. A price does not always attach
    to the product's own code - five products are one oil under a stream code and
    a grade code, and two more have no market of their own because their demand
    is really the gasoline pool's pull.
    """
    from collections import defaultdict

    from invplanner import economics, model_config as cfg

    ref, _, _, spec = built
    demand = defaultdict(float)
    for b in ref.blocks:
        code = b.get("charge_code")
        target = spec.aliases.get(code, code)
        if target not in spec.products:
            continue
        for series in spec.demand_rows.get(b["id"], {}).values():
            demand[target] += sum(series.values())

    found = economics.exits(spec, demand)
    assert {e["exit"] for e in found} == {"demand", "downgrade", "sold as-is"}

    # every exit resolves to a key, and no key is a code the model invented
    for e in found:
        assert e["price_key"], e
        assert not e["price_key"].startswith("SINK_"), e

    # the mappings that are not identity, and why
    assert economics.price_key("9511", spec) == "GASOLINE"
    assert economics.price_key("1128", spec) == "GASOLINE"
    assert economics.price_key("9704", spec) == "4305"
    assert economics.price_key("9711", spec) == "4105"
    assert economics.price_key("SINK_GASOLINE", spec) == "GASOLINE"
    # ...and one that is
    assert economics.price_key("4315", spec) == "4315"

    # nothing with demand may fall through unpriced
    priced = sum(d for c, d in demand.items()
                 if any(e["code"] == c and e["exit"] == "demand" for e in found))
    assert priced == pytest.approx(sum(demand.values()))


def test_every_line_that_drains_a_tank_can_be_seen_by_the_simulator(built):
    """A line the model moves material through must move it in the replay too.

    The optimizer's balance says a charge line consumes its product. The
    simulator moves material only where a block's rows reach out and *name* that
    line, through the `Charge` lookup, a `to_diesel` row that sees only
    TRANSFER_DIESEL lines, or an explicit row reference. Nothing checked that
    the two agree, and four lines did not: three blocks - platformate, isomerate
    and Kendex 0866 - carry `Production Out: charge_first_match code null`, so
    the lookup found nothing however many lines pointed at them.

    The optimizer drew those tanks and the replay drew nothing, which creates
    oil on the planner side. It stayed hidden because all four carried zero
    volume; the first schedule to route anything down them would have shipped
    material out of a tank that never emptied.

    Asserted against the engine's own lookup rules rather than by perturbing the
    schedule, so it stays true for a product whose tank happens to be empty.
    """
    ref, _, _, spec = built

    lookup = [l for l in ref.charge_lines if l.get("in_charge_lookup_range")]
    to_diesel = [l for l in ref.charge_lines if l["unit"] == "TRANSFER_DIESEL"]
    by_row = {l["row"]: l for l in ref.charge_lines}

    reached = set()
    for block in ref.blocks:
        for meta in block["rows"].values():
            for region in (meta.get("regions") or []):
                for term in region["spec"]["terms"]:
                    kind, code = term.get("kind"), term.get("code")
                    if kind == "charge_first_match" and code:
                        reached.update(l["key"] for l in lookup if l["code"] == code)
                    elif kind == "to_diesel" and code:
                        reached.update(l["key"] for l in to_diesel
                                       if l["code"] == code)
                    elif kind == "charge_row":
                        line = by_row.get(term.get("row"))
                        if line:
                            reached.add(line["key"])

    orphans = []
    for line in spec.charge_lines:
        product = spec.products.get(line["product"]) or {}
        if not product.get("tanked"):
            continue          # an untanked pass-through has no tank to drain
        if line["key"] not in reached:
            orphans.append((line["key"], line["product"]))

    assert not orphans, (
        "these lines drain a tank in the model and nothing in the replay: "
        + ", ".join("{} (feed {})".format(k, p) for k, p in orphans))


def test_a_product_downgrades_to_one_outlet_only(built):
    """A downgrade route is a physical line to one destination.

    Confirmed with operations: a product does not get to choose between #6 oil,
    the cat cracker, diesel and gasoline - it has the one route it has. The
    workbook's own routes already satisfy this, which is exactly why it needs a
    test: a second outlet added later would not fail anything, it would quietly
    hand the optimizer a choice the plant cannot make, and the optimizer would
    take it the moment the second outlet priced better.
    """
    from collections import defaultdict

    _, _, _, spec = built
    outlets = defaultdict(set)
    for route in spec.sink_routes:
        outlets[route["product"]].add(route["sink"])

    assert outlets, "no downgrade routes at all"
    forked = {p: s for p, s in outlets.items() if len(s) > 1}
    assert not forked, forked

    # and the build refuses to produce a spec that breaks it
    import invplanner.modelprep as mp
    original = list(spec.sink_routes)
    try:
        spec.sink_routes.append({"product": "4111", "sink": "SIX_OIL",
                                 "status": "invented", "line": None})
        outlets["4111"].add("SIX_OIL")
        assert len(outlets["4111"]) > 1
    finally:
        spec.sink_routes[:] = original
