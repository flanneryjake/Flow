---
name: jake-todo-list
description: Add, update, or present things that need Jake (needs-Jake steps, homework, reminders) in the phone app's To-Do tab via jarvis:jake card markers, in his ADHD-friendly order with true one-click actions.
---

# Jake's To-Do tab and needs-Jake lists

## Where needs-Jake items live (since 2026-10-03)
- The phone app's **To-Do tab** (hub https://laptop-4150egrs.tail3bbcb8.ts.net, app V1.5). It is built from GitHub cards in flanneryjake/jarvis-tasks, so there is no separate list to keep in sync.
- The tab lists open `status:needs-jake` cards plus any open card labelled `todo-tab` (the label leaves the card's status alone). Do NOT use the `jake-todo` label: it belongs to the Workers' summary issue #650.
- The old artifact page (claude.ai/artifact/TFAAwaKfDRaXn4qBfsPr9F) is RETIRED and read-only. Never add items there, and never make a new page, a desktop file, or a long chat list instead.

## How to add or change an item
Put a marker comment on the card (the newest marker wins):
`<!-- jarvis:jake {"place": "rig|5060|phone|homework|junk", "title": "...", "why": "...", "mins": 2, "steps": ["..."], "button": {"label": "...", "url": "..."}, "ask": "...", "after": "approve|close", "pin": false, "since": "YYYY-MM-DD"} -->`
- On a PC: `python ghq.py jake-step <n> --json '{...}'` or `ghq.ask_jake(n, q, step)`.
- `after: approve` = his tick/"yes" approves the card and it runs (a "no" closes it). `after: close` = a Jake-only task; the card is `status:snoozed` + `todo-tab` and closes when he ticks it.
- Homework (school, e.g. PSYC620 discussion replies) = its own card with place `homework` and a due date in the title or why.
- Approving a card clears its marker. Approving a card whose Jake step is still open is refused, so finish or clear the step first.
- Hub API on the 5060: GET /api/todo, POST /api/todo/<n>/done|answer|add.

## How Jake wants needs-Jake lists (ADHD friendly)
1. High-priority BLOCKERS first.
2. Then ONE-CLICK items, and make them truly one click (a button that opens the exact screen, or a yes/no). Never "go find X and then...".
3. Then the rest, grouped **At the rig / At the 5060 / Phone / Homework**.
- Each item: plain title, how long, why it matters. No jargon, no internal ids.
- Never repeat an item that is already done, or that Claude could do itself. Check first: Jake's rule is DO IT YOURSELF; only UAC prompts, physical actions and account sign-ins are his.
- When several things need him at once, give ONE ordered checklist, not several messages.
- Secrets: he saves them as a .txt on the rig desktop, with no "key" or "token" in the file name. Never ask for them in chat.
- Never ask him to open or review anything on the homebase PC; deliver to the phone or the rig (a file review becomes a phone yes/no).
