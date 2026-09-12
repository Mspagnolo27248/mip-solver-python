# Optimization model: what the data says the formulation has to be

Companion to `docs/archive/WEBAPP-MIP-PLAN.md`, which was written before the engine
existed and
reasoned from the domain rather than from measurements. Now that the simulator is
validated and the real schedule is loaded, the problem can be measured.

Reproduce the numbers with:

```bash
python scripts/analyze_optimization.py    # decision structure, coupling, size
python scripts/analyze_physical.py        # the current plan with tanks enforced
python scripts/analyze_transitions.py     # observed changeovers per unit
python scripts/analyze_hydro.py           # the two reactors, and switch losses
python scripts/price_baseline.py          # the plan in dollars
python scripts/build_model.py             # the optimization model spec
```

---

## 1. The physics is hard; the economics is where the elasticity lives

Every tank is real. Inventory cannot go below zero and cannot exceed capacity.
What varies is what you *pay* to stay inside those limits:

- A tank about to overfill is relieved by **downgrading** to a low-netback outlet
  (#6 oil, cat cracker, diesel). Cost = the margin given up.
- Demand that cannot be served is a **lost sale**. Cost = the margin forgone.

So the model is:

```
hard      0 <= I[q,d] <= capacity[q]          for every block, every day
elastic   downgrade flows and lost sales, both costed
```

This is the opposite of the usual first attempt, which makes capacity soft and
demand hard. Making capacity soft lets the solver "store" material nowhere and
produces schedules that look feasible and aren't; making demand hard makes the
model infeasible on day one.

### The overflow valves: unlimited offtake, not unlimited tankage

Four outlets absorb whatever the plan cannot hold — **cat cracker, diesel,
gasoline and #6 oil**. Their demand ceilings are deliberately relaxed: the market
for them is effectively bottomless, so the plant can always sell its way out at
the netback.

The distinction that matters is *offtake*, not *storage*:

| | Modelled as |
|---|---|
| Offtake (how much can be sold) | **unlimited**, priced at the netback |
| Tank and capacity | **real** — diesel still has a 1,600,000 gal tank and can still overfill |

Modelling a sink as an unbounded *tank* is the tempting shortcut and it is wrong:
diesel inventory would grow without limit and quietly hide a genuine capacity
problem. Modelling it as unbounded *demand* is right — the plant is restrained by
price, not by a constraint.

This is also why relaxing the ceilings is safe. A sink that can fill up turns an
overflow into an infeasibility, which is the single most common way these models
become unsolvable. Real limits exist and can be added later as route capacities;
starting without them means the model always has an answer.

| Sink | Model product | Tank | Capacity | Netback |
|---|---|---|---:|---|
| #6 oil | 8201 | real | 546,000 | $1.1667/gal |
| Cat cracker | 8221 | real | none recorded | $1.1667/gal |
| Diesel | DSL | real | 1,600,000 | **needs a price** |
| Gasoline | created | outlet only | — | **needs a price** |

**Gasoline does not exist as a route in the workbook at all** — it is a real
downgrade path the spreadsheet never modelled. Confirmed with operations, it takes
**Kensol 17 (4107), platformate (9511) and isomerate (1128)**.

Kensol 30 goes to **diesel**, not gasoline — and the workbook already carries that
route and uses it heavily (3.2 M gal a year through the K-30 to finished diesel
line), so it needs no change. The light straight run and isomerate *run* are
untanked intermediates inside the platformer cascade: they cannot overflow, so
they need no outlet.

The build keeps the 13 routes read from the workbook's own transfer lines separate
from the 3 added here, so an added route can never pass as an established one.

> **Gasoline demand is already in the model.** Platformate and isomerate take
> their forecast from product **5020 (E10 gasoline)**, split **75/25** between
> them — that is the expected gasoline blending pull, and it is already there. The
> sink is therefore *incremental* offtake on top of that forecast, not a
> replacement for it; treating it as the whole gasoline demand would count the
> same barrels twice.
>
> It also means the sink does nothing for platformate, which is **short, not
> long**: 3.15 M gal of lost sales over 42 days because the forecast pull exceeds
> what the platformer makes. The product the gasoline outlet actually rescues is
> **Kensol 17**, whose 3.3 M gal of forced downgrade currently has nowhere to go.

### What the current plan actually costs

The workbook has no way to represent either response, so it just shows negative
and over-capacity numbers. Running the identical schedule with the tanks enforced
converts those into the flows a planner would have been forced to make:

| Window | Lost sales (gal) | Downgrade required (gal) | Feed shortfall (gal) | Products affected |
|---|---:|---:|---:|---:|
| first 14 days | 1,909,000 | 1,218,659 | 215,452 | 9 |
| first 42 days | 6,673,800 | 5,595,214 | 870,449 | 12 |
| first 90 days | 14,328,600 | 12,734,509 | 1,955,021 | 17 |
| full year | 67,710,656 | 95,361,155 | 12,664,472 | 38 |

**This is the baseline to beat, in units finance can price.** Multiply by the
margin spread per product and the objective is denominated in dollars.

Two things stand out:

1. **The plan routes 10.8 M gal to downgrade outlets over the year but requires
   95.4 M gal.** The gap is what the planner is absorbing by hand, or not doing at
   all. Notably the #6 oil and cat cracker routes carry **zero** volume in the
   current plan even though the rows exist.
2. **Feed shortfall is a different category.** 870 k gal over 42 days is volume the
   schedule asked a unit to charge that the tank could not supply. It has no
   price — it means the plan was not executable as written. In the MIP this
   cannot happen, because charge is a decision bounded by `I >= 0`; it only shows
   up when replaying a *fixed* schedule. It is the cleanest measure of how much
   the current plan is quietly impossible.

Peak inventories confirm the interpretation — the workbook is not forecasting
inventory, it is accumulating material the plan never disposed of:

| Product | Capacity | Workbook peak | Physical peak |
|---|---:|---:|---:|
| 4107 Kensol 17 | 273,000 | 27,419,767 (100×) | 273,000 |
| 8105 Heating oil | 176,834 | 7,861,560 (44×) | 176,834 |
| 4313 Kendex 0842 | 440,000 | 8,546,402 (19×) | 440,000 |
| 9703 Kensol 61 UNHT | 676,200 | 10,495,768 (16×) | 676,200 |

---

## 2. Restricted changeovers: the part that "won't model cleanly"

This is the constraint that has blocked previous attempts, and the data says it is
far simpler than it looks.

### What the MEK unit actually does

Over 366 days the MEK unit made 56 changeovers. Every single one moved **exactly
one rung on the viscosity ladder**:

```
9116            9117              9119               4317
Waxy Light  ──  Waxy Medium  ──  Heavy Waxy    ──   Kendex 0846
Neutral         Neutral          Distillate         (from ROSE)
```

| from \ to | 4317 | 9116 | 9117 | 9119 |
|---|---|---|---|---|
| **4317** | · | · | · | 9 |
| **9116** | · | · | 9 | · |
| **9117** | · | 9 | · | 10 |
| **9119** | 10 | · | 9 | · |

6 of 12 possible transitions are ever used; 55 of 56 are back-to-back with no
idle day. The resulting schedule is a **sweep** up and down the ladder:

```
9117(3) 9119(1) 4317(3) 9119(3) 9117(3) 9116(2) 9117(2) 9119(2) 4317(4) ...
```

**The sweep does not need to be modelled.** It is a consequence of adjacency plus
the need to reach every grade — impose the adjacency and the optimizer will
produce sweeps on its own.

### The clean formulation: a path through a time-expanded graph

Do not build an n×n transition matrix and cut the forbidden entries. **Create
variables only for the arcs that exist.** The unit's schedule is then a path
through a graph over time, which is the same structure as unit commitment and is
what solvers are most heavily tuned for.

Let `A` be the allowed arcs including self-loops `(p,p)` meaning "keep running p":

| Variable | Meaning |
|---|---|
| `x[p,d] ∈ {0,1}` | unit runs product `p` on day `d` |
| `z[(p,q),d] ∈ {0,1}`, `(p,q) ∈ A` | unit moves from `p` to `q` entering day `d` |

```
(1)  Σ_{(p,q) ∈ A} z[(p,q),d] = 1          exactly one arc taken each day
(2)  x[q,d]   = Σ_{(p,q) ∈ A} z[(p,q),d]   what runs today is the arc's head
(3)  x[p,d-1] = Σ_{(p,q) ∈ A} z[(p,q),d]   and its tail is what ran yesterday
```

That is the whole thing. Forbidden transitions are not constrained away — **they
do not exist**, so the solver cannot reach for them and the LP relaxation is not
weakened by trying to exclude them.

Everything else attaches naturally to the arc:

- **Changeover cost**: `Σ c[(p,q)] · z[(p,q),d]` in the objective, per arc, so a
  cleanout-heavy transition can cost more than an easy one.
- **Changeover duration**: if a transition burns a day of off-spec production,
  either give the arc a yield penalty or route it through an intermediate node.
- **Minimum run length**, per product (measured: 4317 ≥ 3, 9116 ≥ 2, 9117 ≥ 2,
  9119 ≥ 1):
  `Σ_{t=d}^{d+L[q]-1} x[q,t] ≥ L[q] · Σ_{p≠q} z[(p,q),d]`
- **Idle**: add an `off` node with arcs to and from whichever products may start
  or end a shutdown. MEK almost never idles between campaigns (1 of 56), so `off`
  is barely used — but the Hydrotreater and Extraction do use it.

### Time, not days: rate bands, switch loss and the two reactors

Three constraints that look separate — minimum/maximum rates per charge product,
time lost on every changeover, and the hydrotreater's two reactors — collapse into
one idea. **Allocate the day's time, not the day.**

A changeover returns the interface to charge, so no material is lost. What is lost
is *unit time*, which is why it belongs in a time budget rather than in the
objective as cash. And because rates are bounded rather than fixed, `charge = rate
× time` is linear when time is the variable.

Three assumptions had to be tested before building on this
(`scripts/analyze_switching.py`), and one of them changed the formulation.

**Test 1 — is a split day just a mid-day changeover?** Yes: **all 10 split days
across every unit are changeover days**, with one charge carrying in from
yesterday or out into tomorrow. Two charges appearing together is the unit
switching mid-day, not two things running at once. So "the day's time splits
between the outgoing and incoming charge, with the loss in between" is faithful.
Two of those days contain *two* changeovers, so capping at one per day is an
approximation — a 2-in-366 one, worth taking for the simpler model.

**Test 2 — does an idle gap reset the unit? No, and this breaks the naive
formulation.** A reactor still holds its last feed while the unit sits idle.
Changeovers do happen across idle gaps — the hydrotreater 5 times (up to 5 days
idle), extraction 4 times (up to 8 days) — and almost always onto a *different*
charge. An arc formulation that links consecutive *running* days would let the
unit idle for a day and restart on anything for free, dodging the flush entirely.

The model therefore needs a **setup state that persists through idle**, not arcs
between running days:

```
s[u,p,d] ∈ {0,1}     unit u is set up for p at the end of day d;  Σ_p s[u,p,d] = 1
z[u,(p,q),d] ∈ {0,1} changeover p→q during day d, allowed arcs only
y[u,p,d] ∈ {0,1}     p receives time on day d
t[u,p,d] ∈ [0,1]     time spent charging p

(1)  s[u,q,d] = s[u,q,d-1] + Σ_p z[u,(p,q),d] − Σ_r z[u,(q,r),d]   setup carryover
(2)  Σ_p y[u,p,d] ≤ 2                              at most two products in a day
(3)  Σ_a z[u,a,d] ≤ 2                              at most two changeovers in a day
(4)  t[u,p,d] ≤ y[u,p,d]  and  y[u,p,d] ≤ s[u,p,d-1] + s[u,p,d]
(5)  Σ_p t[u,p,d] + Σ_a loss[u,a]·z[u,a,d] ≤ 1                     the day's budget
(6)  min_rate[u,p]·t[u,p,d] ≤ chg[u,p,d] ≤ max_rate[u,p]·t[u,p,d]  bbl/day × time
```

Idle is `t = 0` with `s` unchanged — the unit still holds its feed and owes the
flush whenever it next changes. This is the standard setup-carryover structure
from lot-sizing, and solvers handle it well.

Everything else falls out of (5):

| Constraint | How it appears |
|---|---|
| Max rate per product | `max_rate` in (6), in bbl/day |
| Minimum rate | `min_rate` in (6) — **starts at zero**, see below |
| Interface loss | `loss[u,a]` eats the day's budget, so a changeover day has less time to make product |
| Reactor flush | The same term, larger coefficient on arcs that cross reactors |
| Two charges in one day | `t` splits between them via (4) — no special case |
| Idling without resetting | `s` persists while `t = 0` |

**At most two products a day**, constraint (2), confirmed across all 366 days:
MEK and ROSE never exceed one, the hydrotreater reaches two on 9 days and
extraction on 1, and the platformer runs two every day — though there the pair is
a parallel cascade rather than a changeover. Nothing anywhere runs three. Two
changeovers are still possible within that limit: the unit can switch off whatever
it carried in without running it, then switch again mid-day, which happened twice
in the year.

**Minimum rates start at zero.** Products have real minimums, but a minimum is a
*restriction* — starting without them means v0 always has a feasible answer, and
adding the real numbers later can only tighten the plan rather than break it. The
parameter is already wired through, so filling it in is data entry, not a model
change.

### The hydrotreater's two reactors

Two reactors on one unit: **4315 and 4319 are made on the dedicated reactor,
everything else on the other.** Crossing costs a quarter of a day while the
reactor is flushed back to charge, so the plant groups those products together.

Derived independently from the yield rules — 9704 makes 4315, 9705 makes 4319:

| Reactor | Charges | Makes |
|---|---|---|
| R1 | 9704, 9705 | 4315, 4319 |
| R2 | 9703, 9711, 9712, 9713, 9720 | solvents, diesel |

The plan's own behaviour confirms it: **20 reactor crossings a year** (1.6/month,
5.0 days of flush time), with reactor 1 running in **11 campaigns of median 4
days** — the grouping, already visible.

