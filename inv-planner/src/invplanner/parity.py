"""Parity harness: engine output vs. the workbook's own cached values.

Every balance cell the workbook computed is compared against the engine's value
for the same (block, row, date). Cells the workbook holds as an Excel error
(#N/A, #REF!) are reported separately - those are live defects in the workbook,
not engine failures, and they are exactly what a rebuild should stop inheriting.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from . import layout as L
from .engine import Engine, Reference, Scenario, Simulation
from .xlsx import as_date, is_num, load, read_date_row, text

ERROR_STRINGS = {"#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}

# Relative tolerance for float comparison; inventories run to 7 figures so an
# absolute epsilon alone is useless.
REL_TOL = 1e-6
ABS_TOL = 0.5     # half a gallon


class KnownDefects:
    """Documented workbook defects the engine deliberately does not reproduce."""

    def __init__(self, entries: List[Dict[str, Any]]):
        self.entries = entries

    @classmethod
    def load(cls, path: str) -> "KnownDefects":
        if not os.path.exists(path):
            return cls([])
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def match(self, sheet: str, block: str, row: str, date: dt.date
              ) -> Optional[Dict[str, Any]]:
        iso = date.isoformat()
        for e in self.entries:
            if e.get("sheets") and sheet not in e["sheets"]:
                continue
            if e.get("blocks") and block not in e["blocks"]:
                continue
            if e.get("rows") and row not in e["rows"]:
                continue
            if iso < e.get("from_date", "1900-01-01"):
                continue
            if e.get("to_date") and iso > e["to_date"]:
                continue
            return e
        return None


class Diff:
    def __init__(self, block: str, sheet: str, label: str, date: dt.date,
                 expected: Any, actual: float):
        self.block = block
        self.sheet = sheet
        self.label = label
        self.date = date
        self.expected = expected
        self.actual = actual

    @property
    def is_error_cell(self) -> bool:
        return isinstance(self.expected, str) and self.expected.strip() in ERROR_STRINGS

    defect_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        d = {"block": self.block, "sheet": self.sheet, "row": self.label,
             "date": self.date.isoformat(), "workbook": self.expected,
             "engine": round(self.actual, 4) if isinstance(self.actual, float) else self.actual}
        if self.defect_id:
            d["defect"] = self.defect_id
        return d


def close(expected: float, actual: float) -> bool:
    if expected == actual:
        return True
    diff = abs(expected - actual)
    if diff <= ABS_TOL:
        return True
    scale = max(abs(expected), abs(actual))
    return scale > 0 and (diff / scale) <= REL_TOL


class ParityHarness:
    def __init__(self, workbook_path: str, ref: Reference, scn: Scenario,
                 strict_workbook: bool = True,
                 defects: Optional[KnownDefects] = None):
        self.path = workbook_path
        self.ref = ref
        self.scn = scn
        self.wv = load(workbook_path, data_only=True)
        self.defects = defects or KnownDefects([])
        self.sim = Engine(ref, scn, strict_workbook=strict_workbook).run()

    def _stream_columns(self, sheet: str) -> Dict[dt.date, int]:
        return {d: c for c, d in read_date_row(self.wv[sheet], L.STREAM_DATE_ROW,
                                               L.STREAM_FIRST_DATE_COL)}

    def compare(self, max_days: Optional[int] = None) -> Dict[str, Any]:
        dates = self.scn.dates[:max_days] if max_days else self.scn.dates
        cols_cache: Dict[str, Dict[dt.date, int]] = {}
        diffs: List[Diff] = []
        known: List[Diff] = []
        error_cells: List[Diff] = []
        compared = 0
        per_row_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"compared": 0, "diff": 0, "known": 0, "error": 0})

        for block in self.ref.blocks:
            sheet = block["sheet"]
            if sheet not in cols_cache:
                cols_cache[sheet] = self._stream_columns(sheet)
            cols = cols_cache[sheet]
            bal = self.sim.balances.get(block["id"], {})

            for label, meta in block["rows"].items():
                canon = self._canon(label)
                if canon not in bal:
                    continue
                row = meta["row"]
                for d in dates:
                    c = cols.get(d)
                    if not c:
                        continue
                    expected = self.wv[sheet].cell(row, c).value
                    actual = bal[canon].get(d, 0.0)
                    if expected is None:
                        continue
                    compared += 1
                    per_row_stats[canon]["compared"] += 1
                    if isinstance(expected, str):
                        if expected.strip() in ERROR_STRINGS:
                            error_cells.append(Diff(block["id"], sheet, canon, d,
                                                    expected, actual))
                            per_row_stats[canon]["error"] += 1
                        continue
                    if not is_num(expected):
                        continue
                    if not close(float(expected), float(actual)):
                        diff = Diff(block["id"], sheet, canon, d,
                                    float(expected), float(actual))
                        defect = self.defects.match(sheet, block["id"], canon, d)
                        if defect:
                            diff.defect_id = defect["id"]
                            known.append(diff)
                            per_row_stats[canon]["known"] += 1
                        else:
                            diffs.append(diff)
                            per_row_stats[canon]["diff"] += 1

        # The CRUDE sheet is not one of the generic stream blocks, so it needs its
        # own pass; it is kept in barrels and sources its opening from the site
        # crude summary rather than the product lookup.
        crude_cols = {d: c for c, d in
                      read_date_row(self.wv[L.CRUDE_SHEET], L.STREAM_DATE_ROW,
                                    L.STREAM_FIRST_DATE_COL)}
        crude_bal = self.sim.balances.get(L.CRUDE_BLOCK_ID, {})
        for label, row in L.CRUDE_ROW_MAP.items():
            for d in dates:
                c = crude_cols.get(d)
                if not c:
                    continue
                expected = self.wv[L.CRUDE_SHEET].cell(row, c).value
                if expected is None:
                    continue
                actual = crude_bal.get(label, {}).get(d, 0.0)
                compared += 1
                per_row_stats[label]["compared"] += 1
                if isinstance(expected, str):
                    if expected.strip() in ERROR_STRINGS:
                        error_cells.append(Diff(L.CRUDE_BLOCK_ID, L.CRUDE_SHEET,
                                                label, d, expected, actual))
                        per_row_stats[label]["error"] += 1
                    continue
                if not is_num(expected):
                    continue
                if not close(float(expected), float(actual)):
                    diffs.append(Diff(L.CRUDE_BLOCK_ID, L.CRUDE_SHEET, label, d,
                                      float(expected), float(actual)))
                    per_row_stats[label]["diff"] += 1

        prod_diffs, prod_compared, prod_errors = self.compare_production(dates)

        return {
            "cells_compared": compared,
            "cells_differing": len(diffs),
            "cells_known_defect": len(known),
            "known_diffs": known,
            "cells_workbook_error": len(error_cells),
            "production_compared": prod_compared,
            "production_differing": len(prod_diffs),
            "production_workbook_error": prod_errors,
            "per_row": {k: dict(v) for k, v in sorted(per_row_stats.items())},
            "diffs": diffs,
            "error_cells": error_cells,
            "production_diffs": prod_diffs,
            "lookup_conflicts": self.sim.lookup_conflicts,
        }

    def compare_production(self, dates: List[dt.date]
                           ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Compare the gallons roll-up grid (Production - Out rows 99+)."""
        ws = self.wv[L.PO]
        cols = {d: c for c, d in read_date_row(ws, L.PO_GAL_DATE_ROW, L.PO_FIRST_COL)}
        diffs: List[Dict[str, Any]] = []
        compared = 0
        errors = 0
        from .xlsx import norm_code
        for r in range(L.PO_GAL_FIRST_ROW, ws.max_row + 1):
            code = norm_code(ws.cell(r, L.PO_GAL_CODE_COL).value)
            if not code:
                continue
            for d in dates:
                c = cols.get(d)
                if not c:
                    continue
                expected = ws.cell(r, c).value
                if expected is None:
                    continue
                actual = self.sim.prod(code, d)
                if isinstance(expected, str):
                    if expected.strip() in ERROR_STRINGS:
                        errors += 1
                    continue
                if not is_num(expected):
                    continue
                compared += 1
                if not close(float(expected), float(actual)):
                    diffs.append({"code": code, "row": r, "date": d.isoformat(),
                                  "workbook": float(expected),
                                  "engine": round(float(actual), 4)})
        return diffs, compared, errors

    @staticmethod
    def _canon(label: str) -> str:
        label = label.strip()
        if label.startswith("Production Out"):
            return "Production Out"
        if label.startswith("End Inventory"):
            return "End Inventory"
        if label.startswith("Production In"):
            return "Production In"
        return label


