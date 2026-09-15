# UI audit - the diesel products on the reporting screens

**2026-09-13 · commit `2433be0` · started from the question "why are there so many
diesel products in the dashboard? I thought we had one charge product and one
finished product."**

The short answer: the planner is right, and only one screen in the app agrees with
them. Model inputs collapses finished diesel into a single product on purpose and
says why in its own code. Dashboards, Capacity & alerts and Inventory projection
never got that change, so they still draw the workbook's twelve diesel blocks -
including the pool *and* its members, which is the same barrels twice.

## 1. Scope and method

**In scope:** Dashboards (Diesel & heating oil group), and the two screens the same
product list reaches - Capacity & alerts and the Inventory projection picker. Model
inputs is in scope only for its diesel rows.

**How it was checked:** against the app already running on port 8000, through its own
API rather than a browser - the `claude-in-chrome` tools are not available in this
session, so **there are no screenshots**. Every finding below rests on a live response
from the running app plus the code that renders it. Findings are marked `live-api`
where a response confirms what the screen will show, and `code-only` where only the
render code does.

**Scenarios looked at:** 75 `Plan 2026-09-13 New` (Current Plan) and 77 `Run 74 · v2`
(Optimized Result), both 366 days from 2026-09-08, 180-day window.

**Earlier report:** `2026-09-12-plan-and-optimizer.md` put all four reporting screens
out of scope, so none of its findings overlap. Nothing in it is re-checked here.

## 2. Fix first

1. The alerts table reports two diesel emergencies that no plan can fix, and they are
   the loudest red pills in the app (F1).
2. The row labelled **Finished Diesel** on Model inputs is the *charge* tank; **Diesel
   Charge** is the *finished* pool. The two names are swapped (F2).
3. The diesel dashboard draws the DDDD pool beside the members it is the sum of - the
   same material counted twice on one screen (F3).
4. Eight of the twelve diesel cards are grades with no tank, no production and no
   demand, badged green "in bounds", and they pad the "products clear" stat (F4).
5. Model inputs already collapses diesel to two products and explains why. Four other
   screens don't (F5).

**Added after the decisions below were taken, while scoping the build:** there are two
finished-diesel demand rows 2,620,847 gal apart, all of it weekend lifting, and the
optimizer reads one while every screen reads the other (F7). It outranks everything
above, and it has to be settled with operations before the collapse can be built.
It also corrects a claim in F3.

## 3. Findings

### F1 [S1] The alerts table reports two diesel emergencies that no plan can fix
Where: Capacity & alerts -> the table and the "products run dry" stat
(app.js `loadAlerts` / `issuePill`, app.js:441-479)

Planner sees, on Current Plan 75 over 180 days:

| Product | Issue | First day | Low point |
|---|---|---|---|
| 8175 — #2 NRLM DIESEL S15 DYED | `runs dry · 180d` | 2026-09-08 | -16,211,321 gal |
| DDDD — Finished Diesel | `runs dry · 168d` | 2026-09-18 | -9,628,795 gal |
| 9713 — NO.2 DIESEL-HYDRO CHARGE | `overflows · 169d` | 2026-09-19 | peak 6,599,840 |
| 8170 — #2 ONROAD DIESEL S15 UNDYED | `overflows · 168d` | 2026-09-20 | peak 3,961,678 |

Planner will: start with the worst number in the app - a tank 16 million gallons
under on day one - and rebuild the HYDRO schedule around it.

Result: nothing they can schedule will move it. The workbook books **all** finished
diesel demand on 8175, which has no tank and no production, and lands **all**
production on 8170 and 8105, which have no demand
(`model_config.py:23-42`, `AGGREGATIONS["DSL"]`, and the same note in
`optimizer/verify.py:82`). The split is an accounting artifact. Running the v2
optimizer does not help and is not meant to: on Optimized Result 77 the same two rows
read 8175 dry **180 days** and 8170 over cap **180 days**. The optimizer never sees
these products at all - `modelprep` collapses them into one `DSL` before solving
(`modelprep.py:178-232`), so it is optimising a model in which this problem does not
exist while the alerts screen keeps reporting it.

Fix: report diesel on the screens the way the model holds it - one charge product
(9713) and one finished pool (DSL), the collapse `reference_edit.py` already does.
Until then the members must not carry `runs dry` / `overflows` pills.

