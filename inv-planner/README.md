# inv-planner

The refinery planning workbook rebuilt as an application: the workbook's logic as
a validated engine (Phase 0), and a web app with a source → staging → scenario
data layer on top of it (Phase 1).

See `../BUSINESS-LOGIC.md` for the domain decomposition, `../MIP-FORMULATION.md`
for the optimization model and `../docs/archive/WEBAPP-MIP-PLAN.md` for the
original build plan this grew out of.

---

## Running the app

All commands run from the `inv-planner` folder:

```
cd "C:\Users\Mspag\OneDrive\Desktop\inv-excel-decomp\inv-planner"
```

They work the same in PowerShell and bash. You need Python 3.9+ (the Anaconda
Python already on this machine is fine). No Node, no database server, no internet.

### First time only — three commands

```bash
pip install -r requirements.txt   # 1. openpyxl, fastapi, uvicorn, sqlalchemy, pytest
python scripts/seed.py            # 2. read the workbook -> data/seed/*.json      (~1 min)
python scripts/init_db.py         # 3. load into the database + baseline scenario (~30 s)
```

Step 2 reads `../Inventory Planning - FY26 12 MONTH.xlsm` and writes the JSON
seed. Step 3 creates `data/invplanner.db`, syncs all six feeds into staging, and
creates a "Baseline (from workbook)" scenario with the charge schedule as the
planner entered it. You should see:

```
reference version 1 (54 blocks, 1445 products)
  synced inventory                   403 rows  from workbook-seed:scenario.json
  synced open_orders                 333 rows  from workbook-seed:scenario.json
  ...
scenario 1: Baseline (from workbook) (as of 2026-07-23)
```

### Every time — one step

```bash
python scripts/run_api.py
```

Then open **http://127.0.0.1:8000** in your browser.

Leave that terminal window open — it *is* the server. Press **Ctrl+C** in it to
stop the app. Your data lives in `data/invplanner.db` and survives restarts.

Useful flags:

| Command | Does |
|---|---|
| `python scripts/run_api.py --port 8080` | Run on a different port |
| `python scripts/run_api.py --reload` | Auto-restart when you edit code (development) |

### Where to start in the app

1. **Capacity & alerts** opens first — the products that overflow, run dry, or
   leave their control band in the next 30 days. Click any row to jump to its
   projection.
2. **Dashboards** — the chart wall. Pick a family (base oils, waxes, resins,
   solvents, diesel, K-30 & naphthas, crude) and every product in it is charted,
   in sections by stream. Click a chart to open that product's projection.
3. **Stream sheet** — the familiar view. Pick Solvents (or LLN, Cstock, MN, HN,
   Napthas, CRUDE) and read every product's full balance block across the dates,
   the same shape as the workbook tab. "Key rows only" collapses each product to
   begin / production in / sales / forecast / charged out / end / headroom.
4. **Inventory projection** — one product in depth, with the capacity and control
   band drawn on a chart.
5. **Charge schedule** — change a barrels-per-day cell or flip the crude R/L mode;
   the projection re-simulates as soon as you tab out of the cell. **Set a rate
   across the window** writes one rate onto every day in a range, which is how a
   plan is laid down rather than corrected: put the crude, ROSE and Platformer
   rates across the whole horizon, then clear MEK and extraction and let the
   optimizer schedule them. It skips days a unit is down, and setting a rate on
   one line clears the other lines on that unit, because a unit runs one feed at
   a time. "Only days the unit is idle" fills the blank tail of a schedule
   without touching the stretch it already covers, which is what a rolling plan
   needs.

   **Leave the hydrotreater's rows alone.** Clearing them does not free it — v2
   frees it in stage two whatever the grid says, and it keeps 1 cell in 107 of
   what was there. What the rows do is tell *stage one* that something
   downstream consumes 9704, 9705, 9711, 9712, 9703 and 9720, and with them
   deleted stage one sizes MEK and extraction for a plant with no hydrotreater.
   See `optimizer/v2.py`.
6. **Source data** — the six feeds. Type in an Override column to correct a value;
   press "Sync this feed" and watch the override survive. Upload today's
   inventory, open orders or forecast here too, then "Create scenario from
   source data" to plan on them.

The product families are defined in `src/invplanner/groups.py` as plain lists of
product codes — edit them there if a product belongs somewhere else. Anything not
assigned shows up under "Other" rather than disappearing, and a test enforces that
every product appears in exactly one group.

### Running the greedy scheduler

