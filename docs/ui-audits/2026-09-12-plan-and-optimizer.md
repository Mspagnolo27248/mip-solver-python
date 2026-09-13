# UI audit - planning and optimizer interactions

**2026-09-12 · commit `de4f5fd` · the first audit, so there is no earlier report to
compare against.**

## 1. Scope and method

**In scope** (plan and optimizer interactions):
- The header's scenario controls.
- Source data, as the place a plan starts.
- Charge schedule, including the Planned downtime and Set a rate panels.
- Optimizer inputs.
- Runs & results.

**Out of scope** (reporting): Capacity & alerts, Dashboards, Stream sheet, Inventory
projection. Model inputs is out too, except where it changes what a plan uses.

**How it was checked:**
- **Browser:** Chrome, dark theme, against the app already running on port 8000. The
  visible area was **956 × 419 px** because the window had a panel docked beside it.
  I couldn't reach the 1366 px laptop width, and I didn't check a wide screen.
- **Mostly rendered text, not screenshots.** Screenshots timed out whenever Chrome
  treated the tab as hidden, and only one worked. The rest of the evidence was read
  from the rendered page by script. Each finding says whether it rests on the
  **rendered page**, the **run history**, or the **code only**.
- **Read-only.** I moved between views with the page's own functions (`setMode`,
  `switchTab`, `selectScenario`), which is the same as clicking tabs. I didn't type
  in any input, press any button that saves, or open any dialog. So the input-editing
  findings are code-only.
- **Scenarios looked at:**
  - 69, "Plan 2026-09-13", a draft plan.
  - 73, "Optimizer run 72 (from Plan 2026-09-13)", a proposed result.
- **Run history:** the last 25 runs from `/api/optimizer/runs`, runs 48–72, all made
  on 2026-09-12.

## 2. Fix first

1. **Stop a run being started twice, and show that a run is going** (F1). It already
   happened: runs 70 and 71 are duplicates that each took 15 minutes.
2. **Make unverified runs look unusable** (F2). They currently offer Open schedule and
   Export for Excel, exactly like verified runs.
3. **Rename "Units may idle"** (F3). Its label says the opposite of its setting.
4. **Replace the two `prompt()` dialogs with one New scenario form** (F5). It should
   use a date picker in local time and say which schedule it copies.
5. **Split plans from optimizer results in the picker, and stop changing the
   selection after a run** (F7, F8). There are 73 scenarios, 56 of them results, so
   the scenario that matters is hard to find, and it changes under you.

**Quick fixes, a few lines each:**
- Reload the Optimizer inputs after the server rejects a value (F4).
- Add the scroll-wheel guard to the schedule cells and override boxes (F19).
- Drop the stray "null" at the end of the compare bar (F15).
- Show a "default" pill instead of "set" on blank inputs (F14).
- Give the two "Accepted gap" inputs different labels (F13).

## 3. Findings

### S1 - Silent wrong plan

### [S1] F1 - A run can be started twice, and nothing shows one is running
Where: Runs & results -> Run optimizer   (app.js `runOptimizer`, app.js:1318-1345; optimizer_service.py `run`, :226-237)
Planner sees: A "Solving…" toast that disappears after 2.6 s, then the same clickable button, for the whole solve (902 s on v2).
Planner will: Think the click didn't register, or that the run finished, and click again.
Result: Runs 70 and 71 started 14 s apart on the same plan. Each ran 902 s and returned the same $499,910 objective. That was 15 minutes of solving wasted and one duplicate result scenario (71, 72). The server records the run as `running` but doesn't refuse a second one.
Fix:
- Disable the button while the request is open, and relabel it "Solving on "Plan 2026-09-13"… started 8:09 PM, stops by 8:24 PM".
- Have the API return 409 while a run for that scenario has status `running`.
Evidence: run history + code

