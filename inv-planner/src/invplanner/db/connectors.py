"""Feed connectors.

A connector knows how to pull one feed from one source and hand back plain rows.
Everything downstream (raw batch, staging merge, drift detection) is connector
agnostic, so replacing the workbook connector with a direct SQL pull against the
inventory database is a matter of adding a class here - no change to staging,
scenarios, or the engine.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, NamedTuple, Optional


class FeedRow(NamedTuple):
    k1: str
    k2: Optional[str] = None
    k3: Optional[str] = None
    value: Optional[float] = None
    label: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class FeedConnector:
    feed: str = ""
    source: str = ""
    #: True when a pull represents the complete picture, so keys missing from it
    #: mean "zero", not "unchanged".
    full_refresh: bool = True

    def fetch(self) -> List[FeedRow]:
        raise NotImplementedError


class WorkbookSeedConnector(FeedConnector):
    """Reads the feed out of the JSON seed built from the planning workbook.

    This is the bridge that lets the app run on real data today. The live
    connectors replace it one feed at a time without touching anything else.
    """

    def __init__(self, feed: str, seed_path: str, products: Dict[str, Any] = None):
        self.feed = feed
        self.seed_path = seed_path
        self.source = "workbook-seed:" + os.path.basename(seed_path)
        self._products = products or {}

    def _seed(self) -> Dict[str, Any]:
        with open(self.seed_path, encoding="utf-8") as f:
            return json.load(f)

    def _name(self, code: str) -> Optional[str]:
        p = self._products.get(code)
        return p.get("name") if isinstance(p, dict) else None

    def fetch(self) -> List[FeedRow]:
        data = self._seed()
        rows: List[FeedRow] = []

        if self.feed == "inventory":
            # The seed carries product-level net inventory; tank detail arrives
            # with the live connector, so stage a single synthetic tank per product.
            for code, gallons in data.get("opening_inventory", {}).items():
                rows.append(FeedRow(k1=code, k2="ALL", value=float(gallons),
                                    label=self._name(code)))

        elif self.feed == "open_orders":
            for code, by_date in data.get("open_orders", {}).items():
                for date, gallons in by_date.items():
                    rows.append(FeedRow(k1=code, k2=date, value=float(gallons),
                                        label=self._name(code)))

        elif self.feed == "sales_forecast":
            for code, by_month in data.get("sales_forecast", {}).items():
                for month, rate in by_month.items():
                    rows.append(FeedRow(k1=code, k2=month, value=float(rate),
                                        label=self._name(code)))

        elif self.feed == "blend_component_demand":
            for code, by_month in data.get("blend_component_demand", {}).items():
                for month, rate in by_month.items():
                    rows.append(FeedRow(k1=code, k2=month, value=float(rate),
                                        label=self._name(code)))

        elif self.feed == "base_oil_transfer":
            for code, by_month in data.get("base_oil_transfer", {}).items():
                for month, rate in by_month.items():
                    rows.append(FeedRow(k1=code, k2=month, value=float(rate),
                                        label=self._name(code)))

        return rows


class ReferenceBlendBomConnector(FeedConnector):
    """Blend recipes live in the reference document rather than the scenario."""

    feed = "blend_bom"
    full_refresh = True

    def __init__(self, reference_path: str):
        self.reference_path = reference_path
        self.source = "workbook-seed:" + os.path.basename(reference_path)

    def fetch(self) -> List[FeedRow]:
        with open(self.reference_path, encoding="utf-8") as f:
            ref = json.load(f)
        products = ref.get("products", {})
        rows = []
        for entry in ref.get("blend_bom", []):
            comp = entry["component"]
            name = products.get(comp, {}).get("name")
            rows.append(FeedRow(k1=entry["blend"], k2=comp,
                                value=float(entry["fraction"]), label=name))
        return rows


def default_connectors(seed_dir: str) -> List[FeedConnector]:
    scenario_path = os.path.join(seed_dir, "scenario.json")
    reference_path = os.path.join(seed_dir, "reference.json")
    products: Dict[str, Any] = {}
    if os.path.exists(reference_path):
        with open(reference_path, encoding="utf-8") as f:
            products = json.load(f).get("products", {})
    feeds = ["inventory", "open_orders", "sales_forecast",
             "blend_component_demand", "base_oil_transfer"]
    conns: List[FeedConnector] = [
        WorkbookSeedConnector(f, scenario_path, products) for f in feeds]
    conns.append(ReferenceBlendBomConnector(reference_path))
    return conns
