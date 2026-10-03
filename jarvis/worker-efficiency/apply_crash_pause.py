"""#1547 (Jake's 10/03 email): a crashed Claude run must not pause the whole Worker for 5 hours.

Before: finish() treated every failed run the same way. A non-zero exit, empty output, a "what would you like me to do"
reply, or (on the 5060) any card summary mentioning "rate limit" in its first 600 characters called _block(), which
paused EVERY card, P0 and `now` included, until the usage window "reset", a flat 5 h when the text had no reset time.
That is how the 10/03 7:09 AM pause happened.

After:
  * Only a real limit or login message from the Claude CLI pauses the Worker (is_real_limit()).
  * Anything else is a crash: that card is logged `failed` (ghq's existing rule sends it to Jake after two failures in a
    row), the Worker skips it for 30 min, and the queue keeps going. Three crashes in a row rest the Worker 30 min, so a
    broken CLI can't spin.
  * A real limit with no readable reset time re-checks after 1 h instead of 5. "usage limit reached|<epoch>" and
    "resets Oct 6" are read.

    python apply_crash_pause.py --agent <folder with agent.py> [--check | --revert]

Every anchor must match exactly once, or nothing is written. Backup: agent.py.bak-<stamp>-cp. Keeps the file's line
endings. Restart the Worker afterwards.
"""
import argparse
import glob
import os
import shutil
import sys
import time

TAG = 'crash-pause 10/03'

HELPERS = '''# ---- %s (#1547): a crashed run is not a usage limit ----
REAL_LIMIT_MARKERS = ("claude ai usage limit reached", "you've hit your", "you have hit your", "hit your session limit",
                      "hit your limit", "please run /login", "not logged in", "invalid api key",
                      "credit balance is too low", "usage limit reached")
CRASH_SKIP_MIN = 30
CRASH_STREAK_REST = 3


def is_real_limit(why):
    """True only for the Claude CLI's own limit/login message: short, and shaped like the CLI's line, not a sentence
    in a card's summary that happens to mention limits."""
    first = ((why or "").strip().splitlines() or [""])[0].strip().lower()
    if not first or len(first) > 300:
        return False
    if first.startswith(REAL_LIMIT_MARKERS) or "please run /login" in first:
        return True
    import re
    return bool(re.search(r"limit reached\\|\\d{9,}", first) or
                (re.search(r"\\blimit\\b", first) and re.search(r"\\bresets?\\b", first)))


def _crashed(c, why, out_file):
    """A run that failed for a reason other than the usage limit: this card waits, the queue keeps going."""
    streak = STATE["crash_streak"] = STATE.get("crash_streak", 0) + 1
    log(f"CRASHED ({streak} in a row, not a usage limit): {why[:200]}")
    if c.get("number"):
        skip = globals().get("LOST_CLAIM")
        if isinstance(skip, dict):
            skip[c["number"]] = time.time() + CRASH_SKIP_MIN * 60
        gh_log(c, "failed", summary=f"The Claude run crashed ({why[:200]}). This is not a usage limit, so the other "
                                    f"cards keep running; this one waits {CRASH_SKIP_MIN} min.")
    else:
        update(c["id"], status="Approved", claimed_by="",
               agent_log=f"{datetime.now():%%m/%%d %%H:%%M} {ME}: run crashed ({why[:200]}), retrying later.\\nLog: {out_file}")
    if streak >= CRASH_STREAK_REST and BLOCKED["until"] < time.time():
        BLOCKED.update(until=time.time() + CRASH_SKIP_MIN * 60, why=f"{streak} crashed runs in a row: {why[:120]}")
        log(f"BLOCKED {CRASH_SKIP_MIN} min: {streak} crashed runs in a row")
        notify(f"Jarvis Worker on {ME} resting {CRASH_SKIP_MIN} min", f"{streak} Claude runs crashed in a row: {why[:100]}")


def _limit_fallback_hours(why, now=None):
    """No "resets 3pm" in the message: read "|<epoch>" or "resets Oct 6", else re-check in 1 h (was a flat 5 h)."""
    import re
    now = now or datetime.now()
    m = re.search(r"\\|(\\d{9,})", why or "")
    if m:
        secs = int(m.group(1)) - now.timestamp()
        if 0 < secs < 8 * 86400:
            return secs / 3600 + 0.1
    m = re.search(r"resets\\s+([A-Z][a-z]{2})[a-z]*\\s+(\\d{1,2})", why or "")
    if m:
        try:
            t = datetime.strptime(f"{m.group(1)} {m.group(2)} {now.year}", "%%b %%d %%Y")
            secs = (t - now).total_seconds()
            if 0 < secs < 8 * 86400:
                return secs / 3600 + 0.1
        except ValueError:
            pass
    return 1


def finish(''' % TAG

EDITS = [
    ('\ndef finish(', '\n' + HELPERS),
    ('    fail = claude_failed(output)\n    if fail:\n        _block(fail)\n',
     '    fail = claude_failed(output)\n'
     '    if not fail:\n'
     '        STATE["crash_streak"] = 0\n'
     '    elif not is_real_limit(fail):   # #1547: crash, not a usage limit -> only this card waits\n'
     '        return _crashed(c, fail, out_file)\n'
     '    if fail:\n        _block(fail)\n'),
    ('    return 1 if "login" in why.lower() else 5\n',
     '    return 1 if "login" in why.lower() else _limit_fallback_hours(why, now)\n'),
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
    path = os.path.join(a.agent, 'agent.py')
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-cp'))
        if baks:
            shutil.copy2(baks[-1], path)
            print(f'agent.py: restored {os.path.basename(baks[-1])}')
        return 0
    raw, new, why = plan(path)
    print('agent.py: ' + (why or 'ready'))
    if why:
        return 0 if why == 'already installed' else 1
    if a.check:
        return 0
    stamp = time.strftime('%Y%m%d-%H%M')
    shutil.copy2(path, f'{path}.bak-{stamp}-cp')
    with open(path, 'wb') as f:
        f.write(new)
    print(f'Installed (backup agent.py.bak-{stamp}-cp). Restart the Jarvis Worker on this PC.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
