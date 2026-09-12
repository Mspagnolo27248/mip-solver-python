"""v0 must produce a schedule that runs the plant, and be scored honestly.

Two things went wrong before these tests existed, and neither showed up as an
error - both produced a confident, plausible, wrong answer:

1. **The model had no reason to run.** The objective counts only costs, so the
   cheapest schedule was to stop making oil: v0 shut the Platformer off entirely
   and ran the plant at 34% of plan, then reported a large saving.

2. **The referee was measuring the wrong thing.** It summed the workbook's
   blocks, so the ten collapsed diesel grades and the retired 9202 read as an
   890,000 gal disagreement that had nothing to do with the schedule; and it
   compared the downgrade the optimizer *chose* against the downgrade the
   simulator found *still necessary*, two quantities that should never be equal.
"""
import copy
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

SEED = os.path.join(ROOT, "data", "seed")
pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SEED, "reference.json")),
    reason="seed data not built; run scripts/seed.py")

DAYS = 14
PARAMS = {"lost_sale_margin_per_gal": 1.50, "downgrade_discount_per_gal": 0.50,
          "netback_diesel_cost_per_gal": 0.37,
          "netback_gasoline_cost_per_gal": 0.22,
          "horizon_days": DAYS, "time_limit_seconds": 120, "mip_gap": 0.0,
          "charge_floor_fraction": 0.8}


@pytest.fixture(scope="module")
def solved():
    from invplanner import model_config as cfg
    from invplanner.engine import Reference, Scenario, simulate
    from invplanner.modelprep import build
    from invplanner.optimizer import v0

    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:DAYS]
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, sim, horizon=dates, downtime=down)
    res = v0.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=down)
    assert res.status == "optimal", res.status
    return ref, scn, spec, dates, res


def _proposed(scn, res):
    """The base scenario with the optimizer's schedule written in."""
    from invplanner.engine import Scenario
    data = copy.deepcopy(scn.raw)
    lines = data["charge_schedule"]["lines"]
    for source in (res.charge, res.transfer_bbl):
        for key, series in source.items():
            lines.setdefault(key, {})
            for s, bbl in series.items():
                if bbl:
                    lines[key][s] = bbl
                else:
                    lines[key].pop(s, None)
    return Scenario(data)


_SIM = {}


def _sim(ref, scn):
    """The baseline simulation, scored the way the referee scores it."""
    from invplanner.engine import simulate
    if "s" not in _SIM:
        _SIM["s"] = simulate(ref, scn, strict_workbook=False, physical=True)
    return _SIM["s"]


def _demand(ref, spec, product):
    """Gallons of demand the model carries for one product over the window."""
    total = 0.0
    for block in ref.blocks:
        code = block.get("charge_code")
        if spec.aliases.get(code, code) != product:
            continue
        for series in spec.demand_rows.get(block["id"], {}).values():
            total += sum(series.values())
    return total


def test_the_plant_keeps_running(solved):
    """No unit may be throttled below the charge floor, and none may be shut off.

    This is the constraint whose absence let v0 'save' money by not making oil.
    """
    from invplanner import model_config as cfg
    ref, scn, spec, dates, res = solved
    iso = [d.isoformat() for d in dates]
    floor = PARAMS["charge_floor_fraction"]

    per_unit = {}
    for line in spec.charge_lines:
        if line["unit"].startswith(cfg.TRANSFER_UNIT_PREFIXES):
            continue
        planned = sum(scn.charge_bbl(line["key"], d) for d in dates)
        got = sum(res.charge.get(line["key"], {}).get(s, 0.0) for s in iso)
        base, opt = per_unit.get(line["unit"], (0.0, 0.0))
        per_unit[line["unit"]] = (base + planned, opt + got)

    assert per_unit, "no process units in the model"
    for unit, (planned, got) in per_unit.items():
        if planned <= 0:
            continue
        assert got > 0, "{} was shut off entirely".format(unit)
        assert got >= planned * floor - 1.0, (
            "{} ran {:,.0f} bbl against a floor of {:,.0f}".format(
                unit, got, planned * floor))


def test_every_downgrade_route_can_be_written_onto_a_line(solved):
    """A route with no charge line cannot be part of an answer.

    Two ways that happened. The gasoline outlet is confirmed with operations but
    absent from the workbook entirely, so all of its routes were dropped. And
    Kendex D&N goes to #6 oil, an outlet the workbook *does* have - but not for
    that product, so the route was silently unroutable and 4579 filled its tank
    until the model went infeasible on day 125, short by 17 gallons.

    So the line has to exist per (outlet, product), not per outlet, and this
    asserts the property rather than today's list of which ones needed one.
    """
    from invplanner import model_config as cfg

    _, _, spec, _, res = solved
    assert not res.unroutable, res.unroutable
    assert spec.added_lines, "some route needs a line to be written onto"

    outlets = {sid: cfg.SINKS[sid]["outlet"] for sid in cfg.SINKS}
    lines = {(l["unit"], l["workbook_product"]) for l in spec.charge_lines}
    for route in spec.sink_routes:
        unit = outlets[route["sink"]]
        product = route["product"]
        assert any(u == unit and (p == product
                                  or spec.aliases.get(p) == product)
                   for u, p in lines), \
            "{} -> {} has no line to be written onto".format(product, route["sink"])


