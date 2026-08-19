# Making the model want to run, and want to stay put

Companion to `MARGIN-OBJECTIVE.md`, which argues *why* the objective should be
profit. This one is the execution order, and it covers a second change that has to
land at the same time: the brake on changeovers.

They are written together because they are the same change. Separating them
produces a schedule that makes more money on paper than anything the plant can
run.

---

## 1. Why these two are one change

The objective today is all penalties (`optimizer/v0.py:856-870`). Nothing rewards
producing, so the model has no reason to run harder than the demand book requires —
and, just as importantly, **no reason to churn**. A changeover buys it nothing,
so it does not chase one.

That is the only thing currently holding campaign lengths together. The cash cost
of a changeover is zero: `switch_cost` defaults to `0.0` (`db/models.py:366`) and
nothing has ever set it. A changeover costs 0.125 days of unit time and nothing
else.

Flip the objective to margin and that accidental protection disappears on the same
day. Once every gallon earns its netback, the model will chase the highest-margin
feed day by day, and the only obstacle is an eighth of a day. The incentive to run
and the discipline not to churn are the same edit.

**`model_config.py:738-743` already predicted this**, in the entry for interface
material lost on a changeover:

> The model undercounts what a changeover costs, because it charges for the time
> and not for the giveaway. It will therefore switch somewhat more often than is
> truly economic, and campaigns may come out shorter than the plant would choose.

That is written about today's model, where switching earns nothing. Under margin
the same defect is levered by the full netback spread between two feeds.

---

## 2. The baseline of record

What the code does now, so the change can be scored against something.

| | Today | Where |
|---|---|---|
| Direction | `LpMinimize` by default; `LpMaximize` under `objective="margin"` | `optimizer/v0.py:202-203` |
| Serving demand | avoids a penalty | `v0.py:820` |
| Producing a gallon nobody ordered | worth exactly nothing | — |
| Downgrade | costs `value − landed value`, known to double-count | `v0.py:833-854` |
| Terminal stock | penalised at an invented price | `v0.py:729-742` |
| Changeover cash | per unit and per arc, every table empty so it still resolves to the scalar — which defaults to 0 | `v0.py:889`, `model_config.py:892-931` |
| Changeover time | 0.125 d, measured three ways | `modelprep.py:397-402` |
| Minimum campaign | real, but MEK / EXTRACT / ROSE only | `model_config.py:972-978` |
| Charge floor | 1.0 — charges pinned to the planner's schedule | `v0.py:118` |

The charge floor is the tell. It exists only because production earns nothing: at
zero the model shut the Platformer off and ran the plant at **34% of plan**
(`v0.py:113-114`). It is a prosthetic for the missing incentive, and its removal is
the acceptance test in section 7.

---

## 3. Step 1 — prices — **already done**

This section spent a long time describing work that had been finished before it was
written, and several later sections were built on that mistake. The prices are in.

`data/netbacks.json` carries **32 gross-profit figures supplied by operations**,
captured 2026-08-13 from `netbacks.xlsx`, keyed by *sales* code and translated to
the production codes the model tracks. Its own `missing` list is empty, and it is
right to be:

| | |
|---|---|
| tanked products priced | **32** |
| tanked products unpriced | 10 — every one carries **zero demand** |
| demand with a real price behind it | **100%** of the real book |

The ten unpriced products are exactly the streams operations confirmed cannot be
sold: the waxy neutrals 9117/9118/9119, the dewaxed oils 9302/9303/9305, deep
extract 9705, and three waxes with no demand. Unpriced is the correct answer for
them, not a gap.

Two demand codes have no price and should not have one. `DDDD` (5.5 M gal) is the
*derived* finished-diesel pool — the sum of ten grades the model deliberately
collapses into `DSL`, so pricing it would double count the same material. `9202`
(1.2 M gal) is retired: nothing can make it and demand is still booked against it.
Both exclusions are deliberate and documented in `data/reports/model-spec.md`.

**So the margin objective has been running on real operations netbacks all along**,
including every measurement in sections 5 and 5c. Those results are stronger than
they were presented as - the throughput gain is measured against real prices, not
placeholders. What is *not* settled is `terminal_value_fraction`, which is not a
price at all (section 5c), and the reprice-the-baseline check below.

---

## 4. Step 2 — the guardrail does not work, and here is why

`MARGIN-OBJECTIVE.md:129-131` proposes: set every product and sink netback to the
same value, and the schedule should not move. It was implemented
(`flat_netback_per_gal`) and it **does not hold — and should not be expected to.**
The test is invalid, not the objective.

Two reasons, both structural:

**Flat prices invert the processing economics.** Every unit converts feed into
product at a yield below one. If a gallon of feed and a gallon of product are worth
the same, then processing destroys value by exactly the yield loss, so the model's
best move is to run as little as possible and sell the feed. That is the opposite of
what real netbacks produce, where the product is worth more than the feed. A flat
price does not hold the problem constant; it reverses the sign of every conversion
in the plant.

