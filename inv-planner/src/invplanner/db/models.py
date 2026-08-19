"""Database schema: raw -> staging -> scenario.

The three layers exist because planners need to correct feed data without losing
either the original or their correction:

  raw       immutable copies of every source pull, never edited
  staging   what the model reads; each cell carries the source value AND an
            optional override, so a re-sync updates the source without clobbering
            a planner's edit, and drift (override != latest source) stays visible
  scenario  a frozen snapshot of staging plus a charge schedule - one simulation
            run, so plans can be compared and the optimizer has stable inputs

Runs on SQLite for local development and Postgres in deployment; set DATABASE_URL.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


# --------------------------------------------------------------------- feeds
class Feed:
    """Feed identifiers. Keys are positional so one generic cell table can serve
    every feed; `key_labels` drives the UI column headers."""

    INVENTORY = "inventory"
    OPEN_ORDERS = "open_orders"
    SALES_FORECAST = "sales_forecast"
    BLEND_BOM = "blend_bom"
    BLEND_DEMAND = "blend_component_demand"
    BASE_OIL_TRANSFER = "base_oil_transfer"

    ALL = [INVENTORY, OPEN_ORDERS, SALES_FORECAST, BLEND_BOM, BLEND_DEMAND,
           BASE_OIL_TRANSFER]

    META = {
        INVENTORY: {"title": "Tank inventory",
                    "key_labels": ["Product", "Tank"], "unit": "gal",
                    "description": "Nightly snapshot by product and tank."},
        OPEN_ORDERS: {"title": "Open orders",
                      "key_labels": ["Product", "Date"], "unit": "gal",
                      "description": "Committed customer demand by day."},
        SALES_FORECAST: {"title": "Sales forecast",
                         "key_labels": ["Product", "Month"], "unit": "gal/day",
                         "description": "Forecast daily rate per product per month."},
        BLEND_BOM: {"title": "Blend recipes",
                    "key_labels": ["Blend", "Component"], "unit": "fraction",
                    "description": "Component fractions per blended product."},
        BLEND_DEMAND: {"title": "Blend component demand",
                       "key_labels": ["Component", "Month"], "unit": "gal/day",
                       "description": "Daily component pull implied by blend plans."},
        BASE_OIL_TRANSFER: {"title": "Base oil transfers",
                            "key_labels": ["Product", "Month"], "unit": "gal/day",
                            "description": "Monthly base-oil transfer volumes."},
    }


# ----------------------------------------------------------------- reference
class RefKind:
    """The reference values a planner can adjust.

    Each is a conversion factor or a limit that the whole projection rests on, so
    every one is editable, audited, and shown against the value the workbook
    imported.
    """

    CRUDE_YIELD = "crude_yield"          # k1=product, k2=month -> fraction
    UNIT_YIELD = "unit_yield"            # k1=unit|charge|out       -> fraction
    MAX_RATE = "max_rate"                # k1=unit, k2=product      -> bbl/day
    TANK_CAPACITY = "tank_capacity"      # k1=product               -> gallons
    TARGET_LCL = "target_lcl"            # k1=product               -> gallons
    TARGET_UCL = "target_ucl"            # k1=product               -> gallons

    ALL = [CRUDE_YIELD, UNIT_YIELD, MAX_RATE, TANK_CAPACITY, TARGET_LCL,
           TARGET_UCL]

    META = {
        CRUDE_YIELD: {"title": "Crude unit yields", "unit": "fraction",
                      "keys": ["Product", "Month"],
                      "description": "Share of crude charge that becomes each "
                                     "side stream, by month."},
        UNIT_YIELD: {"title": "Process unit yields", "unit": "fraction",
                     "keys": ["Unit / charge / product", ""],
                     "description": "Conversion of each charge into each product "
                                    "at MEK, hydrotreater, extraction, ROSE and "
                                    "the platformer."},
        MAX_RATE: {"title": "Maximum charge rates", "unit": "bbl/day",
                   "keys": ["Unit", "Charge product"],
                   "description": "Charge = max rate x share of the day, so this "
                                  "is the ceiling on a full day's run."},
        TANK_CAPACITY: {"title": "Tank capacity", "unit": "gal",
                        "keys": ["Product", ""],
                        "description": "Physical shell capacity. Inventory can "
                                       "never exceed it."},
        TARGET_LCL: {"title": "Lower control limit", "unit": "gal",
                     "keys": ["Product", ""],
                     "description": "Safety stock. None are set in the workbook."},
        TARGET_UCL: {"title": "Upper control limit", "unit": "gal",
                     "keys": ["Product", ""],
                     "description": "Target ceiling, short of physical capacity."},
    }


class ReferenceOverride(Base):
    """A planner's adjustment to an imported reference value.

    Same shape as the staging layer, and for the same reason: the imported value
    is kept alongside the edit, so a re-import refreshes the source without
    discarding anyone's correction, and drift stays visible.
    """
    __tablename__ = "reference_override"
    __table_args__ = (
        UniqueConstraint("reference_version_id", "kind", "k1", "k2",
                         name="uq_reference_override"),
    )

    id = Column(Integer, primary_key=True)
    reference_version_id = Column(Integer, ForeignKey("reference_version.id"),
                                  nullable=False, index=True)
    kind = Column(String(40), nullable=False, index=True)
    k1 = Column(String(80), nullable=False)
    k2 = Column(String(40))

    source_value = Column(Float)
    override_value = Column(Float)
    override_by = Column(String(120))
    override_at = Column(DateTime)
    reason = Column(Text)
    label = Column(String(255))

    @property
    def effective_value(self):
        return (self.override_value if self.override_value is not None
                else self.source_value)


class ReferenceVersion(Base):
    """Master data (products, capacities, yields, run rates, block definitions).

    Held as one versioned document: it changes rarely, always moves as a set, and
    the engine consumes it whole. Admin screens can normalise pieces out of it
    later without disturbing the feed layers.
    """
    __tablename__ = "reference_version"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    source = Column(String(255))
    note = Column(Text)
    payload = Column(JSON, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    #: Bumped on every reference edit, so cached simulations and the engine's
    #: reference cache both invalidate.
    overrides_version = Column(Integer, default=0, nullable=False)


# ----------------------------------------------------------------- raw layer
class RawBatch(Base):
    __tablename__ = "raw_batch"

    id = Column(Integer, primary_key=True)
    feed = Column(String(50), nullable=False, index=True)
    source = Column(String(255), nullable=False)
    fetched_at = Column(DateTime, default=_now, nullable=False)
    row_count = Column(Integer, default=0, nullable=False)
    status = Column(String(20), default="ok", nullable=False)
    message = Column(Text)
    meta = Column(JSON)

    rows = relationship("RawRow", back_populates="batch",
                        cascade="all, delete-orphan")


class RawRow(Base):
    __tablename__ = "raw_row"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("raw_batch.id"), nullable=False, index=True)
    feed = Column(String(50), nullable=False, index=True)
    k1 = Column(String(64), nullable=False)
    k2 = Column(String(64))
    k3 = Column(String(64))
    value = Column(Float)
    extra = Column(JSON)

    batch = relationship("RawBatch", back_populates="rows")


Index("ix_raw_row_feed_keys", RawRow.feed, RawRow.k1, RawRow.k2, RawRow.k3)


# ------------------------------------------------------------- staging layer
class StagingCell(Base):
    """One editable value. `source_value` is refreshed by syncs; `override_value`
    is the planner's and is never touched by a sync."""
    __tablename__ = "staging_cell"
    __table_args__ = (
        UniqueConstraint("feed", "k1", "k2", "k3", name="uq_staging_cell_key"),
    )

    id = Column(Integer, primary_key=True)
    feed = Column(String(50), nullable=False, index=True)
    k1 = Column(String(64), nullable=False)
    k2 = Column(String(64))
    k3 = Column(String(64))

    source_value = Column(Float)
    source_batch_id = Column(Integer, ForeignKey("raw_batch.id"))
    source_seen_at = Column(DateTime)
    # Value of source_value at the moment the override was made; if the source has
    # moved since, the override is stale and the UI flags it.
    source_at_override = Column(Float)

    override_value = Column(Float)
    override_by = Column(String(120))
    override_at = Column(DateTime)
    override_reason = Column(Text)

    label = Column(String(255))
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    @property
    def effective_value(self) -> float:
        return self.override_value if self.override_value is not None else \
            (self.source_value or 0.0)

    @property
    def has_override(self) -> bool:
        return self.override_value is not None

    @property
    def is_stale(self) -> bool:
        """The source changed after the planner made their edit."""
        if self.override_value is None or self.source_at_override is None:
            return False
        return (self.source_value or 0.0) != self.source_at_override


