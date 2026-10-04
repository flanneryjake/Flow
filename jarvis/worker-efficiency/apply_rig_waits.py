"""Rig fixes from the audit thread's repeat-run trace (10/03), both in budget_guard._gh_finish:

1. Worker self-patch steps go to Claude review. #600 / #609: the run's last step was "apply this patch to agent.py /
   ghq.py / laptop_worker.py and restart the Worker". Workers may never edit the running Worker code, so Worker rule 4
   released the card, it was re-claimed about 2 min later, hit the same wall and went to Jake. Now such a step parks
   the card for Claude's loop triage (ghq.park_for_triage) on the first pass.
2. Checkpoints that only wait on jobs snooze 15 min. #795: a checkpoint pass that only polled queued rig jobs
   ("8 still queued", "waiting on the rig") released the card, and it was re-claimed every 2-5 min, a Claude run
   each time. Now such a checkpoint snoozes the card until now + 15 min (ghq.snooze_until time), then it runs again.

    python apply_rig_waits.py --agent <rig folder with budget_guard.py> [--check | --revert]

Backup: budget_guard.py.bak-<stamp>-rw. Keeps the file's line endings. Restart the Worker afterwards.
Off switches: JARVIS_SELFPATCH_TRIAGE=off, JARVIS_CKPT_SNOOZE_MIN=0.
"""
import argparse
import glob
import os
import shutil
import sys
import time

TAG = 'rig-waits 10/03'

HELPERS = '''# ---- %s: Worker self-patch steps -> Claude review; job-polling checkpoints snooze ----
WORKER_CODE = re.compile(r"\\b(agent|ghq|laptop_worker|budget_guard|local_lane|fallback_lane|sandbox_gate|loopnet|"
                         r"needsjake|worker_script_guard|budget|autotask|classify)\\.py\\b|running worker|"
                         r"worker (code|file)|restart the (jarvis )?worker", re.I)
CKPT_WAITING = re.compile(r"\\b(queued|still (running|waiting|queued)|waiting (on|for)|not (yet )?(done|finished|"
                          r"written|ready)|in progress on the rig|rig jobs?)\\b", re.I)


def _selfpatch_step(need):
    return os.environ.get("JARVIS_SELFPATCH_TRIAGE", "on") != "off" and bool(WORKER_CODE.search(need or ""))


def _ckpt_snooze(n, ckpt_line):
    mins = int(os.environ.get("JARVIS_CKPT_SNOOZE_MIN", "15") or 0)
    if mins <= 0 or not CKPT_WAITING.search(ckpt_line or ""):
        return
    until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).strftime("%%Y-%%m-%%dT%%H:%%M:%%SZ")
    try:
        AG["ghq"].snooze_until(n, "time", until, AG.get("ME"), f"checkpoint is only waiting on jobs; checks again at "
                                                            f"{until} instead of a Claude run every few minutes")
        log(f"CKPT-SNOOZE #{n} until {until}: {ckpt_line[:100]}")
    except Exception as e:
        log(f"checkpoint snooze #{n}: {e}")


def _gh_finish(''' % TAG

EDITS = [
    ('from datetime import datetime, timezone\n', 'from datetime import datetime, timezone, timedelta\n'),
    ('\ndef _gh_finish(', '\n' + HELPERS),
    ('    d = _load()\n    if needs and local_step(needs[0]) and str(n) not in d.get("rule4", {}):\n',
     '    if needs and _selfpatch_step(needs[0]) and hasattr(ghq, "park_for_triage"):\n'
     '        AG["gh_log"](c, "released", tail=output, summary=f"Applying a patch to the running Worker code is not a '
     'Worker step, so this goes to Claude review instead of another pass.\\n\\n{summary}")\n'
     '        ghq.park_for_triage(n, AG.get("ME"), f"Worker self-patch step: {needs[0][:400]}")\n'
     '        log(f"SELFPATCH #{n}: parked for Claude review: {needs[0][:100]}")\n'
     '        return _followups(c, follow)\n'
     '    d = _load()\n    if needs and local_step(needs[0]) and str(n) not in d.get("rule4", {}):\n'),
    ('    return ORIG["gh_finish"](c, output, summary, needs, ckpt, follow, passes)\n',
     '    out = ORIG["gh_finish"](c, output, summary, needs, ckpt, follow, passes)\n'
     '    if ckpt and not needs:\n'
     '        _ckpt_snooze(n, ckpt[0])\n'
     '    return out\n'),
]


def plan(path):
    raw = open(path, 'rb').read()
    s = raw.decode('utf-8').replace('\r\n', '\n')
    if TAG in s:
        return raw, None, 'already installed'
    out = s
    for anchor, repl in EDITS:
        n = out.count(anchor)
        if n != 1:
            return raw, None, f'anchor found {n} times, expected 1:\n    {anchor.strip()[:100]}'
        out = out.replace(anchor, repl, 1)
    if b'\r\n' in raw:
        out = out.replace('\n', '\r\n')
    return raw, out.encode('utf-8'), None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent', required=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    path = os.path.join(a.agent, 'budget_guard.py')
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-rw'))
        if baks:
            shutil.copy2(baks[-1], path)
            print(f'budget_guard.py: restored {os.path.basename(baks[-1])}')
        return 0
    raw, new, why = plan(path)
    print('budget_guard.py: ' + (why or 'ready'))
    if why:
        return 0 if why == 'already installed' else 1
    if a.check:
        return 0
    stamp = time.strftime('%Y%m%d-%H%M')
    shutil.copy2(path, f'{path}.bak-{stamp}-rw')
    with open(path, 'wb') as f:
        f.write(new)
    print(f'Installed (backup budget_guard.py.bak-{stamp}-rw). Restart the Jarvis Worker on the rig.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
