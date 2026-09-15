# Task: show the model's products on the reporting screens, starting with diesel

**Opened 2026-09-13 · commit `2433be0` · not started**

**Source:** [`docs/ui-audits/2026-09-13-dashboard-diesel.md`](../ui-audits/2026-09-13-dashboard-diesel.md)
**Contract:** `conventions.md` rules 16, 17, 18 (decided by the user on 2026-09-13)

---

## 1. Why

The planner asked why the dashboard shows twelve diesel products when the plant has
one charge product and one finished product. They were right. `live_blocks()` hands
every screen the workbook's raw blocks, so Dashboards, Capacity & alerts, Inventory
projection and the Stream sheet show the hydrotreater charge (9713), the DDDD pool,
and all ten finished grades. Two of those grades carry permanent, unfixable alarms;
eight are empty and badged green. Model inputs already collapses diesel to two
products and explains why in `_capacities`' docstring - the fix simply stopped at one
screen.

## 2. Read this before writing any code

**This task is blocked on a question about the plant, not about the code.**

There are **two finished-diesel demand rows in the workbook** and they are
**2,620,847 gal apart** over 180 days on Plan 75:

| | Opening | Production In | Demand (180d) |
|---|---|---|---|
| `DDDD` — what every screen shows today | 645,273 | 3,316,405 | **13,590,474** |
| members summed — what the optimizer solves today | 645,273 | 3,316,405 | **16,211,321** |

Opening and production are identical. The whole gap is demand, and **all of it is on
weekends**: weekday demand matches to the gallon, while across 51 weekend days the
member row lifts 2,620,847 gal more. Both rows carry the same monthly profile - the
same distinct daily values (70,000 / 84,275 / 88,000 / 95,924 / 125,000) - but DDDD
drops to a reduced weekend rate (34 days at 15,000) and 8175 lifts at the full
weekday rate seven days a week.

So collapsing is not a refactor. Whichever series survives changes the reported diesel
position:

- **Collapse onto the model (sum the members)** → reported diesel demand *rises* by
  2.6M gal. The screens start agreeing with the optimizer, which is rule 16's intent.
- **Collapse onto the pool (keep DDDD)** → the screens keep today's numbers, and the
  optimizer has to be changed to match, which means changing what it solves.

**Do not pick one by default, and do not let the implementation pick one by
accident.** The question for operations is one sentence:

> Does the terminal lift diesel on weekends at the weekday rate, or at a reduced rate?

Record the answer here before starting step 2, and say who gave it.

**Answer:** _(not yet obtained)_

Everything in step 1 is safe to build before that answer arrives. Steps 2 onward are
not.

## 3. Scope

**In:** the diesel aggregation reaching the reporting screens, the control-limit
labels, and the dashboard section heading.

