"""Low-level workbook access helpers.

The workbook is read twice: once with formulas (to classify what each row *means*)
and once with cached values (to read data and to compare against in the parity harness).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

import openpyxl
from openpyxl.utils import get_column_letter

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

GAL_PER_BBL = 42.0


def load(path: str, data_only: bool):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(path, data_only=data_only, read_only=False)


def as_date(v: Any) -> Optional[dt.date]:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return None


def month_key(d: dt.date) -> str:
    return MONTHS[d.month - 1]


def norm_code(v: Any) -> Optional[str]:
    """Normalize a product code to a 4-character string.

    Codes in the workbook are inconsistently typed: `LEFT(B,4)` yields text
    ("0530") while hand-entered cells hold numbers (530). Excel's VLOOKUP treats
    those as different keys, which silently breaks a handful of lookups. We
    normalize to zero-padded 4-char text and record the coercions so the parity
    report can attribute the resulting differences to the workbook.
    """
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        return "{:04d}".format(v)
    s = str(v).strip()
    if not s:
        return None
    s = s.split("-")[0].strip()
    if s.isdigit():
        return s.zfill(4)[:4] if len(s) <= 4 else s[:4]
    return s[:4].strip()


def num(v: Any, default: float = 0.0) -> float:
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    return default


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def read_date_row(ws, row: int, start_col: int, max_col: Optional[int] = None
                  ) -> List[Tuple[int, dt.date]]:
    """Read a horizontal date header, stopping at the first run of non-dates."""
    out: List[Tuple[int, dt.date]] = []
    limit = max_col or ws.max_column
    misses = 0
    for c in range(start_col, limit + 1):
        d = as_date(ws.cell(row, c).value)
        if d is None:
            misses += 1
            if misses >= 3 and out:
                break
            continue
        misses = 0
        out.append((c, d))
    return out


def formula(ws, row: int, col: int) -> str:
    v = ws.cell(row, col).value
    return v if isinstance(v, str) and v.startswith("=") else ""


def col_letter(c: int) -> str:
    return get_column_letter(c)
