# What the optimization model has to reproduce

Measured from the baseline scenario as the planner built it: 366 days, 2026-07-23 to 2027-07-23.

## 1. What is actually decided

| Unit | Charge options | Days running | Idle days | Days with >1 product | Distinct products used |
|---|---:|---:|---:|---:|---:|
| MEK | 5 | 160 | 206 | 0 | 4 |
| HYDRO | 9 | 151 | 215 | 9 | 7 |
| EXTRACT | 5 | 151 | 215 | 1 | 3 |
| TOLLING | 1 | 0 | 366 | 0 | 0 |
| ROSE | 2 | 153 | 213 | 0 | 2 |
| PLATFORMER | 6 | 305 | 61 | 305 | 2 |
| CRUDE | 1 (rate + R/L mode) | 360 | 6 | n/a | n/a |

**Split days: 315.** Units do charge more than one product on the same day, so the assignment variable cannot be a simple one-hot per unit-day.

## 2. Are charge rates decisions or constants?

Of 883 charged unit-days with a published run rate, **113 (12.8%) are exactly the monthly run rate** and 770 differ.

Ratio of actual charge to run rate: min 0.20, median 1.07, max 1.62.

Charged products with no run rate at all: 9505 (305 days), 9713 (47 days).

## 3. How the planner campaigns each unit

Consecutive days a unit stays on the same charge product. This is the structure a naive inventory-optimal model destroys.

| Unit | Campaigns | Median length | Mean | Longest | Switches | Switches/month |
|---|---:|---:|---:|---:|---:|---:|
| MEK | 57 | 3 | 2.8 | 5 | 56 | 4.6 |
| HYDRO | 99 | 1 | 1.5 | 4 | 98 | 8.0 |
| EXTRACT | 39 | 3 | 3.9 | 9 | 38 | 3.1 |
| ROSE | 6 | 40 | 25.5 | 46 | 5 | 0.4 |
| PLATFORMER | 2 | 159 | 152.5 | 159 | 1 | 0.1 |
| CRUDE mode R/L | 39 | 5 | 9.2 | 45 | 38 | 3.1 |

Shortest campaign observed per unit: EXTRACT 1d, HYDRO 1d, MEK 1d, PLATFORMER 146d, ROSE 3d. A minimum-run-length constraint should start from these, confirmed with operations.

## 4. The material chain (why units cannot be optimized separately)

**25 products are both produced by one unit and charged to another.** Each is a hard coupling: charging it consumes inventory that an earlier unit had to make first.

| Product | Name | Made by | Charged to |
|---|---|---|---|
| 4107 | KENSOL 17 | PLATFORMER | PLATFORMER |
| 4111 | KENSOL 30 | PLATFORMER | PLATFORMER |
| 4313 | KENDEX 0842 | CRUDE | ROSE |
| 4317 | KENDEX 0846 | ROSE | MEK |
| 4325 | KENDEX 165HTLV | MEK | EXTRACT |
| 4555 | KENDEX 0897 | ROSE | ROSE |
| 8170 | #2 ONROAD DIESEL S15 UNDYED | HYDRO | HYDRO |
| 8175 | #2 NRLM DIESEL S15 DYED | HYDRO | HYDRO |
| 9103 | PLATFORMER CHARGE (NAPHTHA) | CRUDE | PLATFORMER |
| 9116 | WAXY LIGHT NEUTRAL | CRUDE | MEK |
| 9117 | WAXY MEDIUM NEUTRAL | CRUDE | MEK |
| 9119 | HEAVY WAXY DISTILLATE | CRUDE | MEK |
| 9302 | DEWAXED MED NEUTRAL | MEK | EXTRACT |
| 9303 | DEWAXED HEAVY NEUTRAL | MEK | EXTRACT |
| 9305 | DEWAXED BRIGHT STOCK | MEK | EXTRACT |
| 9501 | Isomerate Run | PLATFORMER | PLATFORMER |
| 9505 | Light Straight Run | PLATFORMER | PLATFORMER |
| 9511 | PLATFORMATE | PLATFORMER | PLATFORMER |
| 9703 | KENSOL 61 UNHT | CRUDE | HYDRO |
| 9704 | KENDEX 0150 UNHT | EXTRACT | HYDRO |
| 9705 | KENDEX 0847 UNHT | EXTRACT | HYDRO |
| 9711 | KENSOL 48UNHT | CRUDE | HYDRO |
| 9712 | KENSOL 50 UNHT | CRUDE | HYDRO |
| 9713 | NO.2 DIESEL-HYDRO CHARGE | HYDRO | HYDRO |
| 9720 | DEWAXED, UNEXT LN CHARGE | MEK | HYDRO |

