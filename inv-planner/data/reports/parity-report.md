# Parity report: engine vs. workbook cached values

## Balance cells

| metric | count |
|---|---:|
| compared | 88,464 |
| matching | 88,464 (100.0000%) |
| unexplained differences | 0 |
| known workbook defects (allowlisted) | 0 |
| workbook holds an Excel error | 0 |

## Production grid (gallons by product/day)

| metric | count |
|---|---:|
| compared | 8,160 |
| matching | 8,160 (100.0000%) |
| differing | 0 |
| workbook holds an Excel error | 0 |

## Per balance row

| row | compared | unexplained | known defect | workbook errors |
|---|---:|---:|---:|---:|
| Begin Inventory | 8,800 | 0 | 0 | 0 |
| Blends | 8,640 | 0 | 0 | 0 |
| Downgrade | 160 | 0 | 0 | 0 |
| End Inventory | 8,800 | 0 | 0 | 0 |
| Excess Capacity | 8,799 | 0 | 0 | 0 |
| Forecast | 8,465 | 0 | 0 | 0 |
| Net Charge Available | 8,800 | 0 | 0 | 0 |
| Out to Diesel | 1,760 | 0 | 0 | 0 |
| Production In | 7,840 | 0 | 0 | 0 |
| Production Out | 8,800 | 0 | 0 | 0 |
| Receipts | 160 | 0 | 0 | 0 |
| Sales | 8,640 | 0 | 0 | 0 |
| Tank Capacity | 8,800 | 0 | 0 | 0 |

## Products charged at more than one unit

The workbook's `Production Out` row resolves with a first-match VLOOKUP over the whole charge grid, so only the first line below is subtracted from inventory. Confirm with operations which is intended before the rebuild changes behaviour.

- 4111 charged at PLATFORMER row 105 and TRANSFER_DIESEL row 117 and TRANSFER_FINDSL row 135
- 9116 charged at MEK row 72 and TRANSFER_DIESEL row 115
- 9117 charged at MEK row 69 and SONNEBORN row 122
- 9202 charged at MEK row 70 and TOLLING row 94
- 9305 charged at EXTRACT row 90 and EXTRACT row 91
- 9703 charged at HYDRO row 82 and TRANSFER_DIESEL row 113
- 9711 charged at HYDRO row 80 and TRANSFER_DIESEL row 111
- 9712 charged at HYDRO row 81 and TRANSFER_DIESEL row 112
- 9720 charged at HYDRO row 83 and TRANSFER_DIESEL row 114
