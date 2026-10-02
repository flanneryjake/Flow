# Jarvis autonomy lane

Lets Jarvis file its own cards and start the routine ones without a tap from Jake, while money, posting,
email, deleting and account changes still wait for the PIN.

Why: every card Jarvis creates today lands as staged (or, in Hub v3, "Confirm" when an AI moved it), and the
rig Worker holds cards as Tier C when a risky word appears anywhere in them. On 2026-09-30 the rig logged
"26 approved card(s) for rig, ran 0" for over a day; cards like "Smart Printer Watchdog" and "Automated Case
Study Generator" were held as "money/messaging/publish/accounts". Nothing in them spends, posts or deletes.

## Pieces

| File | What it does |
| --- | --- |
| `classify.py` | Gives a card a tier (`free`, `free_logged`, `ask_once`, `pin`, same names as `guardrails/policy.json`). Only the **title** (the card's objective) can hold a card; risky words in the body become "watch" notes. Negated or planning phrasing ("draft", "proposal", "don't publish", "before anything is bought") doesn't count. |
| `autotask.py` | Wraps `ghq.new_card`. Free / free_logged cards are filed as `status:approved` with an `auto` label, a comment naming the rule, and a wake; everything else is filed staged with the reason. |
| `clock.py` | The cap day: resets at 8 PM US Eastern (DST rule written out, no tz database needed on Windows). |
| `dedupe.py` | Duplicate/noise filter run inside `autotask.propose`. |
| `scheduler.py` | Ranks the queue and spends the daily cap on the best cards; batches small laptop text cards. |
| `selfrepair.py` | Turns watchdog/Worker failures into `diagnose: <machine> <symptom>` cards. |
| `gapreport.py` | Weekly report of failure reasons, slow kinds and held kinds, plus upgrade and skill proposals. |
| `fallback.py` | When Claude is out of usage, narrows the rig to local-model research / next-project cards. |
| `test_*.py`, `fakeghq.py` | `python -m unittest` from this folder. No network: `fakeghq.py` stands in for ghq. The classifier cases are the real rig cards from the Tasks board. |

## Limits

- `JARVIS_AUTO_PER_DAY` (default 100): after that many auto cards since the last 8 PM ET reset, new cards are
  filed staged with `sched:deferred` (scheduler.py promotes the best of them after the reset).
- `JARVIS_AUTO_DEPTH` (default 3): an auto card spawned from a chain of 3 auto cards is filed staged, so a
  loop of follow-ups can't run forever. A follow-up of a card Jake approved starts the count at 0.
- `JARVIS_AUTO_OFF=1`: kill switch, everything is filed staged.
- Duplicates return the existing card instead of filing a new one (dedupe.py, below).

The card-level tier is only the first gate. The run itself must still deny send/post/delete/purchase tools
to unattended Claude runs and stop with `NEEDS PIN: <action>` (Rig Job Rules, Global Policy). That run-time
guard is what makes it safe to let routine cards start on their own.

## Wiring it in (agent.py and the hub, which live on the PCs, not in this repo)

1. Worker: where it creates follow-up cards, call `autotask.propose(title, body, machine=..., spawned_from=n)`
   instead of `ghq.new_card(...)` (or the Notion equivalent).
2. Worker: replace the keyword Tier C check with `classify(title, notes)[0] == 'pin'`, so a card is held only
   when its objective is a PIN action.
3. Hub v3: cards labelled `auto` skip the Confirm step; show the auto comment on the card so Jake can see why
   it ran, and give the kill switch a toggle on the System tab.

CLI: `python autotask.py check "Buy filament"` prints what would happen without filing anything.

## Next jobs (scheduler, dedupe, self-repair, gap report, usage fallback)

All five are standard-library Python, take their GitHub access through `autotask._ghq()` (so the tests swap in
`fakeghq.py`), file cards only through `autotask.propose` (so every card is tiered and deduped), and file
nothing while `JARVIS_AUTO_OFF=1`. Each has a CLI that is a dry run unless told otherwise.

### dedupe.py: no more duplicate cards

On 2026-10-01, 28 duplicate cards had to be closed by hand. `autotask.propose` now calls `dedupe.check` first
and files nothing when:

- an open card, or one closed in the last 7 days, has the same normalized title (case, punctuation and a
  leading `[Tag]` ignored), or
- the title + goal tokens are near-identical (Jaccard >= 0.8, `JARVIS_DEDUPE_JACCARD`; the goal is a `Goal:` line,
  else the first paragraph), or
- it is a selftest and the same selftest (numbers ignored, any age) already failed or was held for a by-hand
  reason (plug, press a button, load filament, log in by hand, ...). That returns status `rejected`.

`propose` returns `(existing_number, 'duplicate' | 'rejected', reason)`. Each rejection is printed to stderr and
appended to `JARVIS_LOG_DIR\autotask-rejects.jsonl` (default `C:\Jarvis\logs` when it exists).
CLI: `python dedupe.py "title" --body "..."`.

### scheduler.py: spend the cap on what matters

The cap is 100 auto cards a day (`JARVIS_AUTO_PER_DAY`), counted since the last 8 PM ET reset. Ranking:
income/template/store and fix/self-repair work first, then research, then chores, then selftests (ones that need
a human step last); `p0`/`p1` and age (+1 a day, at most +10) break ties. Two or more short pure-text cards for the
laptop (no code, paths or commands; body <= 800 chars) become one combined card that costs one slot
(`JARVIS_BATCH_MAX`, default 8 per batch). Candidates are approved, unclaimed, non-PIN cards plus staged cards
that autotask deferred because the cap was hit (it now labels those `sched:deferred`).

```
python scheduler.py                 # print today's plan and what waits for the reset
python scheduler.py --apply         # label sched:today / sched:deferred, file the batch card, promote deferred cards
python scheduler.py --cap-left 20   # plan for a given number of slots
```

`--apply` snoozes batched originals with `sched:batched` and a comment pointing at the batch card; the batch card
asks the Worker to answer each one and close it.

### selfrepair.py: failures become diagnose cards

`selfrepair.run(text, machine)` reads a Machine Health alert, watchdog log lines or a Worker log tail and files
one `diagnose: <machine> <symptom>` card per symptom (Remote Control down / task missing / running by hand /
restart loop, Worker idle with cards waiting, task queue unreadable, health report failing, services down, run
timeouts, errors and tracebacks). Cards are `machine:any`, `p1`, labelled `self-repair` and `agent:claude`, with
the redacted evidence in the body. A lone "Remote Control restarted" files nothing (3+ restarts in one text is a
loop and does). "Needs Jake" and usage-limit lines are ignored. Repeats are deduped, so the same alert every
5 minutes files one card. CLI: `python selfrepair.py --machine rig --text-file alert.txt [--file]`.

### gapreport.py: weekly report

`python gapreport.py --out C:\Jarvis\logs\gap-report.md [--file-cards]` reads the cards touched and the Worker
run comments of the last 7 days and reports failures by reason (usage limit, needs PIN, by-hand step, auth,
missing tool, network, local model, printer, vague card, timeout), the slowest kinds (average minutes of done
runs; kind is the `[Tag]` in the title or the scheduler kind) and the most-held kinds. It drafts up to 3 upgrade
cards and 1 `New skill: <kind> playbook` card; `--file-cards` files them through autotask (an upgrade that is
still open is not filed again).

### fallback.py: keep the rig busy when Claude is out of usage

`fallback.check(log_tail, machine='rig')` looks at the last usage line of the Worker log (`usage limit`,
`WAITING: usage resets ...`). While limited it writes `JARVIS_STATE_DIR\fallback.json` (default
`C:\Jarvis\state`) with mode `local-fallback`, and only cards that are `machine:rig` + `model:local` and research
or next-project drafting may run (`fallback.allowed(card)`). If none are queued it proposes a "Draft the next
project brief" card and a local research card for the oldest open idea (both `model:local`, `lane:fallback`,
deduped). It returns `can_sleep: true` only when nothing is queued for that lane, with a `say` line for the
health row. A later normal run in the log (or `python fallback.py clear`) switches back.
CLI: `python fallback.py check --log C:\Jarvis\logs\worker.log`, `python fallback.py state`.

