---
name: jarvis-task-board
description: Work Jake's Jarvis task board (GitHub issues in flanneryjake/jarvis-tasks): file cards, check status, approve, snooze, close, and pick labels without flooding GitHub.
---

# Jarvis task board (flanneryjake/jarvis-tasks)

Private GitHub repo of issues. It replaced Notion on 2026-10-02 (Notion is retired; never read or write it). The PCs' Workers poll it (60 s, plus an instant POST /wake) and run approved cards. Jake approves from the phone app Inbox or the app's To-Do tab (the Discord bot was archived 2026-10-03).

## Labels
- Status (exactly one): `status:inbox` (idea, not triaged) -> `status:staged` (waiting for Jake's approval) -> `status:approved` (will run) -> `status:working` (claimed); side states `status:needs-jake`, `status:snoozed`. Closed = done (closed as "not planned" = rejected).
- Machine: `machine:homebase` (the 5060), `machine:rig`, `machine:backup`, `machine:laptop` (only the Tars short-text lane), `machine:any`.
  Convention: short pure-text -> laptop; long-form, code or clinical -> rig; web, buying or posting -> any.
- Priority: `p0` (wakes the rig), `p1`, `p2`.
- Kind/grouping: `type:*`, `project:*` (e.g. project:mtg-proxies), `type:idea`.
- Flags: `pin` (money, posting/sending outside, deleting personal data, accounts/credentials, exposing services, merging main: always waits for Jake's PIN), `now` (do-it-now lane), `paused` / `resume`, `for:claude` (a Claude ask), `needs-claude-review`, `from-email` (from Jake's 7 AM report reply), `nightly-cards`, `auto` (auto-approved follow-up), `owner:jake`, `sched:deferred`.
- Special issues: `health` issues (homebase #1, rig #2, backup #889); Fleet control #352 (label `fleet`); daily "Waiting on Jake" report #593.

## Rules the Workers follow (so you write cards that work)
- Approved = runs. Jake decided (2026-09-29) an Approved card should run; only PIN kinds wait.
- A claim is a comment; the earliest wins. One comment per run records minutes.
- Max 3 runs per card per day. 2 failed or timed-out runs -> needs-jake. No-progress timeout: 15 min.
- Follow-up cards: auto-approved routine follow-ups (cap 100/day, at most 2 per card, deduped); PIN or ask-once ones are staged.
- A card waiting only on an input file is SNOOZED, never needs-jake: label `status:snoozed` + a comment `<!-- jarvis:snooze {"path": "...", "machine": "..."} -->` (also on card/machine/time). Bare file names resolve to C:\Jarvis\outputs-repo\cards\card-<n>\<name>.
- Blocked cards go through ghq.send_back() (local-model triage, then claude -p, snooze when possible). Only after 3 snoozes, or when the blocker really is Jake, does it go to Jake once.
- Medium-risk code that passed the Docker sandbox needs no PIN; the card is retitled "[Docker-tested] <plain intent> - <how it went>".
- Clinical content never goes to Gemini.

## How to do common things
- **File a card**: title = short imperative; labels status + machine + priority (+ project/type); body = small concrete steps, a verify step, a stop condition, and where the output goes (jarvis-outputs repo or the card comment). Search open issues first: no duplicates.
- **Approve**: swap `status:staged` -> `status:approved` (no-op if already approved). Never approve a `pin` card for Jake.
- **Read status / "what's running"**: open issues by status label; `status:working` = running now; Health issues for machines.
- **Close as done**: comment what was done and where the result is, then close as completed. Leave PII (résumés, finances) out of the repo; say where it lives instead.
- **Waiting on Jake**: add a `jarvis:jake` step marker so it shows in his To-Do tab (jake-todo-list skill).

## Don't flood GitHub
The PCs share one account limit (5,000 calls/hr) and GitHub's secondary content-creation limit. On 2026-10-02 ~470 comments in 50 min tripped it. So: batch edits, one comment per change (edit your own comment instead of posting new ones), no repeated identical comments within 6 h, and pause on any 403 "secondary rate limit".