Evidence: live-api (`/api/scenarios/75/alerts?days=180`, `/api/scenarios/77/…`)

### F2 [S1] "Finished Diesel" on Model inputs is the charge tank, and the other way round
Where: Model inputs -> Lower control limit and Upper control limit
(`db/reference_edit.py` `_targets`, reference_edit.py:131-140)

Planner sees three diesel rows in each control-limit group:

| Code | Label on screen | What the code actually is |
|---|---|---|
| 9713 | **Finished Diesel** (UCL 500,000) | the hydrotreater **charge** |
| DDDD | **Diesel Charge** (UCL 500,000) | the **finished** pool |
| 8175 | 8175- Diesel (UCL 921,150) | a grade with no tank |

Planner will: tighten the band on the row that says Finished Diesel.

Result: they move the control band on the charge tank. The edit is global and lands
in every scenario at once, clearing every cached projection (`patch_reference`).
Two groups above, in the same view's Tank capacity group, 9713 is correctly named
**NO.2 DIESEL-HYDRO CHARGE** - so one screen gives the same code two names, one of
them the other product's.

The names come from the workbook's own Inventory Targets name column, read verbatim
(`importer.py:118-131`), where they are swapped; `data/seed/reference.json` stores
`"9713": {"name": "Finished Diesel"}` and `"DDDD": {"name": "Diesel Charge"}`. Every
other group on the screen names a product from `products[code].name`, which is right.

Conflicts with: rule 9 - an imported value stays visible next to the planner's edit.
The imported *value* is honoured here; the imported *label* is never checked against
the product it is attached to.

Fix: label control-limit rows from `products[code].name` like every other group, and
keep the workbook's own text beside it only if it is wanted. Collapse the members the
same way Tank capacity does.

Evidence: live-api (`/api/reference/target_ucl`, `/api/reference/tank_capacity`)

### F3 [S2] The dashboard draws the diesel pool and its members side by side
Where: Dashboards -> Diesel & heating oil
(api/main.py:646-700 `dashboard`, app.js `loadDashboard` / `chartCard`, app.js:525-580)

Planner sees: twelve chart cards in one section. `DDDD Finished Diesel` sits between
`9713 NO.2 DIESEL-HYDRO CHARGE` and `8170 #2 ONROAD DIESEL S15 UNDYED`, looking like
a thirteenth tank of its own.

Planner will: read the cards as twelve tanks and add them up.

Result: the same opening inventory and the same production are on screen twice. DDDD
and 8170 both open at **645,273 gal** and both take the same **3,316,405 gal** of
Production In over 180 days. Whatever else is true, those barrels exist once.

**Correction to an earlier draft of this finding.** It said DDDD "is a derived sum of
the ten member grades", quoting `model_config.py:20-23`. That is the config's claim,
and in the engine it is not true: DDDD carries its own balance rows and its End
Inventory diverges from the member sum by up to **2,620,847 gal** over 180 days. The
double count is real; the arithmetic relationship asserted for it is not. See F7 -
that divergence turned out to be the most important thing in this audit.

Fix: draw one finished-diesel series, not two. **Which one is not a UI decision** -
see F7.

Evidence: live-api (`/api/scenarios/75/dashboard?group=diesel&days=180`, and
`/api/scenarios/75/projection` for both blocks)

### F7 [S1] There are two finished-diesel demand rows, 2.6 million gallons apart, and the optimizer and the dashboard read different ones
Where: everywhere finished diesel appears - the screens read DDDD, the optimizer reads
the members (`modelprep.py:178-232` sums members and drops the pool; every screen goes
through `live_blocks`, which keeps both)

Planner sees: a dashboard and an alerts table built on DDDD's numbers, and run results
built on 8175's. Nothing on screen says they are different numbers.

Over 180 days on Plan 75:

| | Opening | Production In | Demand |
|---|---|---|---|
| DDDD (what the screens show) | 645,273 | 3,316,405 | **13,590,474** |
| Members summed (what the optimizer solves) | 645,273 | 3,316,405 | **16,211,321** |