**Out:**
- The engine. It keeps every block, because parity stops checking a block the engine
  does not carry (`live_blocks`' own docstring). The collapse belongs a layer up.
- `modelprep`. What the optimizer solves changes only if operations answer "reduced
  weekend rate", and that is a separate task.
- Other aggregations. `AGGREGATIONS` has one entry today; the work should generalise,
  but nothing else needs migrating.
- The 1,600,000 gal capacity figure — see step 4.

## 4. Work

### Step 1 — the two changes that do not depend on the answer

**1a. Control-limit labels (rule 17, audit F2).** `db/reference_edit.py` `_targets`,
reference_edit.py:131-140.

The workbook's Inventory Targets sheet has the names swapped: `reference.json` stores
`"9713": {"name": "Finished Diesel"}` and `"DDDD": {"name": "Diesel Charge"}`. 9713 is
the charge. The LCL and UCL groups print that name verbatim, so the same screen calls
9713 "NO.2 DIESEL-HYDRO CHARGE" in Tank capacity and "Finished Diesel" two groups
down.

Label from `products[code].name`, as every other group does. **Do not fix the seed
file** - re-importing the workbook brings the swap back. Keep the workbook's own text
only if it earns its place, and if so as a muted note beside the name, per rule 9.

**1b. Dashboard section heading (rule 18, audit F6).** `web/app.js` `loadDashboard`,
app.js:550-554.

Sections are keyed on `b["sheet"]`, the workbook tab a block was pasted on, so the
"Diesel & heating oil" group is headed "Solvents · 12 products". Drop the heading when
a group has one section. Keep sheet names where a group spans several.

### Step 2 — collapse at `live_blocks()` (rule 16, audit F1, F3, F4, F5)

Blocked on section 2.

`model_config.py:1425-1445` is the one function every screen goes through, which is
why the collapse belongs there rather than in each view. It currently drops retired
blocks only.

Two shapes are possible and they are not equivalent:

- **Keep the pool block, drop the members.** Cheap - no new series to compute, block
  ids stay real, `openProjection` keeps working. But it shows DDDD's demand, which is
  the 2.6M-gal-lighter row, and leaves the screens disagreeing with the optimizer.
  Only viable if operations say the reduced weekend rate is right, *and* `modelprep`
  is changed to match in a follow-up.
- **Synthesise the aggregate, drop the pool and the members.** Matches `modelprep`,
  which sums member openings, demand and production and drops the pool
  (modelprep.py:178-232). Needs a synthetic block with a synthetic id, summed balance
  rows, and every consumer taught to handle it.

The second is what rule 16 asks for. Cost it before committing: `sim.balances` is
keyed by block id and consumers read `End Inventory`, `Tank Capacity`, `Begin
Inventory`, `Production In`, `Sales`, `Forecast`, `Blends`, `Production Out`,
`Out to Diesel`, `Downgrade` and `Excess Capacity` (main.py:498-530). The aggregate
needs all of them summed, not just the two the dashboard reads, or the projection
table breaks when someone clicks through to it.

**Six `live_blocks` callers, all in `api/main.py`:**

| Line | Endpoint | What the collapse has to get right |
|---|---|---|
| 488 | `scenario_products` | The projection picker. A dropped block id must not stay selectable |
| 505 | `projection` | 404s on an unknown block id. The aggregate id must resolve here |
| 567 | `list_streams` | Per-sheet product counts shown in the Stream picker |
| 597 | `stream_sheet` | Sorted by `first_row`; a synthetic block has no row in the sheet |
| 673 | `dashboard` | Sections, badges, mini-charts |
| 730 | `alerts` | Pills, and the "products clear" stat derived from this list |

**Also:**
- `groups.py` — the `diesel` group lists twelve codes; it becomes 9713 plus the
  aggregate id, and `group_for()` has to place `DSL`. An unplaced code falls into
  "Other" by design, so nothing vanishes silently. Confirm that still holds.
- `reference_edit._targets` — no collapse today. It needs the same one `_capacities`
  already does (reference_edit.py:95-127).
- `web/app.js` `loadAlerts`, app.js:441-450 — "products clear" is
  `state.products.length - a.length`. Today eight empty diesel grades inflate it.
  Check the number is right after the collapse rather than assuming it follows.

### Step 3 — confirm the aggregate's capacity

`AGGREGATIONS["DSL"].capacity_override` is **1,600,000 gal**, and
`model_config.py:37-41` flags it itself: the members sum to 948,825 gal but six of the
ten have no tank recorded, so the pool carries its own figure - "this is the
aggregate's only invented number. Confirm with operations."

Today it is one row among twelve. After step 2 it is the only diesel capacity on any
screen, and every overflow pill is measured against it. Confirm it at the same time as
the weekend question. A planner may also have typed a capacity against `DSL` on Model
inputs, which wins over the config figure by design (modelprep.py:232-250) - check
whether one is set before treating 1,600,000 as current.

## 5. Tests

Two existing tests are the template, both in `inv-planner/tests/test_api.py`:

- `test_a_retired_product_is_gone_from_every_screen` (test_api.py:375) — asserts the
  outcome across six endpoints and the reference editor rather than the mechanism.
  This is the shape the new test wants: *name an aggregated product and its members
  must not appear anywhere a planner looks, and the aggregate must*.
- `test_dashboard_groups_cover_every_live_product_exactly_once` (test_api.py:201) —
  the "nothing vanishes, nothing is double-counted" guard. It compares against
  `live_blocks`, so it will follow the collapse automatically; check it still proves
  something afterwards rather than passing vacuously.

`test_modelprep.py:39` `test_diesel_collapses_to_one_product` already covers the solver
side and should keep passing untouched — if it breaks, step 2 has reached further than
its scope.

Worth adding regardless of which way section 2 is answered:

- The pool and its members are never both on screen.
- A member block id is not selectable in the projection picker and 404s on
  `/projection`.
- No product carries a `.pill.ok` while holding no material in any plan (audit F4).
- One code resolves to one name across every reference group (audit F2).

## 6. Done when

- A planner on the diesel dashboard sees two products, and they are the two the
  optimizer solves.
- 8175 and 8170 no longer carry `runs dry` / `overflows` pills.
- "Finished Diesel" names the finished pool on every screen, and nothing else does.
- The reported diesel demand matches what a run plans for, and the answer in section 2
  is written down with a name against it.
- `ui-flow.md`'s Dashboards and Capacity & alerts sections match the app again, and
  this task is linked from the audit report as closed.