## 5. Where the planner's own baseline already breaks

The incumbent the optimizer must beat - and the reason commercial constraints have to be elastic rather than hard.

| Measure | Value |
|---|---:|
| product-days simulated | 19,764 |
| product-days below zero inventory | 3,913 (19.8%) |
| product-days above tank capacity | 3,525 (17.8%) |
| total negative inventory (gal-days) | 12,258,985,428 |
| total over-capacity (gal-days) | 12,630,222,857 |

Worst offenders:

| Product | Name | Dry days | Negative gal-days | Over-cap days | Over-cap gal-days |
|---|---|---:|---:|---:|---:|
| 4107 | KENSOL 17 | 0 | 0 | 361 | 4,958,405,877 |
| 8175 | #2 NRLM DIESEL S15 DYED | 366 | 4,921,566,786 | 0 | 0 |
| 9511 | PLATFORMATE | 366 | 4,350,525,000 | 0 | 0 |
| 8105 | HEATING OIL DYED RED&YELLOW | 0 | 0 | 364 | 2,152,316,572 |
| 9703 | KENSOL 61 UNHT | 0 | 0 | 200 | 1,010,145,932 |
| 9117 | WAXY MEDIUM NEUTRAL | 6 | 271,954 | 170 | 836,283,092 |
| DDDD |  | 201 | 835,410,843 | 0 | 0 |
| 4313 | KENDEX 0842 | 0 | 0 | 196 | 820,614,837 |
| 8170 | #2 ONROAD DIESEL S15 UNDYED | 0 | 0 | 343 | 744,667,849 |
| 9103 | PLATFORMER CHARGE (NAPHTHA) | 231 | 469,375,893 | 87 | 164,928,544 |
| 9711 | KENSOL 48UNHT | 0 | 0 | 200 | 479,109,880 |
| 9202 | HT WAXY MEDIUM NEUTRAL | 363 | 370,507,200 | 0 | 0 |

## 6. Demand structure

- Firm open orders exist for **35 products** across **57 calendar days** (2026-07-23 to 2026-10-15).
- That covers **57 of the 366 days** in the horizon (16%); beyond that, demand is entirely forecast.
- Forecast rates are published for 37 products, blend component demand for 15.

The near term is order-driven and the far term is forecast-driven. That argues directly for a detailed binary window over roughly the order horizon, and a coarse aggregate model beyond it.

## 7. Data gaps that would break a solver

- **8 product blocks have no tank capacity at all**, so any capacity constraint on them is vacuous: 8175 #2 NRLM DIESEL S15 DYED, 8136 #2 ONROAD DIESEL S15 B5 UNDYED, 8177 #2 HEATING OIL S15 DYED R&Y, 8120 #1 NRLM DIESEL S-15 DYED, 8165 #1 ONROAD DIESEL S15 UNDYED, 8125 #1 HEATING OIL S-15 DYED, 8115 #1 ONROAD DIESEL S15 B2 UNDYED, 8221 CAT-CRACKER
- 23 of 54 blocks have neither an LCL nor a UCL, so band penalties cannot steer them.
- Only 0 products have a lower control limit; 34 have an upper limit. A safety-stock objective needs LCLs that mostly do not exist yet.
- **9 products are charged at more than one line** (4111, 9116, 9117, 9202, 9305, 9703, 9711, 9712, 9720). The workbook only subtracts the first from inventory; the optimizer must subtract all of them or it will plan on feed that does not exist.