The changeover cost is then just a function of the arc:

```
loss[u,(p,q)]  =  switch_loss[u]                       within a reactor
               =  switch_loss[u] + flush_days[u]       across reactors
```

No reactor variable is needed — membership is a property of the product, so the
arc carries it. **The optimizer will group 4315 and 4319 on its own**, because
crossing is three times as expensive as staying (0.375 d vs 0.125 d), not because
anyone wrote a grouping rule.

### The switch loss is measurable, and the data agrees with the plant

If a changeover costs time, the first day of a campaign should carry less charge
than the days after it. Comparing them:

| Unit | Implied loss, all products |
|---|---:|
| MEK | 12% of a day |
| Extraction | 13% of a day |
| Hydrotreater | 13% of a day |

Three units landing on ~1/8 of a day independently is a strong signal, and
**0.125 d is the default in the config**. The corroboration goes further: on the
hydrotreater the two *reactor-1* products lose **42–44%** on their first day
against ~13% for everything else — the quarter-day flush plus an ordinary switch
(0.375 d ≈ 38%), showing up in the data without anyone quoting it.

Taken together the hydrotreater spends roughly **17 days a year on changeovers**
(98 switches × 0.125 d, plus 20 crossings × 0.25 d). That is the quantity the
optimizer is really trading when it decides how to group.