def test_verification_scores_in_model_space(solved):
    """Products the model retires or collapses are excluded and reported, never
    counted as a disagreement."""
    from invplanner.optimizer import verify
    ref, scn, spec, dates, res = solved
    check = verify.verify(ref, _proposed(scn, res), res.kpis, dates, spec)

    assert check["scored_in_model_space"]
    # 9202 is retired: nothing can make it, yet demand is still booked against it
    assert any(e["product"] == "9202" for e in check["excluded"])
    assert check["excluded_lost_sales_gal"] > 0
    # ...and the diesel grades are pooled rather than excluded or double counted:
    # every product reported is a model product, never a workbook member code.
    assert all(e["product"] not in ("8175", "8105") for e in check["excluded"])
    reported = {w["product"] for w in check["worst_products"]}
    assert reported <= set(spec.products), reported - set(spec.products)
    assert not reported & {"8175", "8105", "8170"}, "diesel members scored apart"


def test_chosen_downgrade_matches_what_the_schedule_executes(solved):
    """The optimizer's downgrade decisions must appear on the transfer lines, and
    nothing may be left for the simulator to force out."""
    from invplanner.optimizer import verify
    ref, scn, spec, dates, res = solved
    check = verify.verify(ref, _proposed(scn, res), res.kpis, dates, spec)

    assert check["comparison"]["downgrade_gal"]["agrees"], \
        check["comparison"]["downgrade_gal"]
    assert check["fully_routed"], check["actual"]["downgrade_required_gal"]


def test_the_schedule_is_executable(solved):
    """Feed shortfall must be zero: the optimizer holds inventory at or above
    zero by construction, so any shortfall on replay means the two models
    disagree about what the schedule consumes."""
    from invplanner.optimizer import verify
    ref, scn, spec, dates, res = solved
    check = verify.verify(ref, _proposed(scn, res), res.kpis, dates, spec)
    assert check["executable"], check["note"]


def test_the_window_does_not_end_by_draining_the_plant(solved):
    """Terminal inventory is priced, so a schedule that empties every tank inside
    the horizon is not free."""
    _, _, _, _, res = solved
    assert "terminal_shortfall_gal" in res.kpis


def test_the_reformer_runs(solved):
    """Kensol 17 is the reformer's feed, not a downgrade.

    The splitter makes 3.59 M gal of it in a 42-day window against 17 k gal of
    solvent demand and a 273 k gal tank, so it plainly does not accumulate - but
    the workbook has no row for the reformer, so the schedule showed it idle for
    all 366 days and the model inferred a shutdown. It then paid twice for the
    same barrels: 3.15 M gal of platformate booked as lost sales, and the heart
    cut that would have made it booked as a downgrade to gasoline. Together, 67%
    of the objective.
    """
    from invplanner import model_config as cfg
    ref, scn, spec, dates, res = solved
    iso = [d.isoformat() for d in dates]

    assert "PLATFORMER#104" in cfg.FLOW_THROUGH_LINES
    # the planner never schedules it...
    assert all(scn.charge_bbl("PLATFORMER#104", d) == 0 for d in dates)
    # ...and the model runs it anyway
    ran = sum(res.charge.get("PLATFORMER#104", {}).get(s, 0.0) for s in iso)
    assert ran > 0, "the reformer was not run"

    # ...so platformate is made rather than shorted. It used to lose 100% of its
    # demand, every day, at every floor setting: nothing could make it at all.
    # What is left is limited by naphtha supply and the reformer's rate, not by
    # the model pretending the unit is shut down.
    demand = _demand(ref, spec, "9511")
    lost = sum(res.lost_sales.get("9511", {}).values())
    assert demand > 0
    assert lost < 0.6 * demand, "{:,.0f} of {:,.0f} gal still shorted".format(
        lost, demand)


def test_the_cascade_does_not_share_a_day(solved):
    """The Platformer's stages run in parallel, so they cannot share a time
    budget - it charges two or more lines on 305 of 366 days. Giving it one
    forces a choice between stages the plant runs together, and once the reformer
    had a rate that alone made the model infeasible."""
    from invplanner import model_config as cfg
    _, _, spec, dates, res = solved
    iso = [d.isoformat() for d in dates]
    assert "PLATFORMER" in cfg.CASCADE_UNITS
    both = [s for s in iso
            if res.charge.get("PLATFORMER#102", {}).get(s, 0.0) > 0
            and res.charge.get("PLATFORMER#104", {}).get(s, 0.0) > 0]
    assert both, "splitter and reformer never ran on the same day"


