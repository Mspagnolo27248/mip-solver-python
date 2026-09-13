# Checks

There are eleven checks. Each one has **what to look for** and **what it looked like
in this app**. Examples use function names and element ids rather than line numbers,
because line numbers change. Confirm an example is still true before you cite it.

---

## 1. Vocabulary continuity

*One concept, one word. One word, one concept.*

- List every word used for each domain concept, in every view. A planner who reads
  "Horizon" on one tab and "Window" on the next will assume they mean different
  things.
- Prefer the workbook's words. Planners already know them.
- Code and API words shouldn't reach the screen: `staging`, `block`, `reference v1`,
  `as_of`, `warm start`, `params`, `proposed`, raw status strings like
  `not_proved_optimal` or `no_solution`.
- Pairs of labels should match their twins: Source/Override/Effective on one table
  and Imported/Override/In use on an identical one is a break.
- Plurals, capitals and abbreviations should be consistent: "LCL" vs "lower
  control", "bbl" vs "barrels", "Replace file" vs "use workbook".

*In this app:* the number of days a view shows is labelled Window, Horizon, Days,
Show, or Detailed horizon depending on the view, and 366 days is "Full horizon" in
one place and "Full year" in another. The projection table's "Headroom" column
shows a field called `excess`.

## 2. Control continuity

*The same job uses the same control, in the same place, with the same look.*

- **Button tiers must mean something.** `.btn` is the one main action of a view,
  `.btn.ghost` is secondary, `.linkish` is inline or tertiary, `.btn.warn` is a
  discouraged path. Flag a view with several primaries, or a destructive action
  styled as primary.
- An action reachable from two places should have **one label, one look and one
  handler**.
- Pickers for the same concept should offer the same options in the same format
  ("30 days" vs a bare "30"), and offsets should step by the same amount.
- A control should sit where its twin sits in the other views: at the right of
  `view-head`, in `.controls`, and so on.
- Things that look the same must behave the same. If some clickable cards open a
  detail panel and others that look identical don't, that's a break. So is a
  clickable table row next to a non-clickable one with no visual difference.

*In this app:* "New scenario" (ghost, header) and "Create scenario from source data"
(primary, Source data) run the same handler under two names and two looks.

## 3. Scope and consequence

*Before acting, can the planner tell what the action will touch?*

For each input or button, ask:

- Does it affect this scenario, every scenario, the source data, or the global
  optimizer settings?
- Does it save the moment focus leaves (`onchange`), or only on a button press? Is
  that visible?
- Can it be undone? If not, is the planner told **before** they act?
- Does it depend on hidden state, like whichever scenario happens to be selected?
  If so, does the control name that state?

*In this app:* the header's scenario picker stays on screen in Source data, Model
inputs and Optimizer inputs, which aren't scoped to a scenario. New scenario copies
the charge grid from whichever scenario is selected, and only the README says so.
The run button naming its scenario (`refreshRunButton`) is the pattern to copy.

## 4. Silent failure

*This project's defining failure. Look hardest here.*

Could a planner do the natural thing and get a wrong plan with nothing on screen
saying so? Look for:

- **Defaults that stand in for a real answer.** A date, a scenario, a horizon or a
  unit that the system fills in when it should ask.
- **Free-text entry with no checking.** A date typed into `prompt()` is never
  validated.
- **Accidental edits.** A number box that changes on the scroll wheel, a cell that
  saves on blur, a toggle one click from its neighbour.
- **Actions that write nothing and look like they worked.** A fill over a range
  outside the scenario, a filter hiding everything, a sync of zero rows.
- **Results that no longer match their inputs.** Inputs changed after a run and the
  run still looks current. Uploads that haven't reached the open scenario.
- **Success and failure that look the same.** Similar pill colours, or a toast that
  vanishes before it's read.

*In this app:* the scroll-wheel guard (`onwheel` blur) exists only in
`loadOptParams`. Check every other `type: 'number'` input.

## 5. Where am I, and what's happening

