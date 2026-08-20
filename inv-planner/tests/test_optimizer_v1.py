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
#: schedules. These tests check the constraints, and a constraint is violated or
#: it is not - optimality has no bearing on it.
#:
#: The gap is *relative*, so it tightened on its own when the phantom downgrade
#: charge came out of the objective: the same 2% that used to allow tens of
#: thousands of dollars of slack now allows about twelve, and CBC no longer
#: closes it inside the limit. Measured at 42 days: 2% runs out of time at 151 s
#: holding an objective of 379,196, while 10% proves in 54 s - on a *worse*
#: incumbent, 385,124. The schedule is not the problem; the proof is.
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
    # A time-limited incumbent is a real schedule and obeys every constraint
    # these tests check, so `feasible` is as good as `optimal` here - insisting
    # on the proof tested the solver's speed rather than the formulation, and
    # broke the whole module the moment the objective got flatter. What must
    # still fail loudly is a run that found nothing: `infeasible`, `no_solution`
    # or an error means there is no schedule to assert anything about.
    assert res.status in ("optimal", "feasible"), res.status
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
    # Determinism is the property under test, not optimality: CBC given the same
    # model twice returns the same incumbent, so the objectives must match to the
    # cent whether or not either was proved best.
    assert same.status in ("optimal", "feasible"), same.status
    assert same.objective == res.objective


#: Short on purpose - see `test_pricing_one_unit_lengthens_that_unit`.
SHORT_DAYS = 14


@pytest.fixture(scope="module")
def short_horizon():
    """A horizon small enough that both sides of the comparison prove optimality."""
    from invplanner import model_config as cfg
    from invplanner.engine import Reference, Scenario, simulate
    from invplanner.modelprep import build

    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:SHORT_DAYS]
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, sim, horizon=dates, downtime=down)
    return ref, scn, spec, dates, down


def test_pricing_one_unit_lengthens_that_unit(short_horizon):
    """The point of the whole change: a cost aimed at one unit reaches that unit.

    **Both solves must prove optimality, and that is why this runs on 14 days
    rather than the module's 42.** Pricing MEK's changeovers at $50k makes the
    search much harder than the baseline's, and at 42 days it stops on the time
    limit - so the comparison was a proved optimum against an unproven
    incumbent, whose quality varies run to run. It passed at 12 switches against
    6, then failed on the same code at 12 against 12, because an incumbent is
    not a measurement. The defect was in this test, not the model: the
    objectives are identical across those runs.

    That only became visible once `sol_status` was read instead of PuLP's
    rewritten `status` - before that the expensive run *claimed* to be optimal
    and the comparison looked sound.

    Only MEK is asserted. Extraction is free to switch more: MEK holding a
    campaign changes what arrives downstream, and relieving that is what the
    model is for, so pinning both would pin an emergent shape rather than the
    mechanism.
    """
    from invplanner.optimizer import v1
    ref, scn, spec, dates, down = short_horizon
    params = dict(PARAMS, horizon_days=SHORT_DAYS, time_limit_seconds=300)

    base = v1.solve(ref, scn, spec, params, horizon=dates, downtime=down)
    dear = v1.solve(ref, scn, spec,
                    dict(params, switch_cost_by_unit={"MEK": 50_000.0}),
                    horizon=dates, downtime=down)

    assert base.status == "optimal", base.status
    assert dear.status == "optimal", dear.status
    assert _switches(dear, "MEK") < _switches(base, "MEK")


# ------------------------------------------------------- v2: the hydrotreater
@pytest.fixture(scope="module")
def v2_solved(solved):
    """One v2 solve shared by both tests - it costs about a minute on its own."""
    from invplanner import model_config as cfg
    from invplanner.optimizer import v0, v2
    ref, scn, spec, dates, _ = solved
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    params = dict(PARAMS, time_limit_seconds=300)
    first = v0.solve(ref, scn, spec, params, horizon=dates, downtime=down,
                     free_units=["MEK", "EXTRACT"])
    both = v2.solve(ref, scn, spec, params, horizon=dates, downtime=down)
    return first, both, spec, dates

def test_v2_frees_all_three_units_by_solving_them_in_turn(v2_solved):
    """Freeing all three at once does not finish; freeing them in turn does.

    Measured before this was written: the joint model proves optimality only over
    7 days (116 s) and times out at 10, 14, 21 and 42. Seven days is shorter than
    a single ROSE campaign, so a rolling horizon built on it would decide a
    40-day campaign a week at a time - which is why the decomposition is by unit
    and not by time.

    Three things were tried and did not fix it, so nobody repeats them: relaxing
    the arcs to continuous (correct, and kept, but not sufficient), a real
    changeover cost on the hydrotreater (cuts its switching 9 -> 6 and still
    cannot close the gap), and a shorter horizon.
    """
    res = v2_solved[1]
    # Each stage must *solve*. It no longer has to prove optimality, and asking
    # for the proof would make this test select a worse schedule: measured at 42
    # days, a gap loose enough to close in time stops on 385,124 while a tighter
    # one finds 379,196 and cannot prove it. The tractability claim is the one
    # worth pinning; the proof was a bonus the flatter objective removed.
    assert res.status in ("optimal", "feasible"), (res.status, res.message)
    assert set(res.schedule) == {"MEK", "EXTRACT", "HYDRO"}, sorted(res.schedule)

    stages = res.kpis["stages"]
    assert [s["units"] for s in stages] == [["MEK", "EXTRACT"], ["HYDRO"]]
    assert all(s["status"] in ("optimal", "feasible") for s in stages), stages

    # ...and the guarantee on offer is named, because "optimal" on a two-stage
    # solve means something weaker than it does on a single one.
    assert res.kpis["globally_optimal"] is False
    # Present whatever else the message carries. A stage that stops on its time
    # limit makes v0 write its own warning, and the caveat used to be dropped
    # rather than added to - losing it on exactly the run that needs it most.
    assert "not a joint optimum" in res.message, res.message
    if any(st["status"] != "optimal" for st in res.kpis["stages"]):
        assert "NOT proved optimal" in res.message, res.message


def test_v2_hands_stage_one_on_untouched(v2_solved):
    """Stage two is *pinned* to stage one's schedule, not floored at it.

    Left to the charge floor, stage two could take 70% of stage one's decision
    back and the two answers would not compose into one schedule. Pinned with a
    hair of tolerance rather than a flat equality: stage one drains 9302 to
    exactly zero, and an exact pin makes the balance infeasible by a margin the
    elastic diagnostic reports as literally zero.
    """
    first, both, spec, dates = (v2_solved[0], v2_solved[1],
                                v2_solved[2], v2_solved[3])
    iso = [d.isoformat() for d in dates]
    for unit in ("MEK", "EXTRACT"):
        for line in [l for l in spec.charge_lines if l["unit"] == unit]:
            a = sum(first.charge.get(line["key"], {}).get(s, 0.0) for s in iso)
            b = sum(both.charge.get(line["key"], {}).get(s, 0.0) for s in iso)
            assert b == pytest.approx(a, rel=1e-4), (line["key"], a, b)