### [S1] F2 - Unverified runs offer the same actions as verified ones
Where: Runs & results -> run card actions   (app.js `loadOptRuns`, app.js:1288-1309; optimizer_service.py:279-294)
Planner sees: Runs 59–62 show a small amber "unverified" pill, and the same **Open schedule / Compare with my schedule / Export for Excel** as every verified run. Their result scenarios (54–58) sit in the picker named like any other result.
Planner will: Export or open the schedule, treating the pill as a caution rather than a stop.
Result: A schedule the simulator rejected ("asks units to charge feed the tanks cannot supply (338,344 gal)") can go into the workbook. README:159 says an unverified run "should not be used". Only the README says it.
Conflicts with: conventions.md Established rule 6, "an unverified run must not look usable".
Fix:
- On unverified cards, remove Export for Excel.
- Show the failure message on the card.
- Rename Open schedule to "Inspect the rejected schedule".
- Mark the result in the picker, e.g. "Run 62 · unverified".
- Whether unverified runs should create a scenario at all is a **decision** (section 7).
Evidence: rendered page + code

### [S1] F3 - "Units may idle" means the opposite
Where: Optimizer inputs -> How much freedom the model has -> Units may idle   (optimizer_service.py:102; app.js:1128-1131)
Planner sees: `Units may idle | off — units may idle (cold start) | on = must run daily`.
Planner will: Read "Units may idle: on" as "idling allowed", when on means every unit must run daily.
Result: The run is solved with the opposite of what was intended. That changes whether a cold start is feasible, and whether the campaign shape can be trusted. The help text itself says "Turn it back on before trusting the campaign shape".
Fix: Label it "Units must run daily", with the unit column "on or off". Keep the two option texts. The words then agree with the value.
Evidence: rendered page

### [S1] F4 - A value the server rejects stays in its box
Where: Optimizer inputs -> any number input   (app.js `saveOptParam`, app.js:1195-1202)
Planner sees: Their typed value, still in the box, after a 2.6 s error toast.
Planner will: Believe the value is saved.
Result: The next run uses the old value, with nothing on screen showing it. The API comment (`main.py` `patch_optimizer_params`) intends the field to "keep its last good value". The screen doesn't.
Fix: Call `loadOptParams()` in a `finally` block, so the box always shows what is actually stored.
Evidence: code-only (typing would have saved a setting)

### [S1] F5 - New scenario: a free-text date, a UTC default, and a copy it never mentions
Where: Header -> New scenario; Source data -> Create scenario from source data   (app.js `newScenario`, app.js:1436-1455; service.py `_copy_schedule`, :286-292)
Planner sees: Two native `prompt()` boxes: a name, then "First day of the plan", in free text, defaulting to `new Date().toISOString()`.
Planner will:
- Accept the default date.
- Type a date in any format; nothing checks it.
- Not realise the charge grid and downtime are copied from whichever scenario happens to be selected.
Result: Three problems.
- **Wrong default date.** "Plan 2026-09-13" was created at 8:06 PM on 9/12 local time. The UTC default already puts tomorrow's date in the name, and would do the same in the date box.
- **Hidden copy.** The copy source is whatever is selected, often an optimizer result, because a run finishing selects one (F7).
- **Blank tail.** The copy lines up by calendar date, so a later plan date leaves blank days at the end.
- **No repair.** The plan date can't be corrected afterwards. The only warning is README:137.
Conflicts with: conventions.md Established rule 3; the silent-date failure `de4f5fd` fixed.
Fix: One in-page form, shared by both buttons:
- Name.
- Plan date, as `<input type="date">` defaulting to **local** today.
- "Copy charge schedule and downtime from", a select preset to the current scenario, **plans listed first**, with "none" allowed.
- A line such as "covers 2026-09-08 → 2027-09-08; the last 5 days will be blank".
- A toast that names what was copied.
Evidence: run history + code

### [S1] F19 - Schedule cells and override boxes still change on the scroll wheel
Where: Charge schedule grid (`.cell-input`); Source data -> feed detail (`.ovr-input`)   (app.js:555, app.js:605, app.js:970; the guard exists only at app.js:1177)
Planner sees: Nothing. The value changes while they scroll the grid with a cell still focused, and saves when they click away.
Planner will: Scroll the 21-day grid to reach another unit.
Result: The same failure that turned 0.30 into -36.7, this time on charge rates and feed values.
Conflicts with: conventions.md Established rule 4.
Fix: Add the same `onwheel: (e) => e.target.blur()` to every `type: 'number'` input, or once through `el()` for number inputs.
Evidence: code-only

### S2 - Misleads or blocks