### Test 3 — the rate parameter was wrong twice over

Two errors in the first draft, both corrected.

**It should be an absolute rate, not a multiple of the planning rate.** The
workbook's monthly run rate is a planning figure, not a ceiling — ROSE runs 4313
at 1.3× it and the platformer runs 9103 at 1.1×. Using it as the maximum would cap
the optimizer below what the plant already does. So the parameter is bbl/day:

```
charge[u,p,d]  ≤  max_rate[u,p] · t[u,p,d]
```

**And it must come from undisturbed days.** Under a time model, a low charge on a
changeover day is *a partial day at full rate*, not a full day at low rate:

| Product | Raw peak | Clean-day peak |
|---|---:|---:|
| 9704 Kendex 0150 UNHT | 0.57–1.49 of planning | **5,200 bbl/day** |
| 9713 Diesel hydro charge | — | **5,000 bbl/day** |

> **Superseded as a ceiling.** Operations have since confirmed the hydrotreater
> tops out at **5,000 bbl/day for the unit**, whatever mix it runs, so every
> HYDRO line in `MAX_RATE_BBL_PER_DAY` now sits at or below 5,000 — 9704
> included. The 5,200 above remains a true *observation*; it is no longer a
> ceiling. Which is this section's own argument turned on the table that
> illustrates it: a clean day is evidence of what was typed into the plan, not of
> what the unit can take. The plan's single 8,500 bbl/day HYDRO entry — a crude
> unit figure on a hydrotreater row — is the proof that those two differ.

