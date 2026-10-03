---
name: jake-todo-list
description: Add, update, or present things that need Jake (needs-Jake items, homework, reminders) on his pinned To-Do page, in his ADHD-friendly order with true one-click actions.
---

# Jake's To-Do page and needs-Jake lists

## The page
- https://claude.ai/artifact/TFAAwaKfDRaXn4qBfsPr9F (pinned in Jake's sidebar; the ONLY to-do page. A duplicate PCsoQDPAgzt7EmaVsrmRMZ is retired.)
- Items live in the artifact database, collection `todo`, one document per item. Edit with the ArtifactData tool (read first, pin every write with `if_version`). Never make a new page, a desktop file, or a long chat list instead.
- Ticks sync across phone and rig, so `done` is real: read it before listing an item again.
- One-tap Approve buttons on the page are applied to GitHub by the rig every 10 min (PIN items excluded).

### Document fields
`{ n (sort number), section, tier, place, title, detail, why, mins, cards: ["206"], action, due, done, doneAt, hidden, hiddenWhy }`
- `section`: remind, quick, clicks, only, ha, later, plus homework (school items, e.g. PSYC620 discussion replies).
- `tier`: `blocker` (high-priority, stops work), `click` (truly one click), `rest`.
- `place`: `rig`, `5060`, `phone`.
- `action`: one of `{label, url}` (a button that opens the exact page), `{copy, label, where}` (a phrase he pastes in a named thread), or `{where}`.
- `detail` may use backticks for code. `why` = one line on what it unlocks. `mins` = honest estimate. `due` = YYYY-MM-DD.
- Resolved elsewhere -> set `done: true` + `doneAt` (or `hidden: true` + `hiddenWhy` for duplicates). Don't delete history.

## How Jake wants needs-Jake lists (ADHD friendly)
1. High-priority BLOCKERS first.
2. Then ONE-CLICK items, and make them truly one click (a link that opens the exact screen, a button, or a file already open on his screen). Never "go find X and then...".
3. Then the rest, grouped **At the rig / At the 5060 / Phone**.
- Each item: plain title, how long, why it matters. No jargon, no internal ids.
- Never repeat an item that is already done, or that Claude could do itself. Check first: Jake's rule is DO IT YOURSELF; only UAC prompts, physical actions and account sign-ins are his.
- When several things need him at once, give ONE ordered checklist, not several messages.
- Secrets: he saves them as a .txt on the rig desktop, with no "key" or "token" in the file name. Never ask for them in chat.
- Never ask him to open or review anything on the homebase PC; deliver to the phone or the rig.
