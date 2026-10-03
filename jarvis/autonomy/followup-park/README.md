# followup-park: Worker follow-ups stop landing on Jake's Approvals (10/03)

**Cause.** `autotask.propose()` files a Worker's follow-up card as approved (label `auto`) until the shared daily auto
limit (`JARVIS_AUTO_PER_DAY`, 100) is reached. After that, every follow-up was filed **staged = on Jake's Approvals**.
On 10/03 the cap was hit at 00:02 ET. About 288 follow-ups landed on Jake that day (about 107 from the rig), and he
tapped through them in batches (15:25, 15:49 ET). Each run then spawned more.

**Fix** (live on the 5060 since 15:53 ET 10/03; this kit is for the rig's `C:\Jarvis\autonomy\autotask.py`):
- A follow-up past the cap (tier free / free_logged, parent not a PIN card) is **parked quietly**: status:snoozed +
  `preapproved`, with a time snooze to 06:00. The wake sweep approves it then, under the parent card's approval. One
  line per card goes to `C:\Jarvis\loopnet\followups-parked.jsonl`, and the nightly digest prints the count.
- A follow-up whose work changes the live Worker / hub code goes to Claude review (park_for_triage), not to Jake.
  This needs the 5060 classify.py rule `worker-self-edit`; without it, this part does nothing.
- PIN tiers, children of PIN parents, and cards that aren't follow-ups are unchanged.

**Apply on the rig** (its own session):
```
python apply_followup_park.py --check     # every block found once + compiles? writes nothing
python apply_followup_park.py --apply     # backup autotask.py.bak-<stamp>-fp
python test_followup_park.py              # 6 checks, fake GitHub
```
Write while the Worker is idle (autonomy\*.py is under the self-edit guard), then restart the Worker.
Undo: `--revert`, or `JARVIS_FOLLOWUP_PARK=off`.
Tested here on the 5060's pre-fix copy and on a CRLF copy: check, apply, 6/6 tests, then a second run says
"already installed". Line endings are kept.

## Rig fork (added 10/03 20:00Z)
The rig's `autotask.py` differs from the 5060's: it uses clock.py and dedupe.py, its cap resets at 8 PM ET, and it
labels past-cap cards `sched:deferred` for scheduler.py. So `--check` on the rig failed on the 5060 blocks.
`rig-orig/` is the rig's live copy (pushed read-only from the rig), and `rig-patch/` is that copy with the same rule.
The installer tries `orig/` first, then `rig-orig/`, and prints which one matched. The commands are unchanged.

On the rig, a parked follow-up:
- is filed as before, then `ghq.snooze_until(n, 'time', <next 06:00>, 'rig', ...)` sets status:snoozed and posts the
  one comment. It gets `preapproved` and no `sched:deferred`, and no "Filed for Jake" comment.
- falls back to the old behaviour (staged + `sched:deferred`) if the snooze fails, so nothing is ever lost.
- wakes at 06:00 ET, not at the rig's 8 PM reset, the same as the 5060 (Claude's day shift starts at 06:00).

Rig test: `python test_followup_park_rig.py` (10 checks, with ghq, classify and dedupe faked). Run here on the rig's
copy with LF and CRLF line endings: check → apply → 10/10 → "already installed" → --revert is byte-identical.
