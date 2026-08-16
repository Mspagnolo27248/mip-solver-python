"""v1 decides what MEK and extraction charge, and must obey the ladder.

The formulation imposes adjacency, one setup at a time, and a minimum campaign.
It does **not** impose the sweep up and down the ladder that the plant actually
runs - that is meant to emerge from inventory pressure. These tests pin the
constraints, not the emergent shape, because pinning the shape would hide the
signal: if the sweep stops appearing, the transition data is wrong.
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

SEED = os.path.join(ROOT, "data", "seed")
pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SEED, "reference.json")),
    reason="seed data not built; run scripts/seed.py")

DAYS = 42
#: A 2% gap on purpose. Proving optimality on this model takes ~51 minutes and
#: buys 0.47% of objective, because the objective is nearly flat across many
#: schedules; the gap costs eleven seconds. These tests check the constraints,
#: and a constraint is violated or it is not - optimality has no bearing on it.
PARAMS = {"lost_sale_margin_per_gal": 1.50, "downgrade_discount_per_gal": 0.50,
          "netback_diesel_cost_per_gal": 0.3667,
          "netback_gasoline_cost_per_gal": 0.22,
          "horizon_days": DAYS, "time_limit_seconds": 120, "mip_gap": 0.02,
          "charge_floor_fraction": 0.3, "switch_cost": 2000.0}


@pytest.fixture(scope="module")
def solved():
    from invplanner import model_config as cfg
    from invplanner.engine import Reference, Scenario, simulate
    from invplanner.modelprep import build
    from invplanner.optimizer import v1

    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:DAYS]
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, sim, horizon=dates, downtime=down)
    res = v1.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=down)
    assert res.status == "optimal", res.status
    return ref, scn, spec, dates, res


def test_the_unit_is_set_up_for_exactly_one_line_every_day(solved):
    """Including idle days. The setup persists so the unit still owes a flush
    whenever it next changes - a model that linked only running days would let it
    idle for a day and restart on anything for free."""
    _, _, _, dates, res = solved
    for unit, sched in res.schedule.items():
        assert len(sched) == len(dates), unit
        assert all(v is not None for v in sched.values()), unit


def test_no_transition_outside_the_allowed_set(solved):
    """Forbidden transitions get no variable at all, so the solver cannot reach
    for them. This asserts the arc set is what was configured, and that the
    schedule never contains a pair outside it."""
    from invplanner import model_config as cfg
    _, _, spec, _, res = solved

    for unit, sched in res.schedule.items():
        ulines = [l for l in spec.charge_lines if l["unit"] == unit]
        allowed = set(cfg.line_arcs(unit, ulines) or [])
        assert allowed, unit
        days = sorted(sched)
        for a, b in zip(days, days[1:]):
            frm, to = sched[a], sched[b]
            if frm == to:
                continue
            assert (frm, to) in allowed, (
                "{}: {} -> {} on {} is not an allowed transition".format(
                    unit, frm, to, b))


def test_extraction_can_switch_mode_without_changing_feed(solved):
    """Normal and deep extraction both charge 9305 at different rates. Keyed on
    the product that switch is 9305 -> 9305, a self-loop the arc formulation
    cannot represent - which is why the setup state is the line."""
    from invplanner import model_config as cfg
    _, _, spec, _, _ = solved
    ulines = [l for l in spec.charge_lines if l["unit"] == "EXTRACT"]
    arcs = set(cfg.line_arcs("EXTRACT", ulines) or [])

    assert ("EXTRACT#90", "EXTRACT#91") in arcs
    assert ("EXTRACT#91", "EXTRACT#90") in arcs
    feed = {l["key"]: l["workbook_product"] for l in ulines}
    assert feed["EXTRACT#90"] == feed["EXTRACT#91"] == "9305"
    # ...and the two modes really do have different ceilings
    assert (cfg.max_rate_info("EXTRACT", "9305", "EXTRACT#91")["bbl"]
            < cfg.max_rate_info("EXTRACT", "9305", "EXTRACT#90")["bbl"])


def test_minimum_campaign_lengths_are_respected(solved):
    """A stock that is started stays on the unit for its minimum run.

    Two kinds of campaign are not evidence either way and are skipped: one
    already running when the window opens, and one starting so late that its
    minimum run would extend past the last day. The model deliberately does not
    constrain the second - requiring days that do not exist would either be
    infeasible or force a campaign to the horizon edge for no physical reason -
    so a short campaign in the closing days is an end effect, not a violation.
    """
    from invplanner import model_config as cfg
    _, _, spec, _, res = solved

    for unit, sched in res.schedule.items():
        by_line = {l["key"]: l for l in spec.charge_lines if l["unit"] == unit}
        days = sorted(sched)
        runs, start = [], 0
        for i in range(1, len(days) + 1):
            if i == len(days) or sched[days[i]] != sched[days[start]]:
                runs.append((sched[days[start]], start, i - start))
                start = i
        for key, at, length in runs:
            if at == 0:
                continue          # carried in; the model never started it
            need = cfg.min_run_days_for_line(
                unit, key, by_line[key]["workbook_product"])
            if at + need > len(days):
                continue          # its window runs past the horizon
            assert length >= need, (
                "{} ran {} for {} day(s), minimum {}".format(
                    unit, key, length, need))


def test_charge_never_exceeds_the_line_rate(solved):
    """charge = rate x time, and time is at most a whole day."""
    from invplanner import model_config as cfg
    _, _, spec, _, res = solved
    for unit in res.schedule:
        for l in [x for x in spec.charge_lines if x["unit"] == unit]:
            ceiling = cfg.max_rate_info(unit, l["workbook_product"],
                                        l["key"])["bbl"]
            for day, bbl in res.charge.get(l["key"], {}).items():
                assert bbl <= ceiling + 1e-6, (l["key"], day, bbl, ceiling)


def test_the_freed_units_keep_running(solved):
    """Must-run binds the day's *time*, not the `ran` indicator and not volume.

    Binding the indicator is vacuous - `ran = 1` with `t = 0` is a unit that is
    notionally on and charging nothing, which is the non-answer must-run exists
    to forbid. Binding the budget makes the day fully allocated between charging
    and changing over, and that is what this asserts, through its consequence:
    the unit charges on the great majority of days it is available.

    **It does not bound volume.** Charge is free in `[0, rate x t]` while minimum
    rates are zero, so a freed unit can allocate its day and still charge very
    little. That gap closes when minimum rates are filled in, or when the
    objective rewards production instead of only penalising its absence - see
    MIN_RATE_FRACTION and MARGIN-OBJECTIVE.md. Until then this is a floor on the
    behaviour, not a guarantee of it.
    """
    _, _, _, _, res = solved
    shape = res.kpis.get("campaign_shape") or {}
    assert shape, "a freed unit must report its campaign shape"
    for unit, c in shape.items():
        # What must-run guarantees: the unit holds a setup and its day is
        # allocated, every available day.
        assert c["days_available"] > 0, unit
        # What it does not: that anything is actually charged. The count is
        # asserted to exist rather than to be zero, because zero is not true
        # today and pretending otherwise would hide the one number that says
        # what minimum rates are worth.
        assert "days_allocated_without_charge" in c, unit
        assert 0 <= c["days_allocated_without_charge"] <= c["days_available"]


def test_v0_is_unchanged_when_no_unit_is_freed(solved):
    """v1 is the same code with `free_units` set. With it empty the model must be
    bit-for-bit the v0 it always was - the shared balance is the whole reason the
    two are one function."""
    from invplanner import model_config as cfg
    from invplanner.optimizer import v0
    ref, scn, spec, dates, _ = solved
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}

    a = v0.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=down)
    b = v0.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=down,
                 free_units=[])
    assert a.status == b.status == "optimal"
    assert a.kpis["lost_sales_gal"] == pytest.approx(b.kpis["lost_sales_gal"])
    assert a.kpis["downgrade_gal"] == pytest.approx(b.kpis["downgrade_gal"])
    assert not a.schedule and not b.schedule


def test_a_time_limited_run_is_never_called_optimal(solved):
    """PuLP's CBC reader rewrites the status, so `status` cannot be trusted.

    When CBC stops on a time limit holding an incumbent it writes "Stopped on
    time limit ... objective X", and `coin_api.get_status` turns that into
    `LpStatusOptimal`, recording the truth only in `sol_status`, which
    `LpProblem.solve()` does not return. Read the wrong field and every
    time-limited run claims to be proved best.

    It was not cosmetic. At 100 days this model reported "Optimal" at every time
    limit while `sol_status` never once said Optimal Solution Found - and the
    incumbent at a 20 s limit scored 10,095,452 against 8,114,383 at 200 s, so a
    schedule 20% worse than achievable was being presented as the best there is.

    One second is far too little for a model with binaries, so the only
    honest answers are `feasible` or `no_solution`.
    """
    from invplanner import model_config as cfg
    from invplanner.optimizer import v1
    ref, scn, spec, dates, _ = solved
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}

    r = v1.solve(ref, scn, spec, dict(PARAMS, time_limit_seconds=1),
                 horizon=dates, downtime=down)
    assert r.status != "optimal", (
        "a 1s limit cannot prove optimality; PuLP's rewritten status is being "
        "read instead of sol_status")
    assert r.status in ("feasible", "no_solution"), r.status
    if r.status == "feasible":
        assert "NOT proved optimal" in (r.message or "")


# --------------------------------------------------- changeover cost, per unit
def _switches(res, unit):
    """Changeovers the schedule actually contains, for one unit."""
    sched = res.schedule[unit]
    days = sorted(sched)
    return sum(1 for a, b in zip(days, days[1:]) if sched[a] != sched[b])


def test_switch_cost_precedence_is_most_specific_wins():
    """Arc beats unit beats global, and the scenario's override beats the
    committed table at the same specificity - a planner calibrating against
    campaign lengths must not have to edit code to do it."""
    from invplanner import model_config as cfg

    assert cfg.switch_cost_for("MEK", "9117", "9119", 2000.0) == 2000.0
    assert cfg.switch_cost_for("MEK", "9117", "9119", 2000.0,
                               {"MEK": 50.0}) == 50.0
    # a figure for one unit must not leak into another
    assert cfg.switch_cost_for("EXTRACT", "9302", "9303", 2000.0,
                               {"MEK": 50.0}) == 2000.0

    cfg.SWITCH_COST_BY_UNIT["ROSE"] = 9000.0
    cfg.SWITCH_COST_BY_ARC["HYDRO"] = {("9711", "9713"): 777.0}
    try:
        assert cfg.switch_cost_for("ROSE", "4313", "4555", 2000.0) == 9000.0
        assert cfg.switch_cost_for("ROSE", "4313", "4555", 2000.0,
                                   {"ROSE": 1.0}) == 1.0
        # the arc's own figure outranks everything, including the override
        assert cfg.switch_cost_for("HYDRO", "9711", "9713", 2000.0,
                                   {"HYDRO": 1.0}) == 777.0
        assert cfg.switch_cost_for("HYDRO", "9711", "9712", 2000.0,
                                   {"HYDRO": 1.0}) == 1.0
    finally:
        cfg.SWITCH_COST_BY_UNIT.pop("ROSE", None)
        cfg.SWITCH_COST_BY_ARC.pop("HYDRO", None)


def test_changing_mode_without_changing_feed_still_costs():
    """Extraction moves between normal and deep mode without changing feed, which
    is 9305 -> 9305 at product level and a real changeover on the unit.

    `changeover_loss_days` returns zero for a self-pair, and copying that here
    would price the mode flip at nothing - so the model would flip it daily and
    the arc set's whole reason for being keyed on the line would be undone."""
    from invplanner import model_config as cfg
    assert cfg.switch_cost_for("EXTRACT", "9305", "9305", 2000.0) == 2000.0


