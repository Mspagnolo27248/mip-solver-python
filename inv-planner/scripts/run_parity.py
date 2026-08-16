"""Run the parity harness against the workbook.

Usage:  python scripts/run_parity.py [--days N] [--loose] [workbook]
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from invplanner import layout as L                    # noqa: E402
from invplanner.engine import Reference, Scenario     # noqa: E402
from invplanner.parity import KnownDefects, ParityHarness, write_report  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "data", "reports")
DEFAULT_XLSM = os.path.abspath(os.path.join(ROOT, "..", L.WORKBOOK_DEFAULT))


def main() -> int:
    args = sys.argv[1:]
    days = None
    strict = True
    path = DEFAULT_XLSM
    i = 0
    while i < len(args):
        if args[i] == "--days":
            days = int(args[i + 1]); i += 2
        elif args[i] == "--loose":
            strict = False; i += 1
        else:
            path = args[i]; i += 1

    ref = Reference.load(os.path.join(SEED, "reference.json"))
    scn = Scenario.load(os.path.join(SEED, "scenario.json"))
    defects = KnownDefects.load(os.path.join(ROOT, "data", "known-defects.json"))
    t0 = time.time()
    h = ParityHarness(path, ref, scn, strict_workbook=strict, defects=defects)
    print("simulated in {:.2f}s ({} blocks, {} days)".format(
        time.time() - t0, len(ref.blocks), len(scn.dates)))
    result = h.compare(max_days=days)
    report = write_report(result, OUT)

    matched = (result["cells_compared"] - result["cells_differing"]
               - result["cells_known_defect"] - result["cells_workbook_error"])
    print("balance cells : {:,} compared, {:,} match, {:,} unexplained, "
          "{:,} known defects, {:,} workbook errors"
          .format(result["cells_compared"], matched, result["cells_differing"],
                  result["cells_known_defect"], result["cells_workbook_error"]))
    print("production    : {:,} compared, {:,} differ".format(
        result["production_compared"], result["production_differing"]))
    print("report        : {}".format(report))
    for row, s in result["per_row"].items():
        if s["diff"]:
            print("   {:24s} {:>7,} differ of {:>7,}".format(row, s["diff"], s["compared"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