### [S2] F6 - The README's greedy-then-v2 workflow can't be done in the app
Where: Runs & results -> Run optimizer, with a greedy result selected   (app.js `runOptimizer`, app.js:1323-1334; README:226)
Planner sees: An amber button, and a refusal: "…is an optimizer result, so it cannot be re-solved. Select "Plan 2026-09-13"…"
Planner will: Follow the README ("run v2 on greedy's result"), hit the refusal, and give up or go back to the plan.
Result: The measured 17.5% improvement can't be reached from the app. Run 67 (base = result 62) shows it was done through the API.
Fix: A **decision** (section 7). Either offer "Refine with v2" on verified greedy cards, which the API already accepts, or change the README to say it only works through the API.
Evidence: run history + rendered page + code

### [S2] F7 - The selected scenario changes after a run
Where: Runs & results, after a run finishes   (app.js `runOptimizer` -> `boot()`, app.js:1344)
Planner sees: The button changes from "Run optimizer on "Plan 2026-09-13"" to an amber "Run optimizer on "Optimizer run 72 (from Plan 2026-09-13)"". The only other sign is the picker.
Planner will: Change the model to v2 and press Run again, expecting the same plan.
Result: A refusal (the loop F6 describes). Or, if the planner goes to Charge schedule or presses New scenario, they are now working from the result, not their plan (F5).
Conflicts with: conventions.md Established rule 2 and `cb491a8`: the selection shouldn't change silently.
Fix: Keep the base scenario selected after a run, and put the new card's **Open schedule** in front of the planner. Opening a result stays an explicit act.
Evidence: rendered page + code

### [S2] F8 - The scenario picker is flooded and pushes New scenario off the screen
Where: Header -> Scenario picker, New scenario   (app.js `boot`; styles.css `.topbar-controls`, :63)
Planner sees:
- One flat list of **73 scenarios, 56 of them "Optimizer run N (from …)"**, newest first.
- The longest name is "Optimizer run 21 (from Optimizer run 20 (from Optimizer run 19 (from Optimizer run 17 (from Plan 2026-08-14))))".
- Nothing distinguishes a plan from a result.
- There is no way to remove one.
- The picker grows to fit the longest name (742 px). At a 956 px view the header is 1175 px wide and **New scenario sits off-screen**.
Planner will: Scroll the list for their plan, and pick a result by mistake. On a narrower window, not find New scenario.
Result: Wrong-scenario runs and copies (F5, F7). A missing primary action.
Fix:
- Two `optgroup`s, "Plans" and "Optimizer results".
- Short result names without nesting: "Run 72 · greedy · from Plan 2026-09-13".
- `max-width` with an ellipsis on the select, and `flex-wrap` on `.topbar-controls`.
- Archiving or deleting scenarios is a **decision** (section 7).
Evidence: rendered page (measured)

### [S2] F9 - Runs aren't tied to the plan in front of you, and Compare picks the latest run silently
Where: Runs & results list; Charge schedule -> compare bar   (optimizer_service.py `list_runs`, `pair_for`, :417-435)
Planner sees: The last 25 runs from every plan, mixed together. On plan 69, "Compare with optimizer" and "Switch to optimizer" without saying which run.
Planner will: Assume Compare shows the best or the v2 result.
Result: On plan 69, Compare opens **run 72 (greedy)**. The two v2 runs (70, 71) can't be reached from the schedule at all.
Fix:
- Runs & results: "This plan's runs / All runs", defaulting to this plan.
- Compare bar: name the run, e.g. "Compare with Run 72 · greedy · verified", and let the planner choose among the plan's verified runs.
Evidence: rendered page + API

### [S2] F10 - Why a run is unverified, or unproved, is only in a tooltip
Where: Runs & results -> status pill   (app.js:1225-1236)
Planner sees: "unverified", or "verified · not proved optimal". The explanation is in the pill's `title`: "The schedule asks units to charge feed the tanks cannot supply (338,344 gal)…", or "…a longer run has scored 20% better on this model".
Planner will: Never hover, and miss the one sentence that says what to do.
Result: Unverified runs get opened anyway (F2). The "a longer run has scored 20% better" hint never reaches the planner.
Fix: Print `r.message` as a muted line under the card header. Keep the tooltip.
Evidence: rendered page

