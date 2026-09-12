# Refinery Charge Scheduling — Freeing the Units, One at a Time

## Objective and Prerequisites

These models decide **what each freed process unit charges on every day of the
horizon**, and how much, so that the refinery serves as much demand as it can
while keeping every tank inside its physical limits.

They are the later steps of a staged build. The first step (`v0`) took the
planner's choice of *what* each unit charges as given and optimised only *how
much*, plus where the resulting overflow went. The steps specified here free that
choice, one group of units at a time:

| Model | Frees | Why this increment |
|---|---|---|
| **v0** | nothing — levels and downgrade routing only | Proves the data, the balances and the costs. No binaries. |
| **v1** | **MEK and EXTRACT** | The two units whose transition structure is known and restrictive. MEK's three dewaxed oils are extraction's only feeds, so freeing MEK while extraction stayed pinned would bound the answer by what extraction was already scheduled to eat — the pair is the smallest increment that can show a real gain. |
| **v2** | **+ HYDRO**, in two stages | The hydrotreater offers 7 charge lines against the others' 4, and 42 transitions against their 6 and 9. Freeing all three at once does not solve; see "Why v2 is staged". |

You should be comfortable with linear programming and with mixed-integer
formulations of scheduling problems. Familiarity with the unit commitment problem
will help, because the transition structure here is the same one.

### What You Will Learn

- How to model a **restricted changeover structure** without an $n \times n$
  transition matrix, by creating variables only for the arcs that exist.
- How to carry a machine's **setup state through idle days**, so that a unit
  cannot dodge a cleanout by shutting down for a day and restarting on something
  else.
- Why the setup state must be the **charge line and not the charge product**, once
  a unit can run one feed two ways.
- How to allocate a day's **time** rather than the day itself, which turns
  changeover duration, rate bands and split days into one idea instead of three
  special cases.
- How a **second setup state above the first** — which reactor the unit is on —
  is written against variables that already exist, without a new binary.
- Why the physical constraints belong on the tanks and the elastic ones belong on
  the commercial outcomes — the reverse of the usual first attempt.
- When to stop asking for a joint optimum and **decompose into stages that each
  prove**, and what guarantee that costs you.

---

## Problem Description

The refinery runs a chain of process units fed by a crude unit whose rate is
fixed by refinery economics and is not a decision here. `PROCESS-FLOW.md` is the
whole plant; this section covers only the units these models free.

### MEK — dewaxing

The MEK unit dewaxes four different waxy stocks. Each produces one dewaxed oil
that feeds the next unit in the chain, plus a slack wax that is sold or
downgraded:

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

### EXTRACT — solvent extraction, and why the setup state is the line

Extraction takes MEK's three dewaxed oils and runs on four charge **lines**, not
three products:

| Line | Charge | Mode | Rate (bbl/d) | Principal product | Min campaign |
|---|---|---|---:|---|---:|
| `EXTRACT#87` | 9302 | — | 2,800 | 9704 Kendex 0150 UNHT → HYDRO R1 | 2 d |
| `EXTRACT#89` | 9303 | — | 1,800 | 4309, sold | 1 d |
| `EXTRACT#90` | 9305 | normal | 2,200 | 4318 Argold Legacy, sold | 2 d |
| `EXTRACT#91` | 9305 | **deep** | 1,300 | 9705 Kendex 0847 UNHT → HYDRO R1 | 1 d |

**This is the reason the formulation is keyed on the line.** The unit changes
between normal and deep extraction *without changing feed* — 6 times one way and
once the other across the plan. At product level that is $9305 \to 9305$, a
self-loop the arc formulation cannot represent at all. Keyed on the line it is an
ordinary arc. Deep extraction also runs at a different rate and makes a different
product, so collapsing the two modes scheduled deep extraction 69% above what the
unit can make until they were separated.

Nine of the twelve line-to-line pairs are observed:

```
   ┌──────────────────── 9305 deep (#91) ────────────────────┐
   │                          ▲  │                           │
   ▼                          │  ▼                           │
 9302 (#87) ◀────────▶ 9303 (#89) ◀────────▶ 9305 normal (#90)
```

The three never observed — `#87→#90`, `#87→#91`, `#90→#87` — say that **deep
extract can return to 9302 and normal extract cannot**, and that nothing goes
directly from 9302 to either 9305 mode. Those are treated as rules; they are
flagged in `model_config.py` for confirmation with operations, because a rule
read off one year of plan is a coincidence until someone says otherwise.