def test_holding_stock_never_outranks_serving_a_customer(solved):
    """A lost sale is margin gone for good; ending the window short is material
    to replace. Priced level the optimizer cannot tell them apart and picks
    arbitrarily - it booked 98,746 gal of Argold lost sales it did not have to.
    Priced merely close, it shorts customers on purpose to hold stock: at
    $1.35/gal against a $1.50 margin the plan shorted 310,736 gal more demand
    than at $0.50. Cheaper terminal must never mean more lost sales."""
    from invplanner import model_config as cfg
    from invplanner.optimizer import v0

    ref, scn, spec, dates, _ = solved
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    margin = PARAMS["lost_sale_margin_per_gal"]

    prev = None
    for price in (margin, 0.9 * margin, PARAMS["downgrade_discount_per_gal"]):
        r = v0.solve(ref, scn, spec,
                     dict(PARAMS, terminal_shortfall_per_gal=price),
                     horizon=dates, downtime=down)
        assert r.status == "optimal", (price, r.status)
        if prev is not None:
            assert r.kpis["lost_sales_gal"] <= prev + 1.0, (
                "pricing terminal cheaper shorted more customers, at "
                "${:.2f}/gal".format(price))
        prev = r.kpis["lost_sales_gal"]

    # and the default must sit well clear of the margin
    assert PARAMS["downgrade_discount_per_gal"] < 0.5 * margin


def test_baseline_and_result_are_scored_the_same_way(solved):
    """The comparison the planner reads must be like-for-like.

    Summed the old way - raw workbook blocks, strict first-match lookups - the
    baseline carries demand booked against a retired product and the diesel
    pool's member-by-member clamping, neither of which the optimizer is asked to
    plan. Put beside a model-space result it overstated the improvement by seven
    points. An error that only ever flatters the tool reporting it is the one to
    have a test for.
    """
    from invplanner.optimizer import verify
    ref, scn, spec, dates, _ = solved

    raw = sum(
        sum(_sim(ref, scn).balances.get(b["id"], {})
            .get("Lost Sales", {}).get(d, 0.0) for d in dates)
        for b in ref.blocks)
    scored = verify.verify(ref, scn, {"lost_sales_gal": 0.0,
                                      "downgrade_gal": 0.0}, dates, spec)

    assert scored["actual"]["lost_sales_gal"] < raw, (
        "model-space scoring should drop what the model does not carry")
    assert scored["excluded_lost_sales_gal"] > 0


def test_a_transfer_is_not_subtracted_twice(solved):
    """Loose mode sums every charge line matching a product. A block that also
    carries the line in a row of its own - `Out to Diesel`, or the one block that
    names a transfer row outright - then subtracts the same barrels twice. It was
    10.8 M gal a year across five products.

    This covers the block-level half of the guard only - see
    `test_a_row_does_not_count_a_line_it_names_outright` for the other half.
    """
    from invplanner import engine as E
    from invplanner.engine import simulate
    ref, scn, _, dates, _ = solved

    def old_charge_total(self, code, d, block_id=None, exclude=()):
        if not code:
            return 0.0
        return sum(self.scn.charge_bbl(l["key"], d)
                   for l in self._lookup_lines if l["code"] == code)

    fixed = simulate(ref, scn, strict_workbook=False, physical=True)
    orig = E.Engine.charge_total
    E.Engine.charge_total = old_charge_total
    try:
        doubled = simulate(ref, scn, strict_workbook=False, physical=True)
    finally:
        E.Engine.charge_total = orig

    def out(sim):
        return sum(sum(sim.balances.get(b["id"], {})
                       .get("Production Out", {}).get(d, 0.0) for d in dates)
                   for b in ref.blocks)

    assert out(fixed) < out(doubled), "the double count is back"


def test_a_row_does_not_count_a_line_it_names_outright(solved):
    """A row that sums a code lookup *and* an explicit row reference must not
    take the same barrels twice.

    The waxy light neutral block does exactly that - `charge_first_match 9116`
    plus `charge_row 115` in one row - and `MEK#72` and `TRANSFER_DIESEL#115`
    both carry code 9116. So in loose mode the transfer came out twice: 271,383
    gal on hand against 542,765 drawn, to the gallon a doubling.

    The block-level `_accounted` guard cannot catch this. It works by skipping
    lines a block subtracts through a row *of its own*, and it deliberately skips
    the production-out row when building that set - which is the row doing the
    double count here.

    Nothing caught it for a different reason too: parity runs in strict mode,
    where the workbook's VLOOKUP stops at the first match, `MEK#72`, and never
    reaches the transfer. It only bites in loose mode with volume on the
    transfer - rare in the plan, ordinary once the optimizer began using the
    diesel route.

    Asserted by putting volume on the transfer and reading the block's own
    production-out row, so it fails on the arithmetic rather than on a total
    that something else could move.
    """
    import copy

    from invplanner.engine import GAL_PER_BBL, Scenario, simulate
    ref, scn, _, dates, _ = solved

    line, block_id = "TRANSFER_DIESEL#115", "LLN:14"
    assert any(l["key"] == line and l["code"] == "9116" for l in ref.charge_lines)
    assert any(l["key"] == "MEK#72" and l["code"] == "9116"
               for l in ref.charge_lines), "the collision this guards is gone"

    rate, days = 1000.0, dates[:10]
    data = copy.deepcopy(scn.raw)
    lines = data["charge_schedule"]["lines"]
    lines.setdefault(line, {})
    for d in days:
        lines[line][d.isoformat()] = rate

    before = simulate(ref, scn, strict_workbook=False, physical=True)
    after = simulate(ref, Scenario(data), strict_workbook=False, physical=True)

    # `Production Out &DG DFO` is the workbook's label; the balance canonicalises
    # it to `Production Out`.
    def drawn(sim):
        return sum(sim.balances[block_id].get("Production Out", {})
                   .get(d, 0.0) for d in days)

    moved = drawn(after) - drawn(before)
    expected = rate * len(days) * GAL_PER_BBL
    assert moved == pytest.approx(expected, rel=1e-9), (
        "{:,.0f} gal charged came out as {:,.0f} - a factor of {:.2f}"
        .format(expected, moved, moved / expected if expected else 0))