### Wiring into agent.py / worker.ps1 (not in this repo)

1. **Follow-up cards**: already `autotask.propose(...)`; dedupe now runs inside it, nothing else to change.
   Treat a `duplicate`/`rejected` status as success (the card exists).
2. **Card selection** (`ghq.ready` result): skip cards labelled `sched:deferred` or `sched:batched`; prefer
   `sched:today`. While `fallback.load_state()['mode'] == 'local-fallback'`, run only cards where
   `fallback.allowed(card)` and run them with the local model.
3. **Scheduler**: run `python scheduler.py --apply` from worker.ps1 (or a scheduled task, ask_once) on homebase
   right after the 8 PM ET reset and every hour after, so newly filed cards are ranked too. Only one machine
   should run it.
4. **Self-repair**: in watchdog.ps1, after the alerts list is built, pipe `($alerts -join '; ')` into
   `python selfrepair.py --machine $machine --file` when it is non-empty. In the Worker, after a `failed` or
   `timeout` run, call `selfrepair.run(log_tail, machine)`.
5. **Usage fallback**: when a run ends `waiting-usage` (or each idle poll), call
   `fallback.check(log_tail, machine)`; if `can_sleep` is true the rig may sleep, otherwise keep polling. Put
   `say` on the health row.
6. **Gap report**: weekly (Sunday evening) on homebase: `python gapreport.py --out ... --file-cards`, then push
   the report path or post it as a comment on a `type:note` card.