Behind that sits a structural problem: **the hydrotreater's campaigns are median 1
day, so four of its seven charges never run a single undisturbed day in the entire
year.** Their maxima had to be grossed up from a partial day (9711 and 9712 at
5,943 bbl/day, 9703 at 5,714, 9720 at 5,486). Every figure in the table is a
**floor on the true maximum, never a specification**, and the build warns on each
grossed-up one. On the unit where the binding constraint lives, that is exactly
where a wrong number would produce a schedule the plant cannot run — so these are
the first thing to confirm.

### The two losses are additive

There is an **interface loss on every changeover** — the interface goes back to
charge — and a **reactor flush on top** when the pair crosses reactors. They add:

```
loss(p→q)  =  interface_loss[u]                        within a reactor   (0.125 d)
           =  interface_loss[u] + flush_days[u]        across reactors    (0.375 d)
```

Worked through, this is the whole rate model:

```
  diesel   max 5,000 bbl/day
  4139     max 3,000 bbl/day

  no changeover, day split 50/50:
      t = 0.5 each   ->  2,500 and 1,500 bbl        time used 1.00

  same split, but the two are on different reactors:
      0.125 interface + 0.250 flush = 0.375 of the day gone
      0.625 left to share, 0.3125 each
      t = 0.3125 each ->  1,563 and   938 bbl
```

Crossing reactors costs **37.5% of that day's production on the unit**. That is
why the plant groups 4315 and 4319, and why the optimizer will too — it falls out
of the time budget, with no grouping rule written anywhere.

### Reactor concurrency: settled

**Only one reactor runs at a time.** Both may appear on the same day, but that is
the day splitting around a crossing, not two reactors running together — so the
hydrotreater is one unit with one time budget and an expensive setup change,
exactly as modelled. The data agreed in advance: in the whole year both reactors
appear on the same day exactly once, and that day reads as a crossing followed by
a shutdown.

### Why the usual approaches fight back

| Approach | Why it hurts |
|---|---|
| Forbidden-pair cuts `x[p,d-1] + x[q,d] ≤ 1` | O(n²) constraints, weak relaxation, and no natural home for changeover cost or duration |
| Full n×n transition matrix | Most arcs are meaningless; the model is large and loose |
| Sequence-position / ordering variables | Notoriously weak relaxations; solvers struggle |
| Encoding the sweep explicitly | Rigid — the optimizer can no longer decide to skip a grade when inventory says it should |

### Allowed arcs measured from the schedule

Starting point, to be confirmed with operations:

| Unit | Allowed transitions observed | Notes |
|---|---|---|
| **MEK** | 9116↔9117, 9117↔9119, 9119↔4317 | Strict ladder, 100% adjacency. Complete once 9202 is retired — no open questions left on this unit. |
| **EXTRACT** | 9302↔9303, 9303↔9305, 9305→9302 | Ladder plus a reset arc. **Asymmetric: 9305→9302 happens 3×, 9302→9305 never. Confirm this is a rule, not a coincidence.** 4325 has no place on the ladder yet |
| **ROSE** | 4313↔4555 | Only two feeds; no restriction |
| **HYDRO** | 36 of 132 pairs used | Cannot be read yet — the unit runs feed combinations (9711+9712, 9704+9705, …). Resolve the split-day question first |
| **PLATFORMER** | n/a | A parallel cascade, not a choice |

---

## 3. Simplifying the workbook into a model

The engine reproduces the workbook exactly, quirks included — that is what makes
it trustworthy. The optimizer needs something different: no double-counted
material, no dead flows, no accounting splits that carry no decision. Those
differences live in `src/invplanner/model_config.py` as explicit, reversible
configuration, and `modelprep.py` refuses to build if a simplification loses
material.

Build it with `python scripts/build_model.py`.

| | Workbook | Model |
|---|---:|---:|
| Products | 54 | 45 (43 tanked + 2 flow-through) |
| Charge lines | 48 | 44 |
| Units with a charge choice | 12 | 11 |

### One diesel product

The workbook splits finished diesel into ten grades and then sums them back into
the `DDDD` pool, so **the same material is represented twice**. The split carries
no decision either: all demand sits on 8175, which has *no tank and no
production*, while all production lands on 8105 and 8170, which have *no demand*.

Collapsing to a single finished-diesel product removes the double count and the
mismatch together. The hydrotreater makes one diesel at 0.98 yield from the
diesel charge stock, and demand goes on that one product.

