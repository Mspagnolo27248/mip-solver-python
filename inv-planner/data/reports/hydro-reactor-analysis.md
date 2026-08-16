# The hydrotreater's two reactors

## Which charge product runs on which reactor

Assigned by what the charge makes: anything producing 4315 or 4319 is on the dedicated reactor.

| Charge | Name | Produces | Reactor |
|---|---|---|---|
| 9713 | NO.2 DIESEL-HYDRO CHARGE | 8105 | **R2** |
| 9705 | KENDEX 0847 UNHT | 4319 | **R1** |
| 8170 | #2 ONROAD DIESEL S15 UNDYED | 8170 | **R2** |
| 8175 | #2 NRLM DIESEL S15 DYED | 8175 | **R2** |
| 9711 | KENSOL 48UNHT | 4115 | **R2** |
| 9712 | KENSOL 50 UNHT | 4118 | **R2** |
| 9703 | KENSOL 61 UNHT | 4129 | **R2** |
| 9720 | DEWAXED, UNEXT LN CHARGE | 4329 | **R2** |
| 9704 | KENDEX 0150 UNHT | 4315 | **R1** |

- **Reactor 1**: 9704, 9705
- **Reactor 2**: 8170, 8175, 9703, 9711, 9712, 9713, 9720

## Same-day combinations

The hydrotreater runs more than one charge on 9 days. If a day were a single indivisible slot this would be impossible - it is the clearest evidence that the model has to allocate *time within the day*, not whole days.

| Combination | Days | Reactors | Reading |
|---|---:|---|---|
| 9711 + 9712 | 3 | R2 | same reactor, shares the day |
| 9711 + 9713 | 3 | R2 | same reactor, shares the day |
| 9704 + 9705 | 1 | R1 | same reactor, shares the day |
| 9711 + 9720 | 1 | R2 | same reactor, shares the day |
| 9704 + 9713 | 1 | R1, R2 | **crosses reactors — a flush day** |

## How often the plan crosses reactors

| Measure | Value |
|---|---:|
| Days the hydrotreater runs | 151 |
| Reactor changes | 20 |
| Reactor changes per month | 1.6 |
| Time lost to reactor flushes, at 1/4 day | 5.0 days |

Reactor 1 runs in 11 campaigns over the year, median 4 days, longest 5. Grouping 4315 and 4319 together is exactly the switch-minimising behaviour the plant describes - and it is what the optimizer will reproduce on its own once a reactor change costs a quarter of a day.

## Rate range by charge product

Charge as a multiple of the published monthly run rate. This is what the min/max rate bounds have to cover.

| Unit | Charge | Days run | Min | Median | Max |
|---|---|---:|---:|---:|---:|
| HYDRO | 9705 | 20 | 0.40 | 1.20 | 1.50 |
| HYDRO | 9711 | 29 | 0.20 | 1.00 | 1.04 |
| HYDRO | 9712 | 21 | 0.40 | 0.96 | 1.04 |
| HYDRO | 9703 | 10 | 0.83 | 1.00 | 1.00 |
| HYDRO | 9720 | 12 | 0.76 | 1.00 | 1.29 |
| HYDRO | 9704 | 21 | 0.57 | 0.74 | 1.49 |
| MEK | 9117 | 60 | 0.81 | 1.00 | 1.14 |
| MEK | 9119 | 36 | 0.71 | 0.95 | 1.17 |
| MEK | 9116 | 20 | 0.84 | 0.98 | 1.18 |
| MEK | 4317 | 44 | 0.75 | 1.20 | 1.60 |
| EXTRACT | 9302 | 71 | 0.72 | 1.00 | 1.12 |
| EXTRACT | 9303 | 29 | 0.24 | 0.86 | 1.17 |
| EXTRACT | 9305 | 19 | 0.68 | 1.10 | 1.22 |
| EXTRACT | 9305 | 33 | 0.33 | 0.72 | 0.87 |
| ROSE | 4313 | 147 | 1.00 | 1.25 | 1.62 |
| ROSE | 4555 | 6 | 1.00 | 1.00 | 1.00 |

Rates are not fixed points and not free either: each product runs within a band. Modelling charge as `rate x time` with the rate bounded turns both the band and the switch loss into one linear constraint on the day's hours.

## Implied switch loss, measured

If a changeover costs time on the unit, the first day of a campaign should carry less charge than the days that follow. Comparing the two gives an estimate of the loss without anyone having to quote it.

| Unit | Charge | First-day mean (bbl) | Later-day mean (bbl) | Implied loss |
|---|---|---:|---:|---:|
| MEK | 4317 | 1,890 | 2,235 | 15% |
| MEK | 9116 | 3,511 | 3,818 | 8% |
| MEK | 9117 | 3,595 | 3,873 | 7% |
| MEK | 9119 | 1,653 | 1,882 | 12% |
| EXTRACT | 9302 | 2,483 | 2,632 | 6% |
| EXTRACT | 9303 | 1,694 | 1,900 | 11% |
| EXTRACT | 9305 | 1,619 | 1,450 | -12% |
| ROSE | 4313 | 1,300 | 1,152 | -13% |
| HYDRO | 9704 | 2,644 | 4,592 | 42% |
| HYDRO | 9705 | 1,650 | 2,940 | 44% |
| HYDRO | 9711 | 4,581 | 3,712 | -23% |
| HYDRO | 9712 | 4,367 | 4,850 | 10% |
| HYDRO | 9713 | 3,522 | 4,583 | 23% |
| HYDRO | 9720 | 4,086 | 4,380 | 7% |

| Unit | Implied switch loss, all products |
|---|---:|
| MEK | **12% of a day** |
| EXTRACT | **13% of a day** |
| ROSE | **-13% of a day** |
| HYDRO | **13% of a day** |

Treat these as a sanity check rather than a specification - the planner may simply enter round numbers. But if the plant quotes a switch loss and these disagree badly, one of the two is wrong and it is worth knowing which before the optimizer starts trading switches against inventory.