**Flat prices make routing a tie.** Downgrading earns the sink's netback, which
under a flat price is the product's netback, so every routing decision is worth the
same and the solver breaks ties arbitrarily. Measured: downgrade went from 1.2 M gal
to 8.4 M gal with no change in what the model was really being asked.

The one thing the flat run *did* catch was a genuine bug, and it is worth recording
how. The first implementation flattened prices at each use site and missed
`lost_cost`, so lost sales were priced on measured margins while everything else was
flat — and the model shorted **7 million gallons**. Flattening is now applied once,
where `value_of`, `sink_value` and `netbacks` are built, so nothing downstream can
disagree. A price override that reaches some prices and not others is not a
guardrail, it is a fourth objective nobody designed.

**What to use instead.** The second guardrail from `MARGIN-OBJECTIVE.md:132-135`
survives and is the one to lean on: score the planner's own schedule under the new
objective and show finance revenue, crude bill and margin. An implausible absolute
margin means the netbacks are wrong, and that is far easier to see in dollars than
in gallons. It needs real prices — which exist (section 3), so **this check can run now.**
It is the last thing standing between the margin objective and being believed, and
it needs no new data, only the scoring code to report revenue, crude bill and
margin separately.

---

## 5. Step 3 — the flip — **built, opt-in, not yet trustworthy**

Live behind `objective="margin"`; `"cost"` remains the default and is byte-for-byte
the model it always was, pinned by
`test_cost_is_the_default_and_margin_is_opt_in`. Both objectives are built from the
same variables and the same balance, so a scenario can be scored under each and the
two compared — which is the point of keeping them side by side rather than
replacing one with the other.

**It does what it was built to do.** Over 42 days, every run proven optimal,
margin charges more than cost at every feasible charge floor — and the gap widens
as the floor tightens:

| charge floor | cost (bbl) | margin (bbl) | difference |
|---:|---:|---:|---:|
| 0.30 | 594,089 | 651,190 | +9.6% |
| 0.50 | 593,538 | 661,854 | +11.5% |
| 0.75 | 596,047 | 682,890 | +14.6% |

Throughput chosen rather than obliged, because production is finally worth
something. Pinned by `test_margin_mode_solves_and_wants_to_run`.

**And it is shaped by a number nobody had measured.** See section 5c.

Three mechanics, per `MARGIN-OBJECTIVE.md:46-51`.

**Maximise revenue; report profit.** Crude rate is a fixed input
(`CRUDE_RATE_IS_INPUT`, `model_config.py:623-635`), so the crude bill is a constant, and a constant cannot
change which schedule is best. Maximising revenue at netbacks has the same argmax
as maximising profit, and avoids charging $1.6667/gal of crude against five joint
products separately — which would make almost every route the plant runs daily
look unprofitable. Profit = revenue − crude bbl × price, reported separately.

**Value terminal inventory at its own netback.** This is the term that does the
work. Serving demand only ever pays the model to cover the book; valuing the tank
is what makes a gallon nobody has ordered *yet* worth producing. Without it, "run
more" pays only where there is demand, and the objective is still
serve-the-book with extra steps.

This is safe here for a specific reason: **tank capacity is already a hard upper
bound on inventory, every product, every day** (`v0.py:293`). Absent that ceiling,
valuing terminal stock would run the plant flat out and bank forever. With it, the
model runs harder only where there is somewhere to put the output — which is
exactly the question worth asking.

**Intermediates are worth nothing at the end, and that is a rule, not a gap.**
The unpriced products are exactly the work in progress — MEK's waxy neutrals
9117/9118/9119, the dewaxed oils 9302/9303/9305 that extraction eats, and deep
extract 9705, carrying 1.36 M gal of opening inventory between them. The fallback
priced them at the lost-sale margin, $1.50/gal, against finished diesel at $0.80
and diesel charge at $0.35 — **work in progress valued at nearly double the
finished product it becomes**, so the model booked a profit for not converting it.

Operations settled it, and more firmly than "not measured yet": **the dewaxed and
waxy feeds cannot be sold at all.** Waxy medium neutral does sell, but there is no
additional outlet, so an extra gallon in that tank cannot be turned into money
either. Zero is the correct price, not a placeholder.

That also gives the plant's own rule for free — *push to the finished-good tanks
rather than hold upstream when there is spare capacity, but never at the expense of
real demand*. Holding intermediate earns nothing; converting it earns the finished
product's terminal value; serving real demand earns the full netback, strictly more
than either. The ordering falls out of the prices and needs no constraint of its
own.

Worth recording that the measured effect of this fix alone was small — lost sales
unchanged, throughput moved 260 bbl. It was wrong and is now right, but it was not
what was distorting the answer. The horizon artefact in section 5b was.

**What disappears — and why `dg_cost` needed no replacement.** Every sink lands in
a product that is *itself* tanked with unlimited offtake (`SINK_LANDS_IN` against
`spec.products`: 8201, 8221, 9713 and DSL are all tanked). So a downgrade never
leaves the system — it moves material between tanks, and the gallon earns later,
when the landing product is sold or serves its own demand.