| | Value |
|---|---:|
| Members merged | 8170, 8175, 8135, 8105, 8136, 8177, 8120, 8165, 8125, 8115 |
| Opening inventory | 500,073 gal (reconciles exactly with the pool) |
| Capacity used | 1,600,000 gal |
| Capacity summed from members | 948,825 gal |
| Demand, 42 days | 2,940,000 gal |
| Production, 42 days | 2,637,005 gal |

Capacity is the aggregate's only invented number: six of the ten grades have no
tank recorded, so the members' sum is understated and the pool's own figure is
used. **Worth confirming.**

9713 (diesel charge) stays a separate product — it is the hydrotreater's *feed*,
not a finished grade.

### Also removed

| Item | Why |
|---|---|
| 9202 and the TOLLING unit | Obsolete flow (§8) |
| `HYDRO#78`, `HYDRO#79` | Finished diesel charged back to the hydrotreater to make itself — a 2% self-consuming loop once the grades collapse |
| The `DDDD` pool block | A derived sum, not a tank |

### Two traps the build caught

**Charge codes are not always product codes.** The MEK charges 9116 (Waxy Light
Neutral), but 9116's inventory lives in the LLN block keyed under 9718 — the two
share a tank. Resolving to the wrong key would let the model consume stock it
never decremented. The spec now carries an alias map, and a test pins it.

**Untanked intermediates.** The Platformer's light straight run (9505) and
isomerate (9501) are charged but have no inventory block anywhere: they are made
and consumed inside the cascade and never tanked. They must stay in the model to
keep the yield chain intact, but with no inventory constraint and no capacity.

`4325 Kendex 165HTLV` is retired too — no longer made or sold. It had survived as
an EXTRACT charge line that was never used and had no place on the transition
ladder, so the optimizer could never have scheduled it anyway. The build still
warns if any product on a restricted unit becomes unreachable, and a test asserts
none currently is.

---

## 4. Scope: crude is an input, the downstream schedule is the problem

**Crude rate is a fixed user input, not a decision.** It is set by refinery
economics — the crude-to-product spread — and only turned down in a poor-margin
scenario. That is a different decision, made at a different level and on a
different clock.

Leaving it as a variable would actively harm the model. Crude margin dwarfs any
downgrade cost, so an optimizer free to cut crude would relieve every inventory
problem by making less oil: the cheapest answer in the model and the most
expensive one in reality. Fixing it points the optimizer at the question actually
worth asking — **given this crude plan, what is the best downstream schedule?**

Crude's daily output enters as fixed supply the downstream units must absorb, so
the optimizer never touches the crude yield rules at all.

### But the R/L mode is a different decision

The mode is a *product-mix* choice, not a rate choice, and it lands squarely in
the downstream problem:

| Mode | Days in the plan | Makes | Which feeds |
|---|---:|---|---|
| R | 313 | 9117 Waxy Medium Neutral | the MEK dewaxer |
| L | 47 | 9118 Low Volatility MN | Sonneborn |

38 mode switches a year — campaigned deliberately, not incidental. Kept as a
decision it costs one binary a day and lets the model say *"run L a day earlier so
the MEK does not run dry on 9117"*. Set `CRUDE_MODE_IS_DECISION = False` if
Sonneborn volumes are contractual and the mode calendar is genuinely fixed.

### Units must run

**Idling is not a free choice.** Inside the region the plan actually schedules,
MEK runs 160 of 162 days, ROSE 153 of 162, extraction 151 of 162 and the
hydrotreater 151 of 160 — and the missing days cluster into outages rather than
scattering. Without a must-run rule the optimizer could relieve any inventory
problem by simply not running, which is never what the plant does and would make
every schedule it produces useless.

```
Σ_p y[u,p,d] ≥ 1        for every day not in a declared turnaround
```

### Turnarounds are user input, set in the app

Planners mark planned downtime **before the optimizer runs**, in the app rather
than in code: *Charge schedule → Planned downtime*. Add a named window per unit,
or click any cell in the **Down** row of the grid to toggle a single day. Charge
cells on a downtime day are locked, so the schedule cannot contradict the
calendar.

Downtime is scenario-scoped and copies with a scenario, so a what-if can carry a
different outage plan. Editing it bumps the schedule version and invalidates the
cached simulation, exactly like a charge edit. Clearing a day inside a window
splits it rather than deleting the turnaround, so one day of a nine-day outage can
be released without re-entering the rest.

The model reads downtime from the scenario; where a planner has set nothing it
falls back to the outages inferred from the workbook, and the build report says
which source it used. The current plan contains outages only implicitly — as
blank stretches rather than declared shutdowns — so those are seeded in as
**placeholders to check against the real calendar, marked `detected` until
someone confirms them**:

| Unit | Detected window | Days |
|---|---|---:|
| MEK | 2026-07-27 → 07-28 | 2 |
| Extraction | 2026-10-02 → 10-09 | 8 |
| Hydrotreater | 2026-10-11 → 10-15 | 5 |
| ROSE | 2026-10-15 → 10-23 | 9 |
| Platformer | 2026-12-18 → 2027-02-08 | 53 |

The October cluster looks like one coordinated autumn turnaround. The 53-day
platformer window is long enough to be either a major turnaround or a deliberate
shutdown — worth confirming which.

Seven single idle days sit outside any outage (hydrotreater 4, extraction 3).
Under must-run the model schedules through them, making it slightly tighter than
the plan; they are either one-day outages worth declaring or they were slack.

### Recycle

