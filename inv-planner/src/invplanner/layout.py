"""Fixed layout of the FY26 workbook.

These row/column anchors are the one genuinely hand-maintained part of the
importer. They are stable within a workbook generation but WILL move if someone
inserts rows, so `importer.validate_layout()` checks every anchor against the
label it expects and fails loudly rather than importing silently-wrong data.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

WORKBOOK_DEFAULT = "Inventory Planning - FY26 12 MONTH.xlsm"

# ---------------------------------------------------------------- Charge Schedule
CS = "Charge Schedule"
CS_DATE_ROW_BBL = 65          # dates across the BBL section
CS_DATE_ROW_FLAG = 17         # dates across the 1/blank flag section
CS_CRUDE_BBL_ROW = 66         # crude unit charge, BBL/day
CS_CRUDE_MODE_ROW = 18        # "R" (regular) / "L" (low volatility)
CS_FIRST_COL = 4              # column D
#: The date sitting in `CS_FIRST_COL` of the charge grid. The grid runs one day
#: per column from here with no gaps, so a date's column is this offset plus the
#: number of days. Needed by the schedule export to say *where* to paste, and
#: checked against the workbook by `validate_layout()` so it cannot drift.
CS_GRID_FIRST_DATE = "2026-06-01"


class ChargeBlock(NamedTuple):
    unit: str
    first_row: int
    last_row: int
    named_range: Optional[str]


# Row ranges of the BBL entry grid. `unit` is the process unit consuming the feed;
# TRANSFER_* blocks are downgrade/transfer routes rather than process units.
CHARGE_BLOCKS: List[ChargeBlock] = [
    ChargeBlock("MEK",              69, 73,  "Charge_Gal_MEK"),
    ChargeBlock("HYDRO",            76, 84,  "Charge_Gal_Hydro"),
    ChargeBlock("EXTRACT",          87, 91,  "Charge_Gal_Exc"),   # row 91 sits OUTSIDE the named range
    ChargeBlock("TOLLING",          94, 94,  "Charge_Gal_tol"),
    ChargeBlock("ROSE",             97, 98,  "Charge_Gal_Rose"),
    ChargeBlock("PLATFORMER",      102, 107, "Charge_Gal_Plat"),
    ChargeBlock("TRANSFER_DIESEL", 111, 118, "Charge_Gal_ToDiesel"),
    ChargeBlock("SONNEBORN",       121, 122, None),
    ChargeBlock("TRANSFER_FINDSL", 135, 136, None),
    ChargeBlock("TRANSFER_6OIL",   143, 145, None),
    ChargeBlock("TRANSFER_CAT",    148, 150, None),
    ChargeBlock("RAILCAR",         153, 156, None),
]

# The `Charge` named range (B68:OY150) that stream sheets scan with a first-match
# VLOOKUP when computing a product's "Production Out".
CHARGE_LOOKUP_FIRST_ROW = 68
CHARGE_LOOKUP_LAST_ROW = 150

# ------------------------------------------------------------- Production - Out
PO = "Production - Out"
PO_CRUDE_CHARGE_ROW = 15      # = Charge Schedule row 66
PO_CRUDE_MODE_ROW = 16        # = Charge Schedule row 18
PO_DATE_ROW = 17
PO_FIRST_COL = 5              # column E
PO_CRUDE_ROWS = (18, 27)      # side streams via CRUDE YIELDS
PO_LOSS_ROW = 29
PO_GAL_DATE_ROW = 98          # gallons roll-up grid
PO_GAL_FIRST_ROW = 99
PO_GAL_CODE_COL = 3           # column C


class UnitSection(NamedTuple):
    unit: str
    first_row: int
    last_row: int


# Sections of the BBL production grid; each row: col A = yield, col B = charge
# product, col C = output product.
PO_UNIT_SECTIONS: List[UnitSection] = [
    UnitSection("MEK",         32, 41),
    UnitSection("HYDRO",       45, 55),
    UnitSection("EXTRACT",     59, 66),
    UnitSection("TOLLING",     70, 70),
    UnitSection("ROSE",        75, 79),
    UnitSection("PLATFORMER",  81, 88),
]

PO_DIESEL_YIELDBACK_ROW = 54          # sum of charge_i * (1/yld_i - 1)
PO_DIESEL_YIELDBACK_SOURCE_ROWS = [45, 49, 50, 51, 53]

# Rows 62 and 66 read the deep-extracted bright stock line (Charge Schedule row 91),
# which lies outside the Charge_Gal_Exc named range, via INDEX instead of VLOOKUP.
PO_EXTRACT_DEEP_ROWS = {62, 66}
CS_EXTRACT_DEEP_ROW = 91

# Transfer rows: production *into* a pool, summed straight off Charge Schedule rows.
PO_TRANSFER_ROWS: Dict[int, dict] = {
    91: {"out": "9713", "rows": (111, 118), "divisor": 1.0},
    92: {"out": "8170", "rows": (135, 136), "divisor": 1.0},
    93: {"out": "4129", "rows": (156, 156), "divisor": 42.0},
    94: {"out": "8201", "rows": (143, 145), "divisor": 1.0},
    95: {"out": "8221", "rows": (148, 150), "divisor": 1.0},
}

# ------------------------------------------------------------------- Reference
PRODUCT_LIST = "Product list "
PRODUCT_LIST_FIRST_ROW = 8

TANK_CAP = "Tank Capacity"
TANK_CAP_FIRST_ROW = 5
TANK_CAP_CODE_COL = 11   # K
TANK_CAP_NAME_COL = 12   # L
TANK_CAP_VALUE_COL = 13  # M

CRUDE_YIELDS = "CRUDE YIELDS"
CY_FIRST_ROW = 7
CY_LAST_ROW = 19
CY_CODE_COL = 2          # B
CY_NAME_COL = 3          # C
CY_FIRST_MONTH_COL = 4   # D = Oct
CY_MONTH_HEADER_ROW = 6

CRUDE_RATES = "CRUDE RATES"
CR_MONTH_ROWS = (5, 16)      # B=month name, C=bbl/day, D=days
CR_RECEIPT_ROW = 5           # H5:S5 keyed by H4:S4 month names
CR_RECEIPT_HDR_ROW = 4
CR_RECEIPT_FIRST_COL = 8     # H

RUN_RATES = "UNIT RUN RATES"
RR_FIRST_ROW = 6
RR_LAST_ROW = 40
RR_CODE_COL = 3          # C
RR_NAME_COL = 4          # D
RR_FIRST_MONTH_COL = 5   # E = Oct
RR_MONTH_HEADER_ROW = 5
RR_K30_TO_DIESEL_ROW = 42
RR_DEWAXED_LLN_ROW = 43

INV_TARGETS = "INV TARGETS"
IT_FIRST_ROW = 3
IT_LAST_ROW = 60
IT_CODE_COL = 1          # A
IT_NAME_COL = 2          # B
IT_LCL_COL = 3           # C
IT_UCL_COL = 4           # D

BLENDS = "BLENDS"
BLENDS_BOM_FIRST_ROW = 63
BLENDS_BOM_LAST_ROW = 175
BLENDS_FRACTION_COL = 1  # A
BLENDS_BLEND_COL = 2     # B
BLENDS_COMPONENT_COL = 3 # C

SALES_FORECAST = "Sales Forecast"
SF_HEADER_ROW = 43
SF_FIRST_ROW = 44
SF_LAST_ROW = 279
SF_RATE_CODE_COL = 20    # T
SF_RATE_FIRST_COL = 21   # U = Oct
SF_BLEND_CODE_COL = 34   # AH
SF_BLEND_FIRST_COL = 35  # AI = Oct

OPEN_ORDERS = "Open Orders"
OO_DATE_ROW = 6
OO_CODE_COL = 1          # A
OO_FIRST_ROW = 9
OO_LAST_ROW = 200
OO_FIRST_COL = 4         # D

INVENTORY = "Inventory"
INV_FIRST_ROW = 9
INV_CODE_COL = 1         # A
INV_TANK_COL = 3         # C
INV_GALLONS_COL = 6      # F  <- what tInv sums (heel-adjusted gallons)
INV_RAW_GALLONS_COL = 4  # D  (raw daily tank inventory, for reference)
# Crude inventory is tracked separately from product inventory, at site level and
# in BARRELS: the CRUDE sheet opens from this cell, not from the product lookup.
INV_CRUDE_TOTAL_CELL = (13, 24)      # X13, "Total ARG BRADFORD PA"
INV_CRUDE_LABEL_CELL = (13, 23)      # W13, used to validate the anchor

BASE_OIL_TRANSFER = "BaseOilTransfer"
BOT_CODE_COL = 48        # AV - codes the stream sheets MATCH against
BOT_FIRST_ROW = 3
BOT_LAST_ROW = 10
BOT_MONTH_HDR_ROW = 3
# The month header sits in AW:BH but the values live in the BotByMonth named
# range (BI3:BU10); the sheet MATCHes the header and INDEXes the value range, so
# Oct..Sep resolve to BJ..BU, not to the header columns.
BOT_FIRST_MONTH_COL = 62  # BJ
BOT_HEADER_FIRST_COL = 49  # AW
# Per-product columns of the transfer analysis table (A:AR) used by a second
# lookup shape: AQ = monthly projection, AR = daily projection.
BOT_TABLE_CODE_COL = 1
BOT_TABLE_FIRST_ROW = 4
BOT_TABLE_LAST_ROW = 10

# -------------------------------------------------------------- Stream sheets
STREAM_SHEETS = ["Solvents", "LLN", "Cstock", "MN", "HN", "Napthas"]
STREAM_DATE_ROW = 12
STREAM_LABEL_COL = 7     # G
STREAM_OUT_COL = 4       # D
STREAM_IN_COL = 5        # E
STREAM_FIRST_DATE_COL = 8  # H
STREAM_AS_OF_CELL = (7, 6)  # F7
# Balance rows are classified from a steady-state column rather than the first
# one: the first column carries edge-case formulas that were never filled across
# (e.g. the finished-diesel weekend rule lives only in column H), so trusting it
# would apply a one-column special case to the whole horizon.
STREAM_CLASSIFY_OFFSET = 60

CRUDE_SHEET = "CRUDE"
CRUDE_TANK_CAPACITY_GAL = 9_600_000.0   # hardcoded in the sheet as 9600000/42 bbl
# The CRUDE sheet is a single block with its own row set, kept in barrels.
CRUDE_ROW_MAP = {
    "Begin Inventory": 14,
    "Receipts": 15,
    "Net Charge Available": 19,
    "Production Out": 20,
    "End Inventory": 21,
    "Tank Capacity": 22,
    "Excess Capacity": 23,
}
CRUDE_BLOCK_ID = "CRUDE:14"

BALANCE_LABELS = [
    "Begin Inventory", "Production In", "Receipts", "Sales", "Blends", "Forecast",
    "Net Charge Available", "Production Out", "Out to Diesel", "Downgrade",
    "End Inventory", "Tank Capacity", "Excess Capacity",
]