# ------------------------------------------------------------ scenario layer
class Scenario(Base):
    __tablename__ = "scenario"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
    created_by = Column(String(120))
    as_of = Column(Date, nullable=False)
    horizon_days = Column(Integer, default=366, nullable=False)
    reference_version_id = Column(Integer, ForeignKey("reference_version.id"),
                                  nullable=False)
    status = Column(String(20), default="draft", nullable=False)
    note = Column(Text)
    # Frozen feed inputs (everything except the charge schedule, which is edited
    # through its own table).
    inputs = Column(JSON, nullable=False)
    # Bumped on every schedule edit so cached simulations invalidate.
    schedule_version = Column(Integer, default=1, nullable=False)

    reference = relationship("ReferenceVersion")
    schedule = relationship("ScheduleEntry", back_populates="scenario",
                            cascade="all, delete-orphan")
    crude_days = relationship("CrudeDay", back_populates="scenario",
                              cascade="all, delete-orphan")
    downtime = relationship("PlannedDowntime", back_populates="scenario",
                            cascade="all, delete-orphan")


class ScheduleEntry(Base):
    """One unit-day charge decision, in barrels."""
    __tablename__ = "schedule_entry"
    __table_args__ = (
        UniqueConstraint("scenario_id", "line_key", "date", name="uq_schedule_entry"),
    )

    id = Column(Integer, primary_key=True)
    scenario_id = Column(Integer, ForeignKey("scenario.id"), nullable=False,
                         index=True)
    line_key = Column(String(64), nullable=False)
    date = Column(Date, nullable=False)
    bbl = Column(Float, nullable=False, default=0.0)

    scenario = relationship("Scenario", back_populates="schedule")