There are rare cases where product is recharged. `RECYCLE_ROUTES` is a hook
awaiting specification — each case needs a source product, a destination unit, and
whether the volume is a decision or a fixed rate. Note this is *not* the interface
material that returns to charge on a changeover: that is already handled as lost
time rather than lost material, so it needs no flow.

---

## 5. What else the measurements changed

**Units are not all "pick one feed per day".** 315 split days. The Platformer
charges 2+ lines on 305 of 366 days because it is a cascade (naphtha → light
straight run → platformate → isomerate) running in parallel, not a unit choosing
between feeds. MEK, EXTRACT and ROSE never split; the Hydrotreater splits on 9 of
151 running days.

**Charge rates are decisions, not constants.** Only **12.8%** of charged unit-days
equal the published monthly run rate; the ratio spans 0.20–1.62, median 1.07. So
charge level is continuous within bounds set by the binary — fewer binaries and a
much stronger relaxation than a fixed-rate model.

**Campaign discipline varies enormously by unit**, so switch penalties must be per
unit. A uniform campaign constraint would forbid how the plant actually runs:

| Unit | Campaigns | Median length | Longest | Switches/month |
|---|---:|---:|---:|---:|
| MEK | 57 | 3 d | 5 d | 4.6 |
| Hydrotreater | 99 | 1 d | 4 d | 8.0 |
| Extraction | 39 | 3 d | 9 d | 3.1 |
| ROSE | 6 | 40 d | 46 d | 0.4 |
| Platformer | 2 | 159 d | 159 d | 0.1 |
| Crude R/L mode | 39 | 5 d | 45 d | 3.1 |

**25 products are both produced by one unit and charged to another** — the reason
units cannot be optimized separately, and the source of "bad schedules" that
charge feed that does not exist.

```
CRUDE ──9711/9712/9703──► HYDRO ──► finished solvents (4115/4118/4129)
      ──9116/9117/9119──► MEK   ──9302/9303/9305──► EXTRACT ──9704/9705──► HYDRO
      ──4313───────────► ROSE  ──4317──► MEK
      ──9103───────────► PLATFORMER cascade
```

---

## 6. Full formulation

### Variables

| Variable | Type | Meaning |
|---|---|---|
| `s[u,p,d]` | binary | unit `u` is **set up for** `p` at the end of day `d`; persists through idle |
| `z[u,(p,q),d]` | binary | changeover arc, allowed arcs only |
| `t[u,p,d]` | continuous ∈ `[0,1]` | fraction of the day unit `u` spends charging `p` |
| `chg[u,p,d]` | continuous, `≤ max_rate[u,p]·t[u,p,d]` | barrels charged; `max_rate` in bbl/day |
| `mode[d]` | binary | crude regular vs low-volatility, modelled as two virtual feeds |
| `dg[q,dest,d]` | continuous ≥ 0 | downgrade to a low-netback outlet |
| `lost[q,d]` | continuous ≥ 0 | unserved demand |
| `I[q,d]` | continuous ∈ `[0, cap[q]]` | inventory, gallons — **hard both sides** |

### Constraints

1. **Balance** per block per day, gallons:
   `I[q,d] = I[q,d−1] + 42·Σ yld(u,p,q)·chg[u,p,d] − 42·Σ_u chg[u,q,d] − dem(q,d) + lost[q,d] − Σ_dest dg[q,dest,d] + 42·Σ transfers_in(q,d)`
   The second term must sum **all** lines charging `q` (see defect list below).
2. **Tanks**: `0 ≤ I[q,d] ≤ cap[q]`, hard.
3. **Downgrade routes**: `dg[q,dest,d] ≥ 0` with **no upper bound**, on routes
   that exist for that product. The sinks' offtake is unlimited by design; their
   own tanks are still constrained by (2) like any other product.
4. **Setup state**: `Σ_p s[u,p,d] = 1` for exclusive units, carried through idle
   days; the Platformer gets continuous rates and no setup binary.
5. **Transitions**: the setup-carryover formulation in §2, on allowed arcs only,
   with at most two products and two changeovers a day.
6. **Day-time budget**: `Σ_p t[u,p,d] + Σ_a loss[u,a]·z[u,a,d] ≤ 1`, with
   `loss` carrying the reactor flush on crossing arcs (§2).
7. **Minimum run length** per unit and product, from the measured minima.
8. **Outages**: `x[u,·,d] = 0` on turnaround days.
9. **Terminal**: `I[q,D_end] ≥ opening[q]`, elastic, so the window does not end by
   draining every tank.

### Objective — minimise real money

```
Σ lost[q,d]  · margin[q]                      lost sales
+ Σ dg[q,dest,d] · (value[q] − netback[dest])   downgrade margin given up
+ Σ c_sw[u,(p,q)] · z[u,(p,q),d]                changeover cost, per arc
+ small deviation-from-current-plan term        so the plan does not churn nightly
```

No penalty weights to tune and no artificial tiers: every term is dollars. That
also makes the result defensible to operations — "this schedule downgrades 400 k
gal of Kensol 17 to #6 oil to avoid 900 k gal of lost Kendex sales" is an argument
someone can check.

### Prices, and what is still missing

Held in `src/invplanner/economics.py` as editable data. Crude and outlet netbacks
are quoted per barrel the way the refinery quotes them; product values and all
model costs are per gallon, because inventory and demand are. One function does
the conversion so nothing else has to.

