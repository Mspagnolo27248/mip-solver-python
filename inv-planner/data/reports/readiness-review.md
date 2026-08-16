# Pre-implementation review

## 1. The plan stops halfway, and that changes how to read everything

| Unit | First scheduled | Last scheduled | Days scheduled |
|---|---|---|---:|
| MEK | 2026-07-23 | 2026-12-31 | 160 |
| HYDRO | 2026-07-23 | 2026-12-29 | 151 |
| EXTRACT | 2026-07-23 | 2026-12-31 | 151 |
| ROSE | 2026-07-23 | 2026-12-31 | 153 |
| PLATFORMER | 2026-07-25 | 2027-07-17 | 305 |
| CRUDE | 2026-07-23 | 2027-07-17 | 360 |

**The processing units are scheduled only to 2026-12-31 — about five months — while the crude unit and platformer run the full year.** Crude keeps making side streams that nothing downstream consumes after New Year.

That is the real explanation for the inventory drift found earlier: Kensol 17 ending the year at 100x its tank, Kensol 61 at 16x. It is not extrapolation gone wrong, it is a half-filled schedule. Consequences:

- **Full-year baseline figures are artefacts.** The 95 M gal of required downgrade over a year is mostly the unscheduled tail. The 42-day and 90-day numbers are sound.
- **The optimizer's 42-day window sits safely inside the populated region** (to 2026-09-02), so the warm start and the comparison baseline are both valid.
- **The aggregate tail cannot be built from this plan.** Months 2-12 have no schedule to aggregate; the tail model has to be driven by demand and capacity, not by copying the workbook.

## 2. Do products share physical tanks? Swing tanks, one at a time

| Measure | Value |
|---|---:|
| Tanks in the inventory feed | 384 |
| Tanks listed against more than one product | 96 |
| Tanks holding more than one product **at the same time** | **0** |

**None.** The 96 multi-product tanks are swing tanks: they take different products at different times, never two at once. So no joint capacity constraint is needed and per-product capacity is sound.

Two consequences worth carrying forward:

- Capacity is really *capacity given the current tank assignment*. Over a 42-day window that is stable enough to treat as fixed.
- **Swing tanks are flexibility the model cannot see.** Faced with an overflow the plant can reassign a tank; the optimizer can only downgrade. It will therefore over-state how much downgrading is needed, which is the safe direction to be wrong in, but worth knowing when a planner disputes a result.

## 3. The crude unit drives everything and is barely modelled

| Measure | Value |
|---|---:|
| Days charging crude | 360 of 366 |
| Charge rate min / median / max | 7,800 / 9,500 / 9,900 bbl/d |
| Distinct charge rates used | 5 |
| R/L mode campaigns | 39 |
| Mode campaign min / median / max | 1 / 5 / 45 days |
| Mode switches per month | 3.1 |

Every downstream product starts here, yet the model treats crude charge as a given. Three questions:

- **Does an R/L mode switch cost time?** It is a cut change; every other unit's changeover costs an eighth of a day. At 38 switches a year that would be real capacity, and it is not in the model.
- **Is the charge rate a decision?** Five distinct rates appear, so the planner does vary it, but no minimum or maximum is recorded anywhere. If crude rate is a lever, it is the most powerful one in the plant.
- **Is crude supply ever binding?** Receipts are a monthly plan and the model assumes crude is always there.

## 4. Outages, within the region that is actually scheduled

Idle stretches measured only up to each unit's last scheduled day, so the unpopulated tail does not masquerade as a turnaround.

| Unit | Longest idle inside the plan | Idle stretches over 5 days |
|---|---:|---:|
| MEK | 2 days | 0 |
| HYDRO | 5 days | 0 |
| EXTRACT | 8 days | 1 |
| ROSE | 9 days | 1 |
| PLATFORMER | 53 days | 1 |

Nothing here looks like a turnaround - these are ordinary gaps between campaigns. **So this plan contains no maintenance outage at all**, which means the model has never been tested against one. Where does the maintenance calendar live, and what happens to the schedule around it?

## 5. What is each unit set up for on day zero?

The setup state persists across days, so the model needs to know what each unit holds when the horizon opens. Get it wrong and day one either gets a free changeover or pays one it does not owe.

| Unit | Charge on its first scheduled day | Reactor |
|---|---|---|
| MEK | 9117 on 2026-07-23 | — |
| HYDRO | 9705 on 2026-07-23 | R1 |
| EXTRACT | 9302 on 2026-07-23 | — |
| ROSE | 4313 on 2026-07-23 | — |
| PLATFORMER | 9103, 9505 on 2026-07-25 | — |

These are what the schedule *wants* on day one, not necessarily what the unit is holding. **The live feed has to supply the true current setup**, the same way it supplies inventory - otherwise every nightly re-solve starts with a free or phantom changeover.

## 6. Blending is demand, not a decision

- 44 blended products drawing on 38 components.
- Of those components, 20 have an inventory block the model tracks; 18 do not.

Blend demand arrives as a fixed monthly rate per component, so **the model cannot decide not to make a blend when a component is short** - it will book a lost sale on the component instead of rescheduling the blend. Whether that matters depends on how much freedom the blend plan really has. If blends can be moved, they are a cheaper relief valve than downgrading and the model is currently blind to it.

## 7. Demand is flat within a month

Firm orders are dated (57 distinct days). Everything beyond them is a flat gal/day rate applied to every day of the month.

Over a 42-day window that shape matters: a tank overflows on the day it overflows, not on the monthly average. If real liftings are lumpy - month-end pulls, weekly liftings - the model will misplace overflow and stockout by days, and a schedule built on it will be tight in the wrong places. Worth asking whether the forecast can carry a shape.

## 8. Nothing has been solved yet

| | Count |
|---|---:|
| Model products | 45 |
| Process charge lines | 23 |
| Units with a setup decision | 5 |

A 42-day window is roughly 966 setup binaries plus the changeover and time variables. That should solve comfortably, but **no model has been built or solved yet** - the estimate is arithmetic, not experience. The first thing v0 proves is whether the solve time is what we think it is.
