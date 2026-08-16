# Inventory Planning Workbook — Business Logic Decomposition

Source: `Inventory Planning - FY26 12 MONTH.xlsm` (37 sheets, ~350,000 formulas, 1 trivial VBA macro).
Purpose of this document: describe everything the workbook does in implementation-neutral terms so it can be rebuilt as a web app.

---

## 1. What this system is

A **production planning and inventory projection model for a lube refinery** (American Refining Group, Bradford PA). Its stated objective (from the hidden `Overview` sheet):

> "Allow production planners to model how the decisions at each unit will impact inventory and tank capacity. A 14-day inventory forecast can then be provided to sales with notes on what items need to be added to allocation and what items can be on a push list or downgraded."

The planner enters a **unit-by-unit charge schedule** (which feedstock each process unit runs each day, ~12 months forward). The model then simulates, **per product per day**:

```
production (from charges × yields) → daily inventory balance → tank capacity check
```

and produces dashboards: projected inventory vs. control limits, excess tank capacity, available-to-promise, downgrade requirements, and monthly production outlook.

Everything is deterministic — there is no optimization or randomness. It is a daily-timestep simulation driven by reference tables and manual planning inputs.

### Units of measure
- Process-unit charges are in **barrels/day (BBL)**; inventory and sales are in **gallons**; conversion is `1 BBL = 42 gal`.
- The planning year is the **fiscal year Oct–Sep** ("FY26"); most monthly reference tables have 12 columns Oct..Sep.

### Process units modeled
| Unit | Role |
|---|---|
| Crude Unit | Distills crude into ~9 side streams (naphtha, K-solvents, waxy neutrals, cylinder stock, off-gas, loss). Runs in one of two modes per day: **R** (regular → makes 9117 Waxy MN) or **L** (low-volatility → makes 9118 Sonneborn MN) |
| MEK Unit | Solvent dewaxing: waxy stock → dewaxed oil + slack wax |
| Hydrotreater | Hydrotreats solvents/distillates: UNHT product → finished (H) product + diesel yield-back |
| Extraction (Furfural) | Dewaxed stock → raffinate (base oil) + extract |
| ROSE Unit | Resid oil supercritical extraction: 4313 → 0846 + resins + 0834; 4555 → light/heavy resin split |
| Unifiner/Platformer | Naphtha → platformate, light straight run, isomerate, Kensol 17/30 |
| Tolling | External tolling of 9202 (HT Waxy MN) |

---

## 2. Data inputs

### 2.1 Automated feed — daily tank inventory (`Inventory` sheet)
- Pulled (via Excel external links) from `Daily Inventory.xlsm` on SharePoint (site *Supplychain37*), which itself comes from a SQL job that runs "Today Between 2am–4am".
- Grain: **product code × tank → gallons** (~840 rows), plus tank-heel adjustments.
- Aggregated in-sheet: `Net Inv (product) = Σ gallons over tanks` (SUMIF), giving the *as-of* starting inventory used by every projection sheet. A crude summary by site (Bradford / Sandyville / in-transit / rail) is also built here.
- Named range `tInv` (`Inventory!P:R` = code → net inventory) is the lookup the balance sheets use.
- **Web-app equivalent:** a nightly inventory snapshot import (API/file drop), stored per product/tank/date.

### 2.2 Open orders (`Open Orders` sheet)
- Pasted/linked ERP export: rows = product (code + description), columns = calendar day (~60 days), values = **negative gallons** committed to customers.
- Special **order-splitting rules** at the top of the sheet re-allocate demand booked against a *sold* product to the *component* products that will actually ship, e.g.:
  - Orders for `4303 KENDEX 0070` count 65% against `4315 0150H` and 35% against `4329 0060HT`.
  - Orders for `4618 KENDEX 0898` count 63% against `4555` and 37% against `4554`.
- **Web-app equivalent:** open-order feed + a configurable "demand transfer" rule table.

### 2.3 Sales forecast (`Sales Forecast` sheet)
- Manual planning board. The operative table (cols T:AF): **product code × month → forecast in gallons per day** (a daily rate, not a monthly total).
- A second computed table (cols AH:AT): **daily blend-component demand** per component code × month (see 2.4).
- The top-of-sheet gallons-by-product-class table is annotated "not feeding any other output — reference only".

