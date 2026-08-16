# Optimization model specification

Built from the workbook reference over the first 42 days. The engine still reproduces the workbook exactly; these simplifications apply only to what the optimizer sees.

| | Workbook | Model |
|---|---:|---:|
| Products | 54 | 45 |
| Charge lines | 48 | 43 |
| Units with a charge choice | 12 | 11 |
| Yield rules | 53 | 48 |

## What was dropped, and why

| Kind | Item | Reason |
|---|---|---|
| pool | DDDD | derived sum of 8170, 8175, 8135, 8105, 8136, 8177, 8120, 8165, 8125, 8115; kept as aggregate DSL |
| product | 9202 | Obsolete tolling flow. Never charged at MEK, never produced (the yield rule looks up 9118 in a charge range holding 9202 and silently return |
| product | 4325 | Kendex 165HTLV is no longer made or sold. It survived as an EXTRACT charge line that was never used and had no place on the transition ladde |
| charge_line | MEK#70 | charges retired product 9202 |
| charge_line | HYDRO#78 | 8170 charged to the hydrotreater to produce 8170. |
| charge_line | HYDRO#79 | 8175 charged to the hydrotreater to produce 8175. |
| charge_line | EXTRACT#88 | charges retired product 4325 |
| charge_line | TOLLING#94 | Never used in the plan; its only product, 9202, is retired. |

## Finished diesel, collapsed

The workbook splits finished diesel into ten grades and then sums them back into the DDDD pool, so the same material is represented twice. The split carries no decision either: all demand sits on 8175, which has no tank and no production, while all production lands on 8105 and 8170, which have no demand. Collapsing to one product removes the double count and the mismatch at once.

| | Value |
|---|---:|
| Members merged | 8170, 8175, 8135, 8105, 8136, 8177, 8120, 8165, 8125, 8115 |
| Opening inventory | 500,073 gal |
| Capacity used | 1,600,000 gal |
| Capacity summed from members | 948,825 gal |
| Production over 42 days | 2,637,005 gal |
| Demand over 42 days | 2,940,000 gal |

_Members sum to 948,825 gal but six of them have no tank recorded, so the pool's own 1,600,000 figure is used. Confirm with operations - this is the aggregate's only invented number._

## Reconciliation

Aggregation must not create or destroy material. Each aggregate is checked against the sum of the workbook blocks it replaces.

| Product | Members | Opening | Demand | Production | OK |
|---|---|---:|---:|---:|---|
| DSL | 10 | 500,073 | 2,940,000 | 2,637,005 | yes |

## Crude is an input, not a decision

Crude rate is set by refinery economics, not by downstream tank logistics — the plant runs it flat out and only turns it down in a poor-margin scenario. Leaving it as a variable would let the optimizer 'solve' every inventory problem by making less oil, the cheapest answer in the model and the most expensive one in reality.

| | Value |
|---|---|
| Crude rate | **fixed user input** |
| Crude R/L mode | **still a decision** |
| Products arriving as fixed supply | 9 |

Mode is kept as a decision because it is a product-mix choice with a direct downstream effect: **R makes 9117, which feeds the MEK dewaxer; L makes 9118, which goes to Sonneborn.** The plan runs R on 313 days and L on 47, switching 38 times a year, so it is campaigned deliberately. It costs one binary a day and lets the model say 'run L a day earlier so the MEK does not run dry'. Set `CRUDE_MODE_IS_DECISION = False` if Sonneborn volumes are contractual and the mode calendar is really fixed.

## Turnarounds and the must-run rule

Units run every day unless a turnaround stops them. Inside the region the plan actually schedules, MEK runs 160 of 162 days, ROSE 153 of 162, extraction 151 of 162, the hydrotreater 151 of 160 — and the missing days cluster into outages rather than scattering. Without a must-run rule the optimizer could relieve any inventory problem by simply not running, which is never what the plant does.

Downtime source for this build: **inferred from the workbook**. Planners set it in the app (Charge schedule → Planned downtime) before the optimizer runs; where nothing has been set the build falls back to the outages inferred from the workbook.

| Unit | Must run | Days down in window | Days it must run |
|---|---|---:|---:|
| MEK | yes | 2 | 40 |
| HYDRO | yes | 0 | 42 |
| EXTRACT | yes | 0 | 42 |
| ROSE | yes | 0 | 42 |
| PLATFORMER | yes | 0 | 42 |

Windows currently configured:

| Unit | From | To | Days |
|---|---|---|---:|
| MEK | 2026-07-27 | 2026-07-28 | detected |
| HYDRO | 2026-10-11 | 2026-10-15 | detected |
| EXTRACT | 2026-10-02 | 2026-10-09 | detected |
| ROSE | 2026-10-15 | 2026-10-23 | detected |
| PLATFORMER | 2026-12-18 | 2027-02-08 | detected |

These were **detected from blank stretches in the workbook, not read from a maintenance calendar** — placeholders to check against the real dates. The October cluster (extraction 2–9, hydrotreater 11–15, ROSE 15–23) looks like one coordinated autumn turnaround.

7 single idle days sit outside any outage (HYDRO 4, EXTRACT 3). Under a must-run rule the model schedules through them, making it slightly tighter than the plan — either they are one-day outages worth declaring, or they were slack.

## The hydrotreater's two reactors

Two reactors on one unit. 4315 and 4319 are made on the dedicated reactor and everything else on the other, so the plant groups those two products together to avoid crossing. A crossing costs a quarter of a day while the reactor is flushed back to charge.

| Reactor | Charges | Makes |
|---|---|---|
| R1 | 9704, 9705 | 4315, 4319 |
| R2 | 9703, 9711, 9712, 9713, 9720 | everything else |

Flush on crossing: **0.25 of a day**. Ordinary switch loss: **0.125 of a day**.

_Derived independently from the yield rules: 9704 makes 4315 and 9705 makes 4319. The plan crosses reactors 20 times a year (1.6 a month, 5.0 days of flush time), and reactor 1 runs in 11 campaigns of median 4 days. First-day charge on 9704 and 9705 is 42-44% below their later days, against ~13% for everything else - the flush plus an ordinary switch, showing up in the data._

## Time lost to changeovers

A changeover returns the interface to charge, so no material is lost — but unit *time* is, which is why this belongs in the day's time budget rather than in the objective as cash.

| Unit | Switch loss | Reactor flush | Worst changeover | Days lost/yr at observed switch rate |
|---|---:|---:|---:|---:|
| EXTRACT | 0.125 d | — | 0.125 d | 4.8 |
| HYDRO | 0.125 d | 0.25 d | 0.375 d | 12.2 |
| MEK | 0.125 d | — | 0.125 d | 7.0 |
| PLATFORMER | 0.000 d | — | 0.000 d | — |
| ROSE | 0.125 d | — | 0.125 d | 0.6 |

The hydrotreater is the expensive one: 98 changeovers a year at 0.125 d is 12.3 days of unit time, plus 20 reactor crossings at 0.25 d for another 5.0 days. **Roughly 17 days a year of hydrotreater capacity goes to changeovers** — which is exactly the quantity the optimizer is trading when it decides whether to group 4315 and 4319.

## Maximum charge rates

`charge = max_rate x time_fraction`, so the parameter is an absolute barrels-per-day maximum. The workbook's monthly run rate is a planning figure rather than a ceiling — the plant runs above it routinely — so it cannot be used as the maximum.

| Unit | Product | Max bbl/day | Basis | Clean days behind it |
|---|---|---:|---|---:|
| MEK | 9117 | 4,000 | clean day | 22 |
| MEK | 9119 | 2,100 | clean day | 3 |
| MEK | 9718 | 4,000 | clean day | 2 |
| MEK | 4317 | 2,400 | clean day | 24 |
| HYDRO | 9713 | 5,000 | clean day | 9 |
| HYDRO | 9705 | 2,500 | clean day | 1 |
| HYDRO | 9711 | 5,943 | **grossed up — confirm** | 0 |
| HYDRO | 9712 | 5,943 | **grossed up — confirm** | 0 |
| HYDRO | 9703 | 5,714 | **grossed up — confirm** | 0 |
| HYDRO | 9720 | 5,486 | **grossed up — confirm** | 0 |
| HYDRO | 9704 | 5,200 | clean day | 4 |
| EXTRACT | 9302 | 2,800 | clean day | 47 |
| EXTRACT | 9303 | 1,800 | clean day | 1 |
| EXTRACT | 9305 | 2,200 | clean day | 21 |
| ROSE | 4313 | 1,300 | clean day | 139 |
| ROSE | 4555 | 1,000 | clean day | 2 |
| PLATFORMER | 9103 | 4,000 | clean day | 132 |
| PLATFORMER | 9505 | **missing** | — | — |
| PLATFORMER | 4107 | **missing** | — | — |
| PLATFORMER | 4111 | **missing** | — | — |
| PLATFORMER | 9501 | **missing** | — | — |
| PLATFORMER | 9511 | **missing** | — | — |

**Minimum rates start at zero.** Products do have real minimums, but a minimum is a restriction — starting without them means v0 always has a feasible answer, and adding the real numbers later can only tighten the plan rather than break it.

**At most 2 products a day** on any unit, and at most 2 changeovers. Confirmed across all 366 days: MEK and ROSE never exceed one product, the hydrotreater reaches two on 9 days and extraction on 1, and the platformer runs two every day — though there the pair is a parallel cascade rather than a changeover. Nothing anywhere runs three.

Every figure is a **floor on the true maximum**, never a specification. The hydrotreater is the problem case: its campaigns are median 1 day, so four of its charges never run a clean day in the whole year and their rates had to be grossed up from a partial day. Those are the numbers to confirm first, because that unit is where the binding constraint lives.

## Units and allowed changeovers

| Unit | Charge options | Transitions | Min run (days) |
|---|---|---|---|
| EXTRACT | 9302, 9303, 9305 | 9302->9303, 9303->9302, 9303->9305, 9305->9303, 9305->9302 | 9302:2, 9303:1, 9305:3 |
| HYDRO | 9713, 9705, 9711, 9712, 9703, 9720, 9704 | unrestricted | 9713:1, 9705:1, 9711:1, 9712:1, 9703:1, 9720:1, 9704:1 |
| MEK | 9117, 9119, 9718, 4317 | 9718->9117, 9117->9718, 9117->9119, 9119->9117, 9119->4317, 4317->9119 | 9117:2, 9119:1, 9718:2, 4317:3 |
| PLATFORMER | 9103, 9505, 4107, 4111, 9501, 9511 | unrestricted | 9103:1, 9505:1, 4107:1, 4111:1, 9501:1, 9511:1 |
| RAILCAR | 0000 | unrestricted | 0000:1 |
| ROSE | 4313, 4555 | unrestricted | 4313:3, 4555:3 |
| SONNEBORN | 9118, 9117 | unrestricted | 9118:1, 9117:1 |
| TRANSFER_6OIL | 4577, 4554, 4454 | unrestricted | 4577:1, 4554:1, 4454:1 |
| TRANSFER_CAT | 4449, 4451 | unrestricted | 4449:1, 4451:1 |
| TRANSFER_DIESEL | 9711, 9712, 9703, 9720, 9718, 4118, 4111, 4329 | unrestricted | 9711:1, 9712:1, 9703:1, 9720:1, 9718:1, 4118:1, 4111:1, 4329:1 |
| TRANSFER_FINDSL | 4111 | unrestricted | 4111:1 |

MEK has 4 charge options, so 12 transitions are conceivable; the ladder allows **6**. The optimizer only ever gets variables for those.

## Downgrade sinks

The overflow valves. What makes them sinks is **unlimited offtake, not unlimited tankage**: the market for them is effectively bottomless, so the plant can always sell its way out at the netback. Their tanks and capacities stay real — diesel can still overfill, and modelling it as an unbounded tank would hide that. Demand ceilings are relaxed; the only thing restraining their use is price.

| Sink | Model product | Tank | Capacity | In the workbook? | Netback |
|---|---|---|---:|---|---|
| #6 oil | 8201 | real | 546,000 | yes | $1.1667/gal |
| Cat cracker | 8221 | real | 0 | yes | $1.1667/gal |
| Diesel | DSL | real | 1,600,000 | yes | **needs a price** |
| Gasoline | SINK_GASOLINE | outlet only | — | **no — added** | **needs a price** |

| Routes | Count |
|---|---:|
| Observed in the workbook's own transfer lines | 13 |
| Added from operations | 3 |

**Routes added** — these do not exist in the spreadsheet:

| Product | Name | Sink | Status |
|---|---|---|---|
| 4107 | KENSOL 17 | Gasoline | confirmed |
| 9511 | PLATFORMATE | Gasoline | confirmed |
| 1128 | ISOMERATE | Gasoline | confirmed |

> **Caution (Gasoline):** Gasoline demand is ALREADY in the model. Platformate and isomerate take their forecast from product 5020 (E10 gasoline) split 75/25 between them, which is the expected gasoline blending pull. The sink is therefore *incremental* offtake on top of that forecast, not a replacement for it - modelling it as the whole gasoline demand would count the same barrels twice. It also means the sink will do nothing for platformate, which is short rather than long: 3.15 M gal of lost sales over 42 days because the forecast pull exceeds what the platformer makes. The product it actually helps is Kensol 17.

## Warnings

- **Gasoline sink does not exist in the workbook and was created. No route exists in the workbook - this is a real downgrade path the spreadsheet never modelled. Takes Kensol 17, platformate and isomerate, confirmed with operations.**
- **HYDRO: 9711 never runs an undisturbed day, so its maximum rate (5,943 bbl/d) is the observed peak grossed up by one changeover. It is a floor - confirm the design rate with operations.**
- **HYDRO: 9712 never runs an undisturbed day, so its maximum rate (5,943 bbl/d) is the observed peak grossed up by one changeover. It is a floor - confirm the design rate with operations.**
- **HYDRO: 9703 never runs an undisturbed day, so its maximum rate (5,714 bbl/d) is the observed peak grossed up by one changeover. It is a floor - confirm the design rate with operations.**
- **HYDRO: 9720 never runs an undisturbed day, so its maximum rate (5,486 bbl/d) is the observed peak grossed up by one changeover. It is a floor - confirm the design rate with operations.**
- **PLATFORMER: 9505 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **PLATFORMER: 4107 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **PLATFORMER: 4111 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **PLATFORMER: 9501 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **PLATFORMER: 9511 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_DIESEL: 9711 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_DIESEL: 9712 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_DIESEL: 9703 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_DIESEL: 9720 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_DIESEL: 9718 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_DIESEL: 4118 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_DIESEL: 4111 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_DIESEL: 4329 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **SONNEBORN: 9118 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **SONNEBORN: 9117 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_FINDSL: 4111 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_6OIL: 4577 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_6OIL: 4554 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_6OIL: 4454 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_CAT: 4449 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **TRANSFER_CAT: 4451 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**
- **RAILCAR: 0000 has no maximum charge rate. `charge = max_rate x time` cannot be written without one.**

## Process charge lines the planner never uses

Feeds the model would offer that the plant does not run — the same shape as the tolling route. Candidates for retirement, or for a missing entry in the transition config.

| Line | Unit | Product | Name |
|---|---|---|---|
| PLATFORMER#104 | PLATFORMER | 4107 | KENSOL 17 |
| PLATFORMER#105 | PLATFORMER | 4111 | KENSOL 30 |
| PLATFORMER#106 | PLATFORMER | 9501 | Isomerate Run |
| PLATFORMER#107 | PLATFORMER | 9511 | PLATFORMATE |

## Downgrade routes with no volume in the current plan

The opposite case: these are relief valves the plant has but the planner is not using. The optimizer will want them, so each needs a daily limit and a netback before it can be trusted to.

| Route | Unit | Product | Name |
|---|---|---|---|
| TRANSFER_DIESEL#114 | TRANSFER_DIESEL | 9720 | DEWAXED, UNEXT LN CHARGE |
| TRANSFER_DIESEL#115 | TRANSFER_DIESEL | 9718 | WAXY LIGHT NEUTRAL |
| TRANSFER_DIESEL#116 | TRANSFER_DIESEL | 4118 | KENSOL 50H |
| TRANSFER_DIESEL#117 | TRANSFER_DIESEL | 4111 | KENSOL 30 |
| TRANSFER_DIESEL#118 | TRANSFER_DIESEL | 4329 | KENDEX 0060HT |
| SONNEBORN#122 | SONNEBORN | 9117 | WAXY MEDIUM NEUTRAL |
| TRANSFER_6OIL#143 | TRANSFER_6OIL | 4577 | KENDEX MNE |
| TRANSFER_6OIL#144 | TRANSFER_6OIL | 4554 | KENDEX 0834 |
| TRANSFER_6OIL#145 | TRANSFER_6OIL | 4454 | KENWAX HEAVY NEUTRAL SLACK WAX |
| TRANSFER_CAT#148 | TRANSFER_CAT | 4449 | KENWAX LIGHT NEUTRAL SLACK WAX |
| TRANSFER_CAT#149 | TRANSFER_CAT | 4451 | KENWAX MED NEUTRAL SLACK WAX |
| RAILCAR#153 | RAILCAR | 0000 |  |
| RAILCAR#154 | RAILCAR | 0000 |  |
| RAILCAR#155 | RAILCAR | 0000 |  |
| RAILCAR#156 | RAILCAR | 0000 |  |

## Notes carried from configuration

- DSL: Members sum to 948,825 gal but six of them have no tank recorded, so the pool's own 1,600,000 figure is used. Confirm with operations - this is the aggregate's only invented number.
- DSL: The workbook splits finished diesel into ten grades and then sums them back into the DDDD pool, so the same material is represented twice. The split carries no decision either: all demand sits on 8175, which has no tank and no production, while all production lands on 8105 and 8170, which have no demand. Collapsing to one product removes the double count and the mismatch at once.
- 9505 (Light Straight Run) has no tank in the workbook: it is made and consumed inside the PLATFORMER cascade. Modelled as a flow-through intermediate with no inventory constraint.
- 9501 (Isomerate Run) has no tank in the workbook: it is made and consumed inside the PLATFORMER cascade. Modelled as a flow-through intermediate with no inventory constraint.
- 8201 (#6 oil) is a downgrade sink: its tank and capacity stay real, but offtake is unlimited at the netback, so it can always be sold down.
- 8221 (Cat cracker) is a downgrade sink: its tank and capacity stay real, but offtake is unlimited at the netback, so it can always be sold down.
- DSL (Diesel) is a downgrade sink: its tank and capacity stay real, but offtake is unlimited at the netback, so it can always be sold down.
- Crude rate is a fixed input: 42 days of crude production enter the model as supply, not as a decision.
