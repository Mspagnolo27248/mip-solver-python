"""The rule-based scheduler obeys the plant's rules, and the simulator agrees.

Two kinds of test here, and the distinction matters because one of them cannot be
written.

**Invariants** are the plant's own rules - the MEK ladder, minimum campaigns, the
reactor visit, outages, rate ceilings. A schedule that breaks one of these is not
a worse answer, it is an answer the plant cannot run, so these are hard asserts.

**Quality is not asserted anywhere.** `greedy` returns an incumbent with no bound,
and `tests/test_optimizer_v1.py` already records why comparing one of those against
a proved optimum is not a measurement. The bake-off against v0/v1/v2 lives in the
module docstring where it can be read and argued with; putting it in an assertion
would pin a number nobody can defend and fail on the day CBC returns a different
incumbent.
"""
import copy
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

SEED = os.path.join(ROOT, "data", "seed")
DAYS = 42

PARAMS = {
    "objective": "cost",
    "lost_sale_margin_per_gal": 1.50,
    "downgrade_discount_per_gal": 0.50,
    "netback_diesel_cost_per_gal": 0.3667,
    "netback_gasoline_cost_per_gal": 0.22,
    "switch_cost": 2000.0,
    "charge_floor_fraction": 0.30,
}

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SEED, "reference.json")),
    reason="seed data not built; run scripts/seed.py")


@pytest.fixture(scope="module")
def solved():
    """One pass, shared by every test. It costs hundredths of a second."""
    from invplanner import model_config as cfg
    from invplanner.engine import Reference, Scenario, simulate
    from invplanner.modelprep import build
    from invplanner.optimizer import greedy

    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:DAYS]
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, sim, horizon=dates, downtime=down)
    res = greedy.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=down)
    assert res.status == "feasible", res.status
    return ref, scn, spec, dates, down, res


def _lines(spec, unit):
    return [l for l in spec.charge_lines if l["unit"] == unit]


def _key_to_line(spec):
    return {l["key"]: l for l in spec.charge_lines}


# ------------------------------------------------------------ the contract

def test_it_never_claims_to_be_optimal(solved):
    """`optimal` is a proof, and nothing here proves anything.

    The status also has to stay inside `("optimal", "feasible")` or
    `optimizer_service` short-circuits before writing a scenario, running the
    verifier, or recording a single KPI - the run would surface as a bare status
    string with no result at all.
    """
    *_, res = solved
    assert res.status == "feasible"
    assert res.objective is None, "an objective with no bound invites comparison"
    assert res.solver == "greedy"


def test_the_simulator_agrees_the_schedule_can_be_run(solved):
    """The real gate. The simulator is an independent implementation of the same
    physics, validated against the workbook cell by cell, so replaying the
    proposed schedule through it is the only honest check that the schedule does
    what the scheduler claims.
    """
    from invplanner.engine import Scenario
    from invplanner.optimizer import verify

    ref, scn, spec, dates, down, res = solved
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

    chk = verify.verify(ref, Scenario(data), res.kpis, dates, spec, downtime=down)
    assert chk["executable"], chk["note"]          # no feed charged that is not there
    assert chk["fully_routed"], chk["note"]        # no overflow left with nowhere to go
    assert chk["verified"], chk["note"]


def test_the_downgrade_it_claims_is_the_downgrade_it_wrote(solved):
    """`verify` reads executed downgrade off the *schedule*, never off `downgrade`.

    So the claim has to be the transfers, in gallons. Getting this wrong reports a
    disagreement where there is none, which is the worst kind of referee failure:
    a real one can then hide behind the noise.
    """
    from invplanner.engine import GAL_PER_BBL

    *_, res = solved
    written = sum(v for d in res.transfer_bbl.values() for v in d.values())
    assert abs(res.kpis["downgrade_gal"] - written * GAL_PER_BBL) < 1.0