That means **downgrades carry no term in the margin objective at all.** Pricing
them would pay for the same gallon twice, which is exactly the double-count
`dg_cost`'s docstring confesses to. The cost of downgrading is not priced, it
*emerges*: the landing product's netback is lower, so the gallon simply earns less.
This is what `MARGIN-OBJECTIVE.md` means by "every gallon earns exactly once,
wherever it ends up, and this function disappears" — and it is a stronger result
than expected, because it needs no new price to be collected for any sink.

Also gone: `terminal_shortfall_per_gal`, which has been standing in for safety
stocks that exist nowhere in the workbook.

---

## 5a. Every run at defaults was infeasible, and said `feasible`

Found while checking whether the throughput comparison survived a longer solve. It
did not need a longer solve — it needed a valid one.

CBC was returning **`Infeasible`**. The status handling reclassified it to
`feasible`, attached the message *"time limit reached after 0s"* to a solve that
took a tenth of a second, and returned a schedule. The test it used was "do all the
variables carry values?" — and they do: CBC hands back the last relaxation it was
holding when it proves infeasibility. Seeding is not evidence of an incumbent.

The blast radius:

- **The shipped default charge floor was 0.8, which is infeasible over 42 days.**
  Every run at defaults came back this way, with a schedule that satisfies none of
  its own constraints and nothing downstream able to tell — the referee replays
  whatever it is handed.
- Every number in the first draft of sections 5 and 5b was measured on infeasible
  models and has been re-measured.
- `models.py` recorded that a floor of 1.0 was infeasible. It is, and so is
  everything from 0.80 up; only the 1.0 case had been noticed, because that was the
  one someone tried deliberately.

**The feasible ceiling is a function of the horizon**, which a single default
cannot express. Measured under both objectives:

| horizon | highest feasible charge floor |
|---:|---|
| 42 days | 0.75 |
| 60 days | 0.75 |
| 100 days | **0.50** |
| 160 days | **0.50** |
| 366 days | **none — infeasible at every floor, including 0.00** |

The default is **0.30**, the only value that holds everywhere. Raising it buys
nothing in the bargain: at 100 days, 0.30 against 0.50 charges 1,417,480 bbl
against 1,416,167 and loses exactly the same 3,030,265 gal. The floor stops
binding well below the ceiling, so the headroom is not worth spending.

### The horizon is bounded by the data, and it is now enforced in code

366 days is infeasible at a floor of *zero*, so the floor is not the cause. The
elastic diagnostic names it immediately: **tank capacity**, 967 violations, worst
of them 9117 waxy medium neutral overflowing by 9.8 M gal/day.

The cause is upstream of the model. **The planner's charge schedule ends
2026-12-31** — for MEK, extraction, ROSE and the hydrotreater alike — about 161
days into a 366-day scenario. Only the Platformer is scheduled beyond it. Crude is
a fixed input and keeps running, so past that date the side streams keep arriving
with no unit charging them, and the feed tanks fill until they burst. 9117 is one
of the products operations confirmed has no outlet at all, so nothing can relieve
it.

**Confirmed with operations: the demand inputs are wrong past 2026-12-31.** So the
366-day numbers above were never a finding about the model — they were the model
answering a question the workbook cannot support. Nothing in the data announces
that boundary: the workbook has columns for all 366 days, and three different
things end at three different points.

| ends | what |
|---|---|
| 2026-10-15 | last firm Open Order — 57 days; forecast beyond |
| **2026-12-31** | **last day the planner filled in a charge — the boundary** |
| 2027-07-17 | extent of the Charge Schedule date columns |

Enforced now in code rather than remembered: `DATA_VALID_THROUGH` in
`model_config`, applied in `v0.solve` where every caller passes through. A horizon
that runs past it is cut back and the result says so
(`kpis["horizon_truncated_days"]`); one entirely past it is refused outright.
Silent truncation is normally the wrong instinct, but the alternative here is a
confident schedule built on demand nobody stands behind.

**The practical limit is two days shorter: 160 days, ending 2026-12-29.** The 161st
is infeasible for a far smaller reason than the full-year case — the elastic
diagnostic returns exactly *one* violation, 9103 platformer charge overflowing its
tank by 81,759 gal on 2026-12-30. An edge-of-window effect on a single product, not
a structural break, and worth knowing before anyone hunts for a deep cause. It is
also why every report in `data/reports` is named `*-160`: the edge was found
empirically long before it was explained.

Within that window, floors of 0.30 and 0.50 both solve to proven optimal at every
horizon up to 160 days.

Fixed by gating on the solver's status rather than on whether variables have
numbers — only CBC's "not solved" means "stopped early with something worth
keeping". Pinned by `test_an_infeasible_model_is_never_reported_as_usable`.

The throughput result survived re-measurement, which is the only reason section 5
still makes a claim.

**This was not the end of it.** The status was wrong in a second, independent way -
PuLP rewrites CBC's "stopped on time limit" into `Optimal` - so "optimal" could not
be trusted either. See section 5b; the timings and guarantees live there.

---

## 5b. "Optimal" was not optimal — PuLP rewrites the solver's status