### 2.4 Blend bill-of-materials (`BLENDS` sheet)
- Table 1 (named range `Bld`): blended finished product × month → daily sales rate (pulled from Sales Forecast).
- Table 2 (rows 62+): **BOM**: `(blend product, component product, fraction)` — e.g. `4590 = 70% 4129 Kensol 61H + 30% 4554 Kendex 0834`.
- Daily component demand = Σ over blends: `blend daily rate × component fraction`; aggregated by component code back into Sales Forecast AH:AT. This becomes the **"Blends" demand line** in every product balance.

### 2.5 Reference / master data
| Sheet | Content |
|---|---|
| `Product list ` (1,455 rows) | Product code (4-digit, text, leading zeros matter) → description → category. Includes pseudo-code `DDDD` = Finished Diesel pool |
| `Tank Capacity` | Two tables: tank-level (product, tank, capacity) and product-level (`Tank_Capacity` named range: code → total shell capacity in gallons) |
| `UNIT RUN RATES` | Unit × charge-product × month → **BBL/day run rate** (planner-maintained; includes a Y/N and R/L helper flags). A change-vs-previous-week diff table sits alongside |
| `CRUDE YIELDS` | Crude-unit **monthly yield vectors**: product × month → fraction of crude charge (K-48 8.1%, Naphtha 35.7%, Waxy MN 16.4–17%, Cylinder Stock 10.7%, etc.). Includes overall recovery factor (0.99 → 1% loss) |
| `CRUDE RATES` | Crude charge BBL/day by month + crude **receipts** BBL/day by month (per supplier "BB:") |
| `INV TARGETS` | Per product: **LCL / UCL** inventory control limits (gallons); expanded into daily-constant series for charting bands |
| Fixed unit yields | Stored in column A of `Production - Out` next to each unit section (see 3.2) |

### 2.6 Manual planning inputs (the "decision variables")
All on `Charge Schedule` (columns = ~365 daily dates):
1. **Crude unit**: daily charge BBL (row 66) and daily mode flag `R`/`L` (row 18).
2. **Per unit, per charge product, per day**: the plan. For MEK/Hydro/Extract the planner enters **BBL directly** in the quantity block (rows 63–107); the 1/blank flag block (rows 17–59) is derived (`=IF(bbl>1,1,"")`) and is what the Overview instructions describe. For Tolling/Platformer the flag is entered and `BBL = flag × UNIT RUN RATES rate for that month`.
3. **Downgrade/transfer entries** (BBL/day): solvents → diesel charge (rows 110–118, mirrored by the one VBA macro `DowngradeSoventsToDiesel` which copies gal/42 into these rows), K-30 → finished diesel, resins/waxes → #6 oil, slack waxes → cat cracker, Sonneborn sales, railcar receipts (Prima600, Calpar, Sonneborn 61H).

---

## 3. Calculation engine

### 3.1 Charge schedule → daily charges
For each `(unit, charge product, date)`: charge in BBL as entered, or `flag × runRate(unit, product, month(date))`. Named ranges partition the BBL grid by unit: `Charge_Gal_MEK`, `Charge_Gal_Hydro`, `Charge_Gal_Exc`, `Charge_Gal_Rose`, `Charge_Gal_Plat`, `Charge_Gal_tol`, `Charge_Gal_ToDiesel`.

### 3.2 Production out (`Production - Out` sheet)
Rows 15–95 compute **BBL produced of each output product each day**; rows 98+ aggregate to **gallons by product code** (`SUMIF × 42`) — this grid (product × date, gallons) is what all balance sheets read.

Rules per unit:

- **Crude Unit** — for each side-stream product `p`:
  `out(p, d) = crudeCharge(d) × crudeYield(p, month(d))`
  with mode exclusivity: 9117 only when mode=R, 9118 only when mode=L. `Loss(d) = charge × lossFraction`.
