"""Read a standalone feed workbook - one small export, one feed.

The FY26 planning workbook carries every feed at once, so refreshing a single
number means re-importing all six from a 6 MB file whose row anchors have to
still be where `layout.py` says they are. These readers take the much smaller
extracts a planner can produce on demand - a tank inventory dump, an open-order
extract, the forecast grid - and turn them into the same values the workbook
importer produces, so nothing downstream can tell the difference.

Three sheet kinds are recognised, covering four of the six feeds:

  inventory    Code | Product | Net Inv                     -> inventory
  open_orders  Code | Product | <one column per ship date>  -> open_orders
  forecast     CODE | Oct..Sep, twice side by side          -> sales_forecast
                                                            + blend_component_demand

Shapes are validated against their own headers rather than assumed. A column
that moved raises `FeedSheetError` instead of importing a plausible-looking
wrong number, which is the same bargain `WorkbookImporter.validate_layout`
makes with the planning workbook.
"""
from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from .xlsx import MONTHS, is_num, load, norm_code, text


class FeedSheetError(RuntimeError):
    """The workbook is not the shape this reader expects."""


class FeedData(NamedTuple):
    #: product code -> second key (date, month, or "ALL") -> value
    values: Dict[str, Dict[Optional[str], float]]
    #: product code -> the name the sheet gave it, where it carried one
    labels: Dict[str, str]
    #: anything a planner should know about how the sheet was read
    notes: List[str]

    @property
    def cells(self) -> int:
        return sum(len(v) for v in self.values.values())


#: Which feeds each uploadable sheet supplies. The forecast export holds two
#: grids side by side, exactly as the workbook's SALES FORECAST tab does.
KIND_FEEDS = {
    "inventory": ("inventory",),
    "open_orders": ("open_orders",),
    "forecast": ("sales_forecast", "blend_component_demand"),
}
KINDS = tuple(KIND_FEEDS)

#: The reverse map, for asking which file a feed now comes from.
FEED_KIND = {f: k for k, fs in KIND_FEEDS.items() for f in fs}

KIND_TITLES = {
    "inventory": "Tank inventory",
    "open_orders": "Open orders",
    "forecast": "Forecast & blend demand",
}

KIND_SHAPES = {
    "inventory": "Code | Product | Net Inv",
    "open_orders": "Code | Product | one column per ship date",
    "forecast": "CODE | Oct...Sep, twice: sales forecast then blend demand",
}


# --------------------------------------------------------------- cell helpers

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%m-%d-%Y")


def _as_date(v):
    """Dates in these exports arrive as real dates or as `MM/DD/YYYY` text."""
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = text(v)
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _as_month(v):
    """`Oct`, `OCT`, `October` and a real date all key as `OCT`."""
    d = _as_date(v)
    if d is not None:
        return MONTHS[d.month - 1]
    s = text(v).upper()[:3]
    return s if s in MONTHS else None


def _header(ws, row=1):
    return dict((c, text(ws.cell(row, c).value))
                for c in range(1, ws.max_column + 1))


def _find_col(header, *names):
    wanted = set(n.lower() for n in names)
    for c in sorted(header):
        if header[c].lower() in wanted:
            return c
    return None


def _row_summary(hdr, limit=8):
    cells = [hdr[c] for c in sorted(hdr) if hdr[c]][:limit]
    return ", ".join(repr(c) for c in cells) or "(empty)"


def _open(path):
    if not os.path.exists(path):
        raise FeedSheetError("no such file: {}".format(path))
    try:
        wb = load(path, data_only=True)
    except Exception as exc:                # noqa: BLE001 - shown to the user
        raise FeedSheetError("{} could not be read as a workbook ({}: {})".format(
            os.path.basename(path), type(exc).__name__, exc))
    if not wb.worksheets:
        raise FeedSheetError("{} has no worksheets".format(os.path.basename(path)))
    return wb


# ------------------------------------------------------------------- readers