def test_no_per_unit_cost_reproduces_the_single_scalar(solved):
    """The default has to be the model that existed before per-unit costs did.

    Not "close": the objective must land on the same number, because with every
    table empty the new expression is the old one with the constant factored
    into the sum."""
    from invplanner import model_config as cfg
    from invplanner.optimizer import v1
    ref, scn, spec, dates, res = solved
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}

    same = v1.solve(ref, scn, spec, dict(PARAMS, switch_cost_by_unit=None),
                    horizon=dates, downtime=down)
    assert same.status == "optimal"
    assert same.objective == res.objective


def test_pricing_one_unit_lengthens_that_unit(solved):
    """The point of the whole change: a cost aimed at one unit reaches that unit.

    Only MEK is asserted. Extraction is free to switch *more* - MEK holding a
    campaign longer changes what arrives downstream, and relieving that is
    exactly what the model is for - so pinning both would be pinning an emergent
    shape rather than the mechanism."""
    from invplanner import model_config as cfg
    from invplanner.optimizer import v1
    ref, scn, spec, dates, res = solved
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}

    dear = v1.solve(ref, scn, spec,
                    dict(PARAMS, switch_cost_by_unit={"MEK": 50_000.0}),
                    horizon=dates, downtime=down)
    # `feasible` and not `optimal`: pricing one unit's changeovers at $50k makes
    # the search harder than the baseline's, and it stops on the time limit with
    # an incumbent. That is the honest label now that `sol_status` is read rather
    # than PuLP's rewritten `status` - this assertion said `optimal` until the
    # rewrite was found, and it was never true.
    #
    # It only weakens the comparison in the safe direction. The baseline is a
    # proved optimum and this is an incumbent, so a *better* schedule may exist
    # for the expensive case - and a better one switches less, not more. The
    # margin is 12 against 6 either way.
    assert dear.status in ("optimal", "feasible"), dear.status
    assert _switches(dear, "MEK") < _switches(res, "MEK")