- **MEK** — per charge product with fixed split (col A):
  `dewaxedOil = charge × yld` and `slackWax = charge × (1 − yld)`. Yields: 9116→81.5% (→9720 + 4449 LN slack), 9117→82.5% (→9302 + 4451), 9202→83% (→9202 HT path, 17% → 4325), 9119→81% (→9303 + 4454), 4317→84% (→9305 + 4459 petrolatum).
- **Hydrotreater** — `finished = charge × yld` (diesel 98%, K-48/50 85%, K-61 82%, 0150/9720 92%, 0847 98%); plus
  `dieselYieldBack(d) = Σ charge_i × (1/yld_i − 1)` → production **into** 9713 diesel charge (the hydrotreater "makes back" the volume lost from solvents as diesel).
- **Extraction** — extract yields ~2.4–4.5% (→ 4577 MNE, 4579, 4586 BSE), raffinate ~92–93% (→ 9704 0150 UNHT, 4309, 4318 0847, 9705).
- **ROSE** — 4313 charge → 68% 4317 (0846) + 15% 4555 (light resins) + 17% 4554 (heavy resins); 4555 charge → 55% 4317 + 45% 8201 #6 oil.
- **Platformer** — 9103 naphtha → 17.1%×94.7% LSR (9505) + 58.2%×94.7% platformate + 21% other; 9505 → 96.8% isomerate; 4107 → 84%.
- **Transfers** — downgrade rows pass entered BBL straight through as production **into** the destination pool (9713 diesel charge, 8170 finished diesel, 8201 #6 oil, 8221 cat cracker feed, 4129 from Sonneborn).

### 3.3 The daily product balance (core algorithm)
Six "stream" sheets — `Solvents`, `LLN`, `Cstock`, `MN`, `HN`, `Napthas` — plus `CRUDE`, all use the same block template. Each block covers one product pair: the **charge-stock code** (e.g. 9711 KENSOL 48UNHT) and its **sold/finished code** (e.g. 4105) — inventory of the pair is pooled. Rows per block, for every date `d` in the 12-month horizon:

```
BeginInv(d)      = if d ≤ asOfDate: actualNetInv(chargeCode) + actualNetInv(soldCode)   [from tInv]
                   else:            EndInv(d−1)

ProductionIn(d)  = productionOutGal(code, d)            [0 for d < asOfDate]   (§3.2)

Sales(d)         = openOrders(soldCode, d)              [0 for d < asOfDate; feed is negative,
                                                         negated on the sheet]

Blends(d)        = dailyBlendComponentDemand(soldCode, month(d))               (§2.4)

Forecast(d)      = max(0, dailyForecastRate(soldCode, month(d)) − Sales(d))
                   -- forecast is netted against booked orders to avoid double-counting

NetChargeAvail(d)= BeginInv(d) + ProductionIn(d) − Sales(d) − Forecast(d) − Blends(d)

ProductionOut(d) = unitCharge(chargeCode, d) × 42       -- consumed as feed to the next unit

OutToDiesel(d)   = downgradeTransfer(chargeCode, d) × 42   (or "Downgrade" row for diesel pool)

EndInv(d)        = NetChargeAvail(d) − ProductionOut(d) − OutToDiesel(d)

TankCapacity     = shellCapacity(soldCode) + shellCapacity(chargeCode)   [constant]

ExcessCapacity(d)= min(TankCapacity, TankCapacity − EndInv(d))
```

`CRUDE` is the same pattern with `Receipts(d)` (from the monthly crude receipt plan) instead of ProductionIn, and crude-unit charge as ProductionOut.

Each stream sheet also carries: month-number helper row, monthly SUMIFS roll-ups at the right edge (cols ~NK+), an **Available to Promise** block, and a lookup-key column `code-description|rowLabel` used by the dashboards to fetch any row via INDIRECT.

### 3.4 Time conventions
- History freeze: for dates before the as-of date, production/sales/forecast are zeroed and BeginInv snaps to actuals — the model only projects forward.
- Monthly-to-daily: forecast and blend demand are maintained as **gal/day rates per month**, applied uniformly to every day of that month.
- The `NAV` hidden sheet + month-number rows implement month-jump navigation in the giant date grids (pure UI, no business logic).

---

## 4. Outputs / views

| Sheet | What it shows |
|---|---|
| `Inventory Detail` | Cross-stream dashboard: for ~80 products, pulls the **Excess Capacity** (or End Inventory) row from its stream sheet for the next ~90 days, plus current daily sales forecast. Built with INDIRECT lookups keyed on (code, stream sheet, row label) |
| `10 Day Forecast` (hidden) | Same pull, 10-day window, grouped by unit charge (Hydro charge, MEK charge, …) with downgrade indicator — this is the "14-day forecast to sales" deliverable |
| `Available To Promise` | Per product: current inventory net of open orders **until the next production run**, tank capacity, % of capacity — the allocation/push-list view |
| `INV TARGETS` | Per product daily series of End Inventory vs. constant LCL/UCL bands (chart data; targets rounded to nearest 1,000) |
| `MONTHLY PRODUCTION` | Production forecast outlook: product × month totals from Production-Out roll-ups |
| `Downgrade` | Projected downgrades to diesel/#6/cat, split "due to full tanks" vs. "due to switches", monthly, bbl and gal |
| `BaseOilTransfer` | Historical base-oil transfer/sales analysis (3 years monthly), rolling 12-mo average, YOY, monthly/daily projections |
| Charts sheets (`SOLVENT CHARTS`, `NAPHTHA CHARTS`, `Base Oil (6 Month)`) | Chart canvases over the above data |
| `Fcst`, `FORECAST-BOARD`, `Long-Run`, `Sales Required to Keep Up`, `BIWEEKLY SCHEDULE`, `Sheet1` (mostly hidden) | Secondary/legacy analysis boards |

---

## 5. Quirks and defects worth knowing before rebuilding

1. **Broken formulas exist**: `Charge Schedule` rows 177–187 ("Check For Issues") contain `#REF!` errors; `10 Day Forecast` has `#N/A` rows (9717/9718 missing from stream sheets). The workbook tolerates these; a rebuild should fix or drop them.
2. **Hardcoded vs. reference data**: the crude daily charge on Charge Schedule (7,500 BBL) is entered directly and does *not* read `CRUDE RATES` C5:C16, even though the Overview instructions say to maintain rates there. Receipts *do* read CRUDE RATES.
3. **Sign conventions**: open orders arrive negative; the Sales row negates them; the Net Available formula then *subtracts* the (negative) Sales row — net effect is correct but the intermediate signs are confusing. Normalize in the rebuild.
4. **Forecast netting is same-day only**: `Forecast(d)` nets the monthly rate against that single day's open orders, not month-to-date bookings — a modeling choice to preserve, question, or make configurable.
5. **Paired-code pooling**: inventory pools charge code + sold code (e.g. 9711+4105); the pairing map is embedded in each block's helper columns (E/D columns of stream sheets) — it must become an explicit table.
6. **Product codes are text** with meaningful leading zeros ("0386", "0101").
7. **One VBA macro only** (`DowngradeSoventsToDiesel`): copies gallon downgrade entries ÷42 into the BBL rows. Everything else is formulas.
8. **Two external workbook links** (the SharePoint/G-drive daily inventory files) are the only integration points.
9. Tank capacity constant uses today's tank *assignment*; products sharing tanks (e.g. tank 7777 appears under several products) can double-count shell capacity.

---

## 6. Proposed web-app decomposition

### 6.1 Domain model (entities)
```
Product            {code (text PK), description, category, isChargeStock, dieselPoolFlag}
ProductPair        {chargeCode → soldCode}                  -- pooling map (§5.5)
Tank               {tankId, capacityGal}
TankAssignment     {tank, product, effectiveDate}
InventorySnapshot  {date, product, tank, gallons, heelAdj}  -- nightly feed
ProcessUnit        {id: CRUDE|MEK|HYDRO|EXTRACT|ROSE|PLATFORMER|TOLLING}
UnitChargeProduct  {unit, product}                          -- what a unit can run
RunRate            {unit, product, month → bblPerDay}
YieldRule          {unit, inputProduct, outputProduct, month?, fraction,
                    modeCondition? (R/L), type: yield|complement|dieselYieldBack}
CrudePlan          {month → chargeBblPerDay, receiptsBblPerDay}
ChargeScheduleEntry{unit, product, date, bbl}               -- the plan
CrudeModeDay       {date, mode: R|L}
TransferEntry      {type: toDieselCharge|toFinishedDiesel|to6Oil|toCat|railReceipt|sonneborn,
                    product, date, bbl}
OpenOrder          {product, date, gallons}
DemandSplitRule    {orderedProduct, componentProduct, fraction}
SalesForecast      {product, month → galPerDay}
BlendBOM           {blendProduct, componentProduct, fraction}
InventoryTarget    {product, lclGal, uclGal}
```

### 6.2 Calculation service
A pure function, re-runnable on any input change:
```
simulate(asOfDate, horizon, plan, feeds, refData) →
  per (product, date): {beginInv, productionIn, sales, forecastNet, blends,
                        netAvailable, productionOut, outToDiesel, endInv,
                        tankCapacity, excessCapacity}
```
implemented exactly as §3.1–3.3. It is embarrassingly simple computationally (≈100 products × 365 days); the entire value of the Excel file is the *rule content* extracted above. Keep the engine server-side (or in a worker) with snapshot inputs versioned, so plans can be compared ("what if MEK runs 9117 next week instead of 9116").

### 6.3 App modules (mapping sheets → screens)
| Module | Replaces sheets |
|---|---|
| **Schedule editor** — calendar grid per unit; paint days with charge product; auto BBL from run rates with override; mode R/L toggle; downgrade/transfer entry | Charge Schedule, Overview workflow |
| **Inventory projection** — per product: stacked daily balance + chart with LCL/UCL and capacity bands; stream and horizon filters | Solvents/LLN/Cstock/MN/HN/Napthas/CRUDE, INV TARGETS, chart sheets |
| **Capacity dashboard** — 10/90-day excess-capacity heatmap grouped by unit charge; overflow alerts (the "needs downgrade" signal) | Inventory Detail, 10 Day Forecast |
| **ATP report** — inventory net of orders until next run, exportable to sales | Available To Promise |
| **Demand management** — forecast rates by product/month, blend BOM admin, demand-split rules, open-order feed monitor | Sales Forecast, BLENDS, Open Orders |
| **Reference data admin** — products, pairs, tanks/capacities, run rates, yields, targets, crude plan | Product list, Tank Capacity, UNIT RUN RATES, CRUDE YIELDS, CRUDE RATES, INV TARGETS |
| **Reports** — monthly production outlook, downgrade projection, base-oil transfer analytics | MONTHLY PRODUCTION, Downgrade, BaseOilTransfer |
| **Integrations** — nightly tank-inventory import, open-orders import | External links, Inventory, Open Orders |

### 6.4 Migration & validation plan
1. Build importers that read *this exact workbook* to seed reference data (yields, run rates, BOMs, targets, capacities, product list) — all locations are documented above.
2. Build the engine; then run a **parity harness**: feed it the workbook's current inputs and diff every `(product, date, measure)` cell against the workbook's cached values (extractable with the scripts used for this analysis). Investigate deltas — several will be workbook bugs (§5), which should be signed off, not replicated.
3. Cut over the two feeds (inventory snapshot, open orders) to direct imports; retire the SharePoint-linked workbook chain.

---

---

## 7. Status: this decomposition is now executable

Everything above is implemented and verified in `inv-planner/` (Phase 0 of
`WEBAPP-MIP-PLAN.md`): a workbook importer, the simulation engine, and a parity
harness that compares the engine against the workbook cell by cell.

**200,026 balance cells and 18,360 production cells compared; 0 unexplained
differences.** The remaining gaps are 1,150 cells attributable to documented
workbook defects (listed in `inv-planner/data/known-defects.json`) and 1,311 cells
where the workbook itself holds `#N/A`/`#REF!`.

Section 5's defect list was confirmed and extended by that exercise — see
`inv-planner/README.md` for the full findings, including the diesel yield-back
formula, the permanently-`#N/A` production row for product 8135, silently-zero
tolling production, and time-varying tank capacity.
