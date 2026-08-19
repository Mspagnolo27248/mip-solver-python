"""The model must keep answering what it answered, or say why not.

This is the test three shipped defects would have failed on their first run, and
none of the others did. Each produced a confident, plausible answer: an infeasible
model relabelled `feasible`, PuLP's rewritten `Optimal` on a time-limited
incumbent, and a reactor constraint that cost binaries and did nothing. Nothing a
person would look at moved. Every one of them moves a field in `baseline.json`.

When this fails, read the diff first. It names the case and the field, so the
question is always "did I mean to do that?" rather than "what broke?". If you did,
`python scripts/snapshot.py --update` and put the reason in the commit.
"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

SEED = os.path.join(ROOT, "data", "seed")
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline.json")

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SEED, "reference.json")),
    reason="seed data not built; run scripts/seed.py")


@pytest.fixture(scope="module")
def current():
    from invplanner.baseline import snapshot
    return snapshot(SEED)


def test_the_model_still_answers_what_it_answered(current):
    from invplanner.baseline import diff
    assert os.path.exists(PATH), "no committed baseline; run scripts/snapshot.py --update"
    with open(PATH) as fh:
        committed = json.load(fh)

    changes = diff(committed, current)
    assert not changes, (
        "the model's answers moved:\n  " + "\n  ".join(changes) +
        "\n\nIf that was intended: python scripts/snapshot.py --update")


def test_only_proven_optima_carry_a_comparable_objective(current):
    """A number you cannot compare should not be recorded as though you can.

    v1 cannot be solved exactly - at `mip_gap` 0 it hits a five-minute limit and
    returns `feasible` over 42 days - so its rows are taken at the planning gap
    and carry no objective. Recording one would invite the mistake this whole
    file exists to prevent: at the 2% gap, the difference a modelling change
    makes is smaller than the gap, and a safety-stock experiment that "recovered
    43,705 gal" turned out to be exactly that.
    """
    for name, row in current.items():
        if row["exact"]:
            assert row["status"] == "optimal", (name, row["status"])
            assert row.get("objective") is not None, name
        else:
            assert "objective" not in row, (
                "{} is not solved exactly, so its objective is not comparable "
                "and must not be recorded".format(name))


def test_the_exact_tier_is_what_makes_this_affordable(current):
    """Exactness has to be cheap or it will be skipped, and v0 is what makes it so.

    Every exact case is v0, which solves 100 days to a proven optimum in about
    three seconds. That is the reason the convention "compare at gap 0 or not at
    all" is followable rather than aspirational.
    """
    from invplanner.baseline import CASES
    exact = [c for c in CASES if c["exact"]]
    assert exact, "an exact tier is the point of this file"
    assert all(c["model"] == "v0" for c in exact), (
        "only v0 proves optimality in reasonable time; see the module docstring")
    assert any(c["days"] >= 100 for c in exact), "the long horizon must be covered"
