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

## 3. Step 1 — prices

Netbacks in $/gal for the **11 products that carry 80% of demand**, plus the four
sinks (`MARGIN-OBJECTIVE.md:100-117`). Start there rather than waiting for a
complete list of 27.

The ratios matter more than the levels. A uniform error in every netback changes
the reported profit and not the schedule; an error in one product *relative to the
others* changes the schedule. So an approximate number for all eleven beats an
exact number for three.

Two sinks (#6 oil, cat cracker) are currently modelled as crude less $0.50. Diesel
and gasoline have never had a price at all.

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
in gallons. It needs real prices, so it cannot run early — which means **the flip
cannot be validated before the netbacks arrive** after all. Step 1 is on the
critical path in a way this document previously denied.

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

**The feasible range**, measured under both objectives: **0.30 through 0.75 solve
to proven optimal; 0.80 and above are infeasible.** The plan asks units to charge
feed that is not there, so obliging the model to reproduce more than three quarters
of it cannot be done. The default is now 0.75.

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
| 1 | **Netbacks for 11 products + 4 sinks** — now the only thing on the critical path | Ratios sane against each other |
| 2 | ~~Equal-netback guardrail~~ — invalid, see section 4. Use the reprice-the-baseline check instead, which needs step 1 | Finance recognises the margin |
| 3 | ~~Flip to maximise~~ — **built** (`objective="margin"`), opt-in, cost still the default | Solves; charges 10.5% more than cost |
| 4 | Calibrate the per-unit changeover cost, one unit at a time (the mechanism is built; the tables are empty) | Campaign medians match observed |
| 4b | **Settle `terminal_value_fraction`** — or replace it with a rolling horizon or safety stocks (section 5b) | A margin run stops being one point on a curve |
| 5 | Drop the charge floor | Schedule does not move |

**What building step 3 changed about this table.** The flip is done, and it turned
out to be *less* blocked on data than expected in one way and *more* in another. The
mechanism needed no netbacks — but it cannot be validated without them, because the
cheap guardrail that was supposed to substitute for real prices does not work. Step 1
is now the only thing on the critical path, and step 4b is the item most likely to
embarrass a margin run shown to anyone.

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
