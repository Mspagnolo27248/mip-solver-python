# The current plan, priced

The same schedule, simulated twice: once the way the workbook computes it, and once with the tanks actually enforced (`0 <= inventory <= capacity`), recording what it cost to stay inside them.

## Totals

| Window | Lost sales (gal) | Downgrade required (gal) | Feed shortfall (gal) | Products affected |
|---|---:|---:|---:|---:|
| first 14 days | 1,909,000 | 1,218,659 | 215,452 | 9 |
| first 42 days | 6,673,800 | 5,595,214 | 870,449 | 12 |
| first 90 days | 14,328,600 | 12,734,509 | 1,955,021 | 17 |
| full year | 67,710,656 | 95,361,155 | 12,664,472 | 38 |

Feed shortfall is the one that matters most for the optimizer: it is volume the schedule asked a unit to charge that the tank could not supply. Unlike a lost sale or a downgrade it has no price - it means the plan was not executable as written.

## By product, first 42 days

| Product | Name | Capacity | Lost sales | Downgrade required | Feed shortfall |
|---|---|---:|---:|---:|---:|
| 4107 | KENSOL 17 | 273,000 | 0 | 3,315,791 | 0 |
| 9511 | PLATFORMATE | 882,000 | 3,150,000 | 0 | 0 |
| 8175 | #2 NRLM DIESEL S15 DYED | **none** | 2,940,000 | 0 | 0 |
| 8105 | HEATING OIL DYED RED&YELLOW | 176,834 | 0 | 1,848,238 | 0 |
| 9103 | PLATFORMER CHARGE (NAPHTHA) | 1,770,000 | 0 | 0 | 708,875 |
| 9202 | HT WAXY MEDIUM NEUTRAL | 462,000 | 583,800 | 0 | 0 |
| 8170 | #2 ONROAD DIESEL S15 UNDYED | 763,590 | 0 | 348,416 | 0 |
| 4319 | ARGOLD | 400,000 | 0 | 82,769 | 0 |
| 9712 | KENSOL 50 UNHT | 535,500 | 0 | 0 | 68,039 |
| 9713 | NO.2 DIESEL-HYDRO CHARGE | 1,200,000 | 0 | 0 | 49,160 |
| 9119 | HEAVY WAXY DISTILLATE | 451,500 | 0 | 0 | 42,213 |
| 9305 | DEWAXED BRIGHT STOCK | 640,000 | 0 | 0 | 2,161 |

## By product, full year

| Product | Name | Capacity | Lost sales | Downgrade required | Feed shortfall |
|---|---|---:|---:|---:|---:|
| 4107 | KENSOL 17 | 273,000 | 0 | 27,146,767 | 0 |
| 8175 | #2 NRLM DIESEL S15 DYED | **none** | 21,418,321 | 0 | 0 |
| 9511 | PLATFORMATE | 882,000 | 20,550,000 | 0 | 0 |
| 9103 | PLATFORMER CHARGE (NAPHTHA) | 1,770,000 | 281,549 | 5,833,365 | 12,004,641 |
| 9117 | WAXY MEDIUM NEUTRAL | 2,069,970 | 0 | 9,835,602 | 110,734 |
| 9703 | KENSOL 61 UNHT | 676,200 | 0 | 9,819,568 | 0 |
| 4313 | KENDEX 0842 | 440,000 | 0 | 8,106,402 | 0 |
| 8105 | HEATING OIL DYED RED&YELLOW | 176,834 | 0 | 7,684,726 | 0 |
| DDDD |  | 1,600,000 | 6,440,506 | 125,832 | 0 |
| 4111 | KENSOL 30 | 1,100,000 | 314,895 | 4,771,574 | 292,728 |
| 9711 | KENSOL 48UNHT | 350,000 | 0 | 5,105,464 | 0 |
| 9712 | KENSOL 50 UNHT | 535,500 | 0 | 3,891,141 | 102,563 |
| 9119 | HEAVY WAXY DISTILLATE | 451,500 | 0 | 3,055,689 | 44,129 |
| 8170 | #2 ONROAD DIESEL S15 UNDYED | 763,590 | 0 | 2,966,650 | 0 |
| 9718 | WAXY LLN DISTILLATE UNHT | 1,000,000 | 0 | 2,916,848 | 0 |
| 9704 | KENDEX 0150 UNHT | 1,200,000 | 2,780,938 | 0 | 0 |
| 4118 | KENSOL 50H | 618,000 | 1,742,656 | 636,177 | 0 |
| 1128 | ISOMERATE | 1,260,000 | 984,665 | 1,188,873 | 0 |
| 4309 | KENDEX 0750 | 750,000 | 2,008,890 | 0 | 0 |
| 4115 | KENSOL 48H | 730,000 | 1,923,915 | 0 | 0 |
| 4129 | KENSOL 61H | 738,000 | 1,558,066 | 78,389 | 0 |
| 9118 | LOW VOLATILITY MEDIUM NEUTRAL | 815,000 | 0 | 1,504,309 | 0 |

## What enforcing the tanks changes

Peak inventory in the workbook against the physical run. Where the workbook climbs far above capacity it is not forecasting inventory, it is accumulating material the plan never disposed of.

| Product | Capacity | Workbook peak | Physical peak | Workbook / capacity |
|---|---:|---:|---:|---:|
| 4107 KENSOL 17 | 273,000 | 27,419,767 | 273,000 | 100x |
| 8105 HEATING OIL DYED RED&YELLOW | 176,834 | 7,861,560 | 176,834 | 44x |
| 4313 KENDEX 0842 | 440,000 | 8,546,402 | 440,000 | 19x |
| 9711 KENSOL 48UNHT | 350,000 | 5,755,464 | 650,000 | 16x |
| 9703 KENSOL 61 UNHT | 676,200 | 10,495,768 | 676,200 | 16x |
| 9712 KENSOL 50 UNHT | 535,500 | 4,324,078 | 535,500 | 8x |
| 9119 HEAVY WAXY DISTILLATE | 451,500 | 3,463,060 | 451,500 | 8x |
| 9117 WAXY MEDIUM NEUTRAL | 2,069,970 | 11,794,838 | 2,069,970 | 6x |
| 8170 #2 ONROAD DIESEL S15 UNDYED | 763,590 | 3,730,240 | 763,590 | 5x |
| 4111 KENSOL 30 | 1,100,000 | 5,263,952 | 1,100,000 | 5x |
| 9718 WAXY LLN DISTILLATE UNHT | 1,000,000 | 3,916,848 | 1,000,000 | 4x |
| 9118 LOW VOLATILITY MEDIUM NEUTRAL | 815,000 | 2,319,309 | 815,000 | 3x |

## Where the downgrades would go

The plan already routes some volume to low-netback outlets explicitly. These are the existing routes the optimizer should use, rather than inventing new ones.

| Route | Volume in the current plan (gal) |
|---|---:|
| Solvents -> diesel charge | 6,425,862 |
| K-30 -> finished diesel | 3,230,167 |
| Sonneborn sales | 1,167,600 |

Total explicitly routed in the plan: 10,823,629 gal. Against a required downgrade of 95,361,155 gal over the same horizon, the gap is what the planner is doing by hand - or not doing at all.