A second status defect, found by asking why a 100-day run consumed its entire
500-second limit and still called itself optimal. It is upstream, in PuLP, and it
invalidated **every v1 result this project has ever labelled optimal.**

`pulp/apis/coin_api.py`, `get_status`:

```python
if status == LpStatusNotSolved and len(statusstrs) >= 5:
    if statusstrs[4] == "objective":
        status = constants.LpStatusOptimal            # <- overwritten
        sol_status = constants.LpSolutionIntegerFeasible   # <- the truth
```

When CBC writes *"Stopped on time limit … objective X"*, PuLP **rewrites the status
to `Optimal`** and records what actually happened in a second field, `sol_status`,
which `LpProblem.solve()` never returns. Read the field `solve()` gives you — as
this code did — and every time-limited run claims to be proved best.

Measured at 100 days, v1, before the fix:

| time limit | `LpStatus` | `sol_status` | objective |
|---:|---|---|---:|
| 5 s | Not Solved | **No Solution Found** | 8,011,867 |
| 20 s | Optimal | **Solution Found** | 10,095,452 |
| 60 s | Optimal | **Solution Found** | 10,095,452 |
| 200 s | Optimal | **Solution Found** | 8,114,383 |

`sol_status` never once says *Optimal Solution Found*. And on a minimisation the
200 s answer beats the 20 s answer by 20% — so a schedule **20% worse than
achievable was being reported as the best there is**. The 5 s row is the other
half of the same defect: CBC stopped with *no integer solution at all*, and the
objective shown is a bound, not a schedule.

Fixed by mapping `sol_status`, which distinguishes all five outcomes that
`status` collapses. Pinned by `test_a_time_limited_run_is_never_called_optimal`.

**What the two labels now mean**, and the difference is not a nuance:

- **`optimal`** — proved within `mip_gap`. At 0.02 that is a real guarantee: no
  schedule is more than 2% better.
- **`feasible`** — stopped early. **No bound whatsoever.** Measured 20% off.

### The `mip_gap` is the whole cost, and 0.01 buys nothing

| gap | status | time | objective |
|---:|---|---:|---:|
| 0.01 | **feasible** — no bound | 502.2 s | 8,114,383.81 |
| 0.02 | **optimal** — proved | 87.4 s | 8,116,383.81 |

Tightening to 0.01 costs **5.7× the runtime and the guarantee**, and gains 0.02% of
objective. It cannot close the gap in 500 s, so it stops early and reports an
unbounded incumbent; 0.02 closes it in 87 s and comes with a proof. This is the
single highest-value setting on the model and it was set against itself.

**Timings, all genuinely proven:**

| model | horizon | gap | status | time |
|---|---:|---:|---|---:|
| v0 | 42 d | 0.02 | optimal | 0.2 s |
| v0 | 100 d | 0.02 | optimal | 0.8 s |
| v1 | 42 d | 0.02 | optimal | 19 s |
| v1 | 100 d | 0.02 | optimal | 87 s |

Horizon is cheap; binaries are not. v0 does 100 days in under a second, v1 needs 87
— and v2 frees the hydrotreater, which has 36 observed transitions and no minimum
campaign length to prune the search. Expect it to be materially worse, and expect
the per-unit changeover cost from section 6 to be the only lever that tightens it.

---

## 5c. The number that decides the answer, and what operations said about it

Valuing terminal stock is what makes production worth something. It is also, by
some distance, **the weakest input in the model** — because it is not really a
price. It stands in for everything after the last day, which the model cannot see.

`terminal_value_fraction`, measured over 42 days at a 0.75 charge floor — the
highest that is feasible — every run proven optimal. The cost objective on the same
scenario loses 1,172,658 gal and charges 596,047 bbl:

| fraction | lost sales | downgrade | sink offtake | charge (bbl) |
|---:|---:|---:|---:|---:|
| 0.00 | 618,868 | 6,086,900 | 3,962,985 | 634,044 |
| **0.25** | **854,224** | 5,152,079 | 3,501,021 | **657,571** |
| 0.50 | 1,626,964 | 3,720,088 | 2,514,814 | 682,890 |
| 0.75 | 2,921,754 | 1,284,689 | 1,510,003 | 647,543 |
| 0.90 | 3,024,625 | 1,284,689 | 1,527,799 | 649,537 |

Monotone, and it moves lost sales by a **factor of five**. Priced high, holding
stock rivals shipping it and the plant hoards; priced at zero, every tank must be
empty on the final day and it dumps. Throughput barely moves across the whole
range — so this parameter decides *where the material goes*, not how much is made.

Two things follow.

**The 0.9 in this document's first draft was wrong.** It was borrowed from
`terminal_cost_for`'s cap, which exists for a different purpose — keeping a
shortfall cheaper than a lost sale — and at 0.9 the margin model shorts 3.0 M gal
to hold stock, worse than the cost objective it replaces.