Opening and production are identical. The entire **2,620,847 gal** gap is demand, and
it is **entirely on weekends**: weekday demand matches to the gallon (difference
exactly 0), while across 51 weekend days 8175 lifts 2,620,847 gal more than DDDD does.
The two rows carry the same monthly profile - the same distinct daily values, 70,000 /
84,275 / 88,000 / 95,924 / 125,000 - but DDDD drops to a reduced weekend rate (34 of
its days sit at 15,000) and 8175 lifts at the full weekday rate seven days a week.

Planner will: read the diesel position off the dashboard, then run the optimizer and
accept a schedule built for 19% more weekend lifting than the dashboard charged them
for.

Result: the optimizer is buying tankage and HYDRO time for weekend demand the
reporting screens never show. Neither number is flagged, and there is no screen on
which they can be compared. This also means F1's remedy is not neutral: collapsing the
screens onto the model's product silently *raises* reported diesel demand by 2.6M gal,
and collapsing onto the pool silently *lowers* what the optimizer plans for. **Someone
who knows the terminal has to say whether diesel lifts on weekends.** It is a question
about the plant, not about the UI.

Fix: settle the demand row first, with operations. Then collapse. The build must not
pick one by default - see the task document.

Evidence: live-api (`/api/scenarios/75/projection` for `Solvents:119` and
`Solvents:149`, full 180-day window, weekday/weekend split computed over both)

### F4 [S2] Eight diesel cards are empty grades badged green, and they pad "products clear"
Where: Dashboards -> Diesel & heating oil; Capacity & alerts -> the stats row
(app.js `chartCard`, app.js:557-575; `loadAlerts`, app.js:441-450)

Planner sees: `8135`, `8105`, `8136`, `8177`, `8120`, `8165`, `8125`, `8115` - eight
cards with a flat line at zero, peak 0, low 0, six of them reading "no capacity set",
each with a green `in bounds` pill. On the alerts screen they never appear, so they
land in `products clear` instead (`state.products.length - a.length`; the projection
picker holds 52 products, 12 of them diesel, and only 4 diesel rows reach alerts).

Planner will: count eight healthy diesel grades, and trust the "clear" number.

Result: `.pill.ok` means "fine" in the contract's visual language. Here it is used for
products that carry no material in any plan - the same green a genuinely healthy tank
gets. The eight are the dead half of the split described in F1.

Conflicts with: the visual language table in `conventions.md` - `.pill.ok` = fine.

Fix: drop them with the collapse in F1. If any must stay, an empty state saying "no
tank, no production, no demand" is the honest badge, not `in bounds`.

Evidence: live-api (dashboard + alerts + `/api/scenarios/75/products`)

### F5 [S3] Model inputs says two diesel products; four other screens say twelve
Where: Model inputs -> Tank capacity, against Dashboards, Capacity & alerts,
Inventory projection and the Stream sheet
(`reference_edit.py` `_capacities`, reference_edit.py:95-127 vs
`model_config.py` `live_blocks`, model_config.py:1425-1445)

Planner sees: Tank capacity lists exactly two diesel rows - `9713 NO.2 DIESEL-HYDRO
CHARGE` at 1,200,000 gal and `DSL Finished diesel` at 1,600,000 gal. Everywhere else
lists twelve.

Planner will: assume the two screens are describing different things, because nothing
says they are the same products.

Result: the reasoning for the collapse is already written, in `_capacities`' own
docstring: "listing the members offered five diesel tanks that no edit could reach -
none of them is a product the optimizer has - and offered no row for the pool that
is. A planner could change every diesel capacity on the screen and move nothing."
Every word of that applies to the dashboard and the alerts table too; the fix just
stopped at one screen. This is the root the four findings above grow from.

Fix: give the reporting screens the same product list. `live_blocks` is the one
function they all go through, so the collapse belongs beside it, not in each view.

Evidence: live-api + code-only

### F6 [S3] The "Diesel & heating oil" dashboard's only section is headed "Solvents"
Where: Dashboards -> Diesel & heating oil -> section heading
(app.js `loadDashboard`, app.js:550-554; sections keyed by `b["sheet"]`, api/main.py:678)

Planner sees: a group tab reading **Diesel & heating oil**, and directly under it a
section heading reading **Solvents · 12 products**.

Planner will: wonder whether they clicked the wrong tab.