def test_a_fixed_unit_is_charged_for_the_changeovers_it_inherits(solved):
    """The price of a changeover must not depend on who chose it.

    The arcs - and `arc_cost` with them - are built only inside
    `for unit in sorted(free)`, so with nothing freed v0's changeover term summed
    over nothing. v0 was charged $0 for switching while inheriting every
    changeover the planner made: 119 over 100 days, $238,000 at the default
    price. The objective therefore meant a different thing in each model version
    and inverted the comparison - v0 read 8.6% better than v2 at 100 days, where
    on equal terms v2 is 20.9% better at 42.

    Two things have to hold at once, and they pull in opposite directions:

      * the objective **must** move with `switch_cost`, or the cost is not being
        charged; and
      * the schedule **must not**, because a fixed unit cannot avoid these
        changeovers and the term is a constant. A constant that moved the answer
        would mean it had been written as something else by mistake.
    """
    from invplanner import model_config as cfg
    from invplanner.optimizer import v0
    ref, scn, spec, dates, _ = solved
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}

    def run(cost):
        p = dict(PARAMS, switch_cost=cost, horizon_days=len(dates))
        return v0.solve(ref, scn, spec, p, horizon=dates, downtime=down)

    free_run, paid = run(0.0), run(2000.0)
    assert free_run.status == "optimal" and paid.status == "optimal"

    inherited = paid.kpis["inherited_switches"]
    assert inherited, "the fixture plan makes no changeovers to inherit"
    n = sum(inherited.values())
    assert paid.kpis["inherited_switch_cost"] == pytest.approx(n * 2000.0)

    # charged...
    assert paid.objective == pytest.approx(free_run.objective + n * 2000.0), (
        "the objective did not move by the price of the inherited changeovers")

    # ...but not steering
    for field in ("charge_bbl", "lost_sales_gal", "downgrade_gal"):
        assert paid.kpis[field] == pytest.approx(free_run.kpis[field]), (
            "{} moved: the inherited cost is not a constant".format(field))

    # A cascade unit runs several lines at once, so it has no setup to change.
    for unit in cfg.CASCADE_UNITS:
        assert unit not in inherited, unit


def test_deep_extraction_keeps_its_own_rate(solved):
    """Extraction runs 9305 two ways, and the modes have different ceilings.

    Normal (EXTRACT#90) makes 4318 Argold, which can only be sold. Deep
    (EXTRACT#91) makes 9705, which can be sold *or* charged to the hydrotreater -
    and the hydrotreater takes nothing else from extraction, so this line is the
    sole route into reactor 1 for that stream. Deep is the more severe cut and
    runs at 1,300 bbl/day against 2,200.

    Keyed on the feed alone, both lines inherited 2,200 and the optimizer
    scheduled deep extract at 2,200 on 4 days of 42 - 69% more than the unit can
    make, feeding a hydrotreater campaign that could not have run.
    """
    from invplanner import model_config as cfg
    _, _, _, _, res = solved

    shallow = cfg.max_rate_info("EXTRACT", "9305", "EXTRACT#90")
    deep = cfg.max_rate_info("EXTRACT", "9305", "EXTRACT#91")
    assert deep["bbl"] < shallow["bbl"], "the modes must not share a ceiling"

    for key, info in (("EXTRACT#90", shallow), ("EXTRACT#91", deep)):
        for day, bbl in res.charge.get(key, {}).items():
            assert bbl <= info["bbl"] + 1e-6, (
                "{} ran {:,.0f} bbl on {} against a {:,} ceiling".format(
                    key, bbl, day, info["bbl"]))


