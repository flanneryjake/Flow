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

CLI equivalents: `python ghq.py ready --machine homebase`, `now "Print plate 5" --watch`, `watch 12`, `claim 12 --machine rig`,
`log 12 --machine rig --outcome done --started 2026-09-30T10:00:00Z --log-file task.txt`, `approve 12`,
`ask 12 "Which printer?"`, `new "Title" --machine rig --priority p1`, `usage --days 7`.

## "Do it now" lane (rig → homebase)

Jake types a task on the rig and homebase starts it at once, pausing whatever card it is running.

On the rig, either type `/homebase print plate 5` in Claude Code (copy `homebase-command.md` to
`%USERPROFILE%\.claude\commands\homebase.md`), or run it directly:

```
python C:\Jarvis\ghq\ghq.py now "Print plate 5" --machine homebase --watch
```

That files an approved card labelled `now` + `p0` (Jake typed it, so it needs no second approval; the guardrails
still apply to what the run may do), POSTs `/wake` to every URL in `JARVIS_WAKE_URLS`, and with `--watch` prints
the card's progress until it finishes. The rig needs `JARVIS_WAKE_URLS=http://100.90.201.22:8790/wake` (or
homebase's tailnet name) as a user environment variable, alongside its `GITHUB_TASKS_TOKEN`.

What `agent.py` does with it:

1. **Wake.** `POST /wake` ends the poll sleep. If a card is already running, it instead sets a flag that the
   run's watcher thread checks.
2. **Check while running.** While a card runs, a watcher thread calls `ghq.now_waiting(MACHINE, etag_file)`
   every 15 s and whenever `/wake` fires (an unchanged queue is a free ETag 304).
3. **Pause.** If a `now` card is waiting and the running card is not itself a `now` card: kill the Claude
   process tree (`taskkill /T /F /PID <pid>`), then `ghq.log_run(current, MACHINE, 'paused', started=...,
   summary='Paused for #N')`. The paused card goes back to approved with a `resume` label, and `ready()` puts
   it right after the `now` cards, so it runs next. Its prompt should say "this run was interrupted; check
   the work folder and continue from where it stopped". A `now` card never pauses another `now` card; they run
   in the order Jake typed them. Hardware already started (a print underway) keeps going; only the Claude run
   stops.
4. **Run it.** Claim and run the `now` card as usual (`ready()` already sorts `now` cards first).
5. **Progress.** Run Claude with `--output-format stream-json --verbose` so output arrives as it happens. 30 s
   into any run, and every 30 s after that while something new has happened, call
   `cid = ghq.progress(number, MACHINE, text, cid)` with the elapsed time and the latest step (last tool call
   or assistant line, secrets redacted). It edits one comment rather than adding new ones. Runs shorter than
   30 s post no progress, just the usual run comment.
6. **Out of usage.** If the Worker is paused on the Claude usage limit when a `now` card arrives, post
   `ghq.progress(number, MACHINE, 'Homebase is out of Claude usage until <time>; this runs first when it resets.')`
   so the rig sees why nothing is happening.

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
