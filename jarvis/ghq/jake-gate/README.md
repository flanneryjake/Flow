# Jake gate (10/03): one filter in front of Jake's lists

> Jake, 10/03: "More tasks are looping still. They are all giving different errors. We need to put a better filter in place."

Every path that would put a card on Jake's lists (needs-jake, a To-Do step, Approvals) asks `jake_gate.decide()` first.
It is deterministic (no AI call) and fails closed toward Claude.

**Reaches Jake only if** it's his own item (owner:jake, from:jake, life, health, homework), or the ask is:
PIN / spend / post or send / delete; a real decision (a proposal, an explicit choice, a yes/no, answers he gives);
a physical action; a sign-in or credentials (accounts, logins, API keys, the Tailscale admin console, secret links);
or a UAC Yes tap (Claude pops the prompt first, so it's one tap).
**Everything else goes to Claude**: apply / patch / copy / edit, run a script, restart, check files or logs, "the sandbox
couldn't write", "the Worker isn't allowed", fix a path, rerun a test.
**A card that was on Jake's list before never goes back to him by itself**: Claude, except PIN items and unanswered decisions.
Asks closer together than 15 minutes count as one (the old double-marker bug).

A 'claude' card is parked: status:snoozed + `for:claude` + `needs-claude-review`, one comment with the reason, and its
To-Do step cleared. No Worker claims it, and it never shows on the To-Do or Approvals. On the 5060 the hub lists the queue
at `GET /api/claude-queue`. The nightly digest and the 7 AM report data get a count line.
Ledger: `C:\Jarvis\loopnet\jake-gate.jsonl`. Off switch: `JARVIS_JAKE_GATE=off`.

## Rig install (its own session)
```
python apply_jake_gate.py --check      # every anchor found + compiles? writes nothing
python apply_jake_gate.py --apply      # backup ghq.py.bak-<stamp>-gate, copies jake_gate.py next to ghq.py
copy test_jake_gate.py next to ghq.py, then: python -m pytest -q test_jake_gate.py   (22 tests)
```
It wires the rig's three chokepoints (`set_status(..., 'needs-jake')`, `jake_step` / `_attach_jake_step`, `ask_jake`).
On the rig every Jake writer funnels through those three: _log_run, the two-failures rule, send_back, needsjake,
repeatguard, gh_finish, budget_guard and autotask. It also makes `ready()` skip `for:claude`.
Write while the Worker is idle (ghq.py is under the self-edit guard), then restart the Worker. Undo: `--revert`.
Tested on a copy of the rig's ghq.py and the 5060's pre-gate copy: check, apply, 22/22 tests, then a second run
refuses ("already has the Jake gate").
On the 5060 the same gate is live since 17:39 ET 10/03. There, the hub's To-Do writers (todo_tab.write_step /
approve_to_todo), needsjake, autotask and new_card(status='needs-jake') are gated too.
