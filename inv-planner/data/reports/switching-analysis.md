# Stress-testing the switching model

## 1. Is a split day just a mid-day changeover?

If a day running two charges is the day the unit switched, then 'at most one changeover per day, with the day's time split between the outgoing and incoming charge' is a faithful model. Checking every split day against the day before and after.

| Unit | Day | Charges that day | Day before | Day after | Reading |
|---|---|---|---|---|---|
| HYDRO | 2026-08-03 | 9711 + 9712 | 9711 | 9712 | **mid-day changeover** |
| HYDRO | 2026-08-08 | 9704 + 9705 | 9704 | 9705 | **mid-day changeover** |
| HYDRO | 2026-08-20 | 9711 + 9720 | 9711 | 9720 | **mid-day changeover** |
| HYDRO | 2026-08-28 | 9704 + 9713 | 9704 | idle | **changeover, then shutdown or a second switch** |
| HYDRO | 2026-09-22 | 9711 + 9713 | 9713 | 9720 | **changeover, then shutdown or a second switch** |
| HYDRO | 2026-10-17 | 9711 + 9713 | 9713 | 9711 | **mid-day changeover** |
| HYDRO | 2026-11-01 | 9711 + 9712 | 9720 | 9712 | **changeover, then shutdown or a second switch** |
| HYDRO | 2026-11-25 | 9711 + 9712 | 9711 | 9712 | **mid-day changeover** |
| HYDRO | 2026-12-27 | 9711 + 9713 | 9705 | 9711 | **changeover, then shutdown or a second switch** |
| EXTRACT | 2026-10-29 | 9303 + 9305 | 9305 | 9302 | **changeover, then shutdown or a second switch** |

Of 10 split days, **10 are changeover days** (5 with the switch fully inside the day, 5 followed by a shutdown or a second switch). 0 are unexplained.

Every split day is a changeover day. Two charges appearing together is the unit switching mid-day, not two things running at once - so 'the day's time splits between the outgoing and incoming charge, with the loss in between' is a faithful model.

Two of these days involve **two** changeovers (a switch at the day boundary and another mid-day). Constraining the model to at most one changeover a day is therefore an approximation - a 2-in-366 one, which buys a much simpler formulation and should be stated rather than hidden.

## 2. Do changeovers happen across idle gaps?

A reactor still holds its last feed while the unit sits idle, so restarting on a different charge should still cost a flush. A model that only links consecutive *running* days would give that away free. How often does the plan change product across an idle gap?

| Unit | Changeovers back to back | Across an idle gap | Longest gap crossed | Same product resumed after idle |
|---|---:|---:|---:|---:|
| MEK | 55 | 1 | 2 | 0 |
| HYDRO | 93 | 5 | 5 | 0 |
| EXTRACT | 34 | 4 | 8 | 1 |
| ROSE | 4 | 1 | 9 | 1 |

Where a unit resumes the *same* charge after idling, no changeover is owed. Where it resumes a different one, the flush is still due - so the model has to carry a 'what the unit is currently set up for' state that persists through idle days, not merely link running days to each other.

## 3. Are low charge ratios turndown, or partial days?

This decides the rate band. Under a daily model a 0.20 ratio means the unit ran all day at 20% rate. Under a time model it more likely means it ran a fifth of a day at full rate. Comparing days in the *middle* of a campaign - no changeover either side, so a full day - against the first and last days of a campaign settles it.

| Unit | Charge | Mid-campaign days | Mid min | Mid median | Mid max | Edge median |
|---|---|---:|---:|---:|---:|---:|
| MEK | 9117 | 22 | 0.81 | 1.00 | 1.14 | 1.00 |
| MEK | 9119 | 3 | 0.79 | 0.79 | 1.17 | 0.95 |
| MEK | 4317 | 24 | 0.75 | 1.20 | 1.60 | 1.15 |
| HYDRO | 9704 | 4 | 1.25 | 1.29 | 1.49 | 0.74 |
| EXTRACT | 9302 | 47 | 0.72 | 1.00 | 1.12 | 1.00 |
| EXTRACT | 9305 | 5 | 0.75 | 1.10 | 1.20 | 1.11 |
| EXTRACT | 9305 | 16 | 0.68 | 0.87 | 0.87 | 0.72 |
| ROSE | 4313 | 139 | 1.00 | 1.25 | 1.62 | 1.30 |

If the mid-campaign minimum is much higher than the overall minimum, the low readings were partial days rather than turndown, and the rate band should be taken from the mid-campaign column only. Setting the band from the raw range would let the optimizer run a unit all day at a rate the plant has never actually held.

## 4. Can both hydrotreater reactors run at once?

Days where charges from both reactors appear: **1**.

| Day | Charges | Day before | Day after |
|---|---|---|---|
| 2026-08-28 | 9704 + 9713 | 9704 | idle |

**This is the question that decides the formulation.** If the two reactors can run concurrently, the hydrotreater is two units sharing a common train and needs two parallel time budgets. If only one can run at a time, it is one unit with an expensive setup change and a single time budget. The phrase 'flush the reactor back to charge' points at sequential use, and 1 day(s) out of 151 running days show both - consistent with a crossing day rather than routine concurrency. Worth confirming explicitly.
