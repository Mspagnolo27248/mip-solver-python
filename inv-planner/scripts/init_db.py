"""Create the database, load reference data, sync every feed, make a scenario.

Usage:  python scripts/init_db.py [--reset]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.db import service as svc              # noqa: E402
from invplanner.db.models import Scenario             # noqa: E402
from invplanner.db.session import (DATABASE_URL, SessionLocal,  # noqa: E402
                                   engine, init_db)
from invplanner.db.models import Base                 # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main() -> int:
    reset = "--reset" in sys.argv
    if reset:
        print("dropping all tables")
        Base.metadata.drop_all(engine)
    init_db()
    print("database: {}".format(DATABASE_URL))

    seed_ref = os.path.join(ROOT, "data", "seed", "reference.json")
    if not os.path.exists(seed_ref):
        print("No seed found. Run scripts/seed.py first.")
        return 1

    db = SessionLocal()
    try:
        ref = svc.load_reference_document(db, seed_ref, note="initial load")
        db.commit()
        print("reference version {} ({} blocks, {} products)".format(
            ref.id, len(ref.payload.get("blocks", [])),
            len(ref.payload.get("products", {}))))

        # The source is printed because it is not always the workbook: a feed
        # with an uploaded sheet in data/uploads keeps reading that sheet, and
        # re-importing the workbook does not quietly take it back.
        for batch in svc.sync_all(db, actor="init"):
            print("  synced {:<24s} {:>6,} rows  from {}".format(
                batch.feed, batch.row_count, batch.source))

        if db.query(Scenario).count() == 0:
            s = svc.create_scenario(db, "Baseline (from workbook)",
                                    as_of=svc._d(ref.payload["scenario_defaults"]["as_of"]),
                                    horizon_days=366, actor="init",
                                    note="Charge schedule as entered in the workbook.")
            print("scenario {}: {} (as of {})".format(s.id, s.name, s.as_of))
        else:
            print("scenarios already present; leaving them alone")
    finally:
        db.close()
    print("\nStart the app with:  python scripts/run_api.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