## 8. Problem size

| Horizon | Assignment binaries | Inventory continuous | Notes |
|---|---:|---:|---|
| 14 days | 406 | 756 | detailed window |
| 42 days | 1,218 | 2,268 | detailed window |
| 90 days | 2,610 | 4,860 | hard for a MIP |
| 366 days | 10,614 | 19,764 | must be aggregated |

28 process charge lines plus the crude mode; 54 inventory blocks. A 42-day detailed window is ~1,218 binaries - comfortably solvable with campaign constraints and a warm start. A year at daily resolution is ~10,614 and is not worth attempting directly.

## 9. Which blocks are real tank accounts

A MIP that constrains every block equally is infeasible before it starts, because many blocks are not tanks. Classified by whether they carry capacity and whether the planner's own baseline keeps them anywhere near it.

| Tier | Blocks | Meaning | Treatment in the MIP |
|---|---:|---|---|
| A - real, behaving | 10 | Has capacity and the baseline respects it | Hard `0 <= I <= cap`, penalised band |
| B - has capacity, baseline violates it | 36 | Either a genuine problem the plan already has, or the block is not really a tank | Elastic capacity with a penalty; review before trusting |
| C - no capacity at all | 8 | Pool or bookkeeping row, not a vessel | No capacity constraint; balance only |

**Tier B - review these first**

| Product | Name | Baseline low | Baseline high | Capacity |
|---|---|---:|---:|---:|
| 9511 | PLATFORMATE | -20,550,000 | -75,000 | 882,000 |
| 9103 | PLATFORMER CHARGE (NAPHTHA) | -6,452,825 | 4,646,053 | 1,770,000 |
| DDDD |  | -6,314,674 | 1,572,640 | 1,600,000 |
| 9704 | KENDEX 0150 UNHT | -2,780,938 | 757,314 | 1,200,000 |
| 4309 | KENDEX 0750 | -2,008,890 | 366,337 | 750,000 |
| 4115 | KENSOL 48H | -1,923,915 | 792,724 | 810,000 |
| 4129 | KENSOL 61H | -1,479,676 | 516,389 | 738,000 |
| 4554 | KENDEX 0834 | -1,252,262 | 227,031 | 765,000 |
| 9202 | HT WAXY MEDIUM NEUTRAL | -1,167,600 | 0 | 462,000 |
| 4118 | KENSOL 50H | -1,106,479 | 1,254,177 | 618,000 |
| 1128 | ISOMERATE | -984,665 | 1,464,209 | 1,260,000 |
| 4315 | KENDEX 0150H | -975,677 | 836,297 | 1,300,000 |
| 4451 | KENWAX MED NEUTRAL SLACK WAX | -901,541 | 290,010 | 704,000 |
| 4318 | ARGOLD LEGACY | -707,251 | 611,461 | 840,000 |
| 4329 | KENDEX 0060HT | -562,712 | 1,092,651 | 1,094,000 |
| 4319 | ARGOLD | -419,613 | 604,865 | 400,000 |
| 4555 | KENDEX 0897 | -404,350 | 259,587 | 300,000 |
| 4454 | KENWAX HEAVY NEUTRAL SLACK WAX | -309,011 | 201,021 | 330,000 |
| 4317 | KENDEX 0846 | -267,120 | 559,101 | 1,080,000 |
| 4577 | KENDEX MNE | -170,991 | 189,173 | 228,000 |
| 4459 | KENWAX 0111 PETROLATUM | -145,265 | 184,412 | 200,000 |
| 9117 | WAXY MEDIUM NEUTRAL | -110,734 | 11,794,838 | 2,069,970 |
| 9713 | NO.2 DIESEL-HYDRO CHARGE | -107,516 | 1,405,323 | 1,200,000 |
| 4449 | KENWAX LIGHT NEUTRAL SLACK WAX | -70,941 | 337,059 | 190,000 |
| 9712 | KENSOL 50 UNHT | -68,039 | 4,324,078 | 535,500 |
| 9119 | HEAVY WAXY DISTILLATE | -44,129 | 3,463,060 | 451,500 |
| 8105 | HEATING OIL DYED RED&YELLOW | 0 | 7,861,560 | 176,834 |
| 4579 | KENDEX D&N | 0 | 65,016 | 36,000 |
| 4107 | KENSOL 17 | 17,991 | 27,419,767 | 273,000 |
| 4313 | KENDEX 0842 | 29,738 | 8,546,402 | 440,000 |
| 9711 | KENSOL 48UNHT | 56,010 | 5,755,464 | 650,000 |
| 4111 | KENSOL 30 | 68,659 | 5,263,952 | 1,100,000 |
| 9118 | LOW VOLATILITY MEDIUM NEUTRAL | 72,762 | 2,319,309 | 815,000 |
| 9718 | WAXY LLN DISTILLATE UNHT | 85,650 | 3,916,848 | 1,000,000 |
| 9703 | KENSOL 61 UNHT | 251,323 | 10,495,768 | 676,200 |
| 8170 | #2 ONROAD DIESEL S15 UNDYED | 500,073 | 3,730,240 | 763,590 |

