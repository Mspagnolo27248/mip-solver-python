# Open items before the MIP

Generated from the model's live configuration, so it stays true as inputs are filled in. When nothing remains under **blocks**, the model can be built.

| | Count | Meaning |
|---|---:|---|
| Blocks | 4 | The model cannot be written without it |
| Verify | 8 | A placeholder is in use; confirm before trusting it |
| Structure | 5 | A different answer changes the formulation |

## Blocks the model

**Lost-sale margin ($/gal)** — _Finance_  
The most influential number in the objective: its ratio to the $0.50/gal downgrade cost decides whether the model downgrades or shorts a customer. A single blended average is enough to start.

**Netback for Diesel** — _Finance_  
One of the four overflow valves has no price, so the optimizer cannot compare it with the others and would treat it as free.

**Netback for Finished diesel** — _Finance_  
One of the four overflow valves has no price, so the optimizer cannot compare it with the others and would treat it as free.

**Netback for Gasoline** — _Finance_  
One of the four overflow valves has no price, so the optimizer cannot compare it with the others and would treat it as free.

## Changes the formulation

**1 products charged at more than one unit** — _Operations_  
The workbook subtracts only the first from inventory. The optimizer must subtract all of them or it plans on feed that does not exist: 9305 (EXTRACT+EXTRACT)

**Is the crude R/L mode a decision or an input?** — _Operations_  
Currently a decision, one binary a day. R makes 9117 which feeds the MEK; L makes 9118 for Sonneborn. Fix it if Sonneborn volumes are contractual.

**Recycle routes are unspecified** — _Operations_  
Rare cases where product is recharged. Each needs a source product, a destination unit, and whether the volume is a decision or a fixed rate. Note the changeover interface is not one of these - it is already handled as lost time.

**Blending is demand, not a decision** — _Modelling_  
The model cannot decline to make a blend when a component is short - it books a lost sale instead. If blends can be moved, that is a cheaper relief valve than downgrading and the model is blind to it.

**Day-zero setup state must come from the live feed** — _Integration_  
The setup carries across days, so each nightly re-solve needs what the unit is actually holding - not what the schedule wanted. Otherwise every run starts with a free or phantom changeover.

## Placeholders to confirm

**Per-product values ($/gal)** — _Finance_  
Downgrade cost currently runs in flat mode at $0.50/gal for every product, which is a floor. Real values make the model protect a specialty solvent more than a cheap stream. Five products carry 96% of the volume.

**Design maximum rate for HYDRO 9711, HYDRO 9712, HYDRO 9703, HYDRO 9720** — _Operations_  
These never run an undisturbed day in the plan, so their maxima were grossed up from a partial day. They are floors, not specifications - and they sit on the unit where the binding constraint lives.

**Minimum stable rates** — _Operations_  
Deliberately starting at zero: a minimum is a restriction, so v0 always has a feasible answer and adding real numbers later can only tighten the plan. The hook is wired through.

**Lower control limits (safety stock)** — _Operations_  
No product has one anywhere in the workbook, so 'do not run dry' can only be written as inventory >= 0 - a stockout, not a service level.

**One-way changeovers: EXTRACT 9305->9302** — _Operations_  
Observed in one direction only. If that was coincidence rather than a rule, the model is more constrained than the plant.

**5 turnaround windows are inferred, not confirmed** — _Operations_  
Read from blank stretches in the workbook rather than a maintenance calendar. Planners can now set the real dates in the app.

**2 downgrade routes added, not observed** — _Operations_  
The gasoline outlet does not exist in the workbook: 1128, 9511

**7 idle days sit outside any declared outage** — _Operations_  
Under must-run the model schedules through them, making it slightly tighter than the plan. Either one-day outages worth declaring, or slack.

## Decided against, for now

Not open items - these have been answered, and the answer was "not yet". Listed so the absence is never read as an oversight, and so the cost of each simplification sits next to it.

**Blend pull priced above ordinary forecast**  
_Decision._ One netback per output product. Demand is summed across Sales, Forecast and Blends, so the model cannot tell which kind it failed to serve and values them alike.  
_What it costs._ Blends do not outrank forecast when something has to give, though operations rank them highest. Blend pull is spread across nine products - 9704 carries 456 k gal of it, 4329 245 k, 4315 188 k - and 4309 is entirely blend, so a short product may be shorted on the blend rather than on the forecast, which is the wrong way round.  
_When to revisit._ Split demand by row and price each separately. The rows are already kept apart in `spec.demand_rows`; it is the optimizer that sums them. Contained change, and the netback file has a column ready for the second number whenever it is wanted.

**Interface material lost on a changeover**  
_Decision._ Changeovers are modelled as clean: the arc carries lost unit time (0.125 d, measured three ways independently) and no material. In reality each charge product has its own split between interface returned to charge and interface genuinely slopped or blended off - confirmed with operations - but that split is not needed at this level.  
_What it costs._ The model undercounts what a changeover costs, because it charges for the time and not for the giveaway. It will therefore switch somewhat more often than is truly economic, and campaigns may come out shorter than the plant would choose. Nothing becomes infeasible or unexecutable - the error is in the objective, not the physics.  
_When to revisit._ The correction already has a home: `switch_cost`, the cash term per changeover, which exists precisely for what lost time does not capture. It needs no interface measurements to set - run v1 at zero and raise it per unit until campaign lengths match the ones the plant actually runs (MEK 3 d median, HYDRO 1 d, EXTRACT 3 d, ROSE 40 d). That calibrates against observed behaviour rather than a quoted number.

**Deep extract (9705) can be sold, not only charged**  
_Decision._ Charge-only. 9705 carries no demand anywhere in the workbook, so there is no volume or price to model a sale with - and a half-modelled outlet with invented demand would distort the one real trade-off on the unit.  
_What it costs._ Extraction's deep mode looks slightly less valuable than it is, and the model has one fewer relief valve when the hydrotreater is down: the plant can sell deep extract, the model can only back it up or downgrade it. It will therefore understate deep extraction and may run it below what the plant would - the conservative direction.  
_When to revisit._ Once product netbacks exist (see docs/archive/MARGIN-OBJECTIVE.md), model it as a transfer that moves 9705 to its sale product at no cost and no yield loss - the same shape as RECYCLE_ROUTES, not a new demand row. That keeps one physical stream with two outlets rather than inventing a second product.

## Solvers are ready

Nothing needs installing: PuLP 2.7 with CBC, and HiGHS through `scipy.optimize.milp`. A 90-binary toy of the same shape as the unit model (setup binaries, time variables, a day budget) solved in 1.5 s on CBC and 0.02 s on HiGHS. Solve time is not the risk it was assumed to be.