**0.25 is now the default, and operations chose it, not the midpoint.** The rule
given is: *run to the finished-good tanks rather than hold upstream when there is
spare capacity, but never at the expense of real demand.* A low fraction is that
rule expressed as a price — holding earns little, so converting and shipping win.
At 0.25 the plan loses **27% fewer gallons than the cost objective and charges
10.3% more**, which satisfies both halves of the rule at once. Nothing above 0.5
does: there the model shorts more demand than the cost objective did, to hold
stock, which is the second half of the rule violated outright.

**A better fraction is still not the fix.** The hoarding is not a pricing error, it is a
horizon artefact: stock held past day 42 has no demand to meet because the window
ends where the data ends, not where the plant stops caring. The real answers are a
rolling horizon or a safety stock per product — which is what
`terminal_shortfall_per_gal` was always standing in for. Until one of those exists,
**treat any single margin run as one point on a curve**, and quote the curve.

---

## 5d. Tanks running to empty — a floor exists now, and it is off

Reported from a run: the model runs finished goods to empty. It does, and the
first job was working out which part of that is the optimizer's doing. Scored per
*block* it looks enormous — DSL alone shows 7.1 M gal lost and 801 days at zero —
but that is an artefact: the model pools ten diesel grades into `DSL` while the
simulator keeps the blocks apart, so demand sits on members with no production.

In **model space** it is smaller and confined to three products:

| product | lost | of demand | plan loses | verdict |
|---|---:|---:|---:|---|
| 9511 Platformate | 1,785,644 | 7,500,000 | **7,500,000** | data gap; optimizer *improves* it 76% |
| DSL finished diesel | 999,640 | 7,100,000 | — | genuine |
| 1128 Isomerate | 244,981 | 2,500,000 | **0** | genuine, and the optimizer's own |

9511 needs no model change: the plan makes *zero* platformate because the reformer
row was never carried in the workbook, and the optimizer runs the flow-through line
and cuts the loss by three quarters. That is a data gap the model is already
papering over.

**The root cause of the rest: nothing constrains the path.** The terminal condition
pins the last day against the first and says nothing about the 98 in between, so a
tank may sit at zero for weeks and satisfy every constraint. Isomerate spent 41 of
100 days empty because at $0.50/gal it is the cheapest thing in the book to short.

### What was built

A per-product minimum inventory, elastic, priced per gallon-day at a fraction of
that product's **own** lost sale and spread across the horizon — so a gallon held
below the floor for the entire window still costs less than shorting a customer
once. That ordering has to hold at every price level, which a flat figure cannot
promise when margins run from $0.50 to $6.00; it is the same failure
`terminal_shortfall_per_gal` was tuned away from.

Elastic is not a preference here. 9511 has no production at all in this data, so a
hard floor on it would make the model infeasible for a reason with nothing to do
with the schedule.

The floor is **days of cover on measured demand**, because that is the honest shape
of what is known — `RefKind.TARGET_LCL` has had a slot for a real lower control
limit since the schema was written and the workbook sets none for any product.
`SAFETY_STOCK_GAL` overrides per product so the first real figure can land without
waiting for the rest.

### It ships off, and the measurement says why

Measured as **exact LP optima** — no freed units, reactor flush off, `mip_gap` 0 —
because at the 2% gap in normal use every difference this knob makes is smaller
than the gap itself. An earlier pass through this section reported that two days
of cover "recovered 43,705 gal of diesel". **That was gap noise and it is
withdrawn.** Solved exactly:

| days of cover | 42 days | 100 days |
|---:|---|---|
| 0 | 1,178,108 | 3,030,265 |
| 2 | 1,178,108 | 3,030,265 |
| 3 | 1,178,108 | 3,030,265 |
| 5 | 1,178,108 | 3,030,265 |
| 7 | 1,194,341 (**+16,233**) | 3,030,265 |

Bit-identical everywhere except one cell, and that cell is the constraint making
things *worse* — seven days of cover at 42 days creates 16,233 gal of lost
isomerate, because stock held is stock not shipped.

**So as priced, the floor changes nothing.** It is soft, and its penalty is
deliberately far below a lost sale, so the model simply absorbs the penalty rather
than rearranging the plan. Making it bite would mean either a hard floor — which
9511 cannot survive, having no production at all — or a penalty that outranks
service, which is precisely the failure this pricing was designed to avoid.

That is a real result rather than a disappointing one: **the tanks are not empty
for want of a buffer.** 1128 is short because the model *chooses to make less
isomerate* — production earns nothing under the cost objective, so the cheapest
gallon in the book is the first to go. No inventory floor can conjure material the
model declined to produce. The fix is section 5's margin objective, not this one.

The mechanism is kept because it is correct and will matter the moment a real
lower control limit exists, or someone wants a hard floor on a product that can
support one. It ships inert, exactly as `SWITCH_COST_BY_UNIT`, `SAFETY_STOCK_GAL`
and `MIN_RATE_FRACTION` do. It also costs real solve time when on. On the live
model at 100 days, for a bit-identical answer each time — same total, same
per-product split:

| days of cover | lost sales | solve |
|---:|---:|---:|
| 0 | 3,030,265 | 129 s |
| 3 | 3,030,265 | 293 s |
| 5 | 3,030,265 | **556 s** |