### HYDRO — the hydrotreater, and its second setup state

The hydrotreater is v2's unit, and it is shaped differently from the other two.
It has seven charge lines and no ladder: **36 of its 42 possible transitions
appear in the plan**, so the arc formulation buys it almost no tightening.

What it has instead is **two reactors on one train**:

| Reactor | Charges | Makes |
|---|---|---|
| **R1** — dedicated | 9704, 9705 | 4315, 4319 |
| **R2** — everything else | 9713, 8170, 8175, 9711, 9712, 9703, 9720 | Kensols, finished diesel |

The plant groups 9704 and 9705 together to avoid crossing between reactors, and a
crossing costs **a quarter of a day** of flush time on top of the ordinary
eighth-day changeover — 0.375 days in total, and the two losses are additive
because they are different physical operations. The reactors are sequential on
one train: the unit is on exactly one of them per day.

Two rules, both from operations rather than from the plan, are what make v2
tractable at all:

- **A minimum reactor visit.** Once the unit goes to R1 it stays four days. This
  is the plan's own median R1 visit measured as reactor time, and it is a rule
  about the *reactor*, not the charge — which is the whole point. A product-level
  minimum lengthens a visit and does nothing to stop the unit leaving R1 for a
  Kensol and coming straight back; tried that way it made visits both longer
  *and* more numerous, 6 crossings against 3 with no minimum at all. R2 carries
  no minimum: it is the general reactor and the state the unit sits in.
- **Per-line minimum campaigns** — 3 days on the two R1 lines, 2 days on the
  rest. Without them a 4-day R1 visit was free to bounce between 9704 and 9705
  and R2 was unconstrained entirely: a 100-day run came back with 59 campaigns at
  a median of 1 day, worse line-level churn than the 43 it had before the reactor
  rule went in.

The unit had neither for a long time, because the workbook's HYDRO campaigns run
a median of 1–2 days and that was read as the plant switching the unit daily. It
is not run that way; the figure behind that reading counts changeovers with no
idle day inside them, not campaigns one day long. `MARGIN-AND-CAMPAIGNS.md`,
"HYDRO's structural lever was there all along", records how the mistake was made
and found. Nothing pruned the search tree until it was corrected.

### What is being traded

Tanks are real. Inventory can neither go below zero nor above capacity. What
varies is what the refinery *pays* to stay inside those limits: a tank about to
overfill is relieved by downgrading to a low-value outlet, and demand that cannot
be served is a lost sale. Both are decisions with a price, and the model chooses
between them.

The task is to decide, for each day of the horizon, which stock each freed unit is
set up for, how much of it to charge, and where any resulting overflow goes — at
least cost, and without ever asking a unit to charge feed that is not in the tank.

---

## Model Formulation

The formulation below is written once, over a set of freed units. v1 instantiates
it with $U^{\text{free}} = \{\text{MEK}, \text{EXTRACT}\}$; v2 adds HYDRO and
solves in two stages, which changes nothing in the constraints and is explained
under "Why v2 is staged".

### Sets and Indices

$d \in D$: Days of the detailed horizon — 42 in the reference window, opening
2026-07-23.

$q \in Q$: Set of all model products (45).

$q \in Q^{T} \subset Q$: Tanked products — those with a physical tank (43).

$q \in Q^{F} \subset Q$: Untanked flow-through intermediates, made and consumed
inside a cascade with no storage anywhere.

$u \in U^{\text{free}}$: Units whose assignment is a decision.

$k \in K_u$: **Charge lines** of freed unit $u$ — the setup state, one per
(product, mode) the unit can run. MEK has 4, EXTRACT 4, HYDRO 7.

