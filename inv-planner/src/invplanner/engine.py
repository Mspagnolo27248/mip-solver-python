"""Simulation engine: charge schedule -> production -> daily inventory balance.

Implements BUSINESS-LOGIC.md sections 3.1-3.3. Pure and deterministic: given a
reference dataset and a scenario it returns the full projection. No Excel, no
database, no solver - so it can serve the web app's what-if calculations, back
the parity harness, and score candidate schedules from the optimizer.

Two lookup modes matter for fidelity:

  strict_workbook=True   reproduce the workbook exactly, including its
                         first-match VLOOKUP semantics (a product charged at two
                         units only counts the first line)
  strict_workbook=False  sum every matching charge line, which is what the
                         physics actually implies

The difference between the two is itself a finding; `Simulation.lookup_conflicts`
lists every product where the two modes disagree.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

GAL_PER_BBL = 42.0
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

BALANCE_ROWS = ["Begin Inventory", "Production In", "Receipts", "Sales", "Blends",
                "Forecast", "Net Charge Available", "Production Out",
                "Out to Diesel", "Downgrade", "End Inventory", "Tank Capacity",
                "Excess Capacity"]

#: Rows that only exist in physical mode (see Engine(physical=True)).
PHYSICAL_ROWS = ["Lost Sales", "Feed Shortfall", "Downgrade Required",
                 "Unconstrained End"]


def _d(s: str) -> dt.date:
    y, m, day = (int(x) for x in s.split("-"))
    return dt.date(y, m, day)


def month_key(d: dt.date) -> str:
    return MONTHS[d.month - 1]


class Reference:
    def __init__(self, data: Dict[str, Any]):
        self.raw = data
        self.products: Dict[str, Dict[str, str]] = data["products"]
        self.tank_capacity: Dict[str, float] = data["tank_capacity"]
        self.inventory_targets: Dict[str, Any] = data["inventory_targets"]
        self.crude_yields: Dict[str, Dict[str, float]] = data["crude_yields"]
        self.crude_loss_fraction: float = data.get("crude_loss_fraction", 0.0)
        self.run_rates: Dict[str, Dict[str, float]] = data["run_rates"]
        self.other_rates: Dict[str, Dict[str, float]] = data["other_rates"]
        self.crude_plan: Dict[str, Dict[str, float]] = data["crude_plan"]
        self.blend_bom: List[Dict[str, Any]] = data["blend_bom"]
        self.charge_lines: List[Dict[str, Any]] = data["charge_lines"]
        self.yield_rules: List[Dict[str, Any]] = data["yield_rules"]
        self.blocks: List[Dict[str, Any]] = data["blocks"]
        self.line_by_key = {l["key"]: l for l in self.charge_lines}

    @classmethod
    def load(cls, path: str) -> "Reference":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))


class Scenario:
    def __init__(self, data: Dict[str, Any]):
        self.raw = data
        self.name: str = data["meta"].get("name", "scenario")
        self.as_of: dt.date = _d(data["meta"]["as_of"])
        self.dates: List[dt.date] = [_d(s) for s in data["dates"]]
        self.opening_inventory: Dict[str, float] = data["opening_inventory"]
        # Crude is held at site level in barrels, separately from product gallons.
        self.crude_opening_bbl: float = float(data.get("crude_opening_bbl") or 0.0)
        self.open_orders: Dict[str, Dict[str, float]] = data["open_orders"]
        self.sales_forecast: Dict[str, Dict[str, float]] = data["sales_forecast"]
        self.blend_component_demand: Dict[str, Dict[str, float]] = \
            data["blend_component_demand"]
        self.base_oil_transfer: Dict[str, Dict[str, float]] = \
            data.get("base_oil_transfer", {})
        self.base_oil_transfer_columns: Dict[str, Dict[str, float]] = \
            data.get("base_oil_transfer_columns", {})
        cs = data["charge_schedule"]
        self.crude_bbl: Dict[str, float] = cs["crude_bbl_per_day"]
        self.crude_mode: Dict[str, str] = cs["crude_mode"]
        self.charge_lines: Dict[str, Dict[str, float]] = cs["lines"]
        self.manual_block_rows: Dict[str, Dict[str, Dict[str, float]]] = \
            data.get("manual_block_rows", {})
        self.capacity_series: Dict[str, Dict[str, float]] = \
            data.get("capacity_series", {})

    @classmethod
    def load(cls, path: str) -> "Scenario":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def charge_bbl(self, line_key: str, d: dt.date) -> float:
        return self.charge_lines.get(line_key, {}).get(d.isoformat(), 0.0)


class Simulation:
    """Result container: production grid + per-block daily balances."""

    def __init__(self) -> None:
        # production_gal[code][date] -> gallons produced
        self.production_gal: Dict[str, Dict[dt.date, float]] = defaultdict(dict)
        # production_bbl_by_rule[po_row][date] -> barrels (mirrors the workbook grid)
        self.production_bbl_by_row: Dict[int, Dict[dt.date, float]] = defaultdict(dict)
        # balances[block_id][label][date] -> value
        self.balances: Dict[str, Dict[str, Dict[dt.date, float]]] = {}
        self.lookup_conflicts: List[str] = []
        self.notes: List[str] = []

    def prod(self, code: Optional[str], d: dt.date) -> float:
        if not code:
            return 0.0
        return self.production_gal.get(code, {}).get(d, 0.0)


class Engine:
    """Simulate a schedule.

    Two modes:

    `physical=False` (default) reproduces the workbook exactly, letting inventory
    run negative or above capacity. That is what the parity harness checks.

    `physical=True` enforces what the tanks actually do - inventory can neither go
    below zero nor above capacity - and records what it cost to stay inside those
    limits:

      Lost Sales         demand that could not be served (margin lost)
      Feed Shortfall     charge a unit could not be given (the schedule was
                         impossible, not merely expensive)
      Downgrade Required volume that had to be pushed to a low-netback outlet
                         (#6 oil, cat cracker) to keep the tank from overfilling

    Those three series are the cost-relevant output: they turn the workbook's
    silent negative and over-capacity numbers into the decisions a planner would
    actually have been forced to make, and they are what the optimizer minimises.
    """

    def __init__(self, ref: Reference, scn: Scenario, strict_workbook: bool = True,
                 physical: bool = False):
        self.ref = ref
        self.scn = scn
        self.strict = strict_workbook
        self.physical = physical

        # Charge lines that participate in the `Charge` named-range lookup used by
        # the stream sheets' "Production Out" row, in sheet order.
        self._lookup_lines = [l for l in ref.charge_lines
                              if l.get("in_charge_lookup_range")]
        self._to_diesel_lines = [l for l in ref.charge_lines
                                 if l["unit"] == "TRANSFER_DIESEL"]
        # Charge lines a block already subtracts through a row of *its own*, so
        # `charge_total` must not subtract them a second time. Only loose mode is
        # exposed: the strict first-match stops at the process line and never
        # reaches these, which is why the double count could sit here unseen.
        #
        # Per block, and that matters. An earlier version kept one global set,
        # which meant a line accounted for in *someone else's* row vanished from
        # every block. The retired 9202 block books the Sonneborn lift as its own
        # Sales, reading the charge row directly - so 9118, whose Production Out
        # is that same line, stopped being drawn down at all and appeared to
        # overflow by 707,233 gal. The workbook is full of blocks reaching into
        # each other's rows; the guard has to be as local as the row it protects.
        by_row = {l["row"]: l["key"] for l in ref.charge_lines}
        self._accounted: Dict[str, Set[str]] = {}
        for block in ref.blocks:
            claimed = set()
            for label, meta in block["rows"].items():
                if label == block.get("prod_out_label", "Production Out"):
                    continue
                for region in meta.get("regions") or []:
                    for term in region["spec"]["terms"]:
                        kind = term.get("kind")
                        if kind == "charge_row" and term["row"] in by_row:
                            claimed.add(by_row[term["row"]])
                        elif kind == "to_diesel":
                            # the block's own Out to Diesel row carries these
                            claimed.update(
                                l["key"] for l in self._to_diesel_lines
                                if l["code"] == term.get("code"))
            self._accounted[block["id"]] = claimed
        self._lines_by_row = {l["row"]: l for l in ref.charge_lines}
        self._block_by_id = {b["id"]: b for b in ref.blocks}
        self._opening_cache: Dict[str, float] = {}
        self._opening_visiting: Set[str] = set()
        self._computing: Set[str] = set()
        # (sheet, row) -> (block id, row label), so a pooled block can resolve the
        # child rows it sums.
        self._row_owner: Dict[Tuple[str, int], Tuple[str, str]] = {}
        for b in ref.blocks:
            for label, meta in b["rows"].items():
                self._row_owner[(b["sheet"], meta["row"])] = (b["id"], label)

    # ------------------------------------------------------------- charge access
    def charge_first_match(self, code: Optional[str], d: dt.date) -> float:
        """Barrels charged of `code`, workbook semantics (first matching row only)."""
        if not code:
            return 0.0
        for line in self._lookup_lines:
            if line["code"] == code:
                return self.scn.charge_bbl(line["key"], d)
        return 0.0

    def charge_total(self, code: Optional[str], d: dt.date,
                     block_id: Optional[str] = None) -> float:
        """Barrels charged of `code` across every unit (physically correct).

        Lines a block already subtracts through a row of its own are left out -
        the `Out to Diesel` row, and the one block that names a transfer row
        explicitly (Kensol 30 to finished diesel). Counting them here as well
        would take the same barrels out twice.
        """
        if not code:
            return 0.0
        return sum(self.scn.charge_bbl(l["key"], d)
                   for l in self._lookup_lines
                   if l["code"] == code
                   and l["key"] not in self._accounted.get(block_id, ()))

    def charge_out(self, code: Optional[str], d: dt.date,
                   block_id: Optional[str] = None) -> float:
        return (self.charge_first_match(code, d) if self.strict
                else self.charge_total(code, d, block_id))

    def to_diesel_bbl(self, code: Optional[str], d: dt.date) -> float:
        if not code:
            return 0.0
        for line in self._to_diesel_lines:
            if line["code"] == code:
                return self.scn.charge_bbl(line["key"], d)
        return 0.0

    # ------------------------------------------------------------- production
    def run_production(self, sim: Simulation) -> None:
        """BUSINESS-LOGIC 3.2 - charges x yields -> gallons produced per product."""
        ref, scn = self.ref, self.scn
        by_row = sim.production_bbl_by_row

        for d in scn.dates:
            iso = d.isoformat()
            mk = month_key(d)
            crude = scn.crude_bbl.get(iso, 0.0)
            mode = scn.crude_mode.get(iso)

            for rule in ref.yield_rules:
                kind = rule["kind"]
                row = rule["po_row"]
                bbl = 0.0

                if kind == "crude":
                    gate = rule.get("mode")
                    if gate and mode != gate:
                        bbl = 0.0
                    else:
                        bbl = crude * rule["monthly_yield"].get(mk, 0.0)

                elif kind == "crude_loss":
                    bbl = crude * rule.get("fraction", 0.0)

                elif kind == "unit":
                    bbl = scn.charge_bbl(rule["charge_line"], d) * rule["yield"]

                elif kind == "diesel_yield_back":
                    # Source rows are already-computed outputs (charge x yield),
                    # so this evaluates to charge x (1 - yield).
                    total = 0.0
                    for term in rule["terms"]:
                        y = term["yield"]
                        if y <= 0:
                            continue
                        produced = by_row.get(term["source_po_row"], {}).get(d, 0.0)
                        total += produced * (1.0 / y - 1.0)
                    bbl = total

                elif kind == "transfer":
                    total = sum(scn.charge_bbl(k, d) for k in rule["charge_lines"])
                    bbl = total / rule.get("divisor", 1.0)

                if bbl:
                    by_row[row][d] = by_row[row].get(d, 0.0) + bbl
                out = rule.get("out")
                if out and bbl:
                    cur = sim.production_gal[out]
                    cur[d] = cur.get(d, 0.0) + bbl * GAL_PER_BBL

    # ---------------------------------------------------------------- demand
    def sales(self, code: Optional[str], d: dt.date) -> float:
        if not code:
            return 0.0
        return self.scn.open_orders.get(code, {}).get(d.isoformat(), 0.0)

    def forecast_rate(self, code: Optional[str], d: dt.date) -> float:
        if not code:
            return 0.0
        return self.scn.sales_forecast.get(code, {}).get(month_key(d), 0.0)

    def blend_demand(self, code: Optional[str], d: dt.date, include_bot: bool) -> float:
        if not code:
            return 0.0
        mk = month_key(d)
        v = self.scn.blend_component_demand.get(code, {}).get(mk, 0.0)
        if include_bot:
            v += self.scn.base_oil_transfer.get(code, {}).get(mk, 0.0)
        return v

    def capacity(self, block: Dict[str, Any], d: Optional[dt.date] = None) -> float:
        series = self.scn.capacity_series.get(block["id"])
        if series and d is not None:
            v = series.get(d.isoformat())
            if v is not None:
                return v
        spec = block.get("capacity") or {}
        if spec.get("kind") == "constant":
            return float(spec.get("value", 0.0))
        return sum(self.ref.tank_capacity.get(c, 0.0)
                   for c in spec.get("codes", []))

    def opening(self, block: Dict[str, Any]) -> float:
        """Opening inventory, resolving pooled blocks recursively.

        Three shapes exist in the workbook: a sum of one or two product codes, a
        literal, and (for the finished-diesel pool) the sum of other blocks'
        openings.
        """
        bid = block["id"]
        if bid in self._opening_cache:
            return self._opening_cache[bid]
        if bid in self._opening_visiting:
            raise ValueError("circular opening-inventory reference at {}".format(bid))
        self._opening_visiting.add(bid)
        try:
            spec = block.get("opening") or {}
            kind = spec.get("kind")
            if kind == "constant":
                val = float(spec.get("value", 0.0))
            elif kind == "sum_of_block_openings":
                val = sum(self.opening(self._block_by_id[b])
                          for b in spec.get("block_ids", [])
                          if b in self._block_by_id)
            else:
                val = sum(self.scn.opening_inventory.get(c, 0.0)
                          for c in spec.get("codes", []))
            for adj in spec.get("adjustments", []):
                other = self._block_by_id.get(adj.get("block_id"))
                if other:
                    val += adj["sign"] * self.opening(other)
        finally:
            self._opening_visiting.discard(bid)
        self._opening_cache[bid] = val
        return val

    def manual(self, block_id: str, label: str, d: dt.date) -> float:
        return (self.scn.manual_block_rows.get(block_id, {})
                .get(label, {}).get(d.isoformat(), 0.0))

    # ---------------------------------------------------------------- balance
    # Rows whose value comes from feeds/schedule rather than from arithmetic on
    # other rows of the same block.
    SOURCED = ["Production In", "Sales", "Blends", "Forecast", "Production Out",
               "Out to Diesel", "Downgrade"]

    def eval_terms(self, sim: Simulation, block: Dict[str, Any], label: str,
                   d: dt.date, sales_so_far: float) -> float:
        """Evaluate one balance row's term list for one day."""
        meta = self._row_meta(block, label)
        if meta is None:
            return 0.0
        spec = self._spec_for(meta, d)
        if not spec:
            return 0.0

        wd = spec.get("weekday_override")
        if wd and d.isoweekday() > wd["threshold"]:
            return wd["value"]

        total = 0.0
        for term in spec.get("terms", []):
            kind = term.get("kind")
            code = term.get("code")

            if kind == "production_grid":
                total += sim.prod(code, d)
            elif kind == "open_orders":
                total += self.sales(code, d)
            elif kind == "blend_demand":
                total += self.scn.blend_component_demand.get(
                    code, {}).get(month_key(d), 0.0) if code else 0.0
            elif kind == "base_oil_transfer_monthly":
                total += self.scn.base_oil_transfer.get(
                    code, {}).get(month_key(d), 0.0) if code else 0.0
            elif kind == "base_oil_transfer_column":
                total += self.scn.base_oil_transfer_columns.get(
                    code, {}).get(str(term.get("column")), 0.0) if code else 0.0
            elif kind == "forecast_net":
                rate = self.forecast_rate(code, d)
                total += 0.0 if sales_so_far > rate else rate - sales_so_far
            elif kind == "charge_first_match":
                total += self.charge_out(code, d, block["id"]) * GAL_PER_BBL
            elif kind == "to_diesel":
                total += self.to_diesel_bbl(code, d) * GAL_PER_BBL
            elif kind == "charge_row":
                line = self._lines_by_row.get(term["row"])
                if line:
                    total += self.scn.charge_bbl(line["key"], d) * GAL_PER_BBL
            elif kind == "pool_sum":
                for row in term["rows"]:
                    child = self._row_owner.get((term["sheet"], row))
                    if child:
                        cid, clabel = child
                        total += self._value(sim, cid, clabel, d)
            elif kind == "manual":
                total += self.manual(block["id"], label, d)

        return total * spec.get("factor", 1.0)

    @staticmethod
    def _spec_for(meta: Dict[str, Any], d: dt.date) -> Optional[Dict[str, Any]]:
        """Pick the formula region covering this date.

        Rows change shape partway through the horizon in several blocks, so the
        applicable terms depend on the day being evaluated.
        """
        regions = meta.get("regions")
        if not regions:
            return None
        iso = d.isoformat()
        chosen = regions[0]
        for reg in regions:
            if reg["from"] <= iso:
                chosen = reg
            else:
                break
        return chosen.get("spec")

    @staticmethod
    def _row_meta(block: Dict[str, Any], label: str) -> Optional[Dict[str, Any]]:
        rows = block["rows"]
        if label in rows:
            return rows[label]
        for k, v in rows.items():
            if k.startswith(label):
                return v
        return None

    def _value(self, sim: Simulation, block_id: str, label: str, d: dt.date) -> float:
        """Read another block's already-computed row (pooled blocks depend on
        their children, which are ordered earlier on the sheet)."""
        bal = sim.balances.get(block_id)
        if not bal:
            block = self._block_by_id.get(block_id)
            if block:
                self._compute_block(sim, block)
            bal = sim.balances.get(block_id, {})
        return bal.get(self._canon(label), {}).get(d, 0.0)

    @staticmethod
    def _canon(label: str) -> str:
        for base in ("Production Out", "End Inventory", "Production In"):
            if label.startswith(base):
                return base
        return label

    def _compute_block(self, sim: Simulation, block: Dict[str, Any]) -> None:
        bid = block["id"]
        if bid in sim.balances or bid in self._computing:
            return
        self._computing.add(bid)
        try:
            as_of = self.scn.as_of
            opening = self.opening(block)
            labels = BALANCE_ROWS + (PHYSICAL_ROWS if self.physical else [])
            out: Dict[str, Dict[dt.date, float]] = {lbl: {} for lbl in labels}
            prev_end: Optional[float] = None

            for d in self.scn.dates:
                future = d >= as_of
                capacity = self.capacity(block, d)
                begin = opening if d <= as_of else (prev_end or 0.0)

                vals: Dict[str, float] = {}
                for label in self.SOURCED:
                    raw = self.eval_terms(sim, block, label, d,
                                          vals.get("Sales", 0.0))
                    # Feed-driven rows are suppressed before the as-of date; the
                    # schedule-driven ones are not (the workbook gates only these).
                    if not future and label in ("Production In", "Sales", "Blends",
                                                "Forecast"):
                        raw = 0.0
                    vals[label] = raw

                net = (begin + vals["Production In"] - vals["Sales"]
                       - vals["Forecast"] - vals["Blends"])
                draw = (vals["Production Out"] + vals["Out to Diesel"]
                        + vals["Downgrade"])
                end = net - draw

                lost = short = required_dg = 0.0
                if self.physical:
                    unconstrained = end
                    # Demand is served before unit charge: a customer order takes
                    # priority over feeding the next unit. Whatever demand the
                    # tank could not cover is a lost sale.
                    if net < 0:
                        lost = -net
                        net = 0.0
                    # What is left has to cover the charge. If it cannot, the
                    # schedule asked a unit to run on feed that does not exist.
                    end = net - draw
                    if end < 0:
                        short = -end
                        end = 0.0
                    # A physical tank cannot overfill; the excess goes to a
                    # low-netback outlet.
                    if capacity > 0 and end > capacity:
                        required_dg = end - capacity
                        end = capacity
                    out["Lost Sales"][d] = lost
                    out["Feed Shortfall"][d] = short
                    out["Downgrade Required"][d] = required_dg
                    out["Unconstrained End"][d] = unconstrained

                excess = capacity if (capacity - end) > capacity else capacity - end

                out["Begin Inventory"][d] = begin
                for label in self.SOURCED:
                    out[label][d] = vals[label]
                out["Net Charge Available"][d] = net
                out["End Inventory"][d] = end
                out["Tank Capacity"][d] = capacity
                out["Excess Capacity"][d] = excess
                prev_end = end

            sim.balances[bid] = out
        finally:
            self._computing.discard(bid)

    def run_balances(self, sim: Simulation) -> None:
        """BUSINESS-LOGIC 3.3 - the daily balance, per product block."""
        for block in self.ref.blocks:
            self._compute_block(sim, block)

    # ------------------------------------------------------------------ crude
    def run_crude(self, sim: Simulation) -> None:
        """The CRUDE sheet's balance, which is kept in BARRELS, not gallons."""
        ref, scn = self.ref, self.scn
        receipts_plan = ref.crude_plan.get("receipts_bbl_per_day", {})
        cap_bbl = 9_600_000.0 / GAL_PER_BBL
        out: Dict[str, Dict[dt.date, float]] = {lbl: {} for lbl in BALANCE_ROWS}
        begin = scn.crude_opening_bbl
        for d in scn.dates:
            rcpt = receipts_plan.get(month_key(d), 0.0)
            prod_out = scn.crude_bbl.get(d.isoformat(), 0.0)
            net = begin + rcpt
            end = net - prod_out
            out["Begin Inventory"][d] = begin
            out["Receipts"][d] = rcpt
            out["Net Charge Available"][d] = net
            out["Production Out"][d] = prod_out
            out["End Inventory"][d] = end
            out["Tank Capacity"][d] = cap_bbl
            out["Excess Capacity"][d] = cap_bbl if (cap_bbl - end) > cap_bbl else cap_bbl - end
            begin = end
        sim.balances["CRUDE:14"] = out

    # -------------------------------------------------------------------- run
    def detect_lookup_conflicts(self) -> List[str]:
        """Products charged at more than one unit, where the workbook's first-match
        VLOOKUP silently ignores the later line."""
        seen: Dict[str, List[str]] = defaultdict(list)
        for l in self._lookup_lines:
            seen[l["code"]].append("{} row {}".format(l["unit"], l["row"]))
        return ["{} charged at {}".format(code, " and ".join(where))
                for code, where in sorted(seen.items()) if len(where) > 1]

    def run(self) -> Simulation:
        sim = Simulation()
        self.run_production(sim)
        self.run_balances(sim)
        self.run_crude(sim)
        sim.lookup_conflicts = self.detect_lookup_conflicts()
        return sim


def simulate(ref: Reference, scn: Scenario, strict_workbook: bool = True,
             physical: bool = False) -> Simulation:
    return Engine(ref, scn, strict_workbook=strict_workbook,
                  physical=physical).run()
