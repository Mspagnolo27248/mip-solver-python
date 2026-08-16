# The process, as modelled

**Confirmed with operations, 2026-08-11.** This is the authoritative description of
the plant the optimizer plans. Every yield, rate and route below is read from the
model's live configuration and the workbook's own yield rules, then checked
against what operations says the plant actually does.

Three things in here were wrong until that check happened, and each cost more than
the modelling that surrounded it:

- **Kensol 17 is the platformer charge**, not a product with nowhere to go. The
  workbook names 9103 "PLATFORMER CHARGE (NAPHTHA)", but 9103 charges the
  *fractionator*; the reformer's feed is 4107. Reading product names instead of
  yield rules is how the reformer leg went missing, and it cost 67% of the
  optimizer's objective — platformate booked as a lost sale, and the heart cut
  that would have made it booked as a downgrade. The same barrels, paid for twice.
- **The Platformer is three vessels**, running in parallel. Given one shared time
  budget they cannot all run, and the model was infeasible.
- **Extraction runs 9305 two ways at different rates.** Keyed on the feed alone,
  deep extraction inherited the normal mode's ceiling and was scheduled 69% above
  what the unit can make.

```
════════════════════════════════════════════════════════════════════════════════
  CRUDE UNIT          fixed rate (~9,500 bbl/d, user input) · R/L mode is a decision
════════════════════════════════════════════════════════════════════════════════
    ├─ 9116  Waxy Light Neutral ─────────────────────────────────────▶ MEK
    ├─ 9117  Waxy Medium Neutral       [R mode, 313 d] ──────────────▶ MEK
    ├─ 9118  Sonneborn feed            [L mode,  47 d] ──────────────▶ sold
    ├─ 9119  Heavy Waxy Distillate ─────────────────────────────────▶ MEK
    ├─ 4313  Kendex 0842 ───────────────────────────────────────────▶ ROSE
    ├─ 9103  Naphtha ("Platformer Charge") ─────────────────────────▶ FRACTIONATOR
    ├─ 9703  Kensol 61 UNHT ────────────────────────────────────────▶ HYDRO R2
    ├─ 9711  Kensol 48 UNHT ────────────────────────────────────────▶ HYDRO R2
    └─ 9712  Kensol 50 UNHT ────────────────────────────────────────▶ HYDRO R2

════════════════════════════════════════════════════════════════════════════════
  MEK  dewaxing        ladder 9116 ─ 9117 ─ 9119 ─ 4317, adjacent moves only
════════════════════════════════════════════════════════════════════════════════
  9117 ◀crude  4,000 ─┬─ 82.5% ▶ 9302 Dewaxed Med Neutral ───────────▶ EXTRACT
                      └─ 17.5% ▶ 4451 Slack wax ─────────── sold · ▶ CAT
  9119 ◀crude  2,100 ─┬─ 81.0% ▶ 9303 Dewaxed Heavy Neutral ─────────▶ EXTRACT
                      └─ 19.0% ▶ 4454 Slack wax ─────────── sold · ▶ #6 OIL
  9116 ◀crude  4,000 ─┬─ 81.5% ▶ 9720 Dewaxed Unext LN ──────────────▶ HYDRO R2
                      └─ 18.5% ▶ 4449 Slack wax ─────────── sold · ▶ CAT
  4317 ◀ROSE   2,400 ─┬─ 84.0% ▶ 9305 Dewaxed Bright Stock ──────────▶ EXTRACT
                      └─ 16.0% ▶ 4459 ────────────────────── sold

════════════════════════════════════════════════════════════════════════════════
  ROSE  solvent de-asphalting                        internal recycle on 4555
════════════════════════════════════════════════════════════════════════════════
  4313 ◀crude  1,300 ─┬─ 68% ▶ 4317 Kendex 0846 ────────────────────▶ MEK
                      ├─ 15% ▶ 4555 LR Resins ──────┐ sold
                      └─ 17% ▶ 4554 Kendex 0834 ──── sold · ▶ #6 OIL
  4555 ◀self   1,000 ─┬─ 55% ▶ 4317 Kendex 0846 ────────────────────▶ MEK
                      └─ 45% ▶ 8201 #6 oil          └──── recycles into ROSE

════════════════════════════════════════════════════════════════════════════════
  EXTRACT  solvent extraction              9305 runs TWO modes, different rates
════════════════════════════════════════════════════════════════════════════════
  9302 ◀MEK    2,800 ─┬─ 92.0% ▶ 9704 Kendex 0150 UNHT ──────────────▶ HYDRO R1
                      └─  4.5% ▶ 4577 Kendex MNE ───────── sold · ▶ #6 OIL
  9303 ◀MEK    1,800 ─┬─ 92.5% ▶ 4309 ───────────────────── sold
                      └─  3.0% ▶ 4579 ───────────────────── sold
  9305 ◀MEK    2,200 ─┬─ 93.0% ▶ 4318 Argold Legacy ─────── sold   [#90 NORMAL]
   (normal)           └─  2.4% ▶ 4586 ───────────────────── sold
  9305 ◀MEK    1,300 ─┬─ 93.0% ▶ 9705 Kendex 0847 UNHT ─────────────▶ HYDRO R1
   (DEEP)             └─  2.4% ▶ 4586 ───────────────────── sold   [#91 DEEP]
                         ▲ deep is the only feed HYDRO R1 takes from extraction

════════════════════════════════════════════════════════════════════════════════
  HYDRO  hydrotreater            two reactors, one time budget · crossing = 0.375 d
════════════════════════════════════════════════════════════════════════════════
  ┌ R1 ─ dedicated reactor ─────────────────────────────────────────────────────┐
  │ 9704 ◀EXTRACT 5,200 ── 92% ▶ 4315 ──────────────────────── sold            │
  │ 9705 ◀EXTRACT 2,500 ── 98% ▶ 4319 ──────────────────────── sold            │
  └────────────────────────────────────────────────────────────────────────────┘
  ┌ R2 ─ everything else ───────────────────────────────────────────────────────┐
  │ 9711 ◀crude   5,943* ─ 85% ▶ 4115 Kensol 48 ────────────── sold            │
  │ 9712 ◀crude   5,943* ─ 85% ▶ 4118 Kensol 50H ──── sold · ▶ DIESEL          │
  │ 9703 ◀crude   5,714* ─ 82% ▶ 4129 ──────────────────────── sold            │
  │ 9720 ◀MEK     5,486* ─ 92% ▶ 4329 Kendex 0060HT ─ sold · ▶ DIESEL          │
  │ 9713 ◀recycle 5,000 ── 98% ▶ DSL  Finished diesel ──────── sold            │
  └────────────────────────────────────────────────────────────────────────────┘
        every line returns charge × (1 − yield) ─┐
                                                 ▼
                 9713 No.2 Diesel-Hydro Charge ──┘  ◀── recycles to R2 above
                          ▲
                          └── plus all DIESEL-sink downgrades land here

════════════════════════════════════════════════════════════════════════════════
  PLATFORMER  three vessels, one workbook label · run in PARALLEL, no shared day
════════════════════════════════════════════════════════════════════════════════
  9103 ◀crude  4,000 ─┬─ 16.2% ▶ 9505 LSR ────────┐  [V1 FRACTIONATOR #102]
                      ├─ 55.1% ▶ 4107 Kensol 17 ──┼─┐  = the real platformer charge
                      └─ 21.0% ▶ 4111 Kensol 30 ──┼─┼─ sold · ▶ DIESEL
                                                  │ │
  9505 (no tank)  n/a ── 96.8% ▶ 9501 (no tank) ◀─┘ │  [V2 ISOMERISATION #103]
                                    └─ booked into ─┼─▶ 1128 Isomerate ─ sold · ▶ GASOLINE
                                                    │
  4107          2,024† ── 84.0% ▶ 9511 Platformate ◀┘  [V3 REFORMER #104]
                                    └──────────────── sold · ▶ GASOLINE

════════════════════════════════════════════════════════════════════════════════
  DOWNGRADE SINKS       unlimited offtake, real tanks, priced at a netback
════════════════════════════════════════════════════════════════════════════════
  #6 OIL   8201  ◀── 4577, 4554, 4454                       (+ 45% of ROSE 4555)
  CAT      8221  ◀── 4449, 4451
  DIESEL   9713  ◀── 9711, 9712, 9703, 9720, 9718, 4118, 4111, 4329
  GASOLINE  —    ◀── 9511, 1128            (4107 removed — it reforms instead)

  *  grossed up from a partial day, never ran clean       † derived, never scheduled
  ◀unit = feed comes from that unit    ▶ = goes to
```