`greedy` builds a schedule by rule instead of by solver. It decides the same
units v2 does plus ROSE, returns in well under a second where v2 takes about a
minute over 42 days, and **cannot tell you how far from best it is** - there is no
optimality gap, because there is no search. Judge it by the verification, not by
an objective. Full reasoning and the bake-off table are in
`src/invplanner/optimizer/greedy.py`.

**The whole workflow, from today's input files to a finished plan.** Every step
below was run end to end on 2026-09-12; scenario and run numbers are from that
walkthrough.

1. **Upload the three files.** Source data -> *Upload source files*. The filename
   does not matter, only the slot: `inventory.xlsx` -> Tank inventory,
   `open orders.xlsx` -> Open orders, `forecast.xlsx` -> Forecast & blend demand
   (which supplies two feeds). Each file is parsed before it replaces anything,
   so a sheet whose columns have moved is rejected rather than half-imported.

2. **Create the scenario.** Same card -> *Create scenario from source data*. It
   asks for a name and for the first day of the plan - the day the uploaded
   inventory was taken - because that date is in none of the files.

   **Select the scenario you want the charge grid from before clicking.** The new
   scenario takes opening inventory and demand from the fresh uploads, but copies
   its grid from whatever is currently open. That matters more for greedy than
   for the MIP: greedy derives the horizon boundary from the grid and holds the
   platformer and the transfer lines at its levels. A blank grid is a known-bad
   input - `v0-42-blank` is recorded infeasible in `tests/baseline.json`.

3. **Run greedy**, then **4. run v2 on greedy's result**, as below.

On the walkthrough, greedy took scenario 64 from 8,863,918 gal of lost sales to
947,712 in 2.5 seconds, verified.

**From the app**

1. **Optimizer inputs** -> *Model* -> **`greedy — rules, no solver (under a
   second)`**. Everything else on that screen still applies; `time_limit_seconds`,
   `mip_gap` and `mip_gap_abs` are simply ignored, since nothing is being solved.
2. Set **Detailed horizon** to **42 days**. That is the horizon this is verified
   at - see the limits below before going longer.
3. **Runs & results** -> **Run optimizer**, picking the scenario to plan from.
4. The run lands as `verified · not proved optimal`, which is the normal good
   outcome and not a warning: `greedy` can never be `done`, because `done` means a
   proof. **A run marked `unverified` should not be used** - the simulator replayed
   the schedule and disagreed with it.
5. The result opens on the planning side as a new scenario, exactly like a MIP run.

**From the command line**, which is also what the app does underneath:

```bash
# start the app
python scripts/run_api.py --port 8000

# point the optimizer at greedy, 42 days
curl -X PATCH http://127.0.0.1:8000/api/optimizer/params      -H "Content-Type: application/json"      -d '{"model_version":"greedy","horizon_days":42}'

# run it against scenario 48
curl -X POST http://127.0.0.1:8000/api/optimizer/run      -H "Content-Type: application/json"      -d '{"scenario_id":48,"actor":"planner"}'
```

**Without the app or the database at all** - the cheapest way to try a change:

```python
from invplanner import model_config as cfg
from invplanner.engine import Reference, Scenario, simulate
from invplanner.modelprep import build
from invplanner.optimizer import greedy

ref = Reference.load("data/seed/reference.json")
scn = Scenario.load("data/seed/scenario.json")
sim = simulate(ref, scn, physical=True)
dates = scn.dates[:42]
down = {u: cfg.turnaround_days(u) for u in cfg.TURNAROUNDS}
spec = build(ref, scn, sim, horizon=dates, downtime=down)

res = greedy.solve(ref, scn, spec, {"objective": "cost",
                                    "lost_sale_margin_per_gal": 1.50,
                                    "downgrade_discount_per_gal": 0.50,
                                    "switch_cost": 2000.0}, horizon=dates,
                   downtime=down)
print(res.status, res.solve_seconds, res.kpis["lost_sales_gal"])
```

**Measured on real scenarios**, run through the API at 42 days. Lost sales are
gallons, and the improvement is against the plan the run started from:

    base scenario                        verified   baseline lost   greedy lost
      48  Cold start (fresh uploads)      yes           9,428,494     1,010,410
      49  Cold start (MEK/EXTRACT free)   yes           6,211,946     1,196,406
      46  Plan Spring TAR                 NO            9,336,869     2,465,202

