---
name: ui-ux-audit
description: Audit the inventory planner's web UI for continuity and usability, or review a front-end change before it lands. Use when the user says the UI is confusing, asks for a UI/UX review or audit, or asks whether a screen makes sense - and use it proactively before calling any change to inv-planner/src/invplanner/web/ (index.html, app.js, styles.css) done. Checks that one concept keeps one name, one job uses one control, a planner can tell what an action will touch, and nothing fails silently.
argument-hint: "[review [git-ref] | audit [view ...]]"
---

# UI/UX audit

## Why this exists

The planner was built one feature at a time. Each addition made sense in its own
commit; the whole stopped making sense. The same idea picked up three names, the
same job got two different controls, and warnings that should have been design
became paragraphs of grey text. The user has been confused by their own app more
than once.

The defects that cost the most here were **silent**, not ugly:

- A scenario created from September uploads was dated 2026-07-23 because one of
  two "new scenario" buttons didn't ask for the date (`de4f5fd`).
- A run solved the previous run's output because the run button didn't say which
  scenario it would solve, and opening a result changed the selection (`cb491a8`).
- Scrolling the page past a focused number box turned 0.30 into -36.7 and ruined a
  run, with nothing on screen to show it (`loadOptParams` comment in `app.js`).

Your job is to be the one person who looks at the app **whole**, the way the planner
will, and to catch this before it ships.

## Pick the mode

Parse the arguments. If there are none, choose:

| Mode | When | Output |
|---|---|---|
| `review [git-ref]` | There's a diff touching `inv-planner/src/invplanner/web/` (uncommitted, or against `git-ref`, default `main`) | Feedback for the agent building the front end, inline, with a verdict |
| `audit [view ...]` | No web diff, or the user asks for an audit. Views are tab ids: `alerts dashboard stream projection schedule feeds reference optparams optruns` | A report in `docs/ui-audits/`, plus a short summary in chat |

## Ground rules

1. **Read `conventions.md` first.** It is the contract. Breaking an established rule
   is a finding. A new pattern the contract doesn't cover must be proposed for it.
2. **Report, don't fix.** Audits and reviews change nothing in the app. The building
   agent (or the user) fixes things. The only file you edit is `conventions.md`, and
   only to record a decision the user made.
3. **Judge as the planner, not the author.** The planner knows refinery scheduling
   and the old Excel workbook. They don't know the code, the API, or words like
   `staging`, `block_id`, `reference version`, `warm start` or `as_of`. If a screen
   only makes sense once you've read `app.js`, that is the finding.
4. **Every finding needs evidence:** the view and control where it shows, plus
   `file:line` or the function name. `conventions.md` entries may be stale, so
   re-check them before repeating them. Never report from memory.
5. **A paragraph of help text is evidence, not a fix.** Grey text explaining a trap
   means the trap is still there. So does a code comment saying "used to", "silently"
   or "which is how".
6. **Recommendations must fit the stack:** vanilla JS, no build step, no
   dependencies, the `el()` helper, and the CSS tokens in `:root`. Never suggest a
   framework or a redesign when a label, a disabled state, or one shared handler
   would do.

## Step 1 - Get the running app in front of you

Code shows what is possible; only the screen shows what the planner sees. Do both.

1. **Is the app already running?**
   `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/scenarios`.
   If not, start it in the background from `inv-planner/` with
   `python scripts/run_api.py --port 8000`. Wait with Monitor and an until-loop on
   that curl, not with sleep. If you started it, stop it when you're done.
2. **Open it in a browser.** Invoke the `claude-in-chrome` skill, load the tools in
   one ToolSearch call, and open a **new tab** at `http://127.0.0.1:8000`. Take a
   screenshot of every view in scope, in both modes (Planning and Optimizer). Open
   the collapsible panels. Choose at least one normal scenario and one optimizer
   result (`status: proposed`), because several controls behave differently between
   them. Check at 1366 px wide, a laptop, as well as the default width.
3. **If the browser isn't available,** say so at the top of the output, work from
   the code, and mark each finding `code-only`.

### The browser session is read-only

`data/invplanner.db` holds the planner's real work. **Safe:** switching tabs, modes,
scenarios, products, streams, dashboard groups and window pickers; opening panels;
clicking a feed or model-input card; "Compare with..." and "Switch to..."; "Open
schedule" on a run; typing in a search box; hovering for tooltips.

**Never:** type in a grid cell, override box or optimizer input, or tab out of one
(every change saves immediately). Also never click a `Down` cell or an R/L crude
mode cell, Add, Apply, remove, clear, reset, Sync this feed, Re-sync all feeds,
Choose/Replace file, use workbook, New scenario, Create scenario from source data,
Run optimizer, or Export for Excel.

Several of these also open a native `prompt()` or `confirm()` dialog, which freezes
the browser extension. To study one of those flows, read its handler instead.

