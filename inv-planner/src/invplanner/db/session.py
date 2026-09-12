"""Engine/session setup. SQLite by default, Postgres via DATABASE_URL."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from .models import Base

#: Columns added after a database was first created. `create_all` only creates
#: missing *tables*, so a new column on an existing table is invisible to it and
#: every query against that table fails until the column is added by hand. The
#: alternative is `init_db.py --reset`, which throws away the planner's scenarios
#: and the optimizer's run history - too much to lose for one column.
ADDED_COLUMNS = [
    ("optimizer_params", "charge_floor_fraction", "FLOAT NOT NULL DEFAULT 0.8"),
    ("optimizer_params", "terminal_shortfall_per_gal", "FLOAT"),
    ("optimizer_params", "model_version", "VARCHAR(8) NOT NULL DEFAULT 'v1'"),
    ("optimizer_params", "switch_cost_by_unit", "JSON"),
    ("optimizer_params", "objective", "VARCHAR(8) NOT NULL DEFAULT 'cost'"),
    ("optimizer_params", "mip_gap_abs", "FLOAT NOT NULL DEFAULT 10000.0"),
    ("optimizer_params", "terminal_value_fraction",
     "FLOAT NOT NULL DEFAULT 0.25"),
    ("optimizer_params", "safety_stock_days", "FLOAT NOT NULL DEFAULT 0.0"),
    ("optimizer_params", "must_run", "BOOLEAN NOT NULL DEFAULT 1"),
    ("optimizer_params", "min_rate_fraction",
     "FLOAT NOT NULL DEFAULT 0.6"),
]

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", ".."))
DEFAULT_SQLITE = "sqlite:///" + os.path.join(ROOT, "data", "invplanner.db")

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_SQLITE)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Bring an existing database up to the current model, without losing it."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in ADDED_COLUMNS:
            if table not in tables:
                continue        # create_all just made it, with the column
            if column in {c["name"] for c in insp.get_columns(table)}:
                continue
            conn.execute(text("ALTER TABLE {} ADD COLUMN {} {}".format(
                table, column, ddl)))


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