- The planner can always tell which scenario, which mode, and whether it is their
  plan or an optimizer proposal, without leaving the view.
- They can tell when that context changes under them, such as `openResult`
  switching scenario, mode and tab in one click.
- **Long operations** show that they're running, can't be started twice, and say
  when they finish, even if the planner has moved to another tab.
- Stale state is marked: overrides older than their source, a run older than the
  inputs it used.

*In this app:* a solve can run for the whole time limit (900 s has been used), while
the "Solving..." toast lasts 2.6 s and the button stays clickable.

## 6. Feedback and errors

- Every action gets a response where the planner is already looking, not only in a
  toast at the bottom of the screen.
- An error says what went wrong in the planner's terms and what to do next. A
  status code and 140 characters of raw API text do neither.
- A refusal (a guard) explains how to get to the allowed path. `runOptimizer`
  naming the scenario to pick instead is the pattern to copy.
- Empty states say why the view is empty and what to do: "No runs yet. Set the
  inputs, then run..."
- Native `alert`, `confirm` and `prompt` dialogs are inconsistent with the rest of
  the UI and can't be validated. Note each one.

## 7. Workflow fit

- Does the order of tabs, panels and controls follow the order the work happens in?
  Setup tasks and daily tasks may need different paths. Say which one the layout
  serves.
- **Dependencies across views:** a step that must happen first in another tab or
  mode. Is the planner told at the point where it matters? Example: downtime is set
  in Planning mode "before running the optimizer", which lives in Optimizer mode.
- Count the number of view switches each walkthrough in SKILL.md Step 2 needs.
- A README section that says "before clicking X, make sure Y" is a missing UI
  affordance.

## 8. Numbers, units, dates

- Every quantity header carries its unit. Gallons (inventory) and barrels per day
  (charge rates) sit side by side in this app, so an unlabelled number is a guess.
- Dates use one format for reading (grids, tables, lists, toasts, dialogs) and the
  native date input for entry. This app has m/d/yy in grids, ISO in several lists
  and toasts, and locale date-time for sync and run times.
- "Nothing" is shown consistently: `—` for not set, `·` for zero, blank for not
  applicable. The meaning of each must be the same everywhere.
- Big numbers use the same abbreviation rules everywhere (`fmtK`), and don't round
  away the difference the planner cares about.

## 9. Explanation debt

- Grey (`.muted`) paragraphs longer than about two sentences. Each one is a design
  problem written down. Ask what control or label would make it unnecessary.
- Essential information hidden in `title` tooltips. These are invisible until hover,
  never appear on keyboard or touch, and can't be skimmed.
- **Comments in `app.js` that describe past confusion** ("used to", "silently",
  "which is how", "nothing on screen"). Treat them as a map. Check that the fix
  covers every place with the same shape, not only the one that broke.
- Help text that is out of date. Model option labels quoting run times should be
  checked against the times measured in the README.

## 10. Visual consistency

- Colours only through the `:root` tokens. A raw hex or `#fff` outside `:root` is
  drift (flag it unless it's on an accent background).
- **Pill meanings are fixed:** `danger` = the plan breaks or input is needed; `warn`
  = needs attention but can be recovered; `ok` = fine or verified; `info` = neutral
  label. The same colour must never mean two things.
- Spacing and sizing units: the stylesheet switches from px to rem partway through.
  That's a sign it was built in layers.
- No inline `style=` for layout that a class already covers.
- Headings, `view-head` layout, card padding and table density should match across
  views.

## 11. Accessibility basics

- Every clickable thing is a `button` or link, or reachable by keyboard. A `tr` with
  `onclick` isn't.
- Visible focus on all controls, including `.cell-input`, `.dt-cell` and `.linkish`.
- **Never colour alone:** R/L crude mode, weekend headers, compare differences, and
  good/bad percentage changes each need a second cue.
- Contrast of `.muted`, `.zero` (opacity .5) and `.cell-down` inputs (opacity .35),
  in both light and dark themes.
- Charts have a text alternative or table, which the projection view already has
  below its chart.