def test_untanked_intermediates_are_made_and_charged_the_same_day(solved):
    """No tank means no buffer, so the downstream unit has no rate of its own.

    The light straight run has no tank anywhere in the plant, so the
    isomerisation unit takes exactly what the fractionator hands it. The plan
    confirms it: on all 305 days both run, the isom charge is 0.16194 x the
    fractionator charge, without a single deviation - the planner has been
    computing this identity by hand.

    Modelled as a ceiling it would be a weaker, guessable restatement; modelled
    as an equality there is nothing left to guess. Before it existed, untanked
    products had no balance row at all, so the optimizer could charge more light
    straight run than was made, or less, with no consequence either way.
    """
    _, _, spec, _, res = solved
    LSR_YIELD = 0.16193700000000003

    assert spec.products["9505"]["tanked"] is False
    for day, frac in res.charge.get("PLATFORMER#102", {}).items():
        isom = res.charge.get("PLATFORMER#103", {}).get(day, 0.0)
        if frac <= 0:
            continue
        # 0.01 bbl is well inside solver noise and well below anything a tank
        # would notice; a real leak would be orders of magnitude larger.
        assert abs(isom - frac * LSR_YIELD) < 0.01, (
            "{}: fractionator {:,.1f} bbl but isom {:,.1f} - the light straight "
            "run went somewhere".format(day, frac, isom))


def test_isomerate_is_credited_with_its_production(solved):
    """1128 takes its production from 9501's yield - the workbook books it into
    isomerate's block directly and no yield rule names 1128 as an output. Without
    the redirect the optimizer thinks isomerate cannot be made at all."""
    _, _, spec, _, _ = solved
    assert spec.production_alias.get("9501") == "1128"


def test_a_block_only_guards_against_its_own_rows(solved):
    """The double-count guard is per block, and that is not a detail.

    A line accounted for in *someone else's* row must still count in this one.
    The retired 9202 block books the Sonneborn lift as its own Sales, reading the
    charge row directly - and with a single global guard that made SONNEBORN#121
    vanish from every block, including 9118's own Production Out. 9118 then had
    production and no offtake, appeared to overflow by 707,233 gal over 160 days,
    and every horizon past 42 days failed verification because of it.
    """
    from invplanner.engine import Engine, simulate

    ref, scn, spec, _, _ = solved
    dates = scn.dates[:160]

    engine = Engine(ref, scn, strict_workbook=False, physical=True)
    # the guard exists, and is keyed by block
    assert isinstance(engine._accounted, dict)
    # 9202's block claims the Sonneborn line; 9118's block does not
    assert "SONNEBORN#121" in engine._accounted.get("MN:112", set())
    assert "SONNEBORN#121" not in engine._accounted.get("MN:98", set())

    sim = simulate(ref, scn, strict_workbook=False, physical=True)
    balance = sim.balances["MN:98"]
    out = sum(balance.get("Production Out", {}).get(d, 0.0) for d in dates)
    assert out > 1_000_000, (
        "9118 must be drawn down by the Sonneborn lift, got {:,.0f}".format(out))
    assert sum(balance.get("Downgrade Required", {}).get(d, 0.0)
               for d in dates) == pytest.approx(0.0, abs=1.0)


# ------------------------------------------------------- the margin objective
def _solve(spec_bundle, **over):
    from invplanner import model_config as cfg
    from invplanner.optimizer import v0
    ref, scn, spec, dates, _ = spec_bundle
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    return v0.solve(ref, scn, spec, dict(PARAMS, **over), horizon=dates,
                    downtime=down)


def test_an_infeasible_model_is_never_reported_as_usable(solved):
    """The worst kind of wrong answer: a confident one.

    CBC hands back the last relaxation it was holding when it proves
    infeasibility, so every variable carries a number. The status handling used
    to take that as evidence of an incumbent, relabel `Infeasible` as
    `feasible`, attach "time limit reached after 0s" to a solve that took a
    tenth of a second, and return a schedule that satisfies none of its own
    constraints.

    It was not an edge case. The shipped default charge floor was 0.8, which is
    infeasible over the full 42-day window, so *every* run at defaults came back
    this way - and a schedule that cannot be executed reads exactly like one
    that can. Nothing downstream could tell the difference, because the referee
    replays whatever it is given.
    """
    from invplanner.optimizer import v0  # noqa: F401  (import shape matches _solve)
    res = _solve(solved, charge_floor_fraction=0.9)
    assert res.status == "infeasible", res.status
    assert "time limit" not in (res.message or "")
    # and the feasible neighbour still solves, so this is the floor binding and
    # not the test having broken the model
    assert _solve(solved, charge_floor_fraction=0.8).status == "optimal"


def test_cost_is_the_default_and_margin_is_opt_in(solved):
    """The margin objective ships alongside the cost one, not instead of it.

    Until margin has been scored against the plan and believed, a scenario that
    says nothing about which objective it wants must get the one it has always
    had - to the same number, not merely a similar schedule."""
    _, _, _, _, res = solved
    explicit = _solve(solved, objective="cost")
    assert explicit.status == res.status
    assert explicit.objective == res.objective


def test_margin_mode_solves_and_wants_to_run(solved):
    """The whole reason for the flip: production has to be worth something.

    Under the cost objective an extra gallon earns nothing, so the model stops
    at the demand book and the charge floor is what keeps the plant running.
    Under margin it should want more throughput than the floor obliges it to
    take."""
    _, _, _, _, res = solved
    margin = _solve(solved, objective="margin")
    assert margin.status in ("optimal", "feasible"), margin.status
    assert margin.kpis["charge_bbl"] > res.kpis["charge_bbl"]