def read_inventory(path):
    """Net gallons on hand per product.

    Staged under the key `ALL` rather than a tank id: the export is already
    netted per product, and matching the seed connector's key is what lets an
    override a planner made against the workbook survive the switch to files.
    """
    ws = _open(path).worksheets[0]
    hdr = _header(ws)
    code_col = _find_col(hdr, "code")
    val_col = None
    for c in sorted(hdr):
        if hdr[c].lower().replace(" ", "") in ("netinv", "netinventory",
                                               "netgallons", "gallons"):
            val_col = c
            break
    if code_col is None or val_col is None:
        raise FeedSheetError(
            "inventory sheet needs a 'Code' column and a 'Net Inv' column; "
            "row 1 reads {}".format(_row_summary(hdr)))
    name_col = _find_col(hdr, "product", "name", "description")

    labels = {}
    notes = []
    totals = defaultdict(float)
    seen = {}
    skipped = 0

    for r in range(2, ws.max_row + 1):
        code = norm_code(ws.cell(r, code_col).value)
        if not code:
            continue
        v = ws.cell(r, val_col).value
        if not is_num(v):
            if text(v):
                skipped += 1
            continue
        if code in seen:
            notes.append("{} appears on rows {} and {}; the two were added "
                         "together".format(code, seen[code], r))
        seen.setdefault(code, r)
        totals[code] += float(v)
        if name_col and text(ws.cell(r, name_col).value):
            labels.setdefault(code, text(ws.cell(r, name_col).value))

    if skipped:
        notes.append("{} rows had a non-numeric Net Inv and were skipped".format(
            skipped))
    if not totals:
        raise FeedSheetError(
            "inventory sheet has a valid header but no product rows")
    values = dict((code, {"ALL": gal}) for code, gal in totals.items())
    return FeedData(values, labels, notes)


def read_open_orders(path):
    """Booked orders per product per ship date, stored as positive demand.

    The extract states orders the way the workbook does, as negative gallons
    leaving the tank; staging holds demand as a positive number, so the sign is
    flipped here. A sheet that mixes signs is rejected rather than guessed at -
    there is no reading of a mixed sheet that is safe to assume.
    """
    ws = _open(path).worksheets[0]
    hdr = _header(ws)
    code_col = _find_col(hdr, "code")
    if code_col is None:
        raise FeedSheetError(
            "open-orders sheet needs a 'Code' column; row 1 reads {}".format(
                _row_summary(hdr)))
    date_cols = []
    for c in sorted(hdr):
        if c <= code_col:
            continue
        d = _as_date(ws.cell(1, c).value)
        if d is not None:
            date_cols.append((c, d))
    if not date_cols:
        raise FeedSheetError(
            "open-orders sheet has no date columns in row 1; found {}".format(
                _row_summary(hdr)))
    name_col = _find_col(hdr, "product", "open orders (detail)", "name",
                         "description")

    raw = defaultdict(dict)
    labels = {}
    notes = []
    seen = {}
    negatives = positives = 0

    for r in range(2, ws.max_row + 1):
        code = norm_code(ws.cell(r, code_col).value)
        if not code:
            continue
        if code in seen:
            notes.append("{} appears on rows {} and {}; their orders were added "
                         "together".format(code, seen[code], r))
        seen.setdefault(code, r)
        if name_col and text(ws.cell(r, name_col).value):
            labels.setdefault(code, text(ws.cell(r, name_col).value))
        for c, d in date_cols:
            v = ws.cell(r, c).value
            if not is_num(v) or not v:
                continue
            if v < 0:
                negatives += 1
            else:
                positives += 1
            iso = d.isoformat()
            raw[code][iso] = raw[code].get(iso, 0.0) + float(v)

    if negatives and positives:
        raise FeedSheetError(
            "open-orders sheet mixes {} negative and {} positive quantities, so "
            "which direction an order runs cannot be determined; export it with "
            "one sign".format(negatives, positives))

    flip = negatives > 0
    if not flip and positives:
        notes.append("quantities were already positive and were taken as demand "
                     "as they stand")
    values = dict((code, dict((k, -v if flip else v) for k, v in by_date.items()))
                  for code, by_date in raw.items())
    if not values:
        raise FeedSheetError("open-orders sheet has date columns but no orders")
    return FeedData(values, labels, notes)


