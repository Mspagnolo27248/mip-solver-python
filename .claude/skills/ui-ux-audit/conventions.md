# UI conventions - the contract

This file is what "consistent" means for this app. There are three sections:

- **Established** is binding. Breaking it is a finding.
- **Open** is waiting on the user's decision. Propose an answer, don't impose one.
- **Leads** are suspected problems from reading the code, not yet confirmed on screen.

When the user decides something, move it from Open to Established with the date.
When an audit confirms or clears a lead, delete it from Leads. The audit report
keeps the record.

---

## Established

### Stack

Vanilla JS, no build step, no dependencies. Elements are made with `el()`, requests
go through `api()`, and messages use `toast()`. Colours, radii and fonts come from
the `:root` tokens in `styles.css`, which also define dark mode. Every
recommendation has to fit this.

### Rules the history already settled

Each rule was learned from a real failure. The commit or comment is the reason.

1. **One action, one code path.** An action reachable from two places runs one
   handler (`de4f5fd`). *Its label and look should match too; see Open.*
2. **A button that acts on something selected elsewhere names it.** "Run optimizer
   on "X"", not "Run optimizer" (`cb491a8`, `refreshRunButton`).
3. **Ask when a default could silently spoil a plan.** The plan's first date is
   asked for every time; it isn't taken from the workbook (`de4f5fd`).
4. **A number input doesn't change on the scroll wheel.** One `wheel` listener on
   `document` in `app.js` covers every number input, including rows drawn later.
5. **Anything that overwrites many cells reads back its range first** (`applyFill`).
6. **Judge a run by its verification.** An unverified run must not look usable.
   `verified · not proved optimal` is the normal good result, not a warning
   (`loadOptRuns`).
7. **Compare like with like.** A before/after table scores both sides the same way
   (`loadOptRuns`).
8. **Don't offer an invalid path that fails slowly.** Re-solving an optimizer result
   is refused up front, and the refusal names the scenario to pick instead
   (`runOptimizer`).
9. **An imported value stays visible next to the planner's edit** (Source data,
   Model inputs).

### Visual language

| Element | Meaning |
|---|---|
| `.btn` | The main action of a view. One per view where possible |
| `.btn.ghost` | Secondary action |
| `.linkish` | Inline or tertiary action, such as a row action or export |
| `.btn.warn` | The action works but is the wrong path in this state |
| `.pill.danger` | Plan breaks or input missing: runs dry, needed, error, not verified |
| `.pill.warn` | Needs attention, can be recovered: overflow, stale, detected downtime |
| `.pill.ok` | Fine: in bounds, set, verified, confirmed |
| `.pill.info` | Neutral label: overridden, edited, model version, which schedule |
| `td.num` | Right-aligned, monospace, tabular figures, for every quantity |
| `.empty` | An empty state that says what to do next |
| `.muted` paragraph under `h2` | One or two sentences on what the view is for |

### Decided on 2026-09-12

Decided by the user after the plan and optimizer audit
(`docs/ui-audits/2026-09-12-plan-and-optimizer.md`).

10. **"Current Plan" and "Optimized Result".** The planner's schedule is the
    *Current Plan*; what a run returns is the *Optimized Result*.
    - Use these two terms everywhere: run cards, the compare bar, picker groups,
      result names, toasts, tooltips, and the API's `role` / `other_role`.
    - Stop using "Your schedule", "my schedule", "warm start", "Optimizer
      proposal" and "proposed" on screen. (F12)
11. **An unverified run is labelled and can't be exported.**
    - Its Optimized Result is kept so the failure can be inspected.
    - It is marked "unverified" on its card and in the picker.
    - The card shows the simulator's reason.
    - The card offers no Export for Excel. (F2)
12. **"Refine with v2" on verified greedy runs.** One action on the run card runs
    v2 on that Optimized Result. The general refusal to re-run an Optimized Result
    stays. (F6)
13. **The picker separates Current Plans from Optimized Results, and scenarios can
    be deleted.**
    - Names are short and not nested, and the picker's width is capped.
    - Delete can't be undone, so it confirms inside the page and names what will
      go. (F8)
    - **Deleting a Current Plan also deletes all of its Optimized Results.** An
      Optimized Result can be deleted on its own, and its Current Plan stays.
      Confirmed by the user on 2026-09-12, along with the two separate lists.
    - *To confirm when it's built:* whether a deleted result's run card goes with
      it, and whether results refined from it (Refine with v2) go too. The
      confirmation should list both.