| Input | Status |
|---|---|
| #6 oil netback | **crude − $0.50/gal** = $1.1667/gal at $70/bbl crude |
| Cat cracker netback | **crude − $0.50/gal** |
| Downgrade cost, flat mode | **$0.50/gal** — the loss on material worth only crude |
| Diesel charge / finished diesel netback | needed — these are downgrades too, into a product with its own price |
| Product value ($/gal) | deferred — flat mode runs without it; drops in per product with no code change |
| Lost-sale margin ($/gal) | **the one number that matters most** (see below) |
| Changeover cost per unit | needed |

Downgrade pricing runs in two modes. **Flat** uses the outlet's discount to crude,
which is the loss if the material is worth only crude — a floor on the true cost,
never an overstatement. **Per product** uses `value − netback` once values exist,
which is what makes the optimizer protect a specialty solvent more than a cheap
stream. `Economics.cost_basis()` reports which was used so a report can never
present a flat number as if it were real.

**Priced baseline over the 42-day window:**

| Component | Volume | $/gal | Cost |
|---|---:|---:|---:|
| Downgrade required | 5,595,214 gal | 0.50 | **$2,797,607** |
| Lost sales | 6,673,800 gal | ? | not yet priced |

### The lost-sale margin is the decisive unknown

Downgrading costs $0.50/gal. Shorting a customer costs their margin. The *ratio*
is what the optimizer reasons about:

| Lost-sale margin | Cost of lost sales | Total | Optimizer's preference |
|---:|---:|---:|---|
| $0.25/gal | $1,668,450 | $4,466,057 | short the customer before downgrading |
| $0.50/gal | $3,336,900 | $6,134,507 | indifferent |
| $1.00/gal | $6,673,800 | $9,471,407 | downgrade 2× before shorting |
| $2.00/gal | $13,347,600 | $16,145,207 | downgrade 4× before shorting |
| $3.00/gal | $20,021,400 | $22,819,007 | downgrade 6× before shorting |

Any margin above $0.50/gal makes downgrading the cheaper relief, which matches how
the plant is described as operating. If a real margin ever comes in *below*
$0.50/gal, that is worth questioning before trusting the schedule.

By volume, five products carry 96% of the total, so the useful price list is
short — and two of them are data problems rather than planning problems (8175 has
no tank capacity recorded; 9202 is obsolete):

| Rank | Product | Downgrade (gal) | Downgrade cost | Lost sales (gal) |
|---:|---|---:|---:|---:|
| 1 | 4107 Kensol 17 | 3,315,791 | $1,657,895 | — |
| 2 | 9511 Platformate | — | — | 3,150,000 |
| 3 | 8175 #2 NRLM diesel | — | — | 2,940,000 |
| 4 | 8105 Heating oil | 1,848,238 | $924,119 | — |
| 5 | 9202 HT Waxy MN | — | — | 583,800 |

---

## 7. Horizon

Firm orders cover **57 days** (16% of the horizon); beyond that demand is pure
forecast, and the baseline degrades sharply after ~6 weeks.

- **Detailed window: 42 days**, full binary model. ~1,218 assignment binaries and
  2,268 inventory continuous — comfortably solvable with a warm start and a 1–3%
  gap.
- **Aggregate tail: months 2–12**, LP only, monthly buckets, no binaries — just
  enough to give the detailed window sane end-of-window inventory.
- **Rolling nightly re-solve** after the inventory feed lands, freezing executed
  days and the next 2–3 committed days.

A full year at daily resolution is ~10,614 binaries and is not worth attempting.

---

## 8. Data that must be fixed first

Not modelling choices — defects that would make any solver produce confident
nonsense.

| Gap | Size | Consequence |
|---|---|---|
| Blocks with no tank capacity | 8 of 54 | Since every tank is physical, these are missing data, not pools. 8175 alone shows 21.4 M gal of "lost sales" that are probably an artefact of having no tank recorded |
| Products with a lower control limit | **0 of 54** | No safety-stock data exists. "Don't run dry" can only be written as `I ≥ 0`, which is a stockout, not a service level |
| Products charged at two lines | 9 | The workbook subtracts only the first from inventory; the optimizer must subtract all, or it plans on feed that does not exist |
| Charged products with no run rate | 2 (9505, 9713) | No upper bound for the charge variable |
| Downgrade route limits and netbacks | none recorded | The objective cannot price a downgrade without them, and #6 oil / cat cracker capacity is unknown |

---

## 9. Build order

> **The build did not follow this table, and the table is the stale half.** As
> built: **v1** frees MEK *and* EXTRACT together, and **v2** adds HYDRO in two
> stages. The crude R/L mode is a decision in the configuration
> (`CRUDE_MODE_IS_DECISION`) rather than a numbered step of its own, and ROSE and
> the Platformer cascade are still pinned. `SCHEDULING-MODEL.md` specifies what
> v1 and v2 actually are; the acceptance criteria below still stand.

Each step is scored by replaying the result through the simulator in physical
mode, so "better" is measured against lost sales, downgrade volume and changeover
count — never asserted.

| Step | Scope | Proves |
|---|---|---|
| **v0** | LP only. Fix the planner's assignments; optimise charge levels and downgrade routing over 42 days | Data, balances and costs are right. If this is infeasible or silly, the problem is data, not MIP hardness |
| **v1** | Free MEK assignment with the ladder arcs | The transition formulation, on the unit where the structure is cleanest |
| **v2** | Add crude R/L mode | The mode–yield coupling |
| **v3** | Add EXTRACT and ROSE | Campaign constraints start to bite |
| **v4** | Add HYDRO and the Platformer cascade | Full chain |
| **v5** | Aggregate tail, rolling horizon, nightly run | Production shape |