class CrudeDay(Base):
    """Crude unit charge rate and operating mode (R regular / L low volatility)."""
    __tablename__ = "crude_day"
    __table_args__ = (
        UniqueConstraint("scenario_id", "date", name="uq_crude_day"),
    )

    id = Column(Integer, primary_key=True)
    scenario_id = Column(Integer, ForeignKey("scenario.id"), nullable=False,
                         index=True)
    date = Column(Date, nullable=False)
    bbl = Column(Float, default=0.0, nullable=False)
    mode = Column(String(1))

    scenario = relationship("Scenario", back_populates="crude_days")


class PlannedDowntime(Base):
    """A period when a unit cannot produce - a turnaround, or a shorter outage.

    Planner input, set before the optimizer runs. Stored as a named range rather
    than loose days because that is how a maintenance calendar reads; single days
    are just a range of one. The optimizer treats these as the only days a unit is
    allowed to be idle, since everything else must run.
    """
    __tablename__ = "planned_downtime"

    id = Column(Integer, primary_key=True)
    scenario_id = Column(Integer, ForeignKey("scenario.id"), nullable=False,
                         index=True)
    unit = Column(String(40), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    reason = Column(String(255))
    #: "confirmed" once someone has checked it against the maintenance calendar;
    #: "detected" while it is only inferred from a gap in an old plan.
    status = Column(String(20), default="confirmed", nullable=False)
    created_by = Column(String(120))
    created_at = Column(DateTime, default=_now, nullable=False)

    scenario = relationship("Scenario", back_populates="downtime")

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class OptimizerParams(Base):
    """Inputs the optimizer needs that the manual planner never does.

    Kept apart from reference data on purpose. Yields, rates and capacities
    describe the plant and belong to the manual side; margins, netbacks and
    switch costs only exist because something is choosing between options. A
    planner working the schedule by hand should never have to see them.
    """
    __tablename__ = "optimizer_params"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, default="Default")
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    is_active = Column(Boolean, default=True, nullable=False)

    # --- economics, $/gal unless stated
    crude_price_per_bbl = Column(Float, default=70.0, nullable=False)
    downgrade_discount_per_gal = Column(Float, default=0.50, nullable=False)
    lost_sale_margin_per_gal = Column(Float)
    netback_diesel_per_gal = Column(Float)
    netback_gasoline_per_gal = Column(Float)
    #: $ per changeover. The time lost is already in the day budget, so this is
    #: only for costs that time does not capture - labour, quality giveaway.
    #:
    #: The default for any unit without one of its own. It cannot be the whole
    #: story: the campaign lengths this has to reproduce run from HYDRO's 1-day
    #: median to ROSE's 40, so one figure across every unit cannot be calibrated
    #: against them.
    switch_cost = Column(Float, default=0.0, nullable=False)

    #: "cost" minimises money given up; "margin" maximises money earned.
    #:
    #: Defaults to "cost" because that is the model that has been scored against
    #: the plan. Margin is the one the plant is actually run for and is built and
    #: solvable, but it turns on `terminal_value_fraction`, which nobody has
    #: measured - see MARGIN-AND-CAMPAIGNS.md before trusting a margin run.
    objective = Column(String(8), default="cost", nullable=False)

    #: What a gallon still in the tank on the last day is worth, as a fraction of
    #: what it fetches sold. Margin mode only.
    #:
    #: The most sensitive number in that objective. Over 42 days it moves lost
    #: sales by a factor of five, monotonically - high and the plant hoards,
    #: zero and it must empty every tank by the final day. Throughput barely
    #: responds, so it decides where material goes rather than how much is made.
    #:
    #: 0.25 follows operations' own rule: run to the finished-good tanks rather
    #: than hold upstream when there is spare capacity, but never at the expense
    #: of real demand. At 0.25 the plan loses 27% fewer gallons than the cost
    #: objective and charges 10.3% more; above 0.5 it shorts more demand than
    #: the cost objective did, to hold stock.
    terminal_value_fraction = Column(Float, default=0.25, nullable=False)

    #: Days of demand cover each product must keep in tank, elastic.
    #:
    #: **The only lower bound on inventory the model has.** Everything else about
    #: a tank is the ceiling: `0 <= inventory <= capacity`, plus a terminal
    #: condition on the last day. The workbook's lower control limits are not
    #: read by the optimizer at all, so without this a tank may sit empty for
    #: weeks and satisfy every constraint.
    #:
    #: Off at zero, and measured that way on purpose: at 0 through 7 days of
    #: cover the answer is bit-identical, and turning it on costs 129s -> 293s ->
    #: 556s at 0/3/5 days for that identical answer. Days of cover is a stand-in
    #: derived from demand, not a figure anyone has set per product.
    safety_stock_days = Column(Float, default=0.0, nullable=False)

    #: `{unit: $ per changeover}`, overriding `switch_cost` for the units named.
    #:
    #: Null means every unit uses the scalar, which is how the model priced
    #: changeovers before this existed. Committed per-unit and per-arc figures
    #: live in `model_config`; this is the scenario's own override, for
    #: calibrating one unit against its campaign lengths without editing code.
    switch_cost_by_unit = Column(JSON)

    #: What ending the window below opening inventory costs, per gallon.
    #:
    #: Shares its economics with a downgrade - material to replace, not margin
    #: lost - so it defaults to the downgrade discount. It must stay well under
    #: the lost-sale margin: set level, the optimizer cannot tell shorting a
    #: customer from drawing a tank down; set close, it shorts customers to hold
    #: stock. Null means "use the downgrade discount".
    terminal_shortfall_per_gal = Column(Float)

    #: How much of the planner's own charge each unit must still run.
    #:
    #: The objective counts only costs and never rewards production, so without a
    #: floor the cheapest schedule is to stop making oil - at zero this model
    #: shut the Platformer off entirely and ran the plant at 34% of plan. 1.0
    #: pins charges to the schedule and optimises routing alone.
    #:
    #: **The feasible ceiling depends on the horizon**, so this cannot be set
    #: without knowing `horizon_days`. Measured under both objectives:
    #:
    #:      42 days   floor <= 0.75
    #:      60 days   floor <= 0.75
    #:     100 days   floor <= 0.50
    #:     160 days   floor <= 0.50
    #:     366 days   infeasible at every floor, including 0.00
    #:
    #: 0.30 because it is the only value that holds across every horizon anyone
    #: runs, and because raising it buys nothing: at 100 days, 0.30 against 0.50
    #: charges 1,417,480 bbl against 1,416,167 and loses exactly the same
    #: 3,030,265 gal. The floor stops being binding well below the ceiling.
    #:
    #: The full year fails for a reason that is not this parameter: **the
    #: planner's charge schedule ends 2026-12-31**, about 161 days into a
    #: 366-day scenario, for MEK, extraction, ROSE and the hydrotreater. Crude
    #: is a fixed input and keeps running, so past that date the feeds pile up
    #: with nothing consuming them and the tanks overflow - 9117 alone by
    #: 9.8 M gal/day. The model is only meaningful out to roughly 160 days,
    #: which is what the reports in `data/reports` were built at.
    #:
    #: The default was 0.8, infeasible on every horizon, and it went unnoticed
    #: because the solver's `Infeasible` was being relabelled `feasible` and a
    #: schedule handed back anyway (fixed in `optimizer/v0.py`).
    charge_floor_fraction = Column(Float, default=0.30, nullable=False)

    #: Which model runs. "v0" fixes the planner's assignments and optimises
    #: levels and routing only; "v1" also decides what MEK and extraction charge,
    #: on the allowed transitions. v0 solves in about a second and v1 in about
    #: ten - see V1-MODEL.md for why proving optimality is not worth its cost.
    #: "v0", "v1" or "v2". v2 frees the hydrotreater as well, in two stages -
    #: freeing all three units at once cannot prove optimality past seven days.
    model_version = Column(String(8), default="v1", nullable=False)

    # --- solve
    horizon_days = Column(Integer, default=42, nullable=False)
    time_limit_seconds = Column(Integer, default=300, nullable=False)
    mip_gap = Column(Float, default=0.02, nullable=False)

    def missing(self) -> list:
        """Inputs with no value, which the optimizer cannot run without."""
        gaps = []
        if self.lost_sale_margin_per_gal is None:
            gaps.append("lost_sale_margin_per_gal")
        if self.netback_diesel_per_gal is None:
            gaps.append("netback_diesel_per_gal")
        if self.netback_gasoline_per_gal is None:
            gaps.append("netback_gasoline_per_gal")
        return gaps


class OptimizerRun(Base):
    """One solve: what it was given, what it produced, and what it cost.

    The result lands as a new scenario, so a proposed schedule is opened, read
    and edited on the manual side exactly like one a planner built.
    """
    __tablename__ = "optimizer_run"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    created_by = Column(String(120))
    base_scenario_id = Column(Integer, ForeignKey("scenario.id"), nullable=False)
    result_scenario_id = Column(Integer, ForeignKey("scenario.id"))
    params = Column(JSON, nullable=False)

    status = Column(String(20), default="queued", nullable=False)
    message = Column(Text)
    solver = Column(String(40))
    solve_seconds = Column(Float)
    objective = Column(Float)
    #: Baseline and optimized KPIs side by side, so "better" is measured.
    kpis = Column(JSON)

    base_scenario = relationship("Scenario", foreign_keys=[base_scenario_id])
    result_scenario = relationship("Scenario", foreign_keys=[result_scenario_id])


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=_now, nullable=False, index=True)
    actor = Column(String(120))
    action = Column(String(80), nullable=False)
    target = Column(String(255))
    detail = Column(JSON)
