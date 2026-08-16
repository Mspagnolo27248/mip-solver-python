# The current plan, priced

Window: first 42 days, 2026-07-23 to 2026-09-02. Crude at **$70.00/bbl ($1.6667/gal)**.

## Downgrade outlets

| Outlet | Netback $/gal | Netback $/bbl | Loss vs crude $/gal | Basis |
|---|---:|---:|---:|---|
| #6 oil | 1.1667 | 49.00 | 0.50 | crude less $0.50/gal |
| Cat cracker | 1.1667 | 49.00 | 0.50 | crude less $0.50/gal |
| Diesel | — | — | — | **needs a price** |
| Finished diesel | — | — | — | **needs a price** |
| Gasoline | — | — | — | **needs a price** |

## Cost of the current plan over 42 days

| Component | Volume (gal) | $/gal | Cost | Basis |
|---|---:|---:|---:|---|
| Downgrade required | 5,595,214 | 0.50 | **$2,797,607** | flat: material valued at crude |
| Lost sales | 6,673,800 | ? | **not yet priced** | needs a margin |

4 of the 4 products with downgrade volume are priced flat and 0 per product. Swapping in real values only moves the number up: the flat figure is the floor.

## Where the trade-off flips

Downgrading costs $0.50/gal. Shorting a customer costs their margin. The ratio between the two is what the optimizer actually reasons about, so here is the whole range rather than a guess.

| Lost-sale margin $/gal | Cost of lost sales | Total plan cost | Lost sales as share | Optimizer's preference |
|---:|---:|---:|---:|---|
| 0.25 | $1,668,450 | $4,466,057 | 37% | short the customer before downgrading |
| 0.50 | $3,336,900 | $6,134,507 | 54% | indifferent |
| 1.00 | $6,673,800 | $9,471,407 | 70% | downgrade 2x before shorting |
| 2.00 | $13,347,600 | $16,145,207 | 83% | downgrade 4x before shorting |
| 3.00 | $20,021,400 | $22,819,007 | 88% | downgrade 6x before shorting |

Any margin above $0.50/gal makes downgrading the cheaper relief, which matches how the plant is described as operating. Below it, the model would rather lose the sale - so if a real margin ever comes in under $0.50/gal, that is worth questioning before trusting the schedule.

## By product, 42 days

| Product | Name | Downgrade (gal) | Downgrade cost | Lost sales (gal) |
|---|---|---:|---:|---:|
| 4107 | KENSOL 17 | 3,315,791 | $1,657,895 | 0 |
| 9511 | PLATFORMATE | 0 | $0 | 3,150,000 |
| 8175 | #2 NRLM DIESEL S15 DYED | 0 | $0 | 2,940,000 |
| 8105 | HEATING OIL DYED RED&YELLOW | 1,848,238 | $924,119 | 0 |
| 9202 | HT WAXY MEDIUM NEUTRAL | 0 | $0 | 583,800 |
| 8170 | #2 ONROAD DIESEL S15 UNDYED | 348,416 | $174,208 | 0 |
| 4319 | ARGOLD | 82,769 | $41,384 | 0 |

Two of the largest lines are data problems rather than planning problems: 8175 has no tank capacity recorded, and 9202 is an obsolete flow that nothing produces.

## 9202 and the tolling route

| Check | Result |
|---|---|
| 9202 charged at MEK | **never** |
| Tolling unit used | **never** |
| 9202 produced over the year | 0 gal |
| Demand still booked against 9202 | 1,167,600 gal |

Confirms the flow is obsolete. Retiring it makes the MEK ladder exactly four rungs (9116 – 9117 – 9119 – 4317), drops the tolling unit from the model, and removes a permanent lost sale the optimizer could never avoid.