def test_every_sink_line_is_written_on_every_day(solved):
    """Dense, or the planner's own transfers survive into the result.

    `_write_result_scenario` starts from a copy of their grid, so a sink line left
    out on a day keeps whatever they had entered - and the schedule the simulator
    replays is then not the one that was built.
    """
    from invplanner import model_config as cfg

    _, _, spec, dates, _, res = solved
    outlets = {s["outlet"] for s in cfg.SINKS.values()}
    expected = {s.isoformat() for s in [d for d in dates]}
    sink_keys = {l["key"] for l in spec.charge_lines if l["unit"] in outlets}
    written = set(res.transfer_bbl)
    assert written, "no sink lines written at all"
    for key in written:
        assert key in sink_keys
        assert set(res.transfer_bbl[key]) == expected, key


def test_idled_lines_carry_an_explicit_zero(solved):
    """A freed unit's unused lines must say zero, not say nothing.

    This is the bug v2 had to fix by merging its stage-one charges "zeros and
    all": a `(line, day)` omitted from `charge` leaves the planner's barrels
    standing in the result, so the unit appears to run two feeds at once.
    """
    from invplanner.optimizer import greedy

    _, _, spec, dates, down, res = solved
    for unit in greedy.FREE_UNITS:
        for line in _lines(spec, unit):
            for d in dates:
                if d in down.get(unit, set()):
                    continue
                assert d.isoformat() in res.charge.get(line["key"], {}), \
                    (line["key"], d)


# --------------------------------------------------------- the plant's rules

def test_nothing_is_charged_on_an_outage_day(solved):
    """Zero tolerance in `verify`, so zero tolerance here."""
    _, _, spec, dates, down, res = solved
    for key, series in res.charge.items():
        unit = key.split("#")[0]
        for d in dates:
            if d in down.get(unit, set()):
                assert not series.get(d.isoformat()), (key, d)


def test_a_unit_runs_one_feed_a_day_and_only_one(solved):
    """One setup a day, and so at most one changeover between two days.

    `MAX_CHANGEOVERS_PER_DAY` says two and nothing reads it. One is what makes the
    transition restriction *hard*: with two, a unit can reach a pair the plant has
    never run by going through an intermediate, and the MIP was observed doing
    exactly that.

    The schedule carries one setup per unit per day by construction, so what is
    worth checking is that the *charges* agree with it - a second line charged on
    a day the unit is set up elsewhere would be two feeds at once, which is the
    thing the setup state exists to forbid.
    """
    from invplanner.optimizer import greedy

    _, _, spec, dates, down, res = solved
    for unit in greedy.FREE_UNITS:
        for d in dates:
            s = d.isoformat()
            setup = res.schedule[unit].get(s)
            running = [l["key"] for l in _lines(spec, unit)
                       if res.charge.get(l["key"], {}).get(s, 0.0) > 1e-6]
            assert len(running) <= 1, (unit, s, running)
            if running:
                assert running[0] == setup, (unit, s, running[0], setup)


def test_mek_walks_its_ladder_one_rung_at_a_time(solved):
    """All 56 observed changeovers move exactly one rung on the viscosity ladder.

    So MEK cannot jump to whatever product is shortest - it can only step toward
    it. A scheduler that picks purely on cover will try to jump, which is why the
    arc set is consulted before the score.
    """
    from invplanner import model_config as cfg

    _, _, spec, dates, _, res = solved
    ladder = cfg.TRANSITIONS["MEK"]["ladder"]
    keyed = _key_to_line(spec)
    seen = [res.schedule["MEK"].get(d.isoformat()) for d in dates]
    for before, after in zip(seen, seen[1:]):
        if before is None or after is None or before == after:
            continue
        a = ladder.index(keyed[before]["workbook_product"])
        b = ladder.index(keyed[after]["workbook_product"])
        assert abs(a - b) == 1, (before, after, "skipped a rung")


def test_no_unit_takes_a_changeover_the_plant_has_never_made(solved):
    """Extraction is the restricted one: 9 of 12 line pairs, and the asymmetry is
    the point - deep extract may return to 9302, normal extract may not.
    """
    from invplanner import model_config as cfg
    from invplanner.optimizer import greedy

    _, _, spec, dates, _, res = solved
    for unit in greedy.FREE_UNITS:
        allowed = set(cfg.line_arcs(unit, _lines(spec, unit)) or [])
        if not allowed:
            continue
        seen = [res.schedule[unit].get(d.isoformat()) for d in dates]
        for before, after in zip(seen, seen[1:]):
            if before is None or after is None or before == after:
                continue
            assert (before, after) in allowed, (unit, before, after)