**Acceptance**: over the 42-day window the optimizer must beat the baseline of
6,673,800 gal of lost sales and 5,595,214 gal of downgrade (**$2,797,607** of
downgrade cost at the flat $0.50/gal), at a changeover count planners judge
operable, with feed shortfall at zero.

---

## 10. 9202 and the tolling route look obsolete

Evidence, all from the current plan:

| Check | Result |
|---|---|
| 9202 charged at MEK anywhere in the plan | **never** |
| Tolling unit used anywhere in the plan | **never** |
| Gallons of 9202 produced over the year | **0** |
| Demand still booked against 9202 | 1,167,600 gal |

The route is dead twice over. The tolling charge line is never used, *and* the
yield rule that would produce 9202 looks up product 9118 in the tolling charge
range where the line is actually 9202 — so it silently returns zero through an
`IFERROR`. Nothing can make 9202, yet the Sonneborn sales row keeps drawing
against it, which is the entire source of its 1.17 M gal of "lost sales".

If it is confirmed obsolete, three things follow:

1. **The MEK ladder is exactly four rungs** — 9116 – 9117 – 9119 – 4317 — and
   fully determined by the observed data. No open question left on that unit.
2. **The tolling unit drops out of the model** entirely.
3. **The demand attached to 9202 must be retired or reassigned**, or the optimizer
   will chase a product nothing produces and book a permanent lost sale it can
   never avoid.

## 11. Pre-implementation review

Run `python scripts/analyze_readiness.py`. Two findings change how earlier numbers
should be read.

### The plan is only half populated

| Unit | Last scheduled day | Days scheduled |
|---|---|---:|
| MEK | 2026-12-31 | 160 |
| Hydrotreater | 2026-12-29 | 151 |
| Extraction | 2026-12-31 | 151 |
| ROSE | 2026-12-31 | 153 |
| Platformer | 2027-07-17 | 305 |
| Crude | 2027-07-17 | 360 |

**The processing units are scheduled only to the end of December while crude runs
the full year**, so crude keeps making side streams nothing downstream consumes.
That — not extrapolation — is why Kensol 17 ends the year at 100× its tank.

- **Full-year baseline figures are artefacts.** The 95 M gal annual downgrade is
  mostly the unscheduled tail. The 42-day and 90-day numbers stand.
- **The 42-day optimizer window sits safely inside the populated region**, so the
  warm start and the comparison baseline are both valid.
- **The aggregate tail cannot be built by aggregating this plan** — there is no
  schedule there to aggregate. It has to be driven by demand and capacity.

### Tanks are swing tanks, and that is good news

96 of 384 tanks are listed against more than one product, but **zero hold two
products at the same time**. They are swing tanks. So no joint-capacity
constraint is needed and per-product capacity is sound.

One consequence to carry forward: **swing tanks are flexibility the model cannot
see.** Faced with an overflow the plant can reassign a tank; the optimizer can
only downgrade. It will therefore overstate how much downgrading is needed — the
safe direction to be wrong in, but worth knowing when a planner disputes a result.

### Still open

| Gap | Why it matters |
|---|---|
| **The crude unit is barely modelled** | It drives every downstream product. 38 R/L mode switches a year with no time cost modelled; 5 distinct charge rates used but no min or max recorded; crude supply assumed infinite |
| **No outage in this plan at all** | Idle stretches inside the scheduled region are ordinary campaign gaps. The model has never been tested against a turnaround |
| **Day-zero setup state** | The live feed must supply what each unit is *currently* holding, not what the schedule wants. Otherwise every nightly re-solve starts with a free or phantom changeover |
| **Blending is demand, not a decision** | The model cannot decline to make a blend when a component is short — it books a lost sale instead. If blends can be moved, that is a cheaper relief valve the model is blind to |
| **Demand is flat within a month** | A tank overflows on the day it overflows, not on the monthly average. Lumpy liftings would misplace overflow and stockout by days |
| **Nothing has been solved yet** | The size estimate is arithmetic, not experience. The first thing v0 proves is whether solve time is what we think |

## 12. Questions for operations

1. **Design maximum rate for 9711, 9712, 9703 and 9720.** Their campaigns are too
   short for the plan to reveal one, so the model is using floors grossed up from
   partial days — on the unit where the binding constraint lives.
2. **What is the lost-sale margin, even as a single average $/gal?** It sets
   whether the model downgrades or shorts; nothing else in the objective is as
   influential.
3. **Is EXTRACT 9305→9302 really allowed while 9302→9305 is not?** Observed 3× and
   0× respectively.
4. **Can the Hydrotreater genuinely run two feeds in a day** (9 of 151 days), or
   are those data entry?
5. **Daily capacity of the #6 oil and cat cracker routes** — currently unused in
   the plan, and the optimizer will want them.
6. **Netbacks** for each downgrade outlet, and margin per product for lost sales.
7. **Lower control limits** — do informal safety stocks exist that were never
   written down?

### Answered

**Does a changeover cost a day of off-spec production, or is it clean?**
**Modelled as clean** — the arc carries lost time and no material, so it needs no
duration and the formulation in §2 stands as written.

Operations confirm the reality is mixed: each charge product has its own split
between interface returned to charge and interface genuinely slopped or blended
off. That split is not needed at this level of the model, and the direction of the
error is known — see `DECIDED_NOT_MODELLED` in `model_config.py`.