def read_forecast(path):
    """The two monthly grids: sales forecast, then blend component demand.

    Both are daily rates in gallons per day keyed by month name, laid out as
    `CODE | Oct ... Sep` twice across the sheet - the left grid the forecast,
    the right one the blend components, in the same order as the workbook's
    columns T and AH.
    """
    ws = _open(path).worksheets[0]
    hdr = _header(ws)
    code_cols = [c for c in sorted(hdr) if hdr[c].lower() == "code"]
    if not code_cols:
        raise FeedSheetError(
            "forecast sheet needs a 'CODE' column; row 1 reads {}".format(
                _row_summary(hdr)))
    if len(code_cols) > 2:
        raise FeedSheetError(
            "forecast sheet has {} 'CODE' columns (at {}); expected the sales "
            "grid and the blend grid".format(len(code_cols), code_cols))

    feeds = KIND_FEEDS["forecast"]
    out = {}
    for feed, code_col in zip(feeds, code_cols):
        out[feed] = _read_month_grid(ws, code_col, feed)
    if len(code_cols) == 1:
        out[feeds[1]] = FeedData({}, {}, [
            "the sheet carried only one CODE grid, so blend component demand "
            "was left empty"])
    return out


def _read_month_grid(ws, code_col, feed):
    months = []
    for c in range(code_col + 1, min(code_col + 13, ws.max_column + 1)):
        m = _as_month(ws.cell(1, c).value)
        if m is None:
            break
        months.append((c, m))
    if len(months) < 12:
        raise FeedSheetError(
            "{}: expected 12 month columns after the CODE column at {}, found "
            "{} ({})".format(feed, code_col, len(months),
                             ", ".join(m for _, m in months) or "none"))

    values = {}
    notes = []
    seen = {}
    for r in range(2, ws.max_row + 1):
        code = norm_code(ws.cell(r, code_col).value)
        if not code:
            continue
        if code in seen:
            notes.append("{}: {} appears on rows {} and {}; the first was "
                         "kept".format(feed, code, seen[code], r))
            continue
        seen[code] = r
        per_month = {}
        for c, m in months:
            v = ws.cell(r, c).value
            if is_num(v) and v:
                per_month[m] = float(v)
        if per_month:
            values[code] = per_month
    return FeedData(values, {}, notes)


# ------------------------------------------------------------------ dispatch

def read(kind, path):
    """Read one uploaded sheet, keyed by the feed or feeds it supplies."""
    if kind not in KIND_FEEDS:
        raise FeedSheetError("unknown sheet kind {!r}; expected one of {}".format(
            kind, ", ".join(KINDS)))
    if kind == "inventory":
        return {"inventory": read_inventory(path)}
    if kind == "open_orders":
        return {"open_orders": read_open_orders(path)}
    return read_forecast(path)


def kind_for_filename(name):
    """Match a dropped file to a sheet kind by its name.

    Planners name these by hand, so `open orders.xlsx`, `open_orders.xlsx` and
    `Open-Orders.XLSX` all have to land on the same feed.
    """
    stem = os.path.splitext(os.path.basename(name))[0].lower()
    stem = " ".join(stem.replace("-", " ").replace("_", " ").split())
    aliases = {
        "inventory": "inventory",
        "inv": "inventory",
        "tank inventory": "inventory",
        "net inventory": "inventory",
        "open orders": "open_orders",
        "openorders": "open_orders",
        "open order": "open_orders",
        "orders": "open_orders",
        "forecast": "forecast",
        "sales forecast": "forecast",
        "sales": "forecast",
        "forecast blends": "forecast",
    }
    return aliases.get(stem)
