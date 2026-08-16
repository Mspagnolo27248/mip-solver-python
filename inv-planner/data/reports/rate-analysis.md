# Maximum charge rates

`charge = max_rate x time_fraction`, so what the model needs is an absolute barrels-per-day maximum. The workbook's monthly run rate is a *planning* figure, not a ceiling - products run above it routinely, so using it as the maximum would cap the optimizer below what the plant already does.

## Observed maxima against the planning rate

`clean max` is the largest charge on a day with no changeover either side - a genuinely full day. `any max` includes partial days, so it understates. Where there is no clean day, the true maximum is higher than anything here.

| Unit | Charge | Name | Planning rate | Clean max | Any max | Clean/planning | Recommended max |
|---|---|---|---:|---:|---:|---:|---:|
| MEK | 9117 | WAXY MEDIUM NEUTRAL | 4,200 | 4,000 | 4,000 | 0.95 | **4,000** |
| MEK | 9119 | HEAVY WAXY DISTILLATE | 2,100 | 2,100 | 2,100 | 1.00 | **2,100** |
| MEK | 9116 | WAXY LIGHT NEUTRAL | 4,400 | 4,000 | 4,000 | 0.91 | **4,000** |
| MEK | 4317 | KENDEX 0846 | 2,400 | 2,400 | 2,400 | 1.00 | **2,400** |
| HYDRO | 9713 | NO.2 DIESEL-HYDRO CHAR | — | 5,000 | 5,000 | — | **5,000** |
| HYDRO | 9705 | KENDEX 0847 UNHT | 2,900 | 2,500 | 3,000 | 0.86 | **2,500** |
| HYDRO | 9711 | KENSOL 48UNHT | 5,200 | **none** | 5,200 | — | **5,943** |
| HYDRO | 9712 | KENSOL 50 UNHT | 5,200 | **none** | 5,200 | — | **5,943** |
| HYDRO | 9703 | KENSOL 61 UNHT | 5,000 | **none** | 5,000 | — | **5,714** |
| HYDRO | 9720 | DEWAXED, UNEXT LN CHAR | 5,200 | **none** | 4,800 | — | **5,486** |
| HYDRO | 9704 | KENDEX 0150 UNHT | 5,250 | 5,200 | 5,200 | 0.99 | **5,200** |
| EXTRACT | 9302 | DEWAXED MED NEUTRAL | 2,800 | 2,800 | 2,800 | 1.00 | **2,800** |
| EXTRACT | 9303 | DEWAXED HEAVY NEUTRAL | 2,400 | 1,800 | 2,100 | 0.75 | **1,800** |
| EXTRACT | 9305 | DEWAXED BRIGHT STOCK | 2,000 | 2,200 | 2,200 | 1.10 | **2,200** |
| EXTRACT | 9305 | DEWAXED BRIGHT STOCK | 2,000 | 1,300 | 1,300 | 0.65 | **1,300** |
| ROSE | 4313 | KENDEX 0842 | 1,000 | 1,300 | 1,300 | 1.30 | **1,300** |
| ROSE | 4555 | KENDEX 0897 | 1,000 | 1,000 | 1,000 | 1.00 | **1,000** |
| PLATFORMER | 9103 | PLATFORMER CHARGE (NAP | 3,650 | 4,000 | 4,000 | 1.10 | **4,000** |
| PLATFORMER | 9505 | Light Straight Run | — | 648 | 648 | — | **648** |

Where `clean max` is blank the recommendation is the observed maximum grossed up by one changeover's time loss, since the day it was set was necessarily partial. It remains a floor, not a specification.

## Config snippet

```python
MAX_RATE_BBL_PER_DAY = {
    "MEK": {
        "9117": {"bbl": 4000, "basis": "clean day", "clean_days": 22},
        "9119": {"bbl": 2100, "basis": "clean day", "clean_days": 3},
        "9116": {"bbl": 4000, "basis": "clean day", "clean_days": 2},
        "4317": {"bbl": 2400, "basis": "clean day", "clean_days": 24},
    },
    "HYDRO": {
        "9713": {"bbl": 5000, "basis": "clean day", "clean_days": 9},
        "9705": {"bbl": 2500, "basis": "clean day", "clean_days": 1},
        "9711": {"bbl": 5943, "basis": "grossed up", "clean_days": 0},
        "9712": {"bbl": 5943, "basis": "grossed up", "clean_days": 0},
        "9703": {"bbl": 5714, "basis": "grossed up", "clean_days": 0},
        "9720": {"bbl": 5486, "basis": "grossed up", "clean_days": 0},
        "9704": {"bbl": 5200, "basis": "clean day", "clean_days": 4},
    },
    "EXTRACT": {
        "9302": {"bbl": 2800, "basis": "clean day", "clean_days": 47},
        "9303": {"bbl": 1800, "basis": "clean day", "clean_days": 1},
        "9305": {"bbl": 2200, "basis": "clean day", "clean_days": 5},
        "9305": {"bbl": 1300, "basis": "clean day", "clean_days": 16},
    },
    "ROSE": {
        "4313": {"bbl": 1300, "basis": "clean day", "clean_days": 139},
        "4555": {"bbl": 1000, "basis": "clean day", "clean_days": 2},
    },
    "PLATFORMER": {
        "9103": {"bbl": 4000, "basis": "clean day", "clean_days": 301},
        "9505": {"bbl": 648, "basis": "clean day", "clean_days": 301},
    },
}
```

## Worked example of the day budget

Two charges sharing a day, taken from the plant's own description.

```
  diesel   max 5,000 bbl/day
  4139     max 3,000 bbl/day

  no changeover loss, day split 50/50:
      t[diesel] = 0.5  ->  5,000 x 0.5 = 2,500 bbl
      t[4139]   = 0.5  ->  3,000 x 0.5 = 1,500 bbl
      sum of time = 1.0                            OK

  same split, but the two are on different reactors:
      interface loss 0.125 + reactor flush 0.250 = 0.375 of the day gone
      time left to share = 0.625, say 0.3125 each
      t[diesel] = 0.3125 -> 5,000 x 0.3125 = 1,563 bbl
      t[4139]   = 0.3125 -> 3,000 x 0.3125 =   938 bbl
```

The second case is the whole point: crossing reactors costs 37.5% of the day's production on that unit, which is why the plant groups 4315 and 4319 - and why the optimizer will too, without being told.