### [S2] F11 - Settings the chosen model ignores look live
Where: Optimizer inputs -> stats and Solver group   (app.js `loadOptParams`, :1102-1108, :1145)
Planner sees: Model = greedy. Yet the stat reads "900 second solve limit", and Solver time limit, both "Accepted gap" inputs, and the v2 option "(~5 min)" all look active. Meanwhile the run cards below show v2 runs of 902 s.
Planner will: Tune the time limit for greedy, or budget five minutes for v2 when it takes fifteen.
Result: Time wasted, and a wrong expectation. README:153 says these settings are ignored for greedy. The screen doesn't.
Fix:
- When the model is greedy, dim the Solver group with "Not used by greedy".
- Replace the time-limit stat with the model: "greedy · 42 days".
- Relabel the v2 option "v2 — also decide the hydrotreater (runs to the time limit)".
Evidence: rendered page

### [S2] F18 - "Ready" ignores downtime, which lives in the other mode
Where: Optimizer inputs -> "Ready / all inputs supplied"; Charge schedule -> Planned downtime (collapsed)
Planner sees: "Ready" in green. The downtime warning ("Set these before running the optimizer") is in Planning mode, in a collapsed panel.
Planner will: Run without checking outages, including "detected" windows that were inferred and never confirmed.
Result: A schedule that runs units through a turnaround, or idles them on days they could run.
Fix: Add a stat to Optimizer inputs and the run area: "Planned downtime on "Plan 2026-09-13": N windows, M detected (unconfirmed)", linking to the panel.
Evidence: rendered page + code

### [S2] F20 - A failed save leaves the unsaved value on screen, in all four editors
Where: Charge schedule cells, Source data overrides, Model inputs overrides, Optimizer inputs   (app.js `editSchedule` :845, `saveOverride` :993, `saveReference` :1084, `saveOptParam` :1195)
Planner sees: An error toast for 2.6 s, then their typed value still in the box. Only `api()` shows the error, and none of the four handlers redraws when a save fails.
Planner will: Believe the value is saved.
Result: The stored value and the screen disagree until the view reloads. This is F4 found in three more places. One cause is a charge typed onto a retired line (`main.py` `edit_schedule`, 400).
Fix: One shared save helper for inputs that save when you leave them. On success, mark the cell saved. On failure, put the stored value back and keep a red outline with the reason.
Evidence: code-only

### [S2] F21 - Typing in a cell doesn't follow the one-feed-per-unit rule that Set a rate follows
Where: Charge schedule grid vs Set a rate across the window   (`main.py` `edit_schedule` :820-847 vs `service.py` `fill_schedule`, `exclusive`, :437)
Planner sees: Set a rate clears the unit's other lines on those days. Typing a rate into a cell saves it beside whatever the unit's other lines already hold, with no warning.
Planner will: Correct one day by typing the new feed's rate, and assume the old feed on that day is gone, as it would be with Set a rate.
Result: Two feeds on one unit on one day, which the plant can't run. The optimizer and the simulator are handed a schedule nobody meant.
Fix: Apply the same `exclusive` clearing to single-cell edits, redraw the grid, and say in the toast what was cleared ("cleared MEK 9117 on 9/14"). Or refuse the edit and name the line already running that day.
Evidence: code-only

### S3 - Discontinuity

### [S3] F12 - The plan-and-proposal vocabulary changes on every screen
Where: Run card, compare bar, scenario names   (app.js:1247, 1252, 1298, 824-836; optimizer_service.py:422-435)
Planner sees:
- **Run card:** "Your schedule / Optimizer", "warm started from", "Compare with my schedule".
- **Compare bar on a plan:** "Your schedule", "Compare with optimizer", "Switch to optimizer".
- **Compare bar on a result:** "Optimizer proposal", "Compare with warm start", "Switch to warm start".
- **Names:** "Optimizer run N (from X)". **Statuses:** draft / proposed.
Planner will: Wonder whether "warm start", "my schedule" and "Your schedule" are the same thing. They are.
Result: Trust erodes. Run 67's card says "Your schedule" for a base that was itself an optimizer result. And the README says greedy-then-MIP "is *not* a solver warm start".
Fix: A **decision** (section 7). Then use one pair of words everywhere, including the API's `other_role`.
Evidence: rendered page