14. **Saving and confirming follow one rule.** Agreed by the user on 2026-09-12
    (report F17, F20, F21).
    - **Single values save when you finish with the box**, as in Excel: Tab, click
      away, or pick from a dropdown. After a save:
      - The cell shows a brief "saved" mark.
      - On failure, the stored value is put back and the box keeps a red outline
        with the reason.
      - A schedule grid refreshes after every cell edit.
      - Every number box has the scroll-wheel guard.
      - A single-cell charge edit follows the same one-feed-per-unit rule as Set a
        rate, and says what it cleared.
    - **Single clicks that remove something** (remove downtime, clear, reset)
      happen immediately, with **Undo** in the toast for a few seconds. They don't
      ask first. Toggles that undo themselves (Down, R/L mode) stay as they are.
    - **Anything that writes many days or deletes** (Set a rate, Re-sync all feeds,
      deleting a scenario) asks first **inside the page** and says what will
      change. No native `confirm()` or `prompt()`.
    - **A panel warns** if the planner leaves it with fields filled in but not added
      or applied.
    - **A run card shows the optimizer settings it used**, because those settings
      are global and save when you finish with the box.

---

## Open - vocabulary

Each row is one concept that appears under several words. The **Proposed** column
is a suggestion until the user confirms it. Before proposing, check what the
workbook calls it.

| Concept | Words in use | Where | Proposed |
|---|---|---|---|
| How many days a view shows | Window, Horizon, Days, Show, Detailed horizon; 366 = "Full horizon" / "Full year" | `#alert-window`, `#dash-days`, `#stream-days`, `#proj-days`, `#sched-days`, optimizer `horizon_days` | ? |
| First day a view shows | Start (7-day steps), From day (21-day steps) | `#stream-offset`, `#sched-offset` | ? |
| Value as received / corrected / in use | Source / Override / Effective; Imported / Override / In use | `loadFeedRows`, `loadReferenceRows` | ? |
| Undo a correction | clear / reset; pill "overridden" / "edited" | same two tables | ? |
| Customer demand | Orders, sales, demand out | `drawProjTable` header, projection and stream-sheet blurbs | ? |
| Feed consumed by units | Charged out, charge out, `production_out` | projection table and blurb | ? |
| Space left in the tank | Headroom (the column shows `excess`) | `drawProjTable` | ? |
| Inventory below zero | runs dry, dry, run dry, stockout | `issuePill`, `chartCard`, `drawProjTable`, alert summary | ? |
| Inventory above capacity | overflows, overflow tank, over cap, out of bounds (also counts dry days) | `issuePill`, `chartCard`, `loadStream` | ? |
| Uploaded data layer | Source data, feeds, staging ("Freeze current staging") | tab, `#sync-all`, `#new-scenario` title | ? |
| Yields, rates, capacities | Model inputs, reference vN | tab, `#ref-meta`, `#scenario-meta` | ? |
| Optimizer settings | Optimizer inputs, params | tab; the README says "params screen" | ? |
| Create a scenario | "New scenario" (ghost), "Create scenario from source data" (primary). Same handler | `#new-scenario`, `#upload-scenario` | ? |
| A day a unit can't run | Planned downtime, Down, outage, turnaround | schedule panel, grid, fill toast | ? |

## Open - patterns

- **Dates.** Reading formats mix m/d/yy (grids), ISO (lists, toasts, dialogs) and
  locale date-time (sync and run times). Pick one for reading.

---

## Leads

The plan and optimizer leads were settled by
`docs/ui-audits/2026-09-12-plan-and-optimizer.md`, and their findings live there now.
These are still open, either outside that audit's scope or not yet seen on screen:

- **Model inputs scope.** The scenario picker stays visible on Model inputs, and its
  "projections recalculated" toast doesn't say that every scenario changes. S2.
- **Same-looking boxes, different reach.** A feed override reaches no existing
  scenario; a model-input override reaches all of them immediately. S2.
- **Feed table cap.** The feed table loads at most 800 rows while its heading shows the
  full total. S2.
- **Raw errors.** Errors appear as `Error: <status> <first 140 chars>` in a 2.6 s toast.
  No error came up during the audit. S2.
- **Proof hidden in the badge.** A proved-optimal run (`done`) and an unproved one both
  show a green "verified". The run history has no `done` run to check against. S3.
- **Mode switch forgets the tab.** Switching mode always opens that mode's first tab.
  S3.
- **Keyboard access.** Alert table rows are clickable `tr` elements that a keyboard
  can't reach. S3.
- **Stylesheet drift.** The stylesheet switches from px to rem after the `optimizer`
  comment. S4.
- **Tabs slide under the header.** `.tabs` sticks at `top: 55px`, but the header is 79 px
  tall at 1366 px wide and 85 px at 956, because the line under the title wraps. When
  the page scrolls, the top of the tab bar is hidden. Measured on 2026-09-13; the
  header's height is the same with or without the step 3 "Current Plan ·" prefix, so
  this was already there. S3.