## What the shape tells you

**The diesel pool is the only true recycle.** Every hydrotreater line returns its
yield loss to 9713, and every DIESEL-sink downgrade lands in the same pool, which
is re-charged to R2 to make finished diesel. Downgrading to diesel is therefore
not disposal — it is feed. That is structurally why diesel absorbs the largest
share of downgrade without the tanks complaining.

**ROSE eats its own product.** 4313 makes 4555, and 4555 is charged back to ROSE.
No other unit does this.

**Every unit is coupled to at least two others.** MEK cannot run without ROSE
(4317), EXTRACT cannot run without MEK (9302 / 9303 / 9305), HYDRO R1 cannot run
without EXTRACT (9704 / 9705). That chain is why the units cannot be optimised
separately, and why throttling one starves everything downstream of it.

## Numbers still unconfirmed

Marked in the diagram. Everything structural is now settled; what remains is
arithmetic.

| Mark | Where | Issue |
|---|---|---|
| `*` | HYDRO 9711, 9712, 9703, 9720 | Grossed up from a partial day; never ran a clean one |
| `†` | PLATFORMER 4107 (reformer) | Derived from the fractionator, never scheduled; now limits platformate |
| — | PLATFORMER 9505 (isomerisation) | No rate recorded at all; 648 bbl/d readable off 305 days of plan |
| — | HYDRO 9705 | 2,500 recorded, 3,000 actually run |
| — | EXTRACT 9303 | 1,800 recorded, 2,100 actually run |

See `data/reports/open-items.md` for the full list, `MIP-FORMULATION.md` for the
optimization model and `MARGIN-OBJECTIVE.md` for pricing it in real margin.