**Greedy first, then the MIP.** Greedy writes its answer as an ordinary scenario,
so the MIP can plan from it. This is *not* a solver warm start - PuLP's
`warmStart` is silently discarded on Windows and was measured as no benefit
anyway (`optimizer/v0.py`). It changes the MIP's inputs: the charge floor applies
to greedy's levels rather than the planner's, and `RATE_BASIS_IS_FLOOR` lifts a
line's ceiling to whatever greedy ran it at.

Measured on scenario 48 at 42 days, `time_limit_seconds` 900:

    v2 straight from the plan      904 s     329,833 gal lost   verified
    greedy                           3 s   1,010,410 gal lost   verified
    greedy, then v2 on its result  904 s     271,952 gal lost   verified

**17.5% better lost sales for the same wall clock**, and the greedy pass is
effectively free at three seconds. Both v2 runs hit the time limit and neither
proved optimality, so these are two incumbents at the same budget - a fair
comparison, unlike an incumbent against a proved optimum. One scenario at one
horizon, so treat it as promising rather than settled.

To do it: run greedy, note `result_scenario_id`, switch Model back to `v2`, and
run again against **that** scenario rather than the original plan.

**Known limits. Read these before trusting a run.**

  * **42 days is the supported horizon.** At 100 days the seed leaves 23,452 gal
    of downgrade unrouted on a single day and the run does not verify; past that
    it degrades badly.
  * **It fails on some scenarios, and scenario 46 is the known one.** It comes
    back `unverified` with a 338,344 gal feed shortfall - the schedule asks units
    to charge feed the tanks cannot supply. The cause is **not** the turnaround
    windows on that scenario, which was the obvious guess: the shortfall is
    already there at a 20-day horizon that ends well before the first outage, and
    it does not move between 20, 24 and 30 days. `9713`, the diesel charge pool,
    carries part of it. Unresolved.
  * **Always read the verification.** These two limits are exactly why the run
    status is worth more than the KPIs: a schedule that improves lost sales by 74%
    and cannot be executed is not an improvement.

### Re-importing after the workbook changes

```bash
python scripts/seed.py         # re-read the workbook
python scripts/init_db.py      # load it as a new reference version
```

`init_db.py` leaves existing scenarios alone. To wipe everything and start clean:

```bash
python scripts/init_db.py --reset
```

### Updating the numbers without re-importing the workbook

Three of the feeds can be uploaded on their own, as the small exports a planner
produces on demand rather than a slice of the 6 MB workbook:

| File | Shape expected in row 1 | Feeds it replaces |
|---|---|---|
| `inventory.xlsx` | `Code \| Product \| Net Inv` | inventory |
| `open orders.xlsx` | `Code \| Product \| one column per ship date` | open_orders |
| `forecast.xlsx` | `CODE \| Oct…Sep`, twice across | sales_forecast, blend_component_demand |

In the app: **Source data → Upload source files**, one file per card. From a
folder of all three at once:

```bash
python scripts/load_inputs.py ../transfer-inputs --dry-run
python scripts/load_inputs.py ../transfer-inputs --scenario "Plan 2026-08-28"
```

Files are matched to feeds by name, parsed before they replace anything, and
stored in `data/uploads/`. Anything not uploaded keeps reading the workbook
seed, so this is per feed rather than all-or-nothing; deleting a file from
`data/uploads/` (or "use workbook" on its card) puts that feed back.

Two things worth knowing. Sign and units follow the workbook: open orders arrive
as negative gallons and are stored as positive demand, the forecast grids are
daily rates in gal/day keyed by month. And uploading changes **source data
only** — an existing scenario keeps the numbers it was frozen with, so make a new
scenario to plan on what you just loaded. Give it the day the inventory was
taken: the "Create scenario from source data" button asks, because a plan that
opens with today's tanks on a date five weeks in the past is wrong in a way
nothing downstream can detect.

Two feeds still come only from the workbook — base-oil transfers and blend
recipes — as does everything in Model inputs (yields, run rates, capacities,
control limits).

### Checking it works

```bash
python -m pytest tests -q          # ~95 tests, ~2 min
python scripts/run_parity.py       # re-verify the engine against the workbook
python scripts/snapshot.py         # did the model's answers move?
```

### After changing anything in the model

`tests/baseline.json` records what the optimizer answers on ten fixed cases, and
`scripts/snapshot.py` diffs against it. Run it after any change to the objective,
the constraints or the configuration:

```bash
python scripts/snapshot.py            # prints every field that moved
python scripts/snapshot.py --update   # accept the change, and say why in the commit
```

