"""Product families for the chart dashboards.

The workbook's chart tabs group by where the chart was pasted rather than by what
the product is: "Base Oil (6 Month)" actually holds base oils, waxes, resins and
crude side by side. These groups keep the same charts but sort them the way
planners talk about the products.

Membership is explicit rather than inferred from names, so it can be reviewed and
corrected by someone who knows the plant. Any block not listed here falls into
"other", which is shown but flagged, so a new product can never silently vanish
from the dashboards.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Order here is the order of the dashboard selector.
PRODUCT_GROUPS: List[Dict[str, Any]] = [
    {
        "id": "base_oil",
        "title": "Base oils",
        "blurb": "Waxy and dewaxed neutrals, bright stock and the finished base "
                 "oil grades, with their charge stocks.",
        "codes": [
            # light neutral
            "9718", "9720", "4329",
            # medium neutral
            "9117", "9302", "9704", "4315", "9118", "9202", "4325", "4577",
            # heavy neutral
            "9119", "9303", "4309", "4579",
            # bright stock / cylinder
            "9305", "9705", "4318", "4319", "4586",
        ],
    },
    {
        "id": "wax",
        "title": "Waxes",
        "blurb": "Slack waxes off the MEK unit, scale wax and petrolatum.",
        "codes": ["4449", "4451", "4454", "4458", "4459"],
    },
    {
        "id": "resin",
        "title": "Resins",
        "blurb": "ROSE unit feed and products: cylinder stock, light and heavy resins.",
        "codes": ["4313", "4317", "4554", "4555"],
    },
    {
        "id": "solvents",
        "title": "Solvents",
        "blurb": "Kensol 48, 50 and 61 — untreated charge stock and hydrotreated "
                 "finished grades.",
        "codes": ["9711", "4115", "9712", "4118", "9703", "4129"],
    },
    {
        "id": "diesel",
        "title": "Diesel & heating oil",
        "blurb": "The diesel pool: hydrotreater charge, finished road and off-road "
                 "grades, heating oils.",
        "codes": ["9713", "DDDD", "8170", "8175", "8135", "8105", "8136", "8177",
                  "8120", "8165", "8125", "8115"],
    },
    {
        "id": "naphtha",
        "title": "K-30 & naphthas",
        "blurb": "Platformer charge and products, Kensol 17 and 30, isomerate.",
        "codes": ["4111", "4107", "9103", "9511", "1128"],
    },
    {
        "id": "crude",
        "title": "Crude",
        "blurb": "Crude receipts against crude unit charge, in barrels.",
        "codes": [],
        "streams": ["CRUDE"],
    },
]

OTHER_GROUP = {
    "id": "other",
    "title": "Other",
    "blurb": "Blocks not yet assigned to a family — transfer pools and byproducts.",
    "codes": [],
}


def group_index() -> Dict[str, str]:
    """product code -> group id"""
    out: Dict[str, str] = {}
    for g in PRODUCT_GROUPS:
        for code in g["codes"]:
            out[code] = g["id"]
    return out


def group_for(code: Optional[str], sheet: str) -> str:
    if sheet.upper() == "CRUDE":
        return "crude"
    return group_index().get(code or "", OTHER_GROUP["id"])


def all_groups() -> List[Dict[str, Any]]:
    return PRODUCT_GROUPS + [OTHER_GROUP]


def group_by_id(gid: str) -> Optional[Dict[str, Any]]:
    return next((g for g in all_groups() if g["id"] == gid), None)


#: Streams stack vertically inside a dashboard in this order.
STREAM_ORDER = ["CRUDE", "Solvents", "LLN", "MN", "HN", "Cstock", "Napthas"]


def stream_sort_key(sheet: str) -> tuple:
    try:
        return (STREAM_ORDER.index(sheet), sheet)
    except ValueError:
        return (len(STREAM_ORDER), sheet)