def test_terminal_value_fraction_dominates_the_answer(solved):
    """Pinned because it is the weakest number in the model, not the strongest.

    What a gallon left in the tank on the last day is worth stands in for
    everything after the horizon, and nobody has measured it. Priced high the
    plant hoards rather than ships; priced at zero every tank must be emptied by
    the final day. This asserts the direction so that anyone who changes the
    default sees, in a failing test, that they moved the most sensitive
    parameter in the objective - see MARGIN-AND-CAMPAIGNS.md."""
    hoards = _solve(solved, objective="margin", terminal_value_fraction=0.9)
    ships = _solve(solved, objective="margin", terminal_value_fraction=0.0)
    assert hoards.status in ("optimal", "feasible")
    assert ships.status in ("optimal", "feasible")
    assert ships.kpis["lost_sales_gal"] < hoards.kpis["lost_sales_gal"]


# ------------------------------------------------- the horizon the data supports
def test_the_horizon_is_capped_at_the_last_believable_day(solved):
    """The workbook has columns for 366 days and inputs for far fewer.

    Nothing in the data announces where it stops being real, and asking past that
    point does not fail cleanly: it returns a confident schedule built on demand
    nobody stands behind, or an infeasibility whose cause is three layers from
    the error. A full-year run was infeasible at *every* charge floor including
    zero, and the reason - crude still running with no unit scheduled to consume
    the feeds - took an elastic diagnostic to find.

    The boundary is **derived, not written down**: it is the last day the planner
    filled in a charge on a unit that decides things. On this workbook that is
    2026-12-31, which is what the constant used to say - so the number is pinned
    here as a fact about the seed data, not as a fact about the calendar.
    """
    from invplanner import model_config as cfg
    import datetime as dt

    ref, scn, spec, _, _ = solved

    assert cfg.DATA_VALID_THROUGH is None, "the override should normally be off"
    assert cfg.schedule_valid_through(scn) == dt.date(2026, 12, 31)
    assert cfg.valid_through(scn) == dt.date(2026, 12, 31)

    kept = cfg.clamp_horizon([dt.date(2026, 12, 30), dt.date(2026, 12, 31),
                              dt.date(2027, 1, 1), dt.date(2027, 7, 23)], scn)
    assert kept == [dt.date(2026, 12, 30), dt.date(2026, 12, 31)]

    # With nothing to measure against, the dates pass through. Inventing a
    # boundary would be worse than having none.
    assert cfg.clamp_horizon([dt.date(2027, 7, 23)]) == [dt.date(2027, 7, 23)]

    # A horizon entirely past the boundary is refused, not solved. This returns
    # before the spec is touched, so the module's 14-day spec is irrelevant here.
    from invplanner.optimizer import v0
    past = [dt.date(2027, 1, 5), dt.date(2027, 1, 6)]
    res = v0.solve(ref, scn, spec, PARAMS, horizon=past)
    assert res.status == "no_solution", res.status
    assert "believed" in res.message

    # 160 days is the longest that solves; the 161st adds one tank overflow
    assert cfg.MAX_SOLVABLE_DAYS == 160
    assert (scn.dates[cfg.MAX_SOLVABLE_DAYS - 1]
            <= cfg.valid_through(scn)), "the solvable window must sit inside the believable one"


def test_extending_the_schedule_extends_the_horizon(solved):
    """The boundary follows the plan, which is the whole reason it is derived.

    Hard-coded, a planner who filled in another month got the same silent
    truncation and no way to see why - the fix was to edit a date in the config,
    which nobody outside this repo can do. Derived, the horizon they can ask for
    grows the moment they extend the grid.
    """
    from invplanner import model_config as cfg
    import copy, datetime as dt

    _, scn, _, _, _ = solved
    scn = copy.deepcopy(scn)
    before = cfg.valid_through(scn)

    for i in range(1, 46):
        day = (before + dt.timedelta(days=i)).isoformat()
        scn.charge_lines.setdefault("MEK#69", {})[day] = 3500.0

    assert cfg.valid_through(scn) == before + dt.timedelta(days=45)
    assert len(cfg.clamp_horizon(scn.dates, scn)) == \
        len(cfg.clamp_horizon(scn.dates, solved[1])) + 45

    # A transfer line is not a charge decision, so filling one in must not move
    # the boundary - otherwise a downgrade the planner typed would license days
    # on which nothing is scheduled to charge the lube units at all.
    scn2 = copy.deepcopy(solved[1])
    edge = cfg.valid_through(scn2)
    for i in range(1, 31):
        day = (edge + dt.timedelta(days=i)).isoformat()
        scn2.charge_lines.setdefault("TRANSFER_DIESEL#111", {})[day] = 900.0
    assert cfg.valid_through(scn2) == edge