def test_a_campaign_lasts_at_least_its_minimum(solved):
    """A line that starts must run its minimum before the unit may leave it.

    The final campaign is exempt: it is cut short by the horizon ending, not by a
    decision, and asserting on it would fail for a reason that is not a defect.
    """
    from invplanner import model_config as cfg
    from invplanner.optimizer import greedy

    _, _, spec, dates, down, res = solved
    keyed = _key_to_line(spec)
    for unit in greedy.FREE_UNITS:
        seen = [res.schedule[unit].get(d.isoformat()) for d in dates]
        runs, cur, length = [], object(), 0
        for setup in seen:
            if setup != cur:
                if length:
                    runs.append((cur, length))
                cur, length = setup, 0
            length += 1
        for key, ran in runs[1:]:          # the first is inherited, not started
            if key is None:
                continue
            need = cfg.min_run_days_for_line(unit, key,
                                             keyed[key]["workbook_product"])
            assert ran >= need, (unit, key, ran, need)


def test_no_line_runs_above_its_own_ceiling(solved):
    """Keyed by **line**, which is the trap.

    `EXTRACT#90` is 2,200 bbl/day and `EXTRACT#91` is 1,300, and both charge 9305.
    Read at product level the deep line inherits the shallow one's ceiling and the
    model schedules 69% more deep extract than the unit can make - feeding a
    hydrotreater campaign that could never have run.
    """
    from invplanner.optimizer import v0

    _, _, spec, dates, _, res = solved
    for line in spec.charge_lines:
        info = v0.line_rate(spec, line["unit"], line["key"],
                            line["workbook_product"])
        if not info:
            continue
        for d in dates:
            bbl = res.charge.get(line["key"], {}).get(d.isoformat(), 0.0)
            assert bbl <= info["bbl"] + 1e-6, (line["key"], d, bbl, info["bbl"])


def test_deep_extraction_keeps_its_own_slower_rate(solved):
    """The specific case the line-level table exists for, pinned on its own."""
    from invplanner.optimizer import v0

    _, _, spec, dates, _, res = solved
    deep = v0.line_rate(spec, "EXTRACT", "EXTRACT#91", "9305")
    assert deep["bbl"] == 1300
    for d in dates:
        assert res.charge.get("EXTRACT#91", {}).get(d.isoformat(), 0.0) <= 1300 + 1e-6


def test_the_hydrotreater_holds_a_reactor_once_it_goes_there(solved):
    """R1 is a four-day minimum visit, and it is a rule about the *reactor*.

    A line minimum lengthens a campaign and does nothing to stop the unit leaving
    R1 for a Kensol and coming straight back; holding the reactor is what puts
    9704 and 9705 in the same visit, which is the grouping operations describe.
    Visits cut short by the horizon or by an outage are not decisions.
    """
    from invplanner import model_config as cfg

    _, _, spec, dates, down, res = solved
    keyed = _key_to_line(spec)
    need = cfg.min_reactor_visit_days("HYDRO", "R1")
    seen = []
    for d in dates:
        key = res.schedule["HYDRO"].get(d.isoformat())
        seen.append(None if key is None
                    else cfg.reactor_of("HYDRO", keyed[key]["workbook_product"]))
    runs, cur, length = [], object(), 0
    for r in seen:
        if r != cur:
            if length:
                runs.append((cur, length))
            cur, length = r, 0
        length += 1
    for reactor, ran in runs[1:]:
        if reactor == "R1":
            assert ran >= need, ("R1 visit too short", ran, need)


def test_it_answers_in_well_under_a_second(solved):
    """The reason this module exists. v2 needs about a minute over 42 days.

    Loose enough not to fail on a slow machine, tight enough that losing an order
    of magnitude shows up.
    """
    *_, res = solved
    assert res.solve_seconds < 5.0, res.solve_seconds
