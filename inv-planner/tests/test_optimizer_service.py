"""The run path scores both sides of its comparison over the same days.

A run reports its result against the plan it started from, and the whole value of
that comparison is that the two numbers are commensurable. They stop being so the
moment the model solves fewer days than the service asked for, which is what
`cfg.clamp_horizon` does whenever the horizon runs past the last day the planner
has filled in a charge.

Refereed against the seed plan at 180 days - clamped to 162 - the old code scored
an 18.8 M gal baseline against a 162-day answer and reported lost sales down
4.4%. Over the days both sides actually covered the plan loses 15.0 M and the run
loses 18.0 M: 19.6% *worse*. A referee may be wrong; it may not be wrong in the
direction that flatters the thing it is refereeing, which is why this is pinned.
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

SEED = os.path.join(ROOT, "data", "seed")

#: Past the seed plan's last filled-in charge (2026-12-31, day 162), so the
#: clamp fires. At 42 days nothing clamps and the bug is invisible.
ASK = 180

PARAMS = {
    "objective": "cost",
    "lost_sale_margin_per_gal": 1.50,
    "downgrade_discount_per_gal": 0.50,
    "netback_diesel_cost_per_gal": 0.3667,
    "netback_gasoline_cost_per_gal": 0.22,
    "switch_cost": 2000.0,
    "charge_floor_fraction": 0.30,
    "horizon_days": ASK,
}

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SEED, "reference.json")),
    reason="seed data not built; run scripts/seed.py")


@pytest.fixture(scope="module")
def clamped():
    """A greedy run asked for more days than the plan can answer for."""
    from invplanner import model_config as cfg
    from invplanner.engine import Reference, Scenario, simulate
    from invplanner.modelprep import build
    from invplanner.optimizer import greedy

    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    sim = simulate(ref, scn, physical=True)
    dates = scn.dates[:ASK]
    down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
    spec = build(ref, scn, sim, horizon=dates, downtime=down)
    res = greedy.solve(ref, scn, spec, PARAMS, horizon=dates, downtime=down)
    return ref, scn, spec, dates, res


def test_the_seed_plan_still_clamps_at_this_horizon(clamped):
    """Guards the fixture itself.

    Every other test here passes trivially if nothing is truncated, so a change
    to the seed schedule that pushed its last charge past day 180 would quietly
    turn this whole module into a no-op rather than failing.
    """
    *_, res = clamped
    assert res.kpis["horizon_truncated_days"] > 0, "nothing was clamped; ASK is too short"
    assert res.kpis["days"] < ASK


def test_the_scored_window_is_the_window_the_model_solved(clamped):
    from invplanner.db.optimizer_service import _solved_window

    *_, dates, res = clamped
    assert len(_solved_window(dates, res)) == res.kpis["days"]


def test_a_model_that_reports_nothing_usable_keeps_the_full_window(clamped):
    """The fallback is deliberate: an unreadable count must not narrow anything."""
    from invplanner.db.optimizer_service import _solved_window

    *_, dates, _ = clamped

    class Result:
        def __init__(self, kpis):
            self.kpis = kpis

    for kpis in (None, {}, {"days": 0}, {"days": -3}, {"days": ASK + 50},
                 {"days": "lots"}):
        assert len(_solved_window(dates, Result(kpis))) == len(dates), kpis


def test_baseline_and_result_are_scored_over_the_same_days(clamped):
    """The bug itself: the comparison the run card divides to get its percentage."""
    from invplanner.db.optimizer_service import _baseline_kpis, _solved_window

    ref, scn, spec, dates, res = clamped
    scored = _solved_window(dates, res)
    baseline = _baseline_kpis(ref, scn, spec, scored)
    assert baseline["days"] == res.kpis["days"]


def test_the_extra_days_really_did_move_the_answer(clamped):
    """Proof this is worth a test, not a tidy-up.

    Scoring the plan over the days the model never planned adds demand to the
    baseline and to nothing else, so the run reads better than it is. Asserting
    the direction rather than the figures: the numbers move with the seed data,
    the sign does not.
    """
    from invplanner.db.optimizer_service import _baseline_kpis, _solved_window

    ref, scn, spec, dates, res = clamped
    honest = _baseline_kpis(ref, scn, spec, _solved_window(dates, res))
    inflated = _baseline_kpis(ref, scn, spec, dates)          # what it used to do
    assert inflated["lost_sales_gal"] > honest["lost_sales_gal"]

    optimized = res.kpis["lost_sales_gal"]
    change = lambda b: (optimized - b) / b
    assert change(inflated["lost_sales_gal"]) < 0 < change(honest["lost_sales_gal"]), (
        "the old window reported an improvement where the real one is a regression")
