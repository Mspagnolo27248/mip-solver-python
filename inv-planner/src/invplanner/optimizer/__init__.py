"""Automatic scheduling.

Kept apart from the manual planning side on purpose: the optimizer needs inputs a
planner working by hand never does - margins, netbacks, switch costs, solver
settings - and mixing them into the reference data would put prices in front of
someone who only wants to move a charge.

The two sides share everything factual: reference data, feeds, scenarios and the
simulation engine. The optimizer reads a scenario the planner built, and writes
its answer back as a new scenario, so a proposed schedule opens on the manual side
exactly like one a person made.
"""

from . import v0  # noqa: F401
