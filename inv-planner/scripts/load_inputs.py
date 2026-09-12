"""Load standalone feed exports and freeze them into a new scenario.

The workbook route (`seed.py` then `init_db.py`) re-imports all six feeds from
the 6 MB planning workbook. This one takes the small exports a planner can
produce on demand and updates only the feeds those files carry.

Usage:
  python scripts/load_inputs.py [dir] [options]

  dir                  folder holding the exports (default: ../transfer-inputs)
                       Files are matched by name: inventory.xlsx,
                       open orders.xlsx, forecast.xlsx
  --scenario NAME      create a scenario from the result (skip to only stage)
  --as-of YYYY-MM-DD   first day of that scenario (default: today)
  --horizon N          days to plan (default: 366)
  --copy-schedule ID   start from an existing scenario's charge schedule
  --dry-run            parse and report, write nothing
"""
import argparse
import datetime as dt
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from invplanner import feedsheets                       # noqa: E402
from invplanner.db import connectors                    # noqa: E402
from invplanner.db import service as svc                 # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    ".."))
DEFAULT_DIR = os.path.abspath(os.path.join(ROOT, "..", "transfer-inputs"))


def discover(src_dir):
    """Match the files in `src_dir` to sheet kinds, by name."""
    found, ignored = {}, []
    for name in sorted(os.listdir(src_dir)):
        if name.startswith("~$") or not name.lower().endswith((".xlsx", ".xlsm")):
            continue
        kind = feedsheets.kind_for_filename(name)
        if kind is None:
            ignored.append(name)
        elif kind in found:
            ignored.append(name + " (already have a {} sheet)".format(kind))
        else:
            found[kind] = os.path.join(src_dir, name)
    return found, ignored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--as-of", dest="as_of", default=None)
    ap.add_argument("--horizon", type=int, default=366)
    ap.add_argument("--copy-schedule", dest="copy_schedule", type=int, default=None)
    ap.add_argument("--actor", default="planner")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true")
    args = ap.parse_args()

    src_dir = os.path.abspath(args.dir)
    if not os.path.isdir(src_dir):
        print("Not a folder: {}".format(src_dir))
        return 1

    found, ignored = discover(src_dir)
    for name in ignored:
        print("  skipped {} - name does not identify a feed".format(name))
    if not found:
        print("No feed sheets found in {}.\nExpected files named {}.".format(
            src_dir, ", ".join(k + ".xlsx" for k in feedsheets.KINDS)))
        return 1

    # Parse everything before writing anything: a folder with one bad export
    # should not leave the feeds half updated.
    parsed = {}
    for kind, path in sorted(found.items()):
        try:
            parsed[kind] = feedsheets.read(kind, path)
        except feedsheets.FeedSheetError as exc:
            print("FAILED {}\n  {}".format(os.path.basename(path), exc))
            return 1

    print("Read from {}".format(src_dir))
    for kind, path in sorted(found.items()):
        print("  {:<12} {}".format(kind, os.path.basename(path)))
        for feed, data in sorted(parsed[kind].items()):
            print("    {:<24} {:>4} products  {:>6} values".format(
                feed, len(data.values), data.cells))
            for note in data.notes:
                print("      note: {}".format(note))

    if args.dry_run:
        print("\nDry run - nothing written.")
        return 0

    dest_dir = connectors.upload_dir(svc.SEED_DIR)
    os.makedirs(dest_dir, exist_ok=True)
    for kind, path in sorted(found.items()):
        dest = connectors.upload_path(svc.SEED_DIR, kind)
        shutil.copyfile(path, dest)
        print("\n  stored {} -> {}".format(kind, dest))

    from invplanner.db.session import SessionLocal, init_db
    init_db()
    db = SessionLocal()
    try:
        if svc.active_reference(db) is None:
            print("\nNo reference loaded. Run scripts/init_db.py first - the "
                  "products, yields and capacities still come from the workbook.")
            return 1

        conns = {c.feed: c for c in connectors.default_connectors(svc.SEED_DIR)}
        print("\nSyncing:")
        for kind in sorted(found):
            for feed in feedsheets.KIND_FEEDS[kind]:
                batch = svc.sync_feed(db, conns[feed], args.actor)
                m = batch.meta or {}
                print("  {:<24} {:>5} rows  (+{} new, {} updated, {} zeroed)"
                      .format(feed, batch.row_count, m.get("created", 0),
                              m.get("updated", 0), m.get("absent_zeroed", 0)))

        if not args.scenario:
            print("\nStaged. Create a scenario to plan on it - in the app, or "
                  "re-run with --scenario NAME.")
            return 0

        as_of = (dt.datetime.strptime(args.as_of, "%Y-%m-%d").date()
                 if args.as_of else dt.date.today())
        s = svc.create_scenario(db, args.scenario, as_of, args.horizon,
                                args.actor, "loaded from " + src_dir,
                                args.copy_schedule)
        print("\nScenario {} '{}' - as of {}, {} days, reference v{}".format(
            s.id, s.name, s.as_of, s.horizon_days, s.reference_version_id))
        opening = s.inputs.get("opening_inventory", {})
        print("  opening inventory  {:>12,.0f} gal over {} products".format(
            sum(opening.values()), sum(1 for v in opening.values() if v)))
        for feed in ("open_orders", "sales_forecast", "blend_component_demand"):
            grid = s.inputs.get(feed, {})
            print("  {:<18} {:>12,.0f} in {} products".format(
                feed, sum(sum(v.values()) for v in grid.values()), len(grid)))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