Four times the search to arrive back where it started.

---

## 6. Step 4 — the brake

Three levers. Two are built, one has never been switched on.

| Lever | Status | Character |
|---|---|---|
| Changeover **time** — 0.125 d off the day budget | Built, measured three ways | Implicit |
| **Minimum campaign length** — `MIN_RUN_DAYS`, enforced `v0.py:659-663` | Built; unavailable on HYDRO | Structural |
| **Cash per changeover** — `switch_cost` | Built per unit and per arc; every table empty | Explicit |

### Prefer the structural lever

A minimum campaign length is a fact about the plant. A switch cost is a guess.

The difference is academic under a minimise objective and decisive under a
maximise one: a cash penalty is *traded against* a real margin spread. If the feed
in front of the unit is worth $3/gal less than the one next to it, the model pays
the switch cost and changes over anyway. A min-run constraint cannot be bought out
at any price.

So the structural lever carries the load, and the cash term trims what is left.

### HYDRO has no structural lever available, and cannot be given one

`MIN_RUN_DAYS` (`model_config.py:972-978`) covers MEK, EXTRACT and ROSE. HYDRO is
absent, and the first instinct — fill it in before v2 frees the unit — turns out to
be impossible. **All twelve HYDRO charges have an observed minimum campaign of one
day** (`data/reports/transition-analysis.md`), and `min_run_days` already returns 1
as its fallback. An entry for HYDRO would be a literal no-op.

That is not a data gap to be closed later. The plant genuinely runs the
hydrotreater in one-day campaigns — 93 of its 98 changeovers are back to back, more
than the other three units combined — so there is no minimum to discover.

The consequence is the reason the cash lever had to be built. **On HYDRO the
per-unit changeover cost is the only brake that exists.** It is also the unit where
the brake is least able to hold: a cash penalty is bought out by a wide enough
margin spread, and HYDRO is the unit whose freedom v2 is about to open up. Expect
to calibrate it there first and to distrust the result longest.

### Settled: ROSE's minimum is 3 days, and the 20-day measurement is emergent

`MIN_RUN_DAYS["ROSE"]["4313"]` is 3 days while the plan never runs 4313 for fewer
than 20, median 41 — which looked like the table being wrong by a factor of seven.
It is not. **Operations confirmed the rule is three days on 4555 (KENDEX 0897).**
4313 is the c-stock, it carries most of the unit's days, and it runs long because
the economics ask for it, not because anything forbids a short run.

Recorded against the table itself (`model_config.py:955-978`) so it is not
"corrected" to 20 later, because that would be the exact mistake this section of
the document warns about: **raising a minimum to match observed behaviour hard-codes
an outcome the model is supposed to derive.** A structural constraint has to be a
rule, not a habit. The 20 days is what the model should be *able to reproduce* from
prices — and if it cannot, the prices are what to look at.

Two smaller things settled with it. ROSE runs essentially continuously, and the
absence of idle days carries no information either way — it is not a shape to
reproduce. And the workbook's charge line for 4555 is labelled "LR RESINS", which
is product 9210's name; 4555 is KENDEX 0897. Cosmetic, since everything keys on the
code, but it is why the generated reports read "4555 LR RESINS".

### The cash lever is now per unit and per arc — **built**

`model_config.py` gives the calibration method, and it is the right one:

> run v1 at zero and raise it **per unit** until campaign lengths match the ones
> the plant actually runs (MEK 3 d median, HYDRO 1 d, EXTRACT 3 d, ROSE 40 d).
> That calibrates against observed behaviour rather than a quoted number.

Until now that could not be executed. `switch_cost` was a single global `Float`
applied uniformly to every arc, and ROSE at a 40-day median cannot share one number
with HYDRO at 1 day — the value that gives ROSE its campaigns freezes the
hydrotreater solid.

**This was a regression against the formulation, not a missing feature.**
`MIP-FORMULATION.md:666` specifies the term as `c_sw[u,(p,q)]` — indexed by unit
*and by arc*. Per-arc matters independently: the reactor-flush transitions cost more
time than an ordinary switch, and the giveaway that time does not capture scales the
same way, which is why `changeover_loss_days` was already per-arc on the time side.
The cash side now matches it.

| Piece | Where |
|---|---|
| `SWITCH_COST_BY_UNIT`, `SWITCH_COST_BY_ARC`, `switch_cost_for()` | `model_config.py:892-931` |
| Per-arc pricing, built where the unit and both feeds are in scope | `v0.py:512, 530-533` |
| Objective term, per arc instead of one constant × a count | `v0.py:889-890` |
| `switch_cost_by_unit` scenario override | `db/models.py`, `db/session.py` (additive migration), `db/optimizer_service.py` |

Precedence is most-specific-wins, matching `max_rate_info` and
`min_run_days_for_line`: **the arc's own figure, then the unit's, then the global
scalar** — with the scenario's override beating the committed table at the same
specificity, so a planner calibrating against campaign lengths never has to edit
code.

Two properties worth keeping true:

- **Every table ships empty**, so an unconfigured model prices changeovers exactly
  as it did before — the objective lands on the same number, not merely a close
  one. Pinned by `test_no_per_unit_cost_reproduces_the_single_scalar`.
- **A same-feed arc is not free.** `changeover_loss_days` returns zero when
  `frm == to`, and copying that here would have priced extraction's normal→deep
  mode flip at nothing, so the model would have flipped it daily. Arcs run between
  distinct *lines*, so an arc is always a real changeover. Pinned by
  `test_changing_mode_without_changing_feed_still_costs`.

Measured effect at the test scenario (42 days, `switch_cost` $2,000, charge floor
0.3), pricing MEK's changeovers at $50,000 and leaving extraction on the scalar:

| | MEK switches | MEK median campaign | EXTRACT switches |
|---|---:|---:|---:|
| Scalar only | 12 | 3 d | 7 |
| MEK priced separately | 6 | 4 d | 9 |

Extraction switching *more* is the mechanism working, not a defect: MEK holding a
campaign changes what arrives downstream, and relieving that is what the model is
for. A global scalar cannot produce this — it moves both units together.

### Crossing the hydrotreater's reactors was free — **fixed**

Reported from a run: the hydrotreater switches between its two reactors too much.
It did, and the cause was the same shape as the changeover cost — a real cost that
existed everywhere except in the model.

A reactor crossing costs a **0.25-day flush**, measured and confirmed. But the
flush lives in the arc formulation, and **only freed units get that.** The
hydrotreater's assignment is fixed, so its day budget was the plain
`Σ charge/rate ≤ 1` with no flush term at all. Crossing cost nothing.

Compounding it, `HYDRO#76` — the diesel charge draw — is flow-through on **R2**,
so it may run on any day between zero and its ceiling, including days R1 is
running. The model took that freedom at no charge:

| | reactor start-ups | days running both |
|---|---:|---:|
| plan | 13 | 1 |
| before | 13 | **5** |
| **after** | **13** | **0** |

Lost sales are identical at 3,030,265 gal, so **the discipline costs nothing in
service** — it was pure waste.

**Start-ups, not same-day overlaps, are what to charge.** Only 1 of the plan's 13
crossings has both reactors inside one day; the rest happen at a day boundary, so a
same-day test would miss twelve of them.

**The `== 1` is what gives it teeth**, and this is worth recording because the first
attempt shipped without it. An indicator bounded below by production and above by
nothing can be switched on for free, so the model simply held both reactors "on"
permanently, never registered a change, and paid no flush. It read as a
3-crossing *regression* that was really solver noise inside the 2% gap — a
constraint that looks present, costs binaries, and does nothing.

Two things this is not. It forbids running both reactors in one day, which the plan
does once in 91 — the same class of approximation as "at most one changeover a day"
already accepted for freed units, and it should be stated rather than discovered.
And it makes v0 a MIP on any unit with two reactors, which is why
`charge_reactor_flush` exists: off, v0 is exactly the LP it was, so "v0 is an LP"
stays a claim anyone can check.

### v2: the hydrotreater schedules itself, in two stages — **built**

Freeing all three units at once does not solve. Measured, `mip_gap` 0.02:

| horizon | all three | MEK+EXTRACT | HYDRO alone |
|---:|---|---|---|
| 7 d | optimal, 116 s | | |
| 10 d | **no proof** | | |
| 14 d | **no proof** | | |
| 21 d | **no proof** | optimal, 9 s | optimal, 159 s |
| 42 d | **no proof** | optimal, 11 s | optimal, 47 s |
| 100 d | | optimal, 130 s | optimal, 170 s |

Seven days is the largest window the joint model can prove, and that is useless —
shorter than a single ROSE campaign, so a rolling horizon built on it would decide
a 40-day campaign a week at a time. **That is why the decomposition is by unit and
not by time.**

The hydrotreater is the hard one for a reason the data already said: 7 charge lines
against MEK's and extraction's 4, 42 transitions against their 6 and 9, and — unlike
them — no minimum campaign anywhere, because every one of its charges is observed
running for a single day. There is nothing to prune the tree with.

**Three things tried that did not fix it**, recorded so nobody repeats them:

- **Relaxing the arcs to continuous.** Correct and kept: the setups sum to one and
  the arcs carry flow between two unit vectors, so a vertex is already integral —
  confirmed by 2,394 arcs returning none fractional. It removes ~80% of the
  binaries and still does not make v2 provable.
- **A real changeover cost on the hydrotreater.** Does its job — switches fall 9 → 6
  over 14 days — and the gap still will not close.
- **A shorter horizon.** Only 7 days proves, which is not a horizon.

**What works: solve the cheap units, hold their answer, then solve the
hydrotreater against it.**

| | 42 days | 100 days |
|---|---|---|
| stage 1 — MEK + EXTRACT | optimal, 9.6 s | optimal, 241 s |
| stage 2 — HYDRO, pinned to stage 1 | optimal, 61 s | optimal, 430 s |
| **total** | **71 s** | **677 s** |
| objective against v1 | 2,784,182 → **2,675,894** (−3.9%) | 8,165,785 → **8,074,551** (−1.1%) |

