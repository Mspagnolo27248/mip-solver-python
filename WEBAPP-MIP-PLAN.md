# Plan: Inventory Planning Web App + Automated Schedule Optimization (MIP)

Companion to `BUSINESS-LOGIC.md`. Three workstreams:

- **A. Simulation engine** (the workbook's logic as code) — the foundation everything else stands on
- **B. Web app + live data integration** with editable staging areas
- **C. MIP schedule optimizer** — including a diagnosis of why your current MIPs fail and a formulation that matches how the refinery actually operates

Build in that order. The engine is both the web app's calculation core **and** the optimizer's validation oracle; without it you can't tell whether a "bad schedule" is a bad formulation or bad input data.

---

## Workstream A — Simulation engine (foundation)

A pure, deterministic function implementing §3 of `BUSINESS-LOGIC.md`:

```
simulate(scenario) -> per (product, day): begin, prodIn, sales, forecastNet,
                      blends, netAvail, prodOut, downgrade, end, capacity, excess
```

- **Inputs**: one `Scenario` object = reference data snapshot + feed snapshot + a charge schedule (manual or optimizer-produced).
- ~100 products × 365 days — milliseconds to run. Keep it dependency-light (plain Python/TypeScript, no solver) so it runs server-side and in tests.
- **Parity harness**: extract the workbook's cached values (the extraction scripts from the decomposition session do this) and diff engine output cell-by-cell. Known workbook defects (BUSINESS-LOGIC §5) get an explicit allowlist — sign off on each intentional deviation.
- Deliverable: engine + parity report + seed importer that reads the `.xlsm` to populate all reference tables (yields, run rates, BOMs, capacities, targets, product pairs).

**Why first**: it forces every implicit rule out of the spreadsheet, validates the data feeds, becomes the KPI evaluator for optimizer acceptance ("replay the MIP schedule through the simulator — does it beat the manual plan?"), and gives the web app instant value (what-if planning) before any optimization exists.

---

## Workstream B — Web app with live source data

### B1. Data architecture: source → staging → scenario

Your requirement — "pull from source into an editable area for the model" — is a three-layer design:

```
RAW (immutable)          STAGING (editable)             SCENARIO (frozen inputs)
─────────────────        ──────────────────             ────────────────────────
raw_inventory   ──sync──► stg_inventory     ──snapshot──► scenario_inputs
raw_open_orders ──sync──► stg_open_orders  /              + charge_schedule
raw_forecast    ──sync──► stg_forecast    /               = one simulation run
raw_blends      ──sync──► stg_blend_bom  /
```

- **Raw**: exact copies of each source pull, timestamped, never edited. Lets you answer "what did the feed say at 6am?" and re-sync safely.
- **Staging**: what the model reads. Each row carries `source_value`, `override_value`, `override_by/at/reason`. A sync updates `source_value` and **never clobbers overrides**; the UI shows drift (override ≠ latest source) so stale edits are visible. This replicates what planners do today in Excel (paste feed, then hand-fix) but with an audit trail.
- **Scenario**: a named, immutable snapshot of staging + a charge schedule. Enables side-by-side plan comparison and gives the optimizer a stable input set.

### B2. Integrations (replace the Excel external links)

| Feed | Today | Target |
|---|---|---|
| Tank inventory | SharePoint `Daily Inventory.xlsm` ← SQL job (2–4am) | Pull **directly from that SQL source** (product × tank × gallons + heels). The workbook chain is just a viewer over a database you already have. Nightly job + on-demand refresh |
| Open orders | Manual ERP paste, ~60 days, negative gallons | Scheduled ERP extract → `raw_open_orders`; keep the demand-split rules (4303→4315/4329 65/35, 4618→4555/4554 63/37) as a **configurable rule table** applied at staging |
| Sales forecast | Manual monthly board (gal/day rates) | Import from wherever S&OP numbers live (even a maintained sheet/CSV upload at first); staging grid is the editing UI |
| Blend BOM + blend forecast | BLENDS sheet | Master-data screen; BOM changes rarely — versioned reference table with effective dates |

Each import: schema validation, unit checks (gallons vs bbl), unknown-product-code quarantine, and a sync log surfaced in the UI.

### B3. Application modules (build order)

1. **Scenario workspace + inventory projection** — run the engine, per-product daily balance table + chart with LCL/UCL bands and capacity line. This alone replaces the stream sheets.
2. **Schedule editor** — per-unit calendar grid: paint charge product per day (one-hot per unit), crude R/L mode toggle, auto-BBL from run rates with per-day override, downgrade/transfer entries. Live re-simulation on edit.
3. **Staging editors + sync dashboards** (inventory, orders, forecast, blends).
4. **Dashboards**: excess-capacity heatmap (10/90-day), ATP report, downgrade projection, monthly production outlook.
5. **Reference data admin**: yields, run rates, capacities, targets, product pairs, split rules — all versioned.
6. **Optimizer UI** (after Workstream C): "Suggest schedule" button → optimizer run status → proposed schedule diffed against current plan → accept/modify.

### B4. Stack recommendation

- **Backend**: Python + FastAPI + Postgres. Python because the simulator, importers, and MIP share one language and the optimization ecosystem (Pyomo, OR-Tools, HiGHS) is Python-native.
- **Frontend**: React + a serious grid component (AG Grid or Glide) — the schedule editor and staging grids are the product; Excel users will judge it by grid ergonomics (copy/paste, fill-right, keyboard nav).
- **Jobs**: simple scheduler (APScheduler/cron) for feed syncs; optimizer runs as background jobs with progress reporting.
- Single-tenant internal tool: keep auth simple (SSO if available), audit everything.

---

## Workstream C — The MIP scheduler

### C1. Why your MIPs fail — diagnosis first

Based on how this system actually works, these are the standard failure modes, and each maps to something concrete in your model. Check them in this order:

**1. Infeasibility: you made physics soft and economics hard — it's the reverse.**
A model with *hard* tank capacities, *hard* demand satisfaction, and *fixed* run rates is almost always infeasible over 365 days — the real refinery doesn't magically fit; it **relieves pressure through downgrades** (solvents→diesel, resins→#6 oil, wax→cat cracker) and occasionally shorts/pushes sales. If downgrade flows and demand slacks aren't *decision variables with costs*, the solver has no relief valve and returns INFEASIBLE. Conversely the only truly hard physics are: inventory ≥ 0 (can't charge from an empty tank) and material balance.
→ *Fix*: elastic model — every commercial constraint gets a slack with a penalty; downgrades are first-class variables. When it still reports infeasible, use the solver's IIS (irreducible infeasible subsystem) to see exactly which constraints clash.

**2. Missing feed-availability coupling → "bad schedules" that charge material that doesn't exist.**
The units form a chain: crude → waxy stocks → MEK → dewaxed → extraction/hydro → finished. Charging MEK with 9117 *consumes 9117 inventory* produced earlier by the crude unit in R-mode. If your model only maximizes production or treats each unit independently, it produces schedules the simulator will show are impossible (negative intermediate inventory) or wasteful. The inventory balance must include the **consumption term** `− 42·charge(u, p, d)` for every charge stock, with `I ≥ 0` hard.

**3. "Solves but chatters": no campaign/switch structure.**
Inventory-optimal schedules alternate products daily (a little of everything every day keeps all tanks balanced). Real units run **campaigns** — switching charge has transition cost (off-spec production, line flushes) and operational limits. Without minimum-run-length constraints and per-switch costs, you get the flip-flop schedules you're describing. This is also the workbook's stated long-term goal: *"plan production to minimize unit switches without constraining inventories needed to meet demand."* That sentence **is** your objective function.

**4. Doesn't solve in reasonable time: horizon and formulation too big/weak.**
365 daily one-hot binaries × ~25 charge products × min-run logic is a hard unit-commitment-class problem, and big-M constraints with M = "huge number" give a hopeless LP relaxation.
→ *Fixes*: rolling horizon (see C4); big-M's equal to the actual run-rate bound (never a generic 1e6); min-run constraints in the standard tight unit-commitment form; symmetry breaking; warm start from the current manual schedule (it's feasible — hand it to the solver as an incumbent); accept a 1–3% MIP gap with a time limit instead of proving optimality.

**5. Objective states the wrong goal.**
"Maximize production" or "minimize inventory" are both wrong here — the plant is demand-driven with tank constraints. The real goal is a *priority stack* (C3). If weights aren't separated by orders of magnitude, the solver happily trades a stockout for two avoided switches.

**6. Silent unit/scale bugs.** Gallons vs barrels (×42), gal/day-rates vs monthly totals, and inventories in the millions next to fractions cause both wrong answers and numerical distress. Scale the model to kbbl and audit every coefficient once.

**7. End-of-horizon draining.** With no terminal condition the solver empties every tank in the last weeks ("free" inventory). Add terminal inventory targets (e.g., ≥ LCL, or a salvage value per gallon).

### C2. Formulation (matches the decomposed system)

**Sets**
- `U` units {CRUDE, MEK, HYDRO, EXTRACT, ROSE, PLAT, TOLL}; `P(u)` charge products per unit
- `Q` all products; `D` days in the detailed horizon; `m(d)` month of day d

**Parameters** (all straight from the workbook seed data)
- `rate(u,p,m)` bbl/day (UNIT RUN RATES; crude from CRUDE RATES)
- `yld(u,p,q,m)` yield fractions (CRUDE YIELDS monthly; fixed unit yields incl. complements & hydro diesel yield-back)
- `dem(q,d)` gal = open orders + netted forecast rate + blend-component demand (from staging)
- `I0(q)` opening inventory (feed); `cap(q)`; `lcl(q), ucl(q)`; `rcpt(d)` crude receipts
- Costs: `c_short(q)`, `c_over(q)`, `c_dg(q, dest)` (product value − destination value), `c_sw(u)`, `c_band(q)`, minimum run length `L(u)`

**Variables**
- `x[u,p,d] ∈ {0,1}` — unit u charges product p on day d; `Σ_p x[u,p,d] ≤ 1` *(verify: can any unit co-charge? the workbook grid allows it; confirm with operations)*
- `mode[d] ∈ {0,1}` — crude R/L (this **is** `x[CRUDE, R-mode]` in disguise; 9117 producible only when R, 9118 only when L)
- `chg[u,p,d] ∈ [0, rate(u,p,m(d))·x[u,p,d]]` — continuous charge (or fix `= rate·x` if rates aren't adjustable day-to-day; ask operations — continuous is easier on the solver *and* truer if they can turn rates down)
- `dg[q,dest,d] ≥ 0` — downgrade/transfer flows (bounded by route capacity)
- `I[q,d] ≥ 0` — inventory (hard nonneg)
- Slacks: `short[q,d] ≥ 0` (unmet demand), `over[q,d] ≥ 0` (above shell capacity — keep, hugely penalized, because "tank overfull" beats "INFEASIBLE" and shows *where* the plan breaks), `belowLCL/aboveUCL ≥ 0`
- `sw[u,p,d] ≥ 0` — startup indicator: `sw ≥ x[u,p,d] − x[u,p,d−1]`

**Constraints**
1. Inventory balance per (q,d), in gallons:
   `I[q,d] = I[q,d−1] + 42·Σ_{u,p} yld(u,p,q)·chg[u,p,d] − 42·chg[u_q,q,d] − dem(q,d) + short[q,d] − Σ_dest dg[q,dest,d] + 42·Σ transfersIn(q,d)`
   (transfersIn: downgrades landing in diesel/#6/cat pools; railcar receipts; crude gets `rcpt(d)` instead of production)
2. Capacity: `I[q,d] ≤ cap(q) + over[q,d]`
3. Bands: `I[q,d] ≥ lcl(q) − belowLCL[q,d]`, `I[q,d] ≤ ucl(q) + aboveUCL[q,d]`
4. Min run length (unit-commitment form): `Σ_{t=d..d+L(u)−1} x[u,p,t] ≥ L(u)·sw[u,p,d]`; optionally min *down* time and max campaign length; crude mode changes get their own switch var/cost
5. Mode-yield link: 9117 production term multiplied only into R-mode days (yields are `x`-linked so this is linear: use `yld` conditional on mode by splitting crude into two "virtual charge products" — crude-R and crude-L — which makes the mode constraint just the one-hot)
6. Maintenance/turnaround windows: `x[u,·,d] = 0` on outage days (planner-entered)
7. Terminal condition: `I[q,D_end] ≥ lcl(q)` (elastic, penalized) or salvage value in objective

**Objective** (minimize; weights separated by ~10× per tier)
```
  T1  Σ c_short·short                 -- serve committed demand      (highest)
  T2  Σ c_over·over                   -- don't overflow tanks
  T3  Σ c_dg·dg                       -- downgrades cost real margin
  T4  Σ c_sw·sw                       -- minimize unit switches
  T5  Σ c_band·(belowLCL + aboveUCL)  -- stay inside inventory bands
  T6  small terms: smoothness, preference for round campaign lengths (lowest)
```
Put real $ on T3 (product price spread vs diesel/#6) and T4 (estimated transition loss) if you can — it makes trade-offs defensible to operations. Tiers T1/T2 are effectively "never do this unless truly forced."

### C3. Solver & tooling

- **Model in Pyomo or python-mip**, solve with **HiGHS** (free, genuinely good at MIP now). If solve times disappoint at full scope, trial **Gurobi/CPLEX** (order-of-magnitude on hard MIPs) or reformulate the scheduling core in **OR-Tools CP-SAT**, which is often *better than MIP solvers* at min-run/switch scheduling logic — keep the LP inventory part in the MIP and let CP-SAT handle sequencing, or just try CP-SAT end-to-end with scaled-integer volumes.
- Always run with: warm start (current manual schedule), MIP gap 1–3%, time limit (e.g. 5 min interactive / 30 min batch), and IIS extraction on infeasibility.
- Log every run: inputs hash, gap, bound, incumbent history — you want "why did Tuesday's run differ" answerable.

### C4. Horizon strategy (this is what makes it solve)

Don't solve 365 daily days. Match granularity to decision relevance:

- **Detailed window: days 1–42** (6 weeks) — full binary model above.
- **Aggregate tail: months 2–12** — LP only (no binaries): monthly charge volumes per unit-product with campaign-count approximations, monthly balances and band penalties. Its job is to give the detailed window correct end-of-window inventory values so week 6 doesn't behave like the end of the world.
- **Rolling re-solve**: re-run nightly after the inventory feed lands, freezing days already executed (and optionally the next 2–3 days as "committed" so the schedule doesn't thrash day to day; add a small deviation-from-previous-plan penalty).

### C5. Incremental scoping (de-risk the formulation)

1. **v0 — LP, no binaries**: fix the current manual schedule's unit assignments; optimize only continuous charge levels + downgrades. Proves data, balances, and costs. If *this* is infeasible or weird, the problem is data/constraints, not MIP hardness.
2. **v1 — one unit**: free the MEK assignment binaries only (biggest inventory lever), all else fixed. Validate against planner intuition.
3. **v2 — MEK + crude mode**, then add hydro/extract/ROSE/platformer one at a time.
4. **v3 — full model + rolling horizon + aggregate tail.**
Each step: replay the schedule through the **simulator** and score KPIs (stockouts, overflow days, downgrade gallons, switch count, band violations) vs. the manual plan. Acceptance = dominates or ties the manual plan on T1–T3 while planners judge it operable.

### C6. Keep the planner in the loop

Ship it as **decision support, not autopilot**: optimizer proposes → UI shows diff vs current plan + KPI comparison (from the simulator) → planner edits/accepts. This builds trust, catches constraints you haven't captured yet (every "that schedule is illegal because…" conversation is a new constraint for the backlog), and matches how the tool will actually be adopted.

---

## Phasing & rough effort

| Phase | Content | Rough duration |
|---|---|---|
| 0 | Simulation engine + workbook seed importer + parity harness | 2–4 wks |
| 1 | DB schema, raw/staging/scenario layers, inventory feed integration, projection view | 3–5 wks |
| 2 | Schedule editor + remaining feeds (orders, forecast, blends) + staging editors | 4–6 wks |
| 3 | Dashboards (capacity heatmap, ATP, downgrade, monthly outlook) + admin screens | 3–4 wks |
| 4 | MIP v0–v1 (LP relief model, MEK-only binaries) with optimizer UI | 3–5 wks |
| 5 | MIP v2–v3 (all units, rolling horizon), nightly auto-run, KPI tracking | 4–8 wks |

Phases 1–3 deliver a usable Excel replacement before any optimization ships; phase 4's LP alone (auto-computed downgrade plan) is likely already valuable.

## Open questions to resolve with operations (they become constraints)

1. Can a unit charge two products the same day (split day), or strictly one? (The Excel grid permits multiple 1's.)
2. Are daily run rates adjustable within a range, or fixed at the monthly rate when running?
3. Minimum campaign lengths per unit? Sequence-dependent transitions (product A→B allowed, A→C requires flush)?
4. Crude mode switching: cost, minimum days per mode, max switches/month?
5. Which downgrade routes have daily volume limits (line/loading capacity)?
6. Are LCL/UCL true operating rules or advisory? Who owns updating them?
7. Turnaround calendar — where does it live today?
8. Real $ values for product-vs-diesel spreads and switch costs (finance can supply).
