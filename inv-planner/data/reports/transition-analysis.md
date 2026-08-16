# Observed changeovers

Measured over 366 days of the planner's own schedule. `gap` is the number of idle days between one campaign ending and the next starting.

## MEK

Charge options: 4317 KENDEX 0846, 9116 WAXY LIGHT NEUTRAL, 9117 WAXY MEDIUM NEUTRAL, 9119 HEAVY WAXY DISTILLATE

**Transition matrix** — rows = from, columns = to. `n` back-to-back / `n*` across an idle gap.

| from \ to | 4317 | 9116 | 9117 | 9119 |
|---|---|---|---|---|
| **4317** | · | · | · | 9 |
| **9116** | · | · | 9 | · |
| **9117** | · | 9 | · | 10 |
| **9119** | 9 / 1* | · | 9 | · |

- 6 of 12 possible product-to-product transitions are ever used (50%).
- **Never observed:** 4317->9116, 4317->9117, 9116->4317, 9116->9119, 9117->4317, 9119->9116
- Always back to back: 9117->9119, 4317->9119, 9119->9117, 9117->9116, 9116->9117

**Campaign length by product**

| Product | Campaigns | Min | Median | Max |
|---|---:|---:|---:|---:|
| 4317 KENDEX 0846 | 10 | 3 | 5 | 5 |
| 9116 WAXY LIGHT NEUTRAL | 9 | 2 | 2 | 3 |
| 9117 WAXY MEDIUM NEUTRAL | 19 | 2 | 3 | 4 |
| 9119 HEAVY WAXY DISTILLATE | 19 | 1 | 2 | 3 |

- 55 of 56 changeovers happen back to back; 1 cross an idle gap (median 2 days).

## HYDRO

Charge options: 9703 KENSOL 61 UNHT, 9704 KENDEX 0150 UNHT, 9704+9705 , 9704+9713 , 9705 KENDEX 0847 UNHT, 9711 KENSOL 48UNHT, 9711+9712 , 9711+9713 , 9711+9720 , 9712 KENSOL 50 UNHT, 9713 NO.2 DIESEL-HYDRO CHARGE, 9720 DEWAXED, UNEXT LN CHARGE

**Transition matrix** — rows = from, columns = to. `n` back-to-back / `n*` across an idle gap.

| from \ to | 9703 | 9704 | 9704+9705 | 9704+9713 | 9705 | 9711 | 9711+9712 | 9711+9713 | 9711+9720 | 9712 | 9713 | 9720 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **9703** | · | 1 | · | · | · | · | · | · | · | 4 | 1 | 3 |
| **9704** | · | · | 1 | 1 | 7 | · | · | · | · | · | · | · |
| **9704+9705** | · | · | · | · | 1 | · | · | · | · | · | · | · |
| **9704+9713** | · | · | · | · | · | 1* | · | · | · | · | · | · |
| **9705** | · | · | · | · | · | · | · | 1 | · | · | 9 | · |
| **9711** | 1 | 2 | · | · | · | · | 2 | · | 1 | 8 | 3 / 1* | 1 |
| **9711+9712** | · | · | · | · | · | · | · | · | · | 3 | · | · |
| **9711+9713** | · | · | · | · | · | 2 | · | · | · | · | · | 1 |
| **9711+9720** | · | · | · | · | · | · | · | · | · | · | · | 1 |
| **9712** | 5 | 1 | · | · | · | 3 | · | · | · | · | 4 | 1 |
| **9713** | 1 | 4 | · | · | 1* | 11 / 2* | · | 2 | · | · | · | · |
| **9720** | 2 | 1 | · | · | · | · | 1 | · | · | · | 3 | · |

- 36 of 132 possible product-to-product transitions are ever used (27%).
- **Never observed:** 9703->9704+9705, 9703->9704+9713, 9703->9705, 9703->9711, 9703->9711+9712, 9703->9711+9713, 9703->9711+9720, 9704->9703, 9704->9711, 9704->9711+9712, 9704->9711+9713, 9704->9711+9720, 9704->9712, 9704->9713, 9704->9720, 9704+9705->9703, 9704+9705->9704, 9704+9705->9704+9713, 9704+9705->9711, 9704+9705->9711+9712, 9704+9705->9711+9713, 9704+9705->9711+9720, 9704+9705->9712, 9704+9705->9713, 9704+9705->9720, 9704+9713->9703, 9704+9713->9704, 9704+9713->9704+9705, 9704+9713->9705, 9704+9713->9711+9712, 9704+9713->9711+9713, 9704+9713->9711+9720, 9704+9713->9712, 9704+9713->9713, 9704+9713->9720, 9705->9703, 9705->9704, 9705->9704+9705, 9705->9704+9713, 9705->9711, 9705->9711+9712, 9705->9711+9720, 9705->9712, 9705->9720, 9711->9704+9705, 9711->9704+9713, 9711->9705, 9711->9711+9713, 9711+9712->9703, 9711+9712->9704, 9711+9712->9704+9705, 9711+9712->9704+9713, 9711+9712->9705, 9711+9712->9711, 9711+9712->9711+9713, 9711+9712->9711+9720, 9711+9712->9713, 9711+9712->9720, 9711+9713->9703, 9711+9713->9704, 9711+9713->9704+9705, 9711+9713->9704+9713, 9711+9713->9705, 9711+9713->9711+9712, 9711+9713->9711+9720, 9711+9713->9712, 9711+9713->9713, 9711+9720->9703, 9711+9720->9704, 9711+9720->9704+9705, 9711+9720->9704+9713, 9711+9720->9705, 9711+9720->9711, 9711+9720->9711+9712, 9711+9720->9711+9713, 9711+9720->9712, 9711+9720->9713, 9712->9704+9705, 9712->9704+9713, 9712->9705, 9712->9711+9712, 9712->9711+9713, 9712->9711+9720, 9713->9704+9705, 9713->9704+9713, 9713->9711+9712, 9713->9711+9720, 9713->9712, 9713->9720, 9720->9704+9705, 9720->9704+9713, 9720->9705, 9720->9711, 9720->9711+9713, 9720->9711+9720, 9720->9712
- **Only ever across an idle gap** (never back to back): 9713->9705 (gaps 1), 9704+9713->9711 (gaps 1)
- Always back to back: 9705->9713, 9711->9712, 9712->9703, 9703->9720, 9720->9713, 9711->9711+9712, 9711+9712->9712, 9703->9704, 9704->9704+9705, 9704+9705->9705, 9712->9713, 9711->9711+9720, 9711+9720->9720, 9720->9703, 9703->9712, 9712->9711, 9711->9704, 9704->9704+9713, 9711->9703, 9713->9704, 9704->9705, 9713->9711+9713, 9711+9713->9720, 9713->9703, 9711+9713->9711, 9720->9704, 9711->9720, 9720->9711+9712, 9712->9704, 9703->9713, 9712->9720, 9705->9711+9713