### [S3] F13 - Labels that don't tell two inputs apart
Where: Optimizer inputs   (optimizer_service.py:144, :151)
Planner sees:
- Two rows, both labelled "Accepted gap": `$` and `fraction`.
- "Charge floor" and "Minimum rate" next to each other. The second one's help has to say "**Not the charge floor above**".
Fix:
- "Accepted gap ($)" and "Accepted gap (% of objective)".
- "Charge floor" -> "Keep at least this much of my plan", "Minimum rate" -> "Lowest rate on a running day".
Evidence: rendered page

### [S3] F14 - A blank input shows a green "set" pill
Where: Optimizer inputs -> End-of-window shortfall   (app.js:1184-1186)
Planner sees: An empty box marked "set".
Fix: When the value is `null` and not required, show `.pill.info` "default", and put the default in the box's placeholder.
Evidence: rendered page

### [S3] F15 - A stray "null" at the end of the compare bar
Where: Charge schedule -> compare bar, on a scenario with a pair and compare off   (app.js `renderCompareBar`, :837-841)
Planner sees: "Export for Excel  Your schedule  Compare with optimizer  Switch to optimizer  **null**". `replaceChildren` prints `null` as text.
Fix: Filter null children before `replaceChildren`, or pass `''`.
Evidence: rendered page

### [S3] F16 - One action with two names and two looks; two primary buttons on Source data
Where: Header -> "New scenario" (`.btn.ghost`); Source data -> "Create scenario from source data" (`.btn`); Source data -> "Re-sync all feeds" (`.btn`)
Planner sees: Two labels for one handler. Source data has two primary buttons, and the less common one (re-sync) looks as important as creating a plan.
Conflicts with: conventions.md rule 1 (one action, one code path), and the visual language (one `.btn` per view).
Fix: Label both "New scenario…". Make Re-sync all feeds `.btn.ghost`.
Evidence: rendered page

### [S3] F17 - Saving works differently inside the Charge schedule
Where: Charge schedule   (app.js `editSchedule` :845-851, `editCrude` :853-860, `toggleDowntime` :759, `removeDowntime` :783, `applyFill` :700)
Planner sees:
- A line cell saves when you leave it and doesn't redraw; a crude cell saves and redraws.
- A Down click saves instantly.
- "remove" deletes an outage with no confirmation.
- Apply asks through a native `confirm()`.
Fix: A **decision** on the save pattern (conventions.md, Open patterns). The proposal:
- Single-cell edits save quietly, with a brief highlight in the cell.
- Anything that writes more than one day, or deletes, confirms inside the page and says what it will change.
Evidence: code + rendered page

## 4. View inventory

| View | Purpose | Controls, and what they change | Saves |
|---|---|---|---|
| Header | Which plan everything acts on | Mode switch (display); Scenario picker (selection: 73 entries, no grouping); New scenario (**creates a scenario**) | Picker: no. New scenario: yes, via 2 prompts |
| Source data | Get today's data in, then start a plan | Upload slots ×3 (**source data, global**); use workbook (source data, confirms); Re-sync all feeds (source data); Create scenario from source data (= New scenario); feed cards → Override box (**source data only, not existing scenarios**) | On change / on click |
| Charge schedule | Lay down or correct a plan | From day, Show (display); Planned downtime panel: Add / remove (**this scenario**); Set a rate panel: Apply (**this scenario**, many cells, confirms); compare bar: Compare, Switch (selection), Export (download); grid cells, crude mode, Down cells (**this scenario**) | Cells when you leave them; buttons on click |
| Optimizer inputs | Settings for every run | 17 inputs in 3 groups (**global, every future run on any plan**); status pills; Ready stat | When you leave the box / change the select |
| Runs & results | Start a run, judge it, act on it | Run optimizer on "X" (**new run and result scenario**); cards: Open schedule, Compare with my schedule (selection + mode), Export for Excel (download) | Run: yes |

## 5. Vocabulary (in scope)