This exists because the defects this model has had did not look like errors. An
infeasible run was relabelled `feasible` and handed back a schedule; PuLP's
rewritten status made every time-limited v1 result claim to be optimal; a reactor
constraint cost binaries and did nothing. None of them changed anything a person
would notice, and all three move a field in the baseline.

**The exact tier is v0 at `mip_gap` 0**, because at the 2% gap used for planning
the difference a modelling change makes is smaller than the gap itself — two
separate experiments here produced confident numbers that turned out to be noise.
v0 solves 100 days exactly in about three seconds, so exactness is affordable. v1
cannot join it (at gap 0 it times out even over 42 days), so its rows are recorded
for *structure* — status, campaign shape, switch counts — and carry no objective.

### If something goes wrong

| Symptom | Fix |
|---|---|
| `No seed found. Run scripts/seed.py first.` | Run `python scripts/seed.py` |
| Browser says the page can't be reached | The server terminal isn't running — start `python scripts/run_api.py` |
| `[Errno 10048] address already in use` | A server is already running. Use it, or start on another port with `--port 8080` |
| App loads but says "Failed to load — is the API running?" | The page is open but the server stopped; restart it and refresh |
| `Workbook not found` from `seed.py` | The `.xlsm` must sit in the parent folder, or pass its path: `python scripts/seed.py "C:\path\to\file.xlsm"` |

### Using Postgres instead of SQLite

The default is a SQLite file, which needs no setup. To point at Postgres, set
`DATABASE_URL` before running the scripts (and `pip install psycopg2-binary`):

```powershell
$env:DATABASE_URL = "postgresql+psycopg2://user:pass@localhost:5432/invplanner"
python scripts/init_db.py
python scripts/run_api.py
```

---

# Phase 0 — simulation engine

The workbook's logic as code, validated cell-by-cell against the workbook itself.
This is the app's calculation core and the optimizer's scoring function: before
anything can be optimized, "what would this schedule do to inventory?" has to be
answerable without Excel.

## Status

| check | result |
|---|---:|
| balance cells compared vs. workbook | 201,433 |
| matching | 198,972 |
| unexplained differences | **0** |
| known workbook defects (allowlisted) | 1,150 |
| cells where the workbook itself holds `#N/A` / `#REF!` | 1,311 |
| production grid cells compared | 18,360 |
| production grid differences | **0** |

Full horizon: 54 product blocks plus the CRUDE sheet × 366 days. Simulation runs
in ~13 s (all of it JSON loading and Python object churn; the arithmetic is
trivial).

## Layout

```
src/invplanner/
  layout.py     row/column anchors of the workbook, validated on every import
  xlsx.py       cell access, date/code coercion
  importer.py   workbook -> reference.json + scenario.json
  feedsheets.py standalone feed exports -> the same values, validated by header
  engine.py     production calculation + daily inventory balance
  parity.py     engine vs. workbook cached values, with a defect allowlist
scripts/
  seed.py       build the JSON seed from the workbook
  load_inputs.py upload a folder of feed exports, optionally into a scenario
  run_parity.py run the comparison, write the report
data/
  seed/         reference.json, scenario.json, import-warnings.txt
  uploads/      feed exports uploaded since, one per sheet kind
  reports/      parity-report.md, parity-diffs.json
  known-defects.json
tests/
```

## Usage

```bash
pip install -r requirements.txt
python scripts/seed.py                 # workbook -> data/seed/*.json
python scripts/run_parity.py           # full-horizon comparison
python scripts/run_parity.py --days 30 # quick check while iterating
python -m pytest tests -q
```

`--loose` switches the engine from workbook-faithful lookups to physically
correct ones (see "Two lookup modes" below).

## Design notes

**Reference vs. scenario.** `reference.json` holds what changes rarely (products,
capacities, yields, run rates, blend BOM, targets, and the per-block balance
definitions). `scenario.json` holds one planning run: the feeds plus the charge
schedule. The engine takes both and returns the projection — no Excel, no
database, no solver — so the same code serves the web app, the tests, and the
optimizer's scoring loop.

**The balance rows are data, not code.** The workbook's blocks are not a uniform
template; they were hand-tweaked over years. Rather than hardcode each variant,
the importer classifies every balance row's formula into a small set of terms
(`production_grid`, `open_orders`, `blend_demand`, `forecast_net`,
`charge_first_match`, `charge_row`, `to_diesel`, `pool_sum`, `manual`, …) and the
engine evaluates them generically. This is what makes per-product configuration
explicit instead of hidden in spreadsheet convention.