Stage two is **pinned** to stage one, not floored at it — the charge floor would let
it take 70% of a settled decision back and the two answers would not compose. Pinned
with a hair of tolerance rather than a flat equality: stage one drains 9302 to
exactly zero on 2026-08-02, and an exact pin makes the balance infeasible by a margin
the elastic diagnostic reports as literally **zero**.

**The guarantee is named, because it is weaker than the word suggests.** Each stage
is optimal *given the other's decisions*; the pair is not a joint optimum, and
`kpis["globally_optimal"]` says so on every result. The joint upper bound that would
measure the gap is not obtainable at any horizon worth planning — so the choice is
not between this and the true optimum, it is between this and no answer.

### The fourth lever, currently unimplemented

`MIP-FORMULATION.md:667` also specifies *"a small deviation-from-current-plan term
so the plan does not churn nightly."* Nothing implements it. It is a different
kind of discipline from the other three — it stabilises the plan **across runs**
rather than lengthening campaigns **within** one — and it becomes more valuable,
not less, once the objective is nearly flat across many schedules, which
`v0.py:874-876` already observes it is. Worth building at v5, when the model starts
running nightly; not needed for this change.

### Calibrate to behaviour, not to a quoted cost

Match the observed campaign-length distribution per unit — MEK 3 d, HYDRO 1 d,
EXTRACT 3 d, ROSE 40 d. This asks operations to measure nothing new, which is the
point: the interface giveaway that `switch_cost` is standing in for is real but
unmeasured, and each charge product splits differently between interface returned
to charge and interface genuinely slopped off (`model_config.py:731-737`).

---

## 7. Step 5 — remove the charge floor

**Drop `charge_floor_fraction` from 1.0 and the schedule should not move.**

This is the test that the whole change worked, and `MARGIN-OBJECTIVE.md:83-87`
names it as such. The floor exists because production earned nothing. Once it
earns, the model should want to run on its own — and if it does not, the netbacks
are wrong or the objective is not describing the plant.

Run it as an experiment before deleting anything.

---

## 8. Sequence

| # | Step | Gate to the next step |
|---:|---|---|
| 1 | ~~Netbacks~~ — **done**: 32 operations figures, 100% of real demand priced | — |
| 2 | ~~Equal-netback guardrail~~ — invalid, see section 4. Use the reprice-the-baseline check instead, which needs step 1 | Finance recognises the margin |
| 3 | ~~Flip to maximise~~ — **built** (`objective="margin"`), opt-in, cost still the default | Solves; charges 10.5% more than cost |
| 4 | Calibrate the per-unit changeover cost, one unit at a time (the mechanism is built; the tables are empty) | Campaign medians match observed |
| 4b | **Settle `terminal_value_fraction`** — or replace it with a rolling horizon or safety stocks (section 5b) | A margin run stops being one point on a curve |
| 5 | Drop the charge floor | Schedule does not move |

**Nothing here is blocked on data.** Step 1 was finished before this document was
written — 32 operations netbacks, 100% of the real demand book priced — and several
paragraphs of this file were built on the wrong assumption that it was pending. Every
margin measurement recorded here was already running on those real prices.

What remains is judgement and scoring, not collection: **step 4b**
(`terminal_value_fraction`, which is not a price and is still the number most likely
to embarrass a margin run shown to anyone) and the reprice-the-baseline check in
section 4, which needs only the code to report revenue, crude bill and margin apart.

**Step 3 is deliberately run with the brake still off.** The extra changeovers the
flip introduces on its own is the number that sizes the brake in step 4; setting a
switch cost first hides it, and there is then no way to tell a well-calibrated
brake from an over-tight one.

**Doing 5 before 4 is the failure mode.** Remove the floor while switching is still
free and one unusable schedule is replaced by another — this time one that reports
a profit.

---

## 9. What can still go wrong

**Sinks priced too high.** `MARGIN-OBJECTIVE.md:143-146`: sink netbacks must sit
strictly below product netbacks, or the model downgrades profitable material on
purpose. Under maximise that is revenue, not a forgotten penalty, and it will read
as a bug when it is not one. Assert it as an input check rather than discovering it
in a schedule.

**Blend demand stays fixed.** The model still cannot decline a blend when a
component is short, and it still cannot tell blend pull from ordinary forecast
(`model_config.py:715-728`), so a short product may be shorted on the blend rather
than on the forecast — the wrong way round. Revaluing components does not fix
either. Independent of this change.

**Crude stays an input.** Margin does not change that. Crude margin dwarfs
everything downstream, so a model free to cut crude would still relieve every
inventory problem by making less oil — the cheapest answer in the model and the
most expensive one in reality.

**Acceptance moves.** Today the bar is losing less than the baseline's 6,673,800
gal of lost sales and 5,595,214 gal of downgrade (`MIP-FORMULATION.md:784-787`).
After this change it becomes the one that was always wanted: **the optimised
schedule must make more money than the plan**, at a changeover count planners judge
operable. Both halves of that sentence are load-bearing.
