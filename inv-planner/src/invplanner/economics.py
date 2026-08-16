"""Prices, netbacks and changeover costs — the objective's coefficients.

Kept as editable data rather than buried in the model, because these are the
numbers operations and finance will argue about, and every one of them changes
what the optimizer decides.

Two conventions, stated explicitly because they are easy to get wrong by a factor
of 42:

* Crude and outlet netbacks are quoted **per barrel**, the way the refinery
  quotes them.
* Product values and all model costs are held **per gallon**, because inventory
  and demand are in gallons.

`per_gal()` converts. Nothing else in the codebase should do that arithmetic.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

GAL_PER_BBL = 42.0


def per_gal(dollars_per_bbl: float) -> float:
    return dollars_per_bbl / GAL_PER_BBL


#: Downgrade destinations, keyed by the charge-schedule transfer unit that feeds
#: them.
@dataclass
class Outlet:
    key: str
    title: str
    #: Netback relative to crude, **per gallon**. #6 oil and the cat cracker are
    #: priced at crude less a fixed discount, so material that is only worth
    #: crude loses exactly this much before operating costs.
    discount_to_crude_per_gal: Optional[float] = None
    #: Set instead of the discount when the outlet is priced independently.
    netback_per_gal: Optional[float] = None

    def netback(self, crude_per_gal: float) -> Optional[float]:
        if self.netback_per_gal is not None:
            return self.netback_per_gal
        if self.discount_to_crude_per_gal is not None:
            return crude_per_gal - self.discount_to_crude_per_gal
        return None


#: All four are unlimited sinks: they absorb whatever the plan cannot hold, and
#: the only thing restraining the optimizer from using them is the netback. A
#: sink with a capacity would turn an overflow into an infeasibility, which is
#: the failure mode these models usually die from.
OUTLETS: Dict[str, Outlet] = {
    "TRANSFER_6OIL": Outlet("TRANSFER_6OIL", "#6 oil",
                            discount_to_crude_per_gal=0.50),
    "TRANSFER_CAT": Outlet("TRANSFER_CAT", "Cat cracker",
                           discount_to_crude_per_gal=0.50),
    # Diesel and gasoline are downgrades into products with their own prices
    # rather than crude-linked netbacks. Both need a number from finance.
    "TRANSFER_DIESEL": Outlet("TRANSFER_DIESEL", "Diesel"),
    "TRANSFER_FINDSL": Outlet("TRANSFER_FINDSL", "Finished diesel"),
    "TRANSFER_GASOLINE": Outlet("TRANSFER_GASOLINE", "Gasoline"),
}


@dataclass
class Economics:
    """One priced scenario."""

    crude_price_per_bbl: float = 70.00

    #: $/gal that a product is worth if sold as itself. Missing entries are the
    #: main gap: without them a downgrade cannot be priced, only counted.
    product_value_per_gal: Dict[str, float] = field(default_factory=dict)

    #: $/gal of margin lost when demand goes unserved. Defaults to the product
    #: value when not set separately.
    lost_sale_margin_per_gal: Dict[str, float] = field(default_factory=dict)

    #: Used where no per-product margin exists. This is the single most
    #: influential unknown left: its ratio to the $0.50 downgrade discount decides
    #: whether the optimizer prefers to downgrade or to short a customer.
    default_lost_sale_margin_per_gal: Optional[float] = None

    #: $ per changeover, by unit. A cleanout-heavy unit costs more to switch.
    changeover_cost: Dict[str, float] = field(default_factory=dict)

    @property
    def crude_price_per_gal(self) -> float:
        return per_gal(self.crude_price_per_bbl)

    def outlet_netback_per_gal(self, outlet_key: str) -> Optional[float]:
        outlet = OUTLETS.get(outlet_key)
        if outlet is None:
            return None
        return outlet.netback(self.crude_price_per_gal)

    def downgrade_cost_per_gal(self, product: str,
                               outlet_key: str) -> Optional[float]:
        """Margin given up by pushing a gallon of `product` to `outlet_key`.

        Two modes, so the model can run before every product is priced:

        * **per-product** — a value is known, so the cost is
          `value − netback`. This is what makes the optimizer protect a
          specialty solvent more than a low-value stream.
        * **flat** — no value yet, so the cost is the outlet's discount to
          crude, i.e. what the material loses if it is only worth crude. This is
          the floor on the true loss, never an overstatement.

        `cost_basis()` reports which one was used, so a report can never silently
        present a flat number as if it were a real per-product one.
        """
        outlet = OUTLETS.get(outlet_key)
        if outlet is None:
            return None
        netback = self.outlet_netback_per_gal(outlet_key)
        value = self.product_value_per_gal.get(product)
        if value is not None and netback is not None:
            return max(0.0, value - netback)
        return outlet.discount_to_crude_per_gal

    def cost_basis(self, product: str, outlet_key: str) -> str:
        if self.product_value_per_gal.get(product) is not None:
            return "per_product"
        outlet = OUTLETS.get(outlet_key)
        if outlet and outlet.discount_to_crude_per_gal is not None:
            return "flat"
        return "unpriced"

    def lost_sale_cost_per_gal(self, product: str) -> Optional[float]:
        if product in self.lost_sale_margin_per_gal:
            return self.lost_sale_margin_per_gal[product]
        if product in self.product_value_per_gal:
            return self.product_value_per_gal[product]
        return self.default_lost_sale_margin_per_gal


#: Baseline assumption set. Product values are deliberately empty: they are the
#: outstanding input, and guessing them would produce a confident wrong answer.
DEFAULT = Economics(
    crude_price_per_bbl=70.00,
    changeover_cost={
        # Placeholders until operations price a transition. Ordered by how much
        # the plant appears to avoid switching each unit.
        "MEK": 0.0,
        "HYDRO": 0.0,
        "EXTRACT": 0.0,
        "ROSE": 0.0,
        "PLATFORMER": 0.0,
        "CRUDE_MODE": 0.0,
    },
)


# ---------------------------------------------------------------- price cover
def price_key(code: str, spec) -> str:
    """The key a product's netback is looked up under.

    Not always the product's own code, for two reasons that both bit before:

    * **Two codes, one oil.** Five products carry a stream code inside the plant
      and a grade code on the invoice - 9704 is Kendex 0150 UNHT as a stream and
      4305 as a grade. A price attaches to the grade.
    * **Demand that is really a blend pull.** Platformate and isomerate are never
      sold to anyone; their forecast is the E10 gasoline pull, split 75/25. Their
      value is the gasoline netback, and pricing them separately would invent a
      market that does not exist.
    """
    from . import model_config as cfg

    pool = cfg.BLEND_POOL_PRODUCTS.get(code)
    if pool:
        return pool
    # A sink the model invented rather than found - gasoline has no block in the
    # workbook at all - is priced as the outlet, since there is no product there
    # to price. The sinks that *are* real products (#6 oil, cat feed, finished
    # diesel) keep their own key and carry their own netback.
    product = (spec.products.get(code) or {}) if spec else {}
    if product.get("is_sink") and not product.get("tanked"):
        return product.get("sink") or code
    entry = cfg.SALES_CODES.get(code)
    return entry["sold_as"] if entry else code


def exits(spec, demand: Dict[str, float]) -> List[Dict[str, Any]]:
    """Every way material leaves the plant, and the price key each needs.

    Three of them, and a schedule cannot be costed unless all three are priced:
    demand served, downgrade routed to a sink, and product lifted as-is. Listing
    them from the spec rather than by hand is the point - a route added later
    shows up here without anyone remembering to add it.
    """
    from . import model_config as cfg

    out: List[Dict[str, Any]] = []
    for code, gal in sorted(demand.items(), key=lambda kv: -kv[1]):
        if gal <= 0:
            continue
        out.append({"exit": "demand", "code": code, "gal": gal,
                    "price_key": price_key(code, spec)})
    for sink in sorted({r["sink"] for r in spec.sink_routes}):
        out.append({"exit": "downgrade", "code": sink, "gal": None,
                    "price_key": sink})
    for code, p in sorted(spec.products.items()):
        if p.get("unlimited_offtake"):
            out.append({"exit": "sold as-is", "code": code, "gal": None,
                        "price_key": price_key(code, spec)})
    return out


# ------------------------------------------------------- measured gross profit
#: Operations' own numbers, captured from the netback sheet and versioned here so
#: the model never depends on a spreadsheet sitting on somebody's desktop.
NETBACKS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data",
    "netbacks.json")


def load_gross_profit(path: Optional[str] = None) -> Dict[str, float]:
    """Gross profit per gallon, keyed by **sales** code.

    Gross profit already nets the crude cost, which is what the objective wants:
    a lost sale gives up exactly this, and a downgrade gives up the difference
    between two of them. No conversion, no crude price, no netback arithmetic.
    """
    try:
        with open(path or NETBACKS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {code: entry["gross_profit_per_gal"]
            for code, entry in (data.get("values") or {}).items()}


def gross_profit(code: str, spec, table: Dict[str, float],
                 default: Optional[float] = None) -> Optional[float]:
    """What a gallon of `code` is worth, looked up under the code it sells as."""
    value = table.get(price_key(code, spec))
    return default if value is None else value
