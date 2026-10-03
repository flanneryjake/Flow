# Jarvis task queue on GitHub

This replaces the Notion Tasks board and the Machine Health table with GitHub Issues in the private repo
`flanneryjake/jarvis-tasks`. GitHub is the one source of truth that the PCs, the phone and cloud Claude can
all reach. The tailnet only carries an instant "wake up" nudge, so an approved card starts at once instead
of waiting for the next poll.

```
 phone app / hub ──approve (PIN for pin cards)──► GitHub Issues ◄── cloud Claude threads, nightly routine
        │                                          ▲      ▲
        └── POST /wake over the tailnet ──►  homebase Worker   rig Worker   (poll every 60 s as fallback)
                                             watchdogs ─► pinned "Health: <machine>" issues
```

## How a card works

| Notion | GitHub |
|---|---|
| Card | Issue |
| Status Inbox / Staged / Approved / Snoozed | label `status:inbox` / `status:staged` / `status:approved` / `status:snoozed` |
| Status In progress + `Claimed by` | `status:working` + `claimed:<machine>` and a claim comment |
| Done | issue closed as completed |
| Notes starting "NEEDS JAKE" | `status:needs-jake`, with the question as a comment. Workers skip it, so no more repeat passes |
| `Agent log` (last run only) | one comment per run with machine, outcome and minutes, so the full history is kept |
| Machine / Priority / Type / Project | `machine:*`, `p0`-`p2`, `type:*`, `project:*` labels |
| Approval code | `pin` label (the hub asks for the PIN before approving; the code itself is not copied) |
| Auto-executable | dropped: an approved card runs (Jake, 2026-09-29) |
| Machine Health rows | pinned issues `Health: homebase` / `Health: rig`, rewritten by each watchdog every 5 min |

Two failed or timed-out runs in a row move a card to `status:needs-jake` on their own, so a card that keeps
hitting the 45-minute timeout stops burning usage.

## Setup

Done from the cloud on 2026-09-30: the repo `flanneryjake/jarvis-tasks` exists, the labels and the two
health issues are in place, and the 200 open Notion cards are copied (their page content stays in Notion,
linked from each issue).

What each PC still needs before its Worker or watchdog can use GitHub (no scripts needed):
1. A fine-grained token for `jarvis-tasks` only, with **Issues: Read and write**
   (https://github.com/settings/personal-access-tokens/new).
2. Start menu → "Edit environment variables for your account" → New: name `GITHUB_TASKS_TOKEN`, value
   the token. Then sign out and back in, or restart the Worker.
3. `ghq.py` saved to `C:\Jarvis\ghq\ghq.py` (a Remote Control session can do this; `install-ghq.ps1`
   does steps 2 and 3 too, where PowerShell scripts are allowed).

## Worker contract (for agent.py)

`agent.py` can `import ghq` from `C:\Jarvis\ghq` or call the CLI. The loop is:

```python
import ghq
cards = ghq.ready(MACHINE, use_etag_file=r'C:\Jarvis\ghq\ready-cache.json')  # an unchanged queue costs no API quota
for card in cards:
    if ghq.claim(card['number'], MACHINE):        # False = the other PC got it first; try the next card
        started = ghq.now_iso()
        ...run Claude Code on the issue title + body...
        ghq.log_run(card['number'], MACHINE, outcome, started=started, summary=..., log_tail=..., model=...)
        break
```

- `outcome` is one of `done`, `needs-jake`, `failed`, `timeout`, `waiting-usage`, `released`. On a usage
  limit, use `waiting-usage` (the card goes back to approved) and pause as today.
- For a question to Jake, use `ghq.ask_jake(number, question)`.
- For a follow-up card, use `ghq.new_card(title, body, machine=..., spawned_from=number)`. It is created as
  `staged`, so Jake approves it. Which kinds of card skip approval is up to the guardrails rewrite.
- Add a `POST /wake` handler that just ends the current poll sleep. `ghq.approve()` calls every URL in
  `JARVIS_WAKE_URLS`, for example `http://homebase:8790/wake,http://rig:8790/wake`.
- Keep polling every 60 s as the fallback. Two PCs polling every minute is about 120 of the 5,000 calls an
  hour the token allows, and ETag 304s are free.

CLI equivalents: `python ghq.py ready --machine homebase`, `claim 12 --machine rig`,
`log 12 --machine rig --outcome done --started 2026-09-30T10:00:00Z --log-file task.txt`, `approve 12`,
`ask 12 "Which printer?"`, `new "Title" --machine rig --priority p1`, `usage --days 7`.

## Phone hub

In the hub, the Approvals list reads
`GET https://api.github.com/repos/flanneryjake/jarvis-tasks/issues?labels=status:staged`. Approve runs
`ghq.approve(n, by='phone')` after the PIN check when the card has the `pin` label. The hub already runs on
homebase, so the token never goes to the phone.

## Cutover

1. Run the setup above. Notion keeps running and the Workers still read Notion.
2. Switch `agent.py` to `ghq` on one PC, then the other, and the hub's approvals list.
3. Once both Workers have claimed from GitHub for a day, stop the watchdog's Notion writes and put
   the Notion board into read-only use (archive). Point the nightly run-plan routine at the repo.