def write_report(result: Dict[str, Any], outdir: str, sample: int = 40) -> str:
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "parity-report.md")
    d = result
    matched = (d["cells_compared"] - d["cells_differing"]
               - d.get("cells_known_defect", 0) - d["cells_workbook_error"])
    pct = (100.0 * matched / d["cells_compared"]) if d["cells_compared"] else 0.0
    pm = d["production_compared"] - d["production_differing"]
    ppct = (100.0 * pm / d["production_compared"]) if d["production_compared"] else 0.0

    lines: List[str] = []
    lines.append("# Parity report: engine vs. workbook cached values\n")
    lines.append("## Balance cells\n")
    lines.append("| metric | count |")
    lines.append("|---|---:|")
    lines.append("| compared | {:,} |".format(d["cells_compared"]))
    lines.append("| matching | {:,} ({:.4f}%) |".format(matched, pct))
    lines.append("| unexplained differences | {:,} |".format(d["cells_differing"]))
    lines.append("| known workbook defects (allowlisted) | {:,} |".format(
        d.get("cells_known_defect", 0)))
    lines.append("| workbook holds an Excel error | {:,} |".format(d["cells_workbook_error"]))
    lines.append("")
    lines.append("## Production grid (gallons by product/day)\n")
    lines.append("| metric | count |")
    lines.append("|---|---:|")
    lines.append("| compared | {:,} |".format(d["production_compared"]))
    lines.append("| matching | {:,} ({:.4f}%) |".format(pm, ppct))
    lines.append("| differing | {:,} |".format(d["production_differing"]))
    lines.append("| workbook holds an Excel error | {:,} |".format(d["production_workbook_error"]))
    lines.append("")

    lines.append("## Per balance row\n")
    lines.append("| row | compared | unexplained | known defect | workbook errors |")
    lines.append("|---|---:|---:|---:|---:|")
    for row, s in d["per_row"].items():
        lines.append("| {} | {:,} | {:,} | {:,} | {:,} |".format(
            row, s["compared"], s["diff"], s.get("known", 0), s["error"]))
    lines.append("")

    if d.get("known_diffs"):
        lines.append("## Known workbook defects (deliberate deviations)\n")
        lines.append("These cells differ because the workbook is wrong and the engine "
                     "is not reproducing the fault. Each is listed in "
                     "`data/known-defects.json` with the action needed.\n")
        by_defect: Dict[str, int] = defaultdict(int)
        for k in d["known_diffs"]:
            by_defect[k.defect_id or "?"] += 1
        lines.append("| defect | cells |")
        lines.append("|---|---:|")
        for k, n in sorted(by_defect.items(), key=lambda kv: -kv[1]):
            lines.append("| {} | {:,} |".format(k, n))
        lines.append("")

    if d["lookup_conflicts"]:
        lines.append("## Products charged at more than one unit\n")
        lines.append("The workbook's `Production Out` row resolves with a first-match "
                     "VLOOKUP over the whole charge grid, so only the first line below "
                     "is subtracted from inventory. Confirm with operations which is "
                     "intended before the rebuild changes behaviour.\n")
        for c in d["lookup_conflicts"]:
            lines.append("- {}".format(c))
        lines.append("")

    if d["error_cells"]:
        lines.append("## Workbook error cells (defects to fix, not inherit)\n")
        by_block: Dict[str, int] = defaultdict(int)
        for e in d["error_cells"]:
            by_block["{} / {}".format(e.sheet, e.label)] += 1
        for k, n in sorted(by_block.items(), key=lambda kv: -kv[1]):
            lines.append("- {} - {:,} cells".format(k, n))
        lines.append("")

    if d["diffs"]:
        lines.append("## Sample differences\n")
        lines.append("| block | row | date | workbook | engine | delta |")
        lines.append("|---|---|---|---:|---:|---:|")
        for diff in d["diffs"][:sample]:
            delta = diff.actual - diff.expected if is_num(diff.expected) else 0.0
            lines.append("| {} | {} | {} | {:,.2f} | {:,.2f} | {:,.2f} |".format(
                diff.block, diff.label, diff.date.isoformat(),
                diff.expected, diff.actual, delta))
        lines.append("")

    if d["production_diffs"]:
        lines.append("## Sample production differences\n")
        lines.append("| code | date | workbook | engine | delta |")
        lines.append("|---|---|---:|---:|---:|")
        for pd_ in d["production_diffs"][:sample]:
            lines.append("| {} | {} | {:,.2f} | {:,.2f} | {:,.2f} |".format(
                pd_["code"], pd_["date"], pd_["workbook"], pd_["engine"],
                pd_["engine"] - pd_["workbook"]))
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open(os.path.join(outdir, "parity-diffs.json"), "w", encoding="utf-8") as f:
        json.dump({"diffs": [x.as_dict() for x in d["diffs"]],
                   "known_defect_diffs": [x.as_dict() for x in d.get("known_diffs", [])],
                   "error_cells": [x.as_dict() for x in d["error_cells"]],
                   "production_diffs": d["production_diffs"]}, f, indent=1)
    return path
