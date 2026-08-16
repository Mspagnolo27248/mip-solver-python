"""Import the workbook into JSON seed files.

Usage:  python scripts/seed.py [path-to-xlsm] [outdir]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner.importer import import_workbook  # noqa: E402
from invplanner import layout as L               # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_XLSM = os.path.abspath(os.path.join(ROOT, "..", L.WORKBOOK_DEFAULT))
DEFAULT_OUT = os.path.join(ROOT, "data", "seed")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XLSM
    outdir = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    if not os.path.exists(path):
        print("Workbook not found: {}".format(path))
        return 1
    print("Importing {}".format(path))
    paths = import_workbook(path, outdir)
    for k, v in paths.items():
        print("  {:22s} {}".format(k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
