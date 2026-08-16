"""Export a schedule as blocks that paste into the workbook's Charge Schedule.

One block per unit, never one big rectangle. The rows between the unit blocks are
not spare: row 85 carries a total, rows 68/75/86 carry the date headers, row 109
carries the "enter BBLs in yellow cells" note. A single contiguous paste from the
first charge row to the last would flatten every one of them.

Deliberately not a writer for the workbook itself. That file carries 192 charts,
29 drawings and a VBA project; openpyxl drops parts of it on load, and driving
Excel to edit it in place ran for 28 minutes on a full recalculation before it
had to be killed. Handing the planner a block to paste takes about a second and
never opens their file.
"""
from __future__ import annotations

import datetime as dt
import io
from collections import defaultdict
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import layout

HEAD = PatternFill("solid", fgColor="1F3B4D")
PASTE = PatternFill("solid", fgColor="FFF3C4")
GREY = PatternFill("solid", fgColor="EEF1F3")
THIN = Side(style="thin", color="C8D0D6")
VALUE_COL = 4                       # values start in column D of the export


def grid_column(day: dt.date) -> int:
    """The workbook column holding `day` in the charge grid."""
    y, m, d = (int(x) for x in layout.CS_GRID_FIRST_DATE.split("-"))
    return layout.CS_FIRST_COL + (day - dt.date(y, m, d)).days


def schedule_workbook(charge_lines: List[Dict[str, Any]],
                      values: Dict[str, Dict[dt.date, float]],
                      dates: List[dt.date],
                      title: str,
                      subtitle: str = "") -> bytes:
    """Build the paste-block workbook.

    `values` is keyed by charge-line key, then date, in barrels. A line absent
    from it, or a day absent from a line, is written blank - which is a real
    instruction rather than a gap, so the caller should pass the whole window.
    """
    row_of, label_of = {}, {}
    for block in layout.CHARGE_BLOCKS:
        for row in range(block.first_row, block.last_row + 1):
            row_of["{}#{}".format(block.unit, row)] = row
    for line in charge_lines:
        if line["key"] in row_of:
            label_of[row_of[line["key"]]] = "{} {}".format(
                line.get("code") or "", line.get("name") or "").strip()

    by_row: Dict[int, Dict[dt.date, float]] = defaultdict(dict)
    for key, series in values.items():
        row = row_of.get(key)
        if row is None:
            continue
        for day, bbl in series.items():
            if bbl:
                by_row[row][day] = bbl

    first_col = grid_column(dates[0])
    last_col = grid_column(dates[-1])
    first_letter = get_column_letter(first_col)
    last_letter = get_column_letter(last_col)

    wb = Workbook()
    ws = wb.active
    ws.title = "Paste blocks"

    ws.cell(1, 1, title).font = Font(bold=True, size=13)
    ws.cell(2, 1, "{} to {} ({} days). Paste each block at the cell named in its "
                  "header. Value columns are {}:{}.".format(
                      dates[0], dates[-1], len(dates),
                      first_letter, last_letter)).font = Font(size=10)
    ws.cell(3, 1, "Highlighted cells only. A blank means the unit does not run "
                  "that line that day - paste it, do not skip it, or the "
                  "schedule becomes a mixture of both.").font = Font(
                      size=10, italic=True)
    if subtitle:
        ws.cell(4, 1, subtitle).font = Font(size=10, color="5A6A76")

    r = 6
    written = []
    for block in layout.CHARGE_BLOCKS:
        rows = list(range(block.first_row, block.last_row + 1))
        if not any(by_row.get(x) for x in rows):
            continue
        target = "{}{}".format(first_letter, block.first_row)
        written.append((block.unit, target, len(rows)))

        head = ws.cell(r, 1, "{}  ->  paste at {}".format(block.unit, target))
        head.font = Font(bold=True, color="FFFFFF")
        head.fill = HEAD
        for k in range(2, VALUE_COL + len(dates)):
            ws.cell(r, k).fill = HEAD
        r += 1

        ws.cell(r, 1, "row").font = Font(bold=True, size=9)
        ws.cell(r, 2, "line").font = Font(bold=True, size=9)
        for i, day in enumerate(dates):
            cell = ws.cell(r, VALUE_COL + i, day)
            cell.number_format = "dd-mmm"
            cell.font = Font(bold=True, size=9)
            cell.alignment = Alignment(horizontal="center")
        r += 1

        for wrow in rows:
            ws.cell(r, 1, wrow).font = Font(size=9)
            ws.cell(r, 1).fill = GREY
            ws.cell(r, 2, label_of.get(wrow, "")).font = Font(size=9)
            ws.cell(r, 2).fill = GREY
            for i, day in enumerate(dates):
                bbl = by_row.get(wrow, {}).get(day)
                cell = ws.cell(r, VALUE_COL + i, round(bbl) if bbl else None)
                cell.fill = PASTE
                cell.number_format = "#,##0"
                cell.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            r += 1
        r += 1

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 2
    for i in range(len(dates)):
        ws.column_dimensions[get_column_letter(VALUE_COL + i)].width = 7
    ws.freeze_panes = ws.cell(1, VALUE_COL)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