**Formula regions.** Several rows change formula partway through the horizon: some
are planner-entered early and formula-driven later, one carries a weekend rule
only in its first column, and a tail span was pasted with a broken cross-sheet
reference. Each row is therefore split into contiguous spans that share a formula
shape, and each span is classified separately. Import warnings list every span
boundary found.

**Two lookup modes.** The workbook resolves a product's "Production Out" with a
first-match `VLOOKUP` over the whole charge grid, so a product charged at two
units only has the *first* line subtracted from inventory. `strict_workbook=True`
(default) reproduces that; `False` sums every matching line, which is what the
physics implies. The parity report lists every product where the two disagree —
confirm the intended behaviour with operations before the rebuild changes it.

**Known defects.** `data/known-defects.json` lists workbook faults the engine
deliberately does not reproduce, each with the action needed. Parity counts them
separately from unexplained differences, so a real regression can never hide
inside them.

## What the parity run surfaced

Findings worth acting on regardless of the rebuild:

1. **Diesel yield-back** — the hydrotreater returns `charge × (1 − yield)` to the
   diesel pool. Applying the factor to the raw charge instead (the intuitive
   reading) overstates it by `1/yield`; this was caught by parity, not by review.
2. **`Production - Out` row 46 is `#N/A` for every day** — it looks up charge
   product 8135 in the hydrotreater range, where 8135 is not a charge line. The
   error propagates through the roll-up, so product 8135 has no usable production
   figure anywhere in the workbook.
3. **Tolling production is silently zero** — the yield rule looks up product 9118
   but the tolling charge line is 9202, so the `IFERROR` swallows the miss.
4. **The tail span (from 2027-05-23) of several Blends rows takes its month from a
   fixed cell on another sheet**, so the last two months of blend demand use the
   wrong month.
5. **Tank capacity is not constant** — it steps up mid-horizon (`=CP23+300000`)
   when a tank project lands. Modelled here as an effective-dated series, which is
   the shape the rebuild wants.
6. **Products charged at two units** are only partly consumed in the model (see
   "Two lookup modes").
7. **Blend BOMs that don't sum to 1.0** (4548 sums to 0.50, 7320 to 0.976) and
   **duplicate open-order rows**, where the workbook's `VLOOKUP` only sees the
   first, are reported as import warnings.

---

# Phase 1 — the application

## Data architecture

```
SOURCE                RAW (immutable)      STAGING (editable)        SCENARIO (frozen)
inventory     ──┐     raw_batch      ──┐   staging_cell        ──┐   scenario.inputs
open orders     ├───► raw_row          ├─► source_value          ├─► schedule_entry
forecast        │     one row per      │   override_value        │   crude_day
blend recipes ──┘     pull, kept       │   override_by/at/reason │   = one simulation run
                                       └── effective = override ?? source
```

**Why three layers.** Planners have always corrected feed data by hand in Excel;
the problem was that the correction and the original were the same cell. Here
every value keeps both. A re-sync writes `source_value` and never touches
`override_value`, so edits survive. Each override also records the source value at
the time it was made, so if the source later moves the cell is flagged **stale** —
the planner sees that their correction was based on a number that has since
changed, instead of silently shadowing new data.

Scenarios freeze staging at a moment in time, which is what makes plan-vs-plan
comparison meaningful and gives the optimizer stable inputs.

**Connectors.** `db/connectors.py` defines a connector per feed, and the choice
of connector is per feed rather than global. `WorkbookSeedConnector` reads the
workbook-derived JSON seed; `UploadedSheetConnector` reads a standalone export a
planner dropped in, parsed by `feedsheets.py`. `default_connectors` prefers an
upload where one exists and falls back to the seed where it does not, which is
why inventory can come from this morning's export while base-oil transfers still
come from the workbook.

That is the same seam the live sources will use. Pointing a feed at the SQL
database behind the SharePoint inventory workbook, or at the ERP order extract,
means adding a third class there; staging, scenarios, and the engine are
untouched either way. The uploaded sheets stage the same keys as the seed
(inventory under `ALL` rather than a tank id, orders as positive demand), so an
override made against workbook data survives the switch and is flagged stale if
the new source disagrees with it.

**Database.** SQLAlchemy models run on SQLite by default (zero setup) and on
Postgres by setting `DATABASE_URL`.

## The app

Four views, mapping onto the workbook they replace:

