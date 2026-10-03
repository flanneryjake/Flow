# Worker efficiency fixes (Jake's 10/03 email: E1-E10, #1547)

Cards: jarvis-tasks #1516-#1525 (E1-E10) and #1547. Findings: jarvis-outputs `worker-efficiency/worker-efficiency-2026-10-03.md`.

## In this folder

| File | What |
|---|---|
| `repeatguard.py` | **E2 + E4.** A no-AI check inside `ghq.claim()`: when a card already asked Jake and nothing changed since (no answer, no ticked To-Do item, no edit, no new file, no arrived input), the claim is refused. The card goes back to needs-jake with its To-Do step re-attached. Approvals alone never count as a change. Fails open. |
| `apply_worker_efficiency.py` | Installs the repeat guard and **E7** (every run records a model: `claude:<default>`, the `--model` passed, `local:*`, or `worker-only` when no model ran) into either Worker fork. It checks every anchor first and writes nothing on a miss, writes backups, and supports `--check` and `--revert`. |
| `apply_crash_pause.py` | **#1547.** A crashed Claude run (non-zero exit, empty output, an API error, or a card summary that merely mentions limits) no longer pauses the whole Worker for 5 h. Only the CLI's own limit or login line does. A crash logs that card `failed` and skips it for 30 min while the queue keeps going. Three crashes in a row rest the Worker 30 min. A limit with no readable reset time re-checks after 1 h (it reads `\|<epoch>` and `resets Oct 6`). Works on both forks, with `--check` and `--revert`. |
| `queue_order.py`, `apply_queue_order.py` | **E10.** Both Workers order approved cards: `now`, the parked card, `resume`, then p0 > p1 > p2 > no priority > p3, and inside each step focus projects before `project:admin` before no project, lower `phase:N` first. Before this, the 173 unlabelled cards (many focus-project cards) waited behind 229 p2 ideas, and the rig ignored `now`/`resume`. Cheap text cards already go to the local model first on the rig (`local_lane.py`). Backup `agent.py.bak-*-qo`. |
| `apply_one_comment.py` | **E8.** One new comment per run: the claim comment the Worker won holds the live progress (5060) and becomes the run record at the end (same markers). It falls back to a new comment if the edit fails. Off switch `JARVIS_ONE_COMMENT=off`. Backup `ghq.py.bak-*-e8`. `repeatguard.py` reads the run's `ended` time from runmeta for this. |
| `apply_windows_tests.py` | **E6 (rig only).** A MEDIUM card with a clean Docker PASS whose only gap is the Windows-only part now runs that part on the rig through the existing sandbox-gate path (the tested script in its own work folder). It still goes to Jake if the Worker runs as admin, or the script asks for admin or system-wide changes (RunAs, HKLM, services, firewall, Defender, drivers, boot, ACLs, highest-privilege tasks) (#592), plus every existing rule. Off switch `JARVIS_E6=off`. Backup `sandbox_gate.py.bak-*-e6`. |
| `apply_wait_met.py` | **Rig fix (#982, 10/03).** A run that asks to wait on something already true (or unwatchable) is parked for Claude's loop triage with `ghq.park_for_triage`, not put on Jake's To-Do. Backup `budget_guard.py.bak-*-wm`. |
| `apply_guard_flag.py` | **Rig fix (10/03 evening).** The rig self-edit guard skips reverting when `C:\Jarvis\audit\fixes-running.flag` was touched during the card, same rule as the 5060. Touch that flag before any live Worker fix; the installer touches it itself. Backup `agent.py.bak-*-gf`. |
| `apply_one_step.py` | **Rig fix (10/03).** `ask_jake` attaches a To-Do step only when the card has no current one, so a needs-jake run no longer puts two items on Jake's To-Do (same fix as the 5060). Backup `ghq.py.bak-*-os`. |
| `apply_rig_waits.py` | **Rig fixes (10/03 trace).** A run whose last step is patching the running Worker code (#600, #609) goes to Claude review via `park_for_triage` on the first pass instead of a rule-4 re-run and then Jake. A checkpoint that only waits on queued jobs (#795) snoozes 15 min instead of a Claude run every 2-5 min. Off switches `JARVIS_SELFPATCH_TRIAGE=off`, `JARVIS_CKPT_SNOOZE_MIN=0`. Backup `budget_guard.py.bak-*-rw`. |
| `replay.py`, `fixtures/` | Replays real card histories (#690, #123, #924, #378, #7) to show which claims the guard would have refused. |
| `test_*.py` | Tests. `WE_SNAPSHOTS=<dir with 5060/ and rig/ copies> pytest` also installs into both forks and drives the patched `claim()`. |

## Replay on last night's worst loops

| Card | Claims | Refused by the guard |
|---|---|---|
| #123 printer test | 22 | 19 |
| #924 Tars web search | 14 | 12 |
| #7 storefront alerts | 8 | 7 |
| #690 print_service autostart | 6 | 5 |
| #378 speech-to-text | 6 | 5 |

The claims that still ran were each card's first claim, or a claim made after a real change (for example #123 after the card it waited on finished).

## Install (on each PC, Worker idle)

```
python apply_worker_efficiency.py --ghq C:\Jarvis\ghq --agent <agent folder> --check
python apply_worker_efficiency.py --ghq C:\Jarvis\ghq --agent <agent folder>
```

Then restart the Worker, and the hub on the 5060.

- **Off switch:** `JARVIS_REPEAT_GUARD=off`.
- **Bot logins:** `JARVIS_BOT_LOGINS` lists the logins whose plain comments don't count as Jake typing (default `clauderigassist-cell`).
- **Marking a fix:** a Claude thread that fixes something on a card adds `<!-- jarvis:changed -->` to its comment so the card runs again.

E3 (hands-on steps to Jake's To-Do by device, card waits until ticked) needed no new code: `ghq.jake_step` already files `rig` / `5060` / `phone` / `homework` items on both forks, a tick re-approves the card, and the repeat guard keeps it from re-running before then.

Order on each PC (each installer is independent; all anchors were checked together on both forks):

```
python apply_crash_pause.py --agent <agent folder>
python apply_queue_order.py --agent <agent folder>
python apply_one_comment.py --ghq C:\Jarvis\ghq
python apply_windows_tests.py --agent <agent folder>      # rig only
```
