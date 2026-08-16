# Refinery Charge Scheduling I — Freeing the Dewaxing Unit

## Objective and Prerequisites

This model decides **what the MEK dewaxing unit charges on each day of a 42-day
horizon**, and how much, so that the refinery serves as much demand as it can
while keeping every tank inside its physical limits.

It is the second step of a staged build. The first step (`v0`) took the planner's
choice of *what* each unit charges as given and optimised only *how much*, plus
where the resulting overflow went. This step frees that choice on one unit — the
one whose transition structure is cleanest — and is therefore the first model in
the series to contain any binary variables at all.

You should be comfortable with linear programming and with mixed-integer
formulations of scheduling problems. Familiarity with the unit commitment problem
will help, because the transition structure here is the same one.

### What You Will Learn

- How to model a **restricted changeover structure** without an $n \times n$
  transition matrix, by creating variables only for the arcs that exist.
- How to carry a machine's **setup state through idle days**, so that a unit
  cannot dodge a cleanout by shutting down for a day and restarting on something
  else.
- How to allocate a day's **time** rather than the day itself, which turns
  changeover duration, rate bands and split days into one idea instead of three
  special cases.
- Why the physical constraints belong on the tanks and the elastic ones belong on
  the commercial outcomes — the reverse of the usual first attempt.

---

## Problem Description

The refinery runs a chain of process units fed by a crude unit whose rate is
fixed by refinery economics and is not a decision here. Downstream of it, the MEK
unit dewaxes four different waxy stocks. Each produces one dewaxed oil that feeds
the next unit in the chain, plus a slack wax that is sold or downgraded:

| Charge | Name | Dewaxed oil (yield) | Slack wax (yield) | Goes on to |
|---|---|---|---|---|
| 9718 | Waxy Light Neutral | 9720 (0.815) | 4449 (0.185) | Hydrotreater R2 |
| 9117 | Waxy Medium Neutral | 9302 (0.825) | 4451 (0.175) | Extraction |
| 9119 | Heavy Waxy Distillate | 9303 (0.810) | 4454 (0.190) | Extraction |
| 4317 | Kendex 0846 | 9305 (0.840) | 4459 (0.160) | Extraction |

Three of those charges arrive from the crude unit as fixed supply. The fourth,
4317, is made by the ROSE unit — so the MEK unit both feeds and is fed by the
rest of the plant, and cannot be scheduled in isolation.

**The unit cannot move freely between charges.** Over 366 days of the current
plan it made 56 changeovers, and every one moved exactly one rung on a viscosity
ladder:

```
9718 ────── 9117 ────── 9119 ────── 4317
Waxy Light  Waxy Med    Heavy Waxy  Kendex 0846
Neutral     Neutral     Distillate  (from ROSE)
```

Six of the twelve conceivable transitions are ever used, and they are exactly the
adjacent pairs in both directions. A changeover costs an eighth of a day of unit
time while the interface is returned to charge; no material is lost.

Tanks are real. Inventory can neither go below zero nor above capacity. What
varies is what the refinery *pays* to stay inside those limits: a tank about to
overfill is relieved by downgrading to a low-value outlet, and demand that cannot
be served is a lost sale. Both are decisions with a price, and the model chooses
between them.

The task is to decide, for each of the 42 days, which stock the MEK unit is set
up for, how much of it to charge, and where any resulting overflow goes — at
least cost, and without ever asking a unit to charge feed that is not in the tank.

---

## Model Formulation

### Sets and Indices

$d \in D = \{1, 2, \dots, 42\}$: Days of the detailed horizon, opening 2026-07-23.

$q \in Q$: Set of all model products (45).

$q \in Q^{T} \subset Q$: Tanked products — those with a physical tank (43).

$q \in Q^{F} \subset Q$: Untanked flow-through intermediates, made and consumed
inside a cascade with no storage anywhere.

$p \in P = \{9718, 9117, 9119, 4317\}$: Charge stocks the MEK unit may run.