$a = (k, k') \in A_u$: **Allowed changeover arcs** on unit $u$. Only observed
pairs exist: 6 of 12 on MEK (the adjacent ladder pairs, both directions), 9 of 12
on EXTRACT, 36 of 42 on HYDRO.

$\ell \in L$: Charge lines on every unit **not** in $U^{\text{free}}$, whose
assignment stays fixed as in `v0`.

$r \in R_u$: Reactors of unit $u$. $|R_u| = 1$ everywhere except HYDRO, where
$R_{\text{HYDRO}} = \{\text{R1}, \text{R2}\}$ and every constraint indexed by $r$
below is written only where $|R_u| > 1$.

$K_{u,r} \subseteq K_u$: The lines that run on reactor $r$.

$k \in K^{\text{dg}} = \{\text{6 oil}, \text{cat}, \text{diesel},
\text{gasoline}\}$: Downgrade outlets. $O_q \subset K^{\text{dg}}$ are the
outlets product $q$ may be routed to.

$D^{\text{down}}_u \subset D$: Days unit $u$ is in turnaround, entered by the
planner.

### Parameters

$\text{GAL} = 42$: Gallons per barrel.

$\text{rate}_k \in \mathbb{R}^+$: Maximum charge rate (in bbl/day) of line $k$.
MEK — 9718: 4,000; 9117: 4,000; 9119: 2,100; 4317: 2,400. EXTRACT — as tabulated
above. Every figure is a floor on the true maximum rather than a specification;
`model_config.py` says so line by line.

$\text{loss}_a \in \mathbb{R}^+$: Unit time (in days) consumed by changeover $a$.
0.125 on every arc, measured three ways independently.

$\text{flush}_u \in \mathbb{R}^+$: Additional unit time consumed by **crossing
reactors**. 0.25 on HYDRO, and additive with $\text{loss}_a$ because a crossing
is also a changeover.

$L_k \in \mathbb{N}$: Minimum run length (in days) once line $k$ is started.
MEK — 9718: 2; 9117: 2; 9119: 1; 4317: 3. EXTRACT and HYDRO as tabulated above.
1 means no constraint.

$V_{u,r} \in \mathbb{N}$: Minimum days on reactor $r$ once the unit goes there.
4 on R1; 1 (no constraint) elsewhere.

$\text{yld}_{k,q} \in \mathbb{R}^+$: Gallons of product $q$ produced per barrel of
line $k$ charged, i.e. the yield fraction times $\text{GAL}$.

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

$\sigma^{0}_k \in \{0,1\}$: Day-zero setup state, 1 for the line each unit is
actually holding when the horizon opens.

$m_q \in \mathbb{R}^+$: Lost-sale margin (in USD/gal).

$c^{\text{dg}}_{k} \in \mathbb{R}^+$: Margin given up (in USD/gal) by routing to
outlet $k$.

$c^{\text{term}} \in \mathbb{R}^+$: Cost (in USD/gal) of ending the window below
opening inventory.

$c^{\text{sw}}_{u,a} \in \mathbb{R}^+$: Cash cost (in USD) of a changeover —
labour, quality giveaway, and anything else the lost time does not already
capture. Per unit and per arc; the tables are built and empty, so it currently
resolves to a scalar that defaults to zero.

### Decision Variables

$x_{k,d} \in \{0,1\}$: 1 if the unit is **set up for** line $k$ at the end of
day $d$. Persists through idle days.

$z_{a,d} \in [0,1]$: 1 if changeover $a$ is made during day $d$. Defined only for
$a \in A_u$. Relaxed to continuous and integral at every vertex anyway — the
setups sum to one and the arcs carry flow between two unit vectors — confirmed by
2,394 arcs coming back with none fractional. It is kept relaxed because it is
free and it is right, not because it made anything provable.

$\rho_{r,d} \in \{0,1\}$: 1 if the unit is on reactor $r$ on day $d$. Written only
where $|R_u| > 1$.

$f_{u,d} \in [0,1]$: 1 if the unit crosses reactors during day $d$.

$\tau_{k,d} \in [0,1]$: Fraction of day $d$ spent charging line $k$. Continuous,
and it needs no binary indicator of its own — $\tau \le x_{d-1} + x_d$ says
"only what the unit is set up for" against variables that are already binary.

$g_{k,d} \in \mathbb{R}^+$: Barrels charged on freed line $k$ on day $d$.

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
2. **One changeover a day per unit**, which is exact on 364 of 366 observed days.
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
7. **A unit is on one reactor at a time.** The plan shows both HYDRO reactors on
   1 running day in 91, which is a crossing caught mid-day rather than
   concurrency.

### Objective Function

**Cost of relief.** Minimise what the refinery pays to keep every tank inside its
limits over the horizon (in USD).

$$
\begin{equation}
\text{Min} \quad Z =
\sum_{d \in D}\sum_{q \in Q^{T}} m_q\, u_{q,d}
\;+\; \sum_{d \in D}\sum_{q \in Q^{T}}\sum_{k \in O_q} c^{\text{dg}}_{k}\, w_{q,k,d}
\;+\; \sum_{d \in D}\sum_{q} c^{\text{dg}}_{\text{flat}}\, v_{q,d}
\;+\; \sum_{q \in Q^{T}} c^{\text{term}} e_q
\;+\; \sum_{d \in D}\sum_{u}\sum_{a \in A_u} c^{\text{sw}}_{u,a}\, z_{a,d}
\tag{0}
\end{equation}
$$

Every term is denominated in dollars, so no penalty weights need tuning and no
artificial priority tiers are required. The changeover term carries only the cash
a switch costs beyond the time it consumes; the time itself is priced by the
production it displaces, through constraint (7).

> **Note.** This is not the only objective the model runs. Per-product netbacks
> now exist, and under `objective="margin"` the problem inverts to a maximisation
> of revenue — the first four terms becoming revenue forgone rather than penalties
> assessed — with profit reported as that revenue less the constant crude bill.
> It is opt-in and cost is still the default. **The constraint set below does not
> change either way.** See `MARGIN-AND-CAMPAIGNS.md` for what the flip measured,
> what it is still missing, and why it and the changeover brake are one change;
> `docs/archive/MARGIN-OBJECTIVE.md` for the original argument.

### Constraints

Every constraint indexed by $k$ or $a$ below is written **once per freed unit**,
over that unit's own lines and arcs.

**Setup carryover.** The line the unit is set up for at the end of a day is what
it was set up for yesterday, adjusted by any changeover made today. This is what
makes the setup persist through an idle day.

$$
\begin{equation}
x_{k,d} = x_{k,d-1} + \sum_{(k',k) \in A_u} z_{(k',k),d} - \sum_{(k,k') \in A_u} z_{(k,k'),d}
\quad \forall\, (k,d) \in K_u \times D
\tag{1}
\end{equation}
$$

**Exclusive setup.** The unit is set up for exactly one line at all times, idle
or not.

$$
\begin{equation}
\sum_{k \in K_u} x_{k,d} = 1 \quad \forall\, d \in D
\tag{2}
\end{equation}
$$

**Day-zero state.** The horizon opens with the unit holding what it is actually
holding, supplied by the live feed rather than by the schedule.

$$
\begin{equation}
x_{k,0} = \sigma^{0}_k \quad \forall\, k \in K_u
\tag{3}
\end{equation}
$$

**Changeover limit.** One changeover a day.

$$
\begin{equation}
\sum_{a \in A_u} z_{a,d} \le 1 \quad \forall\, d \in D
\tag{4}
\end{equation}
$$

The plan contains two days with two changeovers, out of 366, so this is an
approximation — and it earns its place twice over. It makes the arc restriction
**hard**: with a single arc a day the setup moves exactly one allowed step, where
two let the model reach an unobserved pair through an intermediate it never runs.
It also makes constraint (11) unnecessary at $L_k = 1$.

**Time follows setup.** A line may only receive time on a day it is set up for,
either at the start of the day or at the end of it — which is what allows a
changeover day to carry both the outgoing and the incoming charge.

$$
\begin{equation}
\tau_{k,d} \le x_{k,d-1} + x_{k,d} \quad \forall\, (k,d) \in K_u \times D
\tag{5}
\end{equation}
$$

**One reactor a day.** Written only where $|R_u| > 1$.

$$
\begin{equation}
\sum_{r \in R_u} \rho_{r,d} = 1 \quad \forall\, d \in D
\tag{6a}
\end{equation}
$$

$$
\begin{equation}
\sum_{k \in K_{u,r}} \tau_{k,d} \le \rho_{r,d} \quad \forall\, (r,d) \in R_u \times D
\tag{6b}
\end{equation}
$$

**Summing to one is what gives (6) teeth**, and it is also the physics. The first
draft shipped with only the $\le$ link of (6b): an indicator bounded below by
production and above by nothing can be switched on for free, so the model held
both reactors on permanently, never registered a change, and paid no flush. It
read as a 3-crossing regression that was really solver noise inside the 2% gap.
Time is already a fraction of a day, so 1 is the tightest valid big-M in (6b) and
no looser one is needed.

**Crossing detection.** One flush a day at most: with the reactor indicators
summing to one, a change turns exactly one reactor on and one off, so charging per
reactor would bill the same crossing twice.

$$
\begin{equation}
f_{u,d} \ge \rho_{r,d} - \rho_{r,d-1} \quad \forall\, (r,d) \in R_u \times D
\tag{6c}
\end{equation}
$$

**Day-time budget.** The day's time is shared between charging, changing over and
flushing. This is where a changeover is paid for.

$$
\begin{equation}
\sum_{k \in K_u} \tau_{k,d} + \sum_{a \in A_u} \text{loss}_a\, z_{a,d} + \text{flush}_u\, f_{u,d} \le 1
\quad \forall\, d \in D
\tag{7}
\end{equation}
$$

**Must run.** Outside a declared turnaround the day is fully allocated. Without
this the optimizer would relieve any inventory problem by simply not running,
which is never what the plant does.

$$
\begin{equation}
\sum_{k \in K_u} \tau_{k,d} + \sum_{a \in A_u} \text{loss}_a\, z_{a,d} + \text{flush}_u\, f_{u,d} = 1
\quad \forall\, d \in D \setminus D^{\text{down}}_u
\tag{8}
\end{equation}
$$

This binds **time**, not an on/off indicator. An indicator set to one with
$\tau = 0$ is a unit notionally running and charging nothing — precisely the
non-answer must-run exists to forbid.

It does **not** bound volume: charge stays free in $[0,\ \text{rate}_k \tau_{k,d}]$
while minimum rates are zero, so a unit can allocate its day and still charge
little. That closes when minimum rates are supplied, or when the objective
rewards production instead of only penalising its absence.

**Charge rate.** Barrels charged are the rate times the time spent, which is
linear because time is the variable.

$$
\begin{equation}
g_{k,d} \le \text{rate}_k\, \tau_{k,d} \quad \forall\, (k,d) \in K_u \times D
\tag{10}
\end{equation}
$$

**Minimum run length.** A line that is started must stay on the unit for its
minimum campaign. Written one day at a time rather than as a sum over the window:
the two agree on integer solutions, but the disaggregated form is the tighter
relaxation — the aggregated one lets a fractional setup spread across the window
and satisfy the sum without committing to any day, which is exactly the slack the
search then has to close by hand.

$$
\begin{equation}
x_{k,t} \;\ge\; \sum_{(k',k) \in A_u} z_{(k',k),d}
\quad \forall\, k \in K_u,\; d \in D,\; t \in [d,\ d + L_k - 1],\; d + L_k \le |D|
\tag{11}
\end{equation}
$$

Not needed at $L_k = 1$: with one changeover a day, a line switched into is still
there at the end of it. Skipped where the window would run past the horizon, since
requiring days that do not exist would force a campaign to the edge for no
physical reason.

**Minimum reactor visit.** The same idea one level up: a unit that goes to a
reactor stays there. Written only where $V_{u,r} > 1$.

$$
\begin{equation}
\sum_{k \in K_{u,r}} x_{k,t} \;\ge\; \sum_{\substack{(k',k) \in A_u \\ k \in K_{u,r},\, k' \notin K_{u,r}}} z_{(k',k),d}
\quad \forall\, r \in R_u,\; d \in D,\; t \in [d,\ d + V_{u,r} - 1]
\tag{11a}
\end{equation}
$$

**A reactor's state needs no variable of its own here.** The setups sum to one a
day, so the setups of a reactor's lines sum to exactly the indicator "the unit is
on this reactor today", and entering the reactor is the crossing arcs into it.
Both are already in the model.

This is also where (11a) parts company with (11): the window is **not** skipped
near the end of the horizon. Skipping lets a visit start on the last few days and
end immediately — in one run the unit crossed to R1 on day 98 of 100 and left for
R2 the next day, a one-day visit with two clear days after it. A campaign that
cannot be proven to complete must still not be abandoned, so it is held to the end
of the window instead.

**Fixed-line throughput floor.** Every unit not freed still runs at least the given
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
+ \sum_{u}\sum_{k \in K_u} \text{yld}_{k,q}\, g_{k,d}
+ \sum_{\ell \in L} \text{yld}^{L}_{\ell,q}\, g^{L}_{\ell,d}
+ \text{sup}_{q,d}
- \text{GAL} \sum_{u}\sum_{k \in K_u : k \text{ charges } q} g_{k,d}
- \text{GAL} \sum_{\ell \in L : \ell \text{ charges } q} g^{L}_{\ell,d}
- \text{dem}_{q,d} + u_{q,d}
- \sum_{k \in O_q} w_{q,k,d} - v_{q,d}
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

---

## Why v2 is staged

Freeing all three units at once does not solve. Measured, at `mip_gap` 0.02 with
a generous limit:

| Horizon | All three freed | MEK + EXTRACT | HYDRO alone |
|---:|---|---|---|
| 7 d | optimal, 116 s | | |
| 10 d | time limit, no proof | | |
| 14 d | time limit, no proof | | |
| 21 d | time limit, no proof | optimal, 9 s | optimal, 159 s |
| 42 d | time limit, no proof | optimal, 11 s | optimal, 47 s |
| 100 d | | optimal, 130 s | optimal, 170 s |

Seven days is the largest window the joint model can prove, which is useless: it
is shorter than a single ROSE campaign, so a rolling horizon built on it would
decide a 40-day campaign seven days at a time.

**What does not fix it**, all tried and measured:

- Relaxing the arc variables to continuous. Correct, and kept, but it does not
  make v2 provable.
- A real changeover cost on the hydrotreater. It does what it is for — switches
  fall from 9 to 6 over 14 days — but the model still cannot close the gap.
- A shorter horizon. Only 7 days proves, which is not a horizon.

**What does.** Solve the two units that are cheap, hold their answer, then solve
the hydrotreater against it. Both stages prove optimality, and together they cost
about five minutes over 100 days against a joint model that cannot finish ten. One
time budget is spent across the two stages, not one each.

The honest caveat: **this is not a global optimum.** Each stage is optimal given
the other's decisions, which is a real guarantee and a weaker one. The upper bound
the joint model would give is not available at any horizon worth planning, so the
choice is not between this and the true optimum — it is between this and no
answer. Any report of a v2 run must say so.

The reactor rule is what made the hydrotreater stage tractable at all: 42 days
with HYDRO freed went from never proving optimality inside 900 s to proving it in
about three seconds on the reactor rule alone. The per-line minimums give some of
that back, so this is better than it was and not solved.

---

## Size and solve time

Restricting the arcs is the point of the formulation. Forbidden transitions are
not constrained away — **they do not exist**, so the solver cannot reach for them
and the LP relaxation is not weakened by excluding them. The cost of that, stated
plainly: on HYDRO, where the restriction is to the 36 observed pairs rather than
to a ladder, the model can never propose a transition the plant did not happen to
make in this one year. The six unused pairs should be checked before v2 runs in
anger — if any is merely unused rather than forbidden, it belongs in the set.

The table below is **the MEK-only stage**, measured when v1 freed one unit. It is
kept because the lesson generalises and the trade-off is the same one at every
scope; the absolute numbers are not v1's as it now stands.

| Accepted gap | Time limit | Solve | Objective | Distance from optimum |
|---|---:|---:|---:|---:|
| **2%** | 60 s | **11 s** | $2,119,310 | **0.47%** |
| 1% | 120 s | 121 s | $2,109,310 | 0 |
| 0.5% | — | **3,067 s** | $2,109,310 | 0 (proven) |

**Proving optimality costs fifty-one minutes and buys half a percent.** The
objective is nearly flat across many schedules — several assignments cost the
same to within a rounding of switch cost — so the last fraction is far more
search than it is worth. Take the gap.

The eleven-second answer carried about 40% more changeovers than the proven one
(MEK 13 against 9), and those extra switches were most of the 0.47%. Since
campaign shape is what a planner objects to first, the tighter setting is the one
to use for a run you intend to act on; the fast one is for what-ifs.

Four things made the difference, in order of effect:

| Change | Why |
|---|---|
| **Warm start from the planner's schedule** | It is a feasible assignment already. Only the binaries are seeded — the plan violates a rate ceiling on EXTRACT#89, so seeding levels would hand the solver a start it must reject. |
| **Dropped the $y$ binaries** | `t ≤ s[d−1] + s[d]` says the same thing against variables that are already binary. A third of the binaries, for nothing. |
| **One changeover a day** | Makes the arc restriction *hard* — with two, the model reaches an unobserved pair through an intermediate it never runs — and makes minimum run vacuous at `L = 1`. |
| **Disaggregated minimum run** | One constraint per day of the window rather than a sum over it. Same integer solutions, tighter relaxation. |

---

## Python Implementation

The model is built with PuLP and solved with CBC:

```
src/invplanner/optimizer/
  v0.py       LP: charge levels and downgrade routing, assignments fixed.
              Also carries the whole shared formulation - balance, tanks,
              downgrade routing, flow-through, terminal, setup, arcs, reactors.
  v1.py       v0 with MEK and EXTRACT freed.
  v2.py       v1's answer held, then HYDRO freed against it. Two stages.
  rolling.py  window state - opening tanks, setup carry-in, last-window flag.
  verify.py   replay any result through the simulator and check it holds up.
```

v1 and v2 add no formulation of their own. They name the units to free and hand
the same solver the same model:

```python
# v1.py
FREE_UNITS = ["MEK", "EXTRACT"]

def solve(ref, scn, spec, params, **kw):
    return v0.solve(ref, scn, spec, params, free_units=list(FREE_UNITS), **kw)
```

Everything downstream of the assignment is shared with `v0` verbatim. That is
deliberate: every defect this model has had lived in the balance, and a second
copy of it would drift from the first at exactly the moment nobody was looking.

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

The arc set comes from `model_config.TRANSITIONS_BY_LINE` (falling back to the
product-level `TRANSITIONS` where a unit needs no line distinction), and the
reactor structure from `model_config.UNIT_REACTORS`. All of it was measured from
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

A run never hands back a schedule worse than keeping the planner's assignments.

## Analysis

A run is worth acting on only if all of the following hold.

### Schedule shape

The formulation imposes adjacency and a minimum campaign; it does not impose the
sweep up and down the ladder that the plant actually runs. **That sweep should
emerge on its own.** If it does not, the transition data is wrong rather than the
solver.

| Measure | Plan | Model should be near |
|---|---:|---|
| MEK campaigns over 42 days | ~7 | comparable |
| MEK median campaign length | 3 days | 2–5 days |
| MEK switches per month | 4.6 | operable, per planners |
| Days lost to changeovers | ~0.8 | falls out of the campaign count |

Per-unit observed campaign medians to calibrate against: MEK 3 d, EXTRACT 3 d,
ROSE 40 d. HYDRO's workbook median of 1–2 days is **not** a calibration target —
see "HYDRO — the hydrotreater" above for why that figure answers a different
question. On HYDRO, watch reactor crossings instead: the plan crosses 20 times a
year, 1.6 a month, 5.0 days of flush time.

### Cost

The result must beat the plan on the measures that motivated the work, scored the
same way on both sides:

| | Plan, 42 days |
|---|---:|
| Lost sales | 3,207,855 gal |
| Downgrade | 3,398,560 gal |
| Feed shortfall | 870,449 gal |

Under the margin objective the bar changes to the one that was always wanted:
**the optimised schedule must make more money than the plan**, at a changeover
count planners judge operable. Both halves of that sentence are load-bearing.

### Executability

**Feed shortfall must be zero.** The optimizer holds inventory at or above zero by
construction, so any shortfall on replay means the two models disagree about what
the schedule consumes — which is a defect, not a cost.

### Solve time

`v0` solves in under a quarter of a second. v1 proves 42 days in about 11 seconds
and 100 days in about 130. v2's two stages together cost about five minutes over
100 days. If a horizon stops solving, the horizon is the lever, not the
formulation — and a status of `Optimal` is worth checking against the gap it was
actually proven to, because PuLP will report the solver's status more generously
than the solver meant it.

---

## References

1. Williams, H.P. *Model Building in Mathematical Programming*, 5th edition.
2. Gurobi modeling examples: *Factory Planning I & II*, *Food Manufacture I & II* —
   the source of this document's structure.
3. `MIP-FORMULATION.md` — what the data says the formulation has to be, and the
   measurements behind every parameter above.
4. `PROCESS-FLOW.md` — the plant as modelled, confirmed with operations.
5. `MARGIN-AND-CAMPAIGNS.md` — pricing the objective in real margin, the
   changeover brake, and the record of what each change measured.
6. `data/reports/switching-analysis.md` — the stress tests that validated the
   time-allocation model, the setup-through-idle requirement, and the two-
   changeover limit.