Result: `sheet` is the workbook tab the block was pasted on, and all twelve diesel
blocks live on the Solvents sheet. `groups.py`'s own opening paragraph is about exactly
this - the workbook groups "by where the chart was pasted rather than by what the
product is" - and the group tabs were built to fix it. The section heading underneath
puts the workbook's filing back on screen.

Conflicts with: heuristic 1 - workbook filing shouldn't reach the screen when the app
has its own word for the thing.

Fix: hide the stream heading when a group has one section, or name the section for the
product family rather than the sheet.

Evidence: live-api

## 4. View inventory (diesel slice only)

| View | Diesel products shown | Where the list comes from |
|---|---|---|
| Capacity & alerts | 4 (the ones with a non-zero balance) | `live_blocks`, filtered to blocks with an issue |
| Dashboards | 12 | `live_blocks` ∩ `groups.PRODUCT_GROUPS["diesel"].codes` |
| Inventory projection picker | 12 | `live_blocks` |
| Stream sheet (Solvents) | 12 | `live_blocks` |
| Model inputs · Tank capacity | **2** (9713, DSL) | `_capacities`, members collapsed |
| Model inputs · LCL / UCL | 3 (9713, DDDD, 8175), names swapped | `_targets`, no collapse |
| The optimizer's model | **2** (9713, DSL) | `modelprep`, members collapsed |

All four reporting views are display-only; nothing on them saves. The Model inputs
rows save when you leave the box, globally, in every scenario.

## 5. Vocabulary

| The thing | Words on screen | Where |
|---|---|---|
| Hydrotreater charge, code 9713 | "NO.2 DIESEL-HYDRO CHARGE"; **"Finished Diesel"** | dashboard, alerts, projection, Tank capacity / **LCL and UCL** |
| The finished pool, code DDDD | "Finished Diesel"; **"Diesel Charge"**; "Finished diesel" (as `DSL`) | dashboard, alerts / **LCL and UCL** / Tank capacity |
| The family | "Diesel & heating oil"; "Solvents" | group tab / section heading under it |
| A member grade with no tank | "8175- Diesel"; "#2 NRLM DIESEL S15 DYED" | LCL and UCL / everywhere else |

Two codes share the name "Finished Diesel" on two groups of the same screen, and each
of them is also called by the other's job. This is the worst vocabulary break found so
far, because it is not two words for one thing - it is one word for two things that
sit at opposite ends of the same unit.

## 6. Decisions - all three settled on 2026-09-13

Answered by the user the same day. Recorded in `conventions.md` as rules 16, 17 and 18.

1. **The reporting screens show the model's products.** The collapse moves to
   `live_blocks()`, so Dashboards, Capacity & alerts, Inventory projection and the
   Stream sheet all show one charge (9713) and one finished pool (`DSL`, "Finished
   diesel"). The engine keeps every block for parity. Applies to any future
   aggregation, not only diesel. → fixes F1, F3, F4, F5.
2. **Control-limit rows are named from `products[code].name`,** the same source as
   every other group on the screen. Not by editing the seed file - re-importing the
   workbook would bring the swap back. → fixes F2.
3. **A dashboard section heading is dropped when the group has one section.** Sheet
   names stay only where a group spans several. → fixes F6.

## 7. What the build needs to cover

Scoped in full in [`docs/tasks/2026-09-13-diesel-collapse.md`](../tasks/2026-09-13-diesel-collapse.md),
which is blocked on the weekend-demand question in F7. The summary below is what that
document was built from.

Both collapses that exist today were written per-screen (`modelprep` for the solver,
`reference_edit._capacities` for Tank capacity). Moving it to `live_blocks()` means
checking that every caller can take it:

- `api/main.py` `dashboard` (sections, badges), `alerts`, `products`, the stream sheet.
- `groups.py` - the `diesel` group's twelve codes become 9713 plus the aggregate id,
  and `group_for()` needs to place `DSL`. An unplaced code falls into "Other", which
  `groups.py` designed so nothing vanishes silently - worth confirming it still does.
- `reference_edit._targets`, which has no collapse at all today.
- The capacity the pool carries is `AGGREGATIONS["DSL"].capacity_override`, 1,600,000
  gal, and its own note calls it "the aggregate's only invented number - confirm with
  operations". It is now the only diesel capacity on screen, so it is worth confirming.