# ------------------------------------------------- the hydrotreater's reactors
def _reactor_days(spec, res, dates):
    """Which reactors the hydrotreater produces on, per day."""
    from invplanner import model_config as cfg
    hy = [l for l in spec.charge_lines if l["unit"] == "HYDRO"]
    rof = {l["key"]: cfg.reactor_of("HYDRO", l["workbook_product"]) for l in hy}
    out = []
    for d in dates:
        s = d.isoformat()
        on = {rof[l["key"]] for l in hy
              if (res.charge.get(l["key"], {}).get(s, 0.0) or 0.0) > 1e-6}
        out.append(on)
    return out


def test_crossing_the_hydrotreater_reactors_is_not_free(solved):
    """A reactor crossing costs a quarter-day flush, and nothing charged it.

    The flush lives in the arc formulation, which only *freed* units get. The
    hydrotreater's assignment is fixed, so its day budget was the plain
    `sum(charge/rate) <= 1` with no flush term - and `HYDRO#76`, the diesel draw,
    is flow-through on R2 and may run any day at no cost, including days R1 is
    running. Crossing was free, and the optimizer ran both reactors on 5 days
    against the plan's 1.

    The reactors are sequential on one train, so the unit is set up for exactly
    one a day. That `== 1` is what gives the constraint teeth: the first draft
    bounded the indicator below by production and above by nothing, so the model
    held both reactors on permanently, never registered a change, and paid no
    flush at all.
    """
    _, _, spec, dates, _ = solved
    charged = _solve(solved, charge_reactor_flush=True)
    assert charged.status == "optimal", charged.status
    both = [d for d, on in zip(dates, _reactor_days(spec, charged, dates))
            if len(on) > 1]
    assert not both, "both reactors produced on {}".format(both[:3])


def test_the_reactor_flush_can_be_turned_off(solved):
    """v0 is deliberately an LP, and this is the one thing that adds binaries.

    Off, the model must be exactly the LP it was - so the escape hatch is not a
    convenience, it is what keeps `v0 is an LP` a true statement anyone can
    check.
    """
    free = _solve(solved, charge_reactor_flush=False)
    assert free.status == "optimal", free.status
    # charging the flush spends time, so it can never score better
    charged = _solve(solved, charge_reactor_flush=True)
    assert charged.objective >= free.objective - 1e-6


# ------------------------------------------------------------- safety stock
def test_safety_stock_is_off_until_someone_sets_it(solved):
    """Nobody has measured a lower control limit, so the model invents none.

    `RefKind.TARGET_LCL` has had a slot for one since the schema was written and
    the workbook sets none for any product. Days of cover is a stand-in derived
    from demand, not a fact about the tank - so it ships inert, the same way
    `SWITCH_COST_BY_UNIT` and `MIN_RATE_FRACTION` do. A placeholder that changes
    every answer is worse than one that waits.
    """
    _, _, _, _, res = solved
    off = _solve(solved, safety_stock_days=0)
    assert off.objective == pytest.approx(res.objective)


def test_safety_stock_holds_a_floor_without_outranking_a_customer(solved):
    """Turned on, it must keep tanks off the floor - and never at the expense of
    service, which is the failure `terminal_shortfall_per_gal` was tuned away
    from. Priced per gallon-day against the product's own lost sale and spread
    across the horizon, so a gallon held below the floor for the *whole* window
    still costs a fraction of shorting a customer once."""
    from invplanner import model_config as cfg
    ref, scn, spec, dates, _ = solved

    on = _solve(solved, safety_stock_days=3)
    assert on.status == "optimal", on.status

    # the floor is real: it is days of cover on demand, not a tank level
    served = {}
    for b in ref.blocks:
        c = b.get("charge_code")
        p = spec.aliases.get(c, c)
        for row in ("Sales", "Forecast", "Blends"):
            for s, g in spec.demand_rows.get(b["id"], {}).get(row, {}).items():
                if s in {d.isoformat() for d in dates}:
                    served[p] = served.get(p, 0.0) + g
    assert any(v > 0 for v in served.values()), "no demand to derive cover from"

    # and it never buys stock with someone else's order
    assert on.kpis["lost_sales_gal"] <= _solve(
        solved, safety_stock_days=0).kpis["lost_sales_gal"] + 1.0


# --------------------------------------------- the reactor is held, not just fed
def _reactor_visits(res, spec, dates):
    """Length of every stretch the hydrotreater spends on one reactor.

    Read off the setup state rather than production, and carried through idle
    days: a reactor holds its last feed while the unit sits, so an idle day
    continues a visit rather than ending one.
    """
    from invplanner import model_config as cfg
    rof = {l["key"]: cfg.reactor_of("HYDRO", l["workbook_product"])
           for l in spec.charge_lines if l["unit"] == "HYDRO"}
    visits, held = [], None
    for d in dates:
        line = (res.schedule.get("HYDRO") or {}).get(d.isoformat())
        if line is not None:
            held = rof.get(line, held)
        if held is None:
            continue
        if visits and visits[-1][0] == held:
            visits[-1][1] += 1
        else:
            visits.append([held, 1])
    return visits


