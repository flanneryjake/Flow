# Loop net: what the rig (and any other Worker PC) needs

The loop net is live on homebase (the 5060) since 2026-10-03. The ledger lives here in `C:\Jarvis\loopnet\`
and the hub serves it to other PCs. The rig only needs the library files, nothing else.

## Copy these two files into the rig's ghq folder
- `C:\Jarvis\ghq\loopnet.py` (new)
- `C:\Jarvis\ghq\ghq.py` (hooks added). If the rig's ghq.py differs, port just these pieces:
  `_loopnet()`, `_breaker()` + the `_breaker(path)` call in `request()` for non-GET calls, the `record(...)` lines in
  `claim()` and `approve()`, `park_for_triage()` and the loop check at the top of `send_back()`, and the all/any gate
  (`GATE_KINDS`, `_gate_condition`, `_gate_text`, `snooze_until_gate`, the `kind in GATE_KINDS` branches in
  `snooze_until()` and `condition_met()`, and `_waits_on_card` in `wake_snoozed()`).

## How the rig reaches the ledger
- On any PC whose `C:\Jarvis\watchdog\machine.txt` is not `homebase`, loopnet talks to the hub at
  `https://laptop-4150egrs.tail3bbcb8.ts.net/api/loopnet/*` with the header `X-Jarvis-Notify: 1` (same rule as /api/notify).
  Override with the user env var `JARVIS_HUB_URL`.
- If the hub can't be reached, the gate falls back to GitHub (`ghq.bounce_history`), so a card that already
  bounced is still caught.
- The GitHub write breaker counts per PC (the rig keeps its own counter in %TEMP%).

## What the rig's Worker does differently
- `ghq.send_back(n, 'rig', reason)` now returns `('loop',)` when the card already came back for Jake before. The card
  is snoozed, unclaimed, and labelled `triage` + `needs-claude-review`, and homebase's triage lane handles it within 2 minutes.
  Jake is not asked. Nothing else in agent.py has to change (it only logs the return value).
- To wait on several things, call `ghq.snooze_until(n, 'all' | 'any', [{"kind": "file", "value": "D:\\full\\path.csv"},
  {"kind": "card", "value": 12}, {"kind": "time", "value": "2026-10-04T01:00Z"}, {"kind": "any", "value": [...]}], 'rig', reason)`.
  Files must be full paths. `wake_snoozed('rig')` re-approves the card when the gate opens.

## Morning report
After the 7 AM report email is sent, call once:
    POST https://laptop-4150egrs.tail3bbcb8.ts.net/api/loopnet/rollover   {"by": "morning report"}   (header X-Jarvis-Notify: 1)
or on homebase: `python C:\Jarvis\ghq\loopnet.py rollover morning report`.
If nothing calls it, the counter rolls by itself at 07:30. `GET /api/loopnet/status` gives yesterday's loop count for the report.

## Off switch
User env var `JARVIS_LOOPNET=off` turns the gate, ledger and breaker off on that PC (everything behaves as before).
