# Worker efficiency fixes (Jake's 10/03 email: E1-E10, #1547)

Cards: jarvis-tasks #1516-#1525 (E1-E10) and #1547. Findings: jarvis-outputs `worker-efficiency/worker-efficiency-2026-10-03.md`.

## In this folder

| File | What |
|---|---|
| `repeatguard.py` | **E2 + E4.** A no-AI check inside `ghq.claim()`: when a card already asked Jake and nothing changed since (no answer, no ticked To-Do item, no edit, no new file, no arrived input), the claim is refused. The card goes back to needs-jake with its To-Do step re-attached. Approvals alone never count as a change. Fails open. |
| `apply_worker_efficiency.py` | Installs the repeat guard and **E7** (every run records a model: `claude:<default>`, the `--model` passed, `local:*`, or `worker-only` when no model ran) into either Worker fork. It checks every anchor first and writes nothing on a miss, writes backups, and supports `--check` and `--revert`. |
| `replay.py`, `fixtures/` | Replays real card histories (#690, #123, #924, #378, #7) to show which claims the guard would have refused. |
| `test_repeatguard.py`, `test_install.py` | Tests. `WE_SNAPSHOTS=<dir with 5060/ and rig/ copies> pytest` also installs into both forks and drives the patched `claim()`. |

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
