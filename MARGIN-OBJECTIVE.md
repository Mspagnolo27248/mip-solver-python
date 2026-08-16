# Pricing the model in real margin

Companion to `MIP-FORMULATION.md`. That document costs the plan in avoided losses;
this one turns it into profit, which is what the plant is actually run for.

## 1. Why the current objective is the wrong shape

v0 minimises cost:

```
lost sales x margin  +  downgrade x $0.50/gal  +  end-of-window shortfall x $0.50/gal
```

Every term is a penalty. Nothing earns anything. Three consequences follow, and
all three showed up in the numbers:

- **Producing is never rewarded.** On 18-35% of unit-days the optimizer stops
  short of the rate ceiling for no reason other than "nothing wants the output" -
  an extra gallon earns nothing, fills a tank, and eventually gets downgraded at
  a loss. The model literally cannot answer *"would running harder make money?"*,
  only *"what is the cheapest way to serve this demand?"*
- **Downgrade is modelled as a pure loss.** It is not. #6 oil, the cat cracker,
  diesel and gasoline all *sell*. Downgrading is profitable - just less
  profitable than the fully priced product. Charging it a flat $0.50/gal
  understates the plant's real options and overstates the cost of relief.
- **End-of-window inventory needs an invented price.** $0.50/gal is standing in
  for safety stocks that exist nowhere in the workbook. It is currently the
  largest single term in the objective and nobody has measured it.

## 2. Margin per gallon, and the trap in it

Profit per gallon is **price less the cost of the crude it came from**. At $70/bbl
crude that is $1.6667/gal of crude cost.

**Do not attribute full crude cost to every gallon of every product.** One barrel
of crude becomes naphtha *and* neutrals *and* distillate; the yields sum to about
one across all of them. Charging $1.6667/gal against each product separately
counts the same barrel five times and makes almost everything look unprofitable -
including routes the plant runs every day.

The way out is that **crude rate is a fixed input** (`CRUDE_RATE_IS_INPUT = True`,
and deliberately so - see `MIP-FORMULATION.md` section 4). If crude volume is
fixed, the crude bill is a **constant**, and a constant cannot change which
schedule is best. So:

```
optimise      maximise  SUM over every outlet of  gallons x netback[outlet]
report        profit = that revenue  -  (crude bbl x crude price)
```

Same argmax, no double counting, and the crude cost still appears in the number
finance reads. This is what makes the intuition work mechanically:

| | Today | Under margin |
|---|---|---|
| Selling at full price | avoids a penalty | earns the product netback |
| Downgrading | costs $0.50/gal | earns the **sink's** netback - lower, still positive |
| Lost sale | costs the margin | earns nothing - the revenue simply never arrives |
| Producing more | worth nothing | worth its netback, so the model wants to run |
| Stock left at the end | invented $0.50/gal penalty | **valued at its own netback** |

That last row matters as much as the others: inventory carried out of the window
is material you still own, so it is worth what it is worth. The
`terminal_shortfall_per_gal` knob disappears entirely - a tank of Kensol 61 is
valued as Kensol 61, not as a penalty someone had to guess.

## 3. What has to change in the model

The formulation barely moves. `economics.py` already carries values and netbacks
and already reports which basis it used, and `MIP-FORMULATION.md` section 6
anticipates this: *"per product uses value - netback once values exist."*

| Change | Where |
|---|---|
| Flip `LpMinimize` to `LpMaximize`, objective = revenue | `optimizer/v0.py` |
| Value served demand at the product netback | `optimizer/v0.py` |
| Value sink offtake and downgrade at the **sink's** netback | `optimizer/v0.py` |
| Drop the lost-sale and downgrade penalty terms - they become revenue not earned | `optimizer/v0.py` |
| Value terminal inventory at netback; delete `terminal_shortfall_per_gal` | `optimizer/v0.py`, `db/models.py` |
| Report revenue, crude bill and margin separately | `db/optimizer_service.py` |
| Netback per product, editable, versioned | new table + `economics.py` |

The charge floor stays until margin proves it unnecessary. It exists because
production earned nothing; once it earns, the model should want to run on its
own. **Removing the floor and getting the same schedule is the test that the
margin objective works** - and is worth running as an experiment before deleting
anything.

## 4. The data to collect

Netback in **$/gal** for each product with demand. 27 products carry demand in the
42-day window, and the list is short where it counts:

| Coverage | Products |
|---|---:|
| 80% of demand volume | **11** |
| 95% | 19 |
| all | 27 |

The eleven that matter, largest first:

| # | Product | 42-day demand (gal) | Cumulative |
|---:|---|---:|---:|
| 1 | 9511 Platformate | 3,150,000 | 20.1% |
| 2 | DSL Finished diesel | 2,940,000 | 38.9% |
| 3 | 1128 Isomerate | 1,050,000 | 45.6% |
| 4 | 9704 Kendex 0150 UNHT | 1,011,899 | 52.1% |
| 5 | 4115 | 935,246 | 58.1% |
| 6 | 9103 Platformer charge | 888,427 | 63.7% |
| 7 | 4315 | 687,741 | 68.1% |
| 8 | 4118 Kensol 50H | 607,148 | 72.0% |
| 9 | 4129 | 464,954 | 75.0% |
| 10 | 4309 | 461,376 | 77.9% |
| 11 | 4451 Kenwax Med Neutral Slack Wax | 419,416 | 80.6% |

Plus **four sink netbacks**: #6 oil, cat cracker, diesel, gasoline. Two are
currently modelled as crude less $0.50; the other two have never had a price.

**The ratios matter more than the levels.** The model reasons about which gallon
to give up, so a uniform error in all netbacks changes the reported profit and not
the schedule. An error in one product relative to the others changes the schedule.
Start with the eleven, at whatever accuracy is available, rather than waiting for
a complete list.

## 5. Guardrails

Two checks that must pass before anyone trusts a margin-priced run:

1. **Equal netbacks reproduce today's answer.** Set every product and sink to the
   same value and the schedule should not move. If it does, revenue and the old
   penalties are not describing the same problem.
2. **The baseline reprices to a number finance recognises.** Score the planner's
   own schedule under the new objective and show it to finance as revenue, crude
   bill and margin. If the absolute margin is implausible, the netbacks are wrong -
   and that is far easier to see in dollars than in gallons.

Then the acceptance test becomes the one that was always wanted: **the optimized
schedule must make more money than the plan**, not merely lose less.

## 6. What this does not fix

- **Blend demand is fixed**, so the model still cannot decline a blend when a
  component is short. Revaluing components does not give it that freedom.
- **Sink netbacks must sit below product netbacks.** If a sink is priced too high
  the model will downgrade profitable material on purpose, which will look like a
  bug and will not be one.
- **Crude stays an input.** Margin does not change that: crude margin dwarfs
  everything downstream, so a model free to cut crude would still relieve every
  inventory problem by making less oil.