**Tier C - no capacity**

| Product | Name | Baseline low | Baseline high | Capacity |
|---|---|---:|---:|---:|
| 8175 | #2 NRLM DIESEL S15 DYED | -21,418,321 | -30,000 | 0 |
| 8136 | #2 ONROAD DIESEL S15 B5 UNDYED | 0 | 0 | 0 |
| 8177 | #2 HEATING OIL S15 DYED R&Y | 0 | 0 | 0 |
| 8120 | #1 NRLM DIESEL S-15 DYED | 0 | 0 | 0 |
| 8165 | #1 ONROAD DIESEL S15 UNDYED | 0 | 0 | 0 |
| 8125 | #1 HEATING OIL S-15 DYED | 0 | 0 | 0 |
| 8115 | #1 ONROAD DIESEL S15 B2 UNDYED | 0 | 0 | 0 |
| 8221 | CAT-CRACKER | 0 | 0 | 0 |

## 10. How far out the baseline is meaningful

If violations grow with distance, the annual projection is extrapolation rather than a plan - which decides how far the optimizer should look and how far the baseline can be trusted as a warm start.

| Window | Product-days dry | Product-days over capacity | Blocks dry | Blocks over |
|---|---:|---:|---:|---:|
| days 1-14 | 6.7% | 2.8% | 7 | 2 |
| days 15-42 | 8.7% | 5.5% | 8 | 4 |
| days 43-90 | 7.6% | 7.3% | 7 | 8 |
| days 91-180 | 8.1% | 14.3% | 10 | 14 |
| days 181-366 | 31.3% | 25.3% | 23 | 17 |

Per-product drift, day 14 against day 366:

| Product | Name | Capacity | Day 14 | Day 366 | Day 366 / capacity |
|---|---|---:|---:|---:|---:|
| 4107 | KENSOL 17 | 273,000 | 1,009,933 | 27,416,816 | 100x |
| 8175 | #2 NRLM DIESEL S15 DYED | 0 | -670,000 | -21,418,321 | no capacity |
| 9511 | PLATFORMATE | 882,000 | -1,050,000 | -20,550,000 | -23x |
| 9117 | WAXY MEDIUM NEUTRAL | 2,069,970 | 360,305 | 11,794,838 | 6x |
| 9703 | KENSOL 61 UNHT | 676,200 | 251,323 | 10,490,850 | 16x |
| 4313 | KENDEX 0842 | 440,000 | 88,612 | 8,546,008 | 19x |
| 8105 | HEATING OIL DYED RED&YELLOW | 176,834 | 658,560 | 7,861,560 | 44x |
| 9103 | PLATFORMER CHARGE (NAPHTHA) | 1,770,000 | -118,285 | -6,452,825 | -4x |
| 9711 | KENSOL 48UNHT | 350,000 | 124,201 | 5,755,464 | 16x |
| DDDD |  | 1,600,000 | 729,633 | -6,314,674 | -4x |