$a = (p, p') \in A$: **Allowed changeover arcs.** Only the six adjacent pairs on
the ladder exist:
$A = \{(9718,9117), (9117,9718), (9117,9119), (9119,9117), (9119,4317), (4317,9119)\}$.

$\ell \in L$: Charge lines on every unit other than MEK, whose assignment stays
fixed as in `v0`.

$k \in K = \{\text{6\,oil}, \text{cat}, \text{diesel}, \text{gasoline}\}$:
Downgrade outlets.

$R_q \subset K$: Outlets product $q$ may be routed to.

$D^{\text{down}} \subset D$: Days the MEK unit is in turnaround. Two, in this
window.

### Parameters

$\text{GAL} = 42$: Gallons per barrel.

$\text{rate}_p \in \mathbb{R}^+$: Maximum charge rate (in bbl/day) of stock $p$ —
9718: 4,000; 9117: 4,000; 9119: 2,100; 4317: 2,400.

$\text{loss}_a \in \mathbb{R}^+$: Unit time (in days) consumed by changeover $a$.
0.125 for every arc on this unit, measured three ways independently.

$L_p \in \mathbb{N}$: Minimum run length (in days) once stock $p$ is started —
9718: 2; 9117: 2; 9119: 1; 4317: 3.

$\text{yld}_{p,q} \in \mathbb{R}^+$: Gallons of product $q$ produced per barrel of
stock $p$ charged, i.e. the yield fraction times $\text{GAL}$.

$\text{yld}^{L}_{\ell,q} \in \mathbb{R}^+$: The same, for the fixed lines.

$\text{cap}_q \in \mathbb{R}^+$: Tank capacity (in gallons) of product $q$.

$I^{0}_q \in \mathbb{R}^+$: Opening inventory (in gallons).

$\text{dem}_{q,d} \in \mathbb{R}^+$: Demand (in gallons) — firm orders plus netted
forecast plus blend-component pull.

$\text{sup}_{q,d} \in \mathbb{R}^+$: Fixed supply (in gallons) arriving from the
crude unit, which is an input rather than a decision.

$\text{plan}_{\ell,d} \in \mathbb{R}^+$: Barrels the planner scheduled on fixed
line $\ell$.

$\phi \in [0,1]$: Charge floor — the fraction of the planner's own charge each
fixed line must still run.

$\sigma^{0}_p \in \{0,1\}$: Day-zero setup state, 1 for the stock the unit is
actually holding when the horizon opens.

$m_q \in \mathbb{R}^+$: Lost-sale margin (in USD/gal).

$c^{\text{dg}}_{k} \in \mathbb{R}^+$: Margin given up (in USD/gal) by routing to
outlet $k$.

$c^{\text{term}} \in \mathbb{R}^+$: Cost (in USD/gal) of ending the window below
opening inventory.

$c^{\text{sw}} \in \mathbb{R}^+$: Cash cost (in USD) of a changeover — labour,
quality giveaway, and anything else the lost time does not already capture.

### Decision Variables

$x_{p,d} \in \{0,1\}$: 1 if the MEK unit is **set up for** stock $p$ at the end of
day $d$. Persists through idle days.

$z_{a,d} \in \{0,1\}$: 1 if changeover $a$ is made during day $d$. Defined only
for $a \in A$.

$\tau_{p,d} \in [0,1]$: Fraction of day $d$ spent charging stock $p$. Continuous,
and it needs no binary indicator of its own — $\tau \le x_{d-1} + x_d$ says
"only what the unit is set up for" against variables that are already binary.

$g_{p,d} \in \mathbb{R}^+$: Barrels of stock $p$ charged to MEK on day $d$.

$g^{L}_{\ell,d} \in \mathbb{R}^+$: Barrels charged on fixed line $\ell$.

$w_{q,k,d} \in \mathbb{R}^+$: Gallons of $q$ routed to outlet $k$.

$u_{q,d} \in \mathbb{R}^+$: Gallons of demand for $q$ left unserved.

$v_{q,d} \in \mathbb{R}^+$: Gallons sold down from a sink beyond its own forecast.

$I_{q,d} \in [0, \text{cap}_q]$: Ending inventory (in gallons).

$e_q \in \mathbb{R}^+$: Gallons by which the window ends below opening inventory.

### Assumptions

1. **Crude rate is an input, not a decision.** Its margin dwarfs anything
   downstream, so an optimizer free to cut crude would relieve every inventory
   problem by making less oil.
2. **At most two changeovers a day**, which is exact on 364 of 366 observed days.
3. **A changeover is clean** — it costs unit time and no material, so the arc
   needs no duration. Confirmed with operations, with a caveat they supplied:
   each charge product really has its own split between interface returned to
   charge and interface slopped or blended off. Carrying that split is not needed
   at this level. The consequence is that the model undercounts what a switch
   costs and will therefore switch somewhat more often than is truly economic —
   an error in the objective, not in the physics, and one the cash term
   $c^{\text{sw}}$ exists to absorb.
4. **Charge is continuous within the rate band.** Only 12.8% of charged unit-days
   in the plan sit exactly on the published run rate, so the level is a decision.
5. **Minimum charge rates are zero.** A minimum is a restriction; starting without
   one guarantees the model has a feasible answer, and adding real numbers later
   can only tighten the plan.
6. **The MEK charges workbook product 9116, whose inventory lives in the block
   keyed 9718** — the two share a tank. Resolving to the wrong key would let the
   model consume stock it never decremented.

### Objective Function

**Cost of relief.** Minimise what the refinery pays to keep every tank inside its
limits over the horizon (in USD).

$$
\begin{equation}
\text{Min} \quad Z =
\sum_{d \in D}\sum_{q \in Q^{T}} m_q\, u_{q,d}
\;+\; \sum_{d \in D}\sum_{q \in Q^{T}}\sum_{k \in R_q} c^{\text{dg}}_{k}\, w_{q,k,d}
\;+\; \sum_{d \in D}\sum_{q} c^{\text{dg}}_{\text{flat}}\, v_{q,d}
\;+\; \sum_{q \in Q^{T}} c^{\text{term}} e_q
\;+\; \sum_{d \in D}\sum_{a \in A} c^{\text{sw}} z_{a,d}
\tag{0}
\end{equation}
$$

Every term is denominated in dollars, so no penalty weights need tuning and no
artificial priority tiers are required. The changeover term carries only the cash
a switch costs beyond the time it consumes; the time itself is priced by the
production it displaces, through constraint (7).

> **Note.** Once per-product netbacks exist, this objective inverts to a margin
> maximisation and the first four terms become revenue forgone rather than
> penalties assessed. See `MARGIN-OBJECTIVE.md`. The constraint set below does not
> change.

### Constraints

**Setup carryover.** The stock the unit is set up for at the end of a day is what
it was set up for yesterday, adjusted by any changeover made today. This is what
makes the setup persist through an idle day.

$$
\begin{equation}
x_{p,d} = x_{p,d-1} + \sum_{(p',p) \in A} z_{(p',p),d} - \sum_{(p,p') \in A} z_{(p,p'),d}
\quad \forall\, (p,d) \in P \times D
\tag{1}
\end{equation}
$$

**Exclusive setup.** The unit is set up for exactly one stock at all times, idle
or not.

$$
\begin{equation}
\sum_{p \in P} x_{p,d} = 1 \quad \forall\, d \in D
\tag{2}
\end{equation}
$$

**Day-zero state.** The horizon opens with the unit holding what it is actually
holding, supplied by the live feed rather than by the schedule.

$$
\begin{equation}
x_{p,0} = \sigma^{0}_p \quad \forall\, p \in P
\tag{3}
\end{equation}
$$

**Changeover limit.** One changeover a day.

$$
\begin{equation}
\sum_{a \in A} z_{a,d} \le 1 \quad \forall\, d \in D
\tag{4}
\end{equation}
$$

The plan contains two days with two changeovers, out of 366, so this is an
approximation — and it earns its place twice over. It makes the arc restriction
**hard**: with a single arc a day the setup moves exactly one allowed step, where
two let the model reach an unobserved pair through an intermediate it never runs.
It also makes constraint (11) unnecessary at $L_p = 1$.

**Time follows setup.** A stock may only receive time on a day it is set up for,
either at the start of the day or at the end of it — which is what allows a
changeover day to carry both the outgoing and the incoming charge.

$$
\begin{equation}
\tau_{p,d} \le x_{p,d-1} + x_{p,d} \quad \forall\, (p,d) \in P \times D
\tag{5}
\end{equation}
$$

**Day-time budget.** The day's time is shared between charging and changing over.
This is where a changeover is paid for.

$$
\begin{equation}
\sum_{p \in P} \tau_{p,d} + \sum_{a \in A} \text{loss}_a\, z_{a,d} \le 1
\quad \forall\, d \in D
\tag{7}
\end{equation}
$$

**Must run.** Outside a declared turnaround the day is fully allocated, between
charging and changing over. Without this the optimizer would relieve any
inventory problem by simply not running, which is never what the plant does.

$$
\begin{equation}
\sum_{p \in P} \tau_{p,d} + \sum_{a \in A} \text{loss}_a\, z_{a,d} = 1
\quad \forall\, d \in D \setminus D^{\text{down}}
\tag{8}
\end{equation}
$$

This binds **time**, not an on/off indicator. An indicator set to one with
$\tau = 0$ is a unit notionally running and charging nothing — precisely the
non-answer must-run exists to forbid.

It does **not** bound volume: charge stays free in $[0,\ \text{rate}_p \tau_{p,d}]$
while minimum rates are zero, so a unit can allocate its day and still charge
little. That closes when minimum rates are supplied, or when the objective
rewards production instead of only penalising its absence.

**Charge rate.** Barrels charged are the rate times the time spent, which is
linear because time is the variable.

$$
\begin{equation}
g_{p,d} \le \text{rate}_p\, \tau_{p,d} \quad \forall\, (p,d) \in P \times D
\tag{10}
\end{equation}
$$

**Minimum run length.** A stock that is started must stay on the unit for its
minimum campaign. Written one day at a time rather than as a sum over the window:
the two agree on integer solutions, but the disaggregated form is the tighter
relaxation — the aggregated one lets a fractional setup spread across the window
and satisfy the sum without committing to any day, which is exactly the slack the
search then has to close by hand.

$$
\begin{equation}
x_{p,t} \;\ge\; \sum_{(p',p) \in A} z_{(p',p),d}
\quad \forall\, p \in P,\; d \in D,\; t \in [d,\ d + L_p - 1],\; d + L_p \le |D|
\tag{11}
\end{equation}
$$

Not needed at $L_p = 1$: with one changeover a day, a stock switched into is
still there at the end of it. Skipped where the window would run past the
horizon, since requiring days that do not exist would force a campaign to the
edge for no physical reason.

**Fixed-line throughput floor.** Every other unit still runs at least the given
fraction of what the planner scheduled. The objective counts only costs, so
without a floor the cheapest schedule elsewhere is to stop making oil.

$$
\begin{equation}
\phi \cdot \text{plan}_{\ell,d} \;\le\; g^{L}_{\ell,d} \;\le\; \max\!\left(\text{rate}_\ell,\, \text{plan}_{\ell,d}\right)
\quad \forall\, (\ell,d) \in L \times D
\tag{12}
\end{equation}
$$

**Inventory balance.** For every tanked product on every day, in gallons.

$$
\begin{equation}
I_{q,d} = I_{q,d-1}
+ \sum_{p \in P} \text{yld}_{p,q}\, g_{p,d}
+ \sum_{\ell \in L} \text{yld}^{L}_{\ell,q}\, g^{L}_{\ell,d}
+ \text{sup}_{q,d}
- \text{GAL} \sum_{p \in P : p = q} g_{p,d}
- \text{GAL} \sum_{\ell \in L : \ell \text{ charges } q} g^{L}_{\ell,d}
- \text{dem}_{q,d} + u_{q,d}
- \sum_{k \in R_q} w_{q,k,d} - v_{q,d}
\tag{13}
\end{equation}
$$
$$
\forall\, (q,d) \in Q^{T} \times D
$$

Consumption must sum **every** line that charges $q$. The workbook subtracts only
the first, which is a defect the optimizer must not inherit or it will plan on
feed that does not exist.

**Tank limits.** Hard on both sides, as variable bounds.

$$
\begin{equation}
0 \le I_{q,d} \le \text{cap}_q \quad \forall\, (q,d) \in Q^{T} \times D
\tag{14}
\end{equation}
$$

**Lost sales are bounded by demand.** You cannot fail to serve more than was
asked for.

$$
\begin{equation}
u_{q,d} \le \text{dem}_{q,d} \quad \forall\, (q,d) \in Q^{T} \times D
\tag{15}
\end{equation}
$$

**Flow-through identity.** An intermediate with no tank has no buffer, so
everything made of it is charged the same day. This replaces a rate ceiling on
the downstream unit rather than supplementing one.

$$
\begin{equation}
\sum_{\ell \in L} \text{yld}^{L}_{\ell,q}\, g^{L}_{\ell,d}
\;=\;
\text{GAL} \sum_{\ell \in L : \ell \text{ charges } q} g^{L}_{\ell,d}
\quad \forall\, (q,d) \in Q^{F} \times D
\tag{16}
\end{equation}
$$

**Terminal inventory.** The window hands the next one a plant in the state it
found it. Elastic, because demand genuinely can exceed supply and that is a
situation the planner needs an answer for rather than a solver error.

$$
\begin{equation}
I_{q,|D|} + e_q \ge I^{0}_q \quad \forall\, q \in Q^{T}
\tag{17}
\end{equation}
$$

### Size and solve time

Six arcs rather than twelve is the point of the formulation. Forbidden
transitions are not constrained away — **they do not exist**, so the solver cannot
reach for them and the LP relaxation is not weakened by excluding them.

| Accepted gap | Time limit | Solve | Objective | Distance from optimum |
|---|---:|---:|---:|---:|
| **2%** | 60 s | **11 s** | $2,119,310 | **0.47%** |
| 1% | 120 s | 121 s | $2,109,310 | 0 |
| 0.5% | — | **3,067 s** | $2,109,310 | 0 (proven) |

**Proving optimality costs fifty-one minutes and buys half a percent.** The
objective is nearly flat across many schedules — several assignments cost the
same to within a rounding of switch cost — so the last fraction is far more
search than it is worth. Take the gap.

The eleven-second answer carries about 40% more changeovers than the proven one
(MEK 13 against 9), and those extra switches are most of the 0.47%. Since campaign
shape is what a planner objects to first, the tighter setting is the one to use
for a run you intend to act on; the fast one is for what-ifs.

Four things made the difference, in order of effect:

| Change | Why |
|---|---|
| **Warm start from the planner's schedule** | It is a feasible assignment already. Only the binaries are seeded — the plan violates a rate ceiling on EXTRACT#89, so seeding levels would hand the solver a start it must reject. |
| **Dropped the $y$ binaries** | `t ≤ s[d−1] + s[d]` says the same thing against variables that are already binary. A third of the binaries, for nothing. |
| **One changeover a day** | Makes the arc restriction *hard* — with two, the model reaches an unobserved pair through an intermediate it never runs — and makes minimum run vacuous at `L = 1`. |
| **Disaggregated minimum run** | One constraint per day of the window rather than a sum over it. Same integer solutions, tighter relaxation. |

---

## Python Implementation

The model is built with PuLP and solved with CBC. It lives beside `v0`:

```
src/invplanner/optimizer/
  v0.py       LP: charge levels and downgrade routing, assignments fixed
  v1.py       + setup binaries, changeover arcs and the time budget on MEK
  verify.py   replay any result through the simulator and check it holds up
```

Every parameter above is read from the model specification rather than hardcoded:

```python
spec  = modelprep.build(ref, scn, sim, horizon=dates, downtime=downtime)
unit  = spec.units["MEK"]

unit["products"]              # ['9117', '9119', '9718', '4317']
unit["allowed_arcs"]          # the six ladder arcs
unit["min_run_days"]          # {'9117': 2, '9119': 1, '9718': 2, '4317': 3}
unit["max_rate_by_line"]      # bbl/day, per line
unit["changeover_loss_days"]  # 0.125 on every arc
```

The arc set comes from `model_config.UNIT_TRANSITIONS`, which was measured from
the plan and is meant to be confirmed with operations rather than trusted.

## Model Deployment

The model runs behind the planning application. A planner sets the economic
inputs and the planned downtime, presses **Run**, and the result lands as a new
scenario they can open, edit and compare against their own.

```bash
python scripts/run_api.py        # then Optimizer → Run on the baseline scenario
```

Each run is replayed through the simulator — an independent implementation of the
same physics, validated cell-by-cell against the workbook — and is marked
`unverified` if the two disagree. **The optimizer's own numbers are computed from
its own variables, so they are exactly what not to trust.**

## Analysis

A run is worth acting on only if all of the following hold.

### Schedule shape

The formulation imposes adjacency and a minimum campaign; it does not impose the
sweep up and down the ladder that the plant actually runs. **That sweep should
emerge on its own.** If it does not, the transition data is wrong rather than the
solver.

| Measure | Plan | Model should be near |
|---|---:|---|
| Campaigns over 42 days | ~7 | comparable |
| Median campaign length | 3 days | 2–5 days |
| Switches per month | 4.6 | operable, per planners |
| Days lost to changeovers | ~0.8 | falls out of (7) |

### Cost

The result must beat the plan on the measures that motivated the work, scored the
same way on both sides:

| | Plan, 42 days |
|---|---:|
| Lost sales | 3,207,855 gal |
| Downgrade | 3,398,560 gal |
| Feed shortfall | 870,449 gal |

### Executability

**Feed shortfall must be zero.** The optimizer holds inventory at or above zero by
construction, so any shortfall on replay means the two models disagree about what
the schedule consumes — which is a defect, not a cost.

### Solve time

`v0` solves in under a quarter of a second. 588 binaries with a warm start from
the planner's own schedule should stay well inside a planner's patience; if it
does not, the horizon is the lever, not the formulation.

---

## References

1. Williams, H.P. *Model Building in Mathematical Programming*, 5th edition.
2. Gurobi modeling examples: *Factory Planning I & II*, *Food Manufacture I & II* —
   the source of this document's structure.
3. `MIP-FORMULATION.md` — what the data says the formulation has to be, and the
   measurements behind every parameter above.
4. `PROCESS-FLOW.md` — the plant as modelled, confirmed with operations.
5. `MARGIN-OBJECTIVE.md` — pricing the objective in real margin.
6. `data/reports/switching-analysis.md` — the stress tests that validated the
   time-allocation model, the setup-through-idle requirement, and the two-
   changeover limit.