def test_the_hydrotreater_holds_a_reactor_once_it_goes_there(solved):
    """4315 and 4319 are grouped because the reactor is held, not because it pays.

    The plant does not come off the dedicated reactor to run a Kensol and go
    straight back, and for a long time nothing in the model said so. The flush
    made a crossing cost 0.375 day against 0.125, which the docs predicted would
    group the two R1 products on its own. It did not: run 33 crossed 18 times
    against the plan's 13.

    A minimum on the charge *line* does not fix it either - that lengthens a
    visit while leaving the model free to leave and return, and measured at
    R1 3 / R2 2 over 42 days it took 6 crossings against 3 with no minimum at
    all. The rule is about the reactor, so the constraint is too.

    Only a visit *entered inside the window* can owe a minimum, so two are
    exempt and both for the same reason - the window edge, not a decision:

      * the first, when the unit opens already on the reactor. `day_zero` for the
        hydrotreater is 9705, which is R1, so it habitually does; the visit began
        before the window and how long it has already run is not knowable here.
      * the last, when it runs to the final day. It cannot be proven to complete,
        but the constraint still holds it rather than letting it be abandoned -
        which is why this does not take `minrun`'s exemption. Skipping instead of
        clamping let the model enter R1 on day 16 of 18, run one day and leave
        for R2, a one-day visit with two clear days after it.

    Not solved at the module's 14 days: there the only R1 visit is the truncated
    one at the end, so every assertion below would be vacuously true. Nor pinned
    to one longer horizon - which window happens to hold a complete visit moves
    whenever the constraint set does, and a test that silently stops proving
    anything is worse than one that fails. So it scans, takes the first window
    that yields an interior visit, and fails if none of them does.
    """
    from invplanner import model_config as cfg
    from invplanner.engine import simulate
    from invplanner.modelprep import build
    from invplanner.optimizer import v0
    ref, scn, _, _, _ = solved
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    sim = simulate(ref, scn, physical=True)
    need = cfg.min_reactor_visit_days("HYDRO", "R1")

    for days in (32, 35, 26, 28):
        dates = scn.dates[:days]
        spec = build(ref, scn, sim, horizon=dates, downtime=down)
        res = v0.solve(ref, scn, spec, dict(PARAMS, horizon_days=days,
                                            charge_floor_fraction=0.30),
                       horizon=dates, downtime=down, free_units=["HYDRO"])
        assert res.status == "optimal", "{}d: {}".format(days, res.status)
        visits = _reactor_visits(res, spec, dates)
        interior = [n for i, (r, n) in enumerate(visits)
                    if r == "R1" and 0 < i < len(visits) - 1]
        if not interior:
            continue
        short = [n for n in interior if n < need]
        assert not short, "{}d: R1 visits of {} days, minimum is {}".format(
            days, short, need)
        return

    assert False, "no window held a complete R1 visit - the test proves nothing"


def test_a_unit_with_one_reactor_gains_no_visit_constraint(solved):
    """The visit rule must not leak onto units that never asked for one.

    `min_reactor_visit_days` returns 1 for anything without a figure, and 1 is
    the same "no constraint" convention `min_run_days` uses - so extraction and
    MEK build exactly the model they built before.
    """
    from invplanner import model_config as cfg
    assert cfg.min_reactor_visit_days("MEK", "R1") == 1
    assert cfg.min_reactor_visit_days("EXTRACT", "R1") == 1
    assert cfg.min_reactor_visit_days("HYDRO", "R2") == 1
    assert cfg.min_reactor_visit_days("HYDRO", "R1") > 1


# ------------------------------------------------ bounding a runaway solve
def test_the_watchdog_leaves_an_ordinary_solve_alone(solved):
    """It must be invisible on every run that behaves.

    The backstop exists for the tail - 900s budgets that have returned in
    1,808s, 5,544s and once 29,550s - and the cost of getting it wrong is
    killing a solve that was going to finish. So the normal path is asserted
    explicitly rather than assumed: no timeout status, and no overrun note on a
    run that came in on budget.
    """
    _, _, _, _, res = solved
    assert res.status == "optimal", res.status
    assert "was killed" not in (res.message or "")
    assert "against a" not in (res.message or "")


def test_the_watchdog_only_ever_kills_this_process_own_solver():
    """Scoped to our own children, and inert without psutil.

    Killing by image name would reach a solve running in another window or
    another worker of the same app. A planner losing someone else's run to a
    watchdog they never set is worse than the hang being bounded.
    """
    from invplanner.optimizer.v0 import _Watchdog

    # fires against a process holding no CBC child: finds nothing, kills nothing
    w = _Watchdog(0.01)
    if w._thread is not None:
        w._thread.join(timeout=5)
    assert w.fired is False
    w.cancel()

    # and a cancelled watchdog never fires, however long its deadline was
    w2 = _Watchdog(0.01)
    w2.cancel()
    if w2._thread is not None:
        w2._thread.join(timeout=5)
    assert w2.fired is False