## Step 2 - Build the map before judging

Continuity problems can't be seen one screen at a time. Build these first.

**Audit mode:**

- **Start from `ui-flow.md`.** It already maps the screens, what each edit reaches,
  what the page does on its own, the five workflows, and every action that changes
  data, all as read from the code. Confirm it on screen, correct it where the app
  differs, and build the items below on top of it.
- **View inventory.** For each view: its purpose in one line; its controls (label,
  kind, what they change, and **scope**: this scenario / all scenarios / source
  data / optimizer settings / display only); whether a change saves immediately;
  the terms it uses; where it links to.
- **Vocabulary table.** Every user-visible word for each domain concept, and where
  it appears. Look at labels, table headers, pills, toasts, button text, `title`
  tooltips, empty states, confirm/prompt text, and API messages the UI shows. Start
  from the table in `conventions.md` and extend it.
- **Workflow walkthroughs.** Follow each of these on screen, step by step. Note every
  moment the planner has to *remember* something the screen doesn't show, or leave
  the view they're in to find out:
  1. Morning check: what breaks in the next 30 days, and why?
  2. Plan on today's data: upload three files, create a scenario, check it.
  3. Optimize and decide: set inputs, run, read the result, compare with the plan,
     export it.
  4. Correct a wrong number: a feed value, a yield, a charge rate.
  5. Plan an outage: add downtime, see its effect.
  `inv-planner/README.md` describes how these are *supposed* to go. Every warning in
  the README that the UI doesn't make itself is a lead.

**Review mode:**

- Run `git diff <ref> -- inv-planner/src/invplanner/web inv-planner/src/invplanner/api`.
  Include the API because messages and labels are sent from there too.
- Check the diff against `ui-flow.md`. If it changes a control, a jump between views,
  what an action touches, or something the page does on its own, name the section of
  `ui-flow.md` that must change. The work isn't done until the file matches.
- For every label, control, pill, toast or message the diff adds or changes, find its
  **siblings** elsewhere: grep for the words, the CSS class, and controls that do the
  same job. Compare them. A new control that is right in isolation but different
  from its twin is the most common way this app got worse.

## Step 3 - Apply the checks

Work through `heuristics.md`, all eleven checks. It lists what to look for in each,
with examples from this app.

## Step 4 - Rate each finding

Rate by what happens to the **plan**, not by how it looks:

| | Severity | Meaning |
|---|---|---|
| **S1** | Silent wrong plan | The planner does the natural thing and ends up with a wrong plan, wrong data, or a wasted run, and nothing on screen tells them |
| **S2** | Misleads or blocks | The planner misreads something, can't find how to do a task, or can't tell what an action will touch. They notice eventually |
| **S3** | Discontinuity | The same thing is named, placed, styled or behaves differently in different places. They adapt, and it costs trust |
| **S4** | Polish | Spacing, alignment, wording, small visual drift |

When unsure between two levels, choose the higher one and say why.

## Step 5 - Write it up

Use this format for each finding, in both modes:

```
### [S1] <the claim, in one line>
Where: <view> -> <control>   (app.js `functionName`, app.js:LINE)
Planner sees: <what is on screen>
Planner will: <what they will reasonably think or do>
Result: <what happens to the plan or data>
Conflicts with: <conventions.md rule, or its sibling at file:line>   (omit if none)
Fix: <the smallest change that fits the conventions>
Evidence: screenshot | code-only
```

**Review mode:** reply inline, most severe first, then give a verdict:

- **Ready.** Nothing S1 or S2 introduced.
- **Fix before done.** List the S1 and S2 findings.
- **Needs a decision.** The change adds a pattern or term `conventions.md` doesn't
  settle. Say what the decision is.

Only report problems the diff **introduces or touches**. Put pre-existing problems
you noticed under one short "Not introduced here" heading, S1 and S2 only.

**Audit mode:** write `docs/ui-audits/YYYY-MM-DD-<scope>.md` containing:

1. Scope and method: commit, views, browser or code-only, the scenarios you looked at.
2. **Fix first:** the five findings that would remove the most confusion, each one line.
3. Findings, grouped by severity.
4. View inventory.
5. Vocabulary table: canonical term, the variants, where each appears.
6. Workflow walkthroughs, and where each one makes the planner remember something.
7. Decisions needed.

If a previous report exists in that folder, say which of its findings are fixed,
still open, or new. In chat, give the Fix-first list and a link to the file.

## Step 6 - Keep the contract current

Don't choose the right term or pattern yourself. **Propose** one, with a reason.
Usually that reason is "this is the workbook's word and the planner already uses
it", or "this is the version used in most places". Then ask the user. Use
AskUserQuestion with up to four decisions at a time.

Record each answer in `conventions.md`. Move the row from **Open** to
**Established**, and name the date and what was decided. A conflict that comes up
again after it was settled is automatically S3 or worse.