| View | Replaces | What it does |
|---|---|---|
| **Capacity & alerts** | Inventory Detail, 10 Day Forecast | Ranks products that overflow their tanks, run dry, or leave their control band in the window. Overflow is the "needs a downgrade" signal the workbook exists to produce. Click a row to open its projection. |
| **Dashboards** | Base Oil (6 Month), SOLVENT CHARTS, NAPHTHA CHARTS | The chart tabs, regrouped by product family — base oils, waxes, resins, solvents, diesel, K-30 & naphthas, crude. One small chart per product showing ending inventory against tank capacity and the LCL/UCL band, stacked vertically in sections by stream. Click any chart for the full projection. |
| **Stream sheet** | Solvents, LLN, Cstock, MN, HN, Napthas, CRUDE | The workbook layout itself: pick a stream and read every product in it, each with its full balance block — beginning inventory, production in, sales, forecast, blends, charged out, ending inventory, capacity, headroom — with dates across. Days that go negative or over capacity are highlighted on the ending-inventory row. |
| **Inventory projection** | INV TARGETS, chart tabs | One product in depth: daily balance with a chart of ending inventory against tank capacity and the LCL/UCL band. |
| **Charge schedule** | Charge Schedule | Editable bbl/day grid per unit, plus the crude R/L mode toggle. An edit re-simulates immediately. Also holds **planned downtime** — the days a unit cannot produce, set before an optimizer run. |
| **Source data** | the feed paste areas | Staging editor: source value, override, effective value, who changed it and why, with stale-override flags and per-feed re-sync. Also where inventory, open orders and the forecast grid are uploaded as standalone files. |

The front end is dependency-free and served by FastAPI — no build step, nothing to
install, no CDN. It is deliberately replaceable: when grid ergonomics start to
matter (bulk paste, fill-right, keyboard nav), swap it for React + a real grid
component against the same API.

## API

```
GET   /api/health
GET   /api/feeds                          feed list with row/override/stale counts
GET   /api/feeds/{feed}/rows              staging rows (search, filter, paging)
POST  /api/feeds/{feed}/sync              re-pull one feed
POST  /api/feeds/sync-all
GET   /api/feeds/{feed}/batches           sync history
GET   /api/feeds/uploads                  which feeds read from an uploaded file
POST  /api/feeds/upload/{kind}            the workbook as the raw body; parse, store, sync
DEL   /api/feeds/upload/{kind}            drop it, back to the workbook seed
PATCH /api/staging/{cell_id}              set or clear an override
GET   /api/scenarios                      list
POST  /api/scenarios                      freeze staging into a new scenario
GET   /api/scenarios/{id}/products        the 54 product blocks
GET   /api/dashboards                     chart dashboard groups
GET   /api/scenarios/{id}/dashboard       chart series for one product family
GET   /api/scenarios/{id}/streams         stream sheets and their product counts
GET   /api/scenarios/{id}/stream          all products on a sheet, full balance rows
GET   /api/scenarios/{id}/projection      daily balance for one product
GET   /api/scenarios/{id}/alerts          capacity / stockout / band breaches
GET   /api/scenarios/{id}/schedule        charge grid for a date window
PATCH /api/scenarios/{id}/schedule        edit a unit-day charge
POST  /api/scenarios/{id}/schedule/fill   one rate across a date range, or clear a unit
PATCH /api/scenarios/{id}/crude           edit crude rate or R/L mode
GET   /api/scenarios/{id}/downtime        planned downtime, windows and days
POST  /api/scenarios/{id}/downtime        add a downtime window
POST  /api/scenarios/{id}/downtime/toggle flip one unit-day
DEL   /api/scenarios/{id}/downtime/{id}   remove a window
```

Simulations are cached per `(scenario, schedule_version)` and invalidated on edit.

## Tests

`tests/test_api.py` covers the three things Phase 1 must get right:

- an override survives a re-sync, and goes stale when the source moves under it
- a scenario frozen from the database simulates **identically** to the JSON
  scenario the parity harness validated against the workbook
- every endpoint the front end calls actually exists (guards against UI/API drift)

## Next

- Point the inventory connector at the SQL source behind `Daily Inventory.xlsm`
  and the orders connector at the ERP extract; add the demand-split rules
  (4303→4315/4329 65/35, 4618→4555/4554 63/37) as a rule table applied at staging.
- Reference-data admin screens (yields, run rates, capacities, targets), so the
  reference document stops being import-only.
- Then Phase 4: the MIP, scored by this engine. The open question flagged in
  Phase 0 — nine products are charged at more than one unit and the workbook only
  consumes the first — should be settled with operations before then, since it
  changes the inventory balance the optimizer is constrained by.