**Campaign length by product**

| Product | Campaigns | Min | Median | Max |
|---|---:|---:|---:|---:|
| 9703 KENSOL 61 UNHT | 9 | 1 | 1 | 2 |
| 9704 KENDEX 0150 UNHT | 9 | 1 | 2 | 3 |
| 9704+9705  | 1 | 1 | 1 | 1 |
| 9704+9713  | 1 | 1 | 1 | 1 |
| 9705 KENDEX 0847 UNHT | 10 | 1 | 2 | 2 |
| 9711 KENSOL 48UNHT | 19 | 1 | 1 | 2 |
| 9711+9712  | 3 | 1 | 1 | 1 |
| 9711+9713  | 3 | 1 | 1 | 1 |
| 9711+9720  | 1 | 1 | 1 | 1 |
| 9712 KENSOL 50 UNHT | 15 | 1 | 1 | 2 |
| 9713 NO.2 DIESEL-HYDRO CHARGE | 21 | 1 | 2 | 4 |
| 9720 DEWAXED, UNEXT LN CHARGE | 7 | 1 | 2 | 2 |

- 93 of 98 changeovers happen back to back; 5 cross an idle gap (median 1 days).

## EXTRACT

Charge options: 9302 DEWAXED MED NEUTRAL, 9303 DEWAXED HEAVY NEUTRAL, 9303+9305 , 9305 DEWAXED BRIGHT STOCK (DEEP

**Transition matrix** — rows = from, columns = to. `n` back-to-back / `n*` across an idle gap.

| from \ to | 9302 | 9303 | 9303+9305 | 9305 |
|---|---|---|---|---|
| **9302** | 1* | 10 / 1* | · | · |
| **9303** | 5 / 1* | · | · | 9 / 1* |
| **9303+9305** | 1 | · | · | · |
| **9305** | 3 | 5 | 1 | · |

- 7 of 12 possible product-to-product transitions are ever used (58%).
- **Never observed:** 9302->9303+9305, 9302->9305, 9303->9303+9305, 9303+9305->9303, 9303+9305->9305
- Always back to back: 9305->9303, 9305->9302, 9305->9303+9305, 9303+9305->9302

**Campaign length by product**

| Product | Campaigns | Min | Median | Max |
|---|---:|---:|---:|---:|
| 9302 DEWAXED MED NEUTRAL | 12 | 2 | 7 | 9 |
| 9303 DEWAXED HEAVY NEUTRAL | 16 | 1 | 2 | 3 |
| 9303+9305  | 1 | 1 | 1 | 1 |
| 9305 DEWAXED BRIGHT STOCK (DEEP | 10 | 3 | 6 | 7 |

- 34 of 38 changeovers happen back to back; 4 cross an idle gap (median 1 days).

## ROSE

Charge options: 4313 KENDEX 0842, 4555 LR RESINS

**Transition matrix** — rows = from, columns = to. `n` back-to-back / `n*` across an idle gap.

| from \ to | 4313 | 4555 |
|---|---|---|
| **4313** | 1* | 2 |
| **4555** | 2 | · |

- 2 of 2 possible product-to-product transitions are ever used (100%).
- Always back to back: 4313->4555, 4555->4313

**Campaign length by product**

| Product | Campaigns | Min | Median | Max |
|---|---:|---:|---:|---:|
| 4313 KENDEX 0842 | 4 | 20 | 41 | 46 |
| 4555 LR RESINS | 2 | 3 | 3 | 3 |

- 4 of 5 changeovers happen back to back; 1 cross an idle gap (median 9 days).

## PLATFORMER

Charge options: 9103+9505 

**Transition matrix** — rows = from, columns = to. `n` back-to-back / `n*` across an idle gap.

| from \ to | 9103+9505 |
|---|---|
| **9103+9505** | 1* |

- 0 of 0 possible product-to-product transitions are ever used (0%).

**Campaign length by product**

| Product | Campaigns | Min | Median | Max |
|---|---:|---:|---:|---:|
| 9103+9505  | 2 | 146 | 159 | 159 |

- 0 of 1 changeovers happen back to back; 1 cross an idle gap (median 53 days).
