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

from .. import feedsheets


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


class UploadedSheetConnector(FeedConnector):
    """One feed, read from a standalone workbook a planner uploaded.

    Same contract as the seed connector, different source: a small export the
    planner produced today rather than a slice of the 6 MB planning workbook.
    Because it stages the same keys, an override made against workbook data
    survives the switch and is flagged stale if the uploaded number differs.
    """

    full_refresh = True

    def __init__(self, feed: str, path: str, kind: str,
                 products: Dict[str, Any] = None):
        self.feed = feed
        self.path = path
        self.kind = kind
        self.source = "upload:" + os.path.basename(path)
        self._products = products or {}

    def _name(self, code: str) -> Optional[str]:
        p = self._products.get(code)
        return p.get("name") if isinstance(p, dict) else None

    def fetch(self) -> List[FeedRow]:
        data = feedsheets.read(self.kind, self.path)[self.feed]
        rows: List[FeedRow] = []
        for code, by_key in data.values.items():
            # The reference name wins so the same product reads the same way
            # whichever connector supplied it; the sheet's own name is the
            # fallback for a code the reference has never seen.
            label = self._name(code) or data.labels.get(code)
            for k2, value in by_key.items():
                rows.append(FeedRow(k1=code, k2=k2, value=float(value),
                                    label=label))
        return rows


#: Where uploaded sheets are kept, as a sibling of the JSON seed. Overridable so
#: a test - or a second planner on the same box - can point somewhere else.
UPLOAD_DIR_ENV = "INVPLANNER_UPLOAD_DIR"


def upload_dir(seed_dir: str) -> str:
    return (os.environ.get(UPLOAD_DIR_ENV)
            or os.path.join(os.path.dirname(os.path.abspath(seed_dir)), "uploads"))


def upload_path(seed_dir: str, kind: str) -> str:
    return os.path.join(upload_dir(seed_dir), kind + ".xlsx")


def uploaded_kinds(seed_dir: str) -> Dict[str, str]:
    """The sheet kinds currently uploaded, mapped to their file path."""
    return dict((kind, upload_path(seed_dir, kind))
                for kind in feedsheets.KINDS
                if os.path.exists(upload_path(seed_dir, kind)))


def default_connectors(seed_dir: str) -> List[FeedConnector]:
    """A connector per feed, preferring an uploaded sheet over the seed.

    Uploads are per feed, not all-or-nothing: dropping in an open-orders extract
    leaves inventory and the forecast reading from the workbook seed until their
    own files arrive.
    """
    scenario_path = os.path.join(seed_dir, "scenario.json")
    reference_path = os.path.join(seed_dir, "reference.json")
    products: Dict[str, Any] = {}
    if os.path.exists(reference_path):
        with open(reference_path, encoding="utf-8") as f:
            products = json.load(f).get("products", {})
    uploaded = uploaded_kinds(seed_dir)
    feeds = ["inventory", "open_orders", "sales_forecast",
             "blend_component_demand", "base_oil_transfer"]
    conns: List[FeedConnector] = []
    for feed in feeds:
        kind = feedsheets.FEED_KIND.get(feed)
        if kind in uploaded:
            conns.append(UploadedSheetConnector(feed, uploaded[kind], kind,
                                                products))
        else:
            conns.append(WorkbookSeedConnector(feed, scenario_path, products))
    conns.append(ReferenceBlendBomConnector(reference_path))
    return conns