| Concept | Words in use | Where | Proposed |
|---|---|---|---|
| The planner's schedule a run started from | Your schedule, my schedule, warm start, base, "(from X)", draft | Run card, compare bar, names | **"Current Plan"** (decided 2026-09-12) |
| What the optimizer returns | Optimizer, Optimizer proposal, optimizer (role), result, proposed, "Optimizer run N" | Run card, compare bar, names, status | **"Optimized Result"** (decided 2026-09-12) |
| Starting a run | Run optimizer, re-solve, solve, Solving… | Button, refusal toast, tooltip | "Run" |
| Units forced to run | Units may idle, must run daily, cold start | Optimizer inputs | "Units must run daily" (F3) |
| Solver stopping rule | Accepted gap ×2, mip gap, time limit, solve limit | Optimizer inputs, stat | "Accepted gap ($)", "Accepted gap (%)", "Solver time limit" |
| A run the simulator accepted | verified, Yes / simulator reproduces the run, not proved optimal, done | Run card | Keep "verified"; show the proof state as its own pill |
| Making a scenario | New scenario, Create scenario from source data, Freeze current staging (tooltip) | Header, Source data | "New scenario…" |
| Days a unit can't run | Planned downtime, Down, outage, turnaround, detected / confirmed | Schedule panel, grid, fill toast | "Planned downtime", with "Down" as the grid's short form |
| How far ahead | Detailed horizon (optimizer), 366 day horizon (meta), Show N days (schedule) | Optimizer inputs, header, schedule | Open (conventions.md) |

## 6. Workflows

These are the plan and optimizer walkthroughs from `ui-flow.md`, confirmed where the
browser allowed. Each numbered item is a moment the planner has to **remember**
something the screen doesn't show.

**W2 - Plan on today's data.**
1. Select the right source scenario **before** New scenario (F5).
2. The date's time zone (F5).
3. The blank tail of a copied grid (F5).
4. That uploads never reach existing scenarios. This is stated only in a grey
   paragraph on the upload card.

*Mode switches: none. Tab changes: 2.*

**W3 - Optimize and decide.**
1. Check downtime in the other mode first (F18).
2. Select a plan, not a result. The list is flooded (F8).
3. Which settings the chosen model ignores (F11).
4. Don't click Run twice (F1).
5. After the run, the selection has moved (F7).
6. Hover the pill to learn why a run is unverified (F10).
7. Don't export an unverified run (F2).
8. Which run "Compare with optimizer" shows (F9).
9. greedy-then-v2 needs the API (F6).

*Mode switches: at least 2. Tab changes: 4+.*

**W4 - Correct a feed value.**
1. The override reaches no existing scenario. A new scenario is needed.

**W5 - Plan an outage.**
1. "remove" and Down clicks don't confirm, and there is no undo (F17).
2. The outage has to exist before the run, in the other mode (F18).

## 7. Decisions

**Decided by the user on 2026-09-12.** These are recorded as rules 10–13 in
`conventions.md`.

- **F12, words:** "Current Plan" and "Optimized Result".
- **F2, unverified runs:** label them, show the reason, block export, and keep the
  scenario for inspection.
- **F6, greedy then v2:** add "Refine with v2" on verified greedy run cards.
- **F8, clearing out results:** two separate lists (Current Plans, Optimized
  Results) with short names, and delete with an in-page confirmation. Deleting a
  Current Plan deletes its Optimized Results; an Optimized Result can be deleted on
  its own.
- **F17, F20, F21, saving and confirming:**
  - Single values save when you finish with the box, show a saved mark, restore
    the stored value if the save fails, and follow the one-feed rule.
  - Single removals happen immediately, with Undo.
  - Bulk writes and deletes ask first inside the page.
  - Unsaved panels warn before you leave them.
  - Run cards show the settings they used.
  - This is rule 14 in `conventions.md`.

**Still open:**

- **F8, delete details:** whether a deleted result's run card goes with it, and
  whether results refined from it go too.

**The options that were put to the user:**

1. **Words for the two schedules** (F12): "Plan / Proposal", "Your schedule / Optimizer proposal", or "Base / Result".
2. **Unverified runs** (F2): keep the scenario but block export and label it, stop creating a scenario for them, or leave them as they are.
3. **greedy then v2** (F6): a "Refine with v2" action on verified greedy runs, or keep the refusal and fix the README.
4. **Clearing out results** (F8): group and shorten names only, add archive, or add delete.
5. **Save and confirm pattern** (F17): see the proposal in the finding.
