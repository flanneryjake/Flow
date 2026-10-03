r"""followup-park (10/03): Worker follow-ups stop landing on Jake's Approvals. For a PC's C:\Jarvis\autonomy\autotask.py.

What it changes in autotask.propose():
  * A follow-up (spawned_from set) that would be filed for Jake ONLY because the daily auto limit is reached, is tier
    free / free_logged, and whose parent is not a PIN card, is parked quietly instead: status:snoozed + `preapproved`,
    with a time snooze to 06:00 (this PC's clock). The Worker's wake sweep approves it then, under the parent card's
    approval. It never goes on Approvals or the To-Do. Each one is a line in C:\Jarvis\loopnet\followups-parked.jsonl.
  * A follow-up whose work changes the live Worker / hub code (classify.py tier ask_once "worker-self-edit") is parked
    for Claude's review (ghq.park_for_triage), not filed for Jake. Only if this PC's classify.py has that rule.
  * PIN tiers, children of PIN parents, and cards that aren't follow-ups behave exactly as before.
On 10/03 about 288 follow-ups landed on Jake after the shared 100/day cap was hit at 00:02 ET (about 107 from the rig).

    python apply_followup_park.py                       dry run on C:\Jarvis\autonomy\autotask.py
    python apply_followup_park.py --check               exit 0 only if every block would apply cleanly
    python apply_followup_park.py --apply               backup autotask.py.bak-<stamp>-fp, compile check, roll back on failure
    python apply_followup_park.py --revert              put the newest -fp backup back
    python apply_followup_park.py --dir D:\x\autonomy   another folder

Patches by context: each changed block from orig\ -> patch\ (the 5060's before/after) must be found exactly once in
the target, or nothing is written. Keeps the file's line endings. Off switch after applying: JARVIS_FOLLOWUP_PARK=off.
The Worker's self-edit guard covers C:\Jarvis\autonomy\*.py: write while the Worker is idle (or with Jake's OK touch
C:\Jarvis\audit\fixes-running.flag first), then restart the Worker. Test: python test_followup_park.py <autonomy dir>
"""
import argparse
import difflib
import glob
import os
import py_compile
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = 'autotask.py'
TAG = 'followup-park 10/03'


def _read(path):
    raw = open(path, 'rb').read()
    return raw, raw.decode('utf-8').replace('\r\n', '\n')


def hunks(old, new, ctx):
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    out = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(ctx):
        (_, i1, _, j1, _), (_, _, i2, _, j2) = group[0], group[-1]
        out.append(("".join(a[i1:i2]), "".join(b[j1:j2])))
    return out


def patch_text(target, old_file, new_file):
    """Most context first; less only if this PC's copy differs around a change. Every block must match once."""
    last_bad = None
    for ctx in (3, 2, 1):
        out, bad = target, None
        for old, new in hunks(old_file, new_file, ctx):
            if not old.strip() or out.count(old) != 1:
                bad = old
                break
            out = out.replace(old, new)
        if bad is None:
            return out, None
        last_bad = bad
    return None, last_bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=r'C:\Jarvis\autonomy')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    path = os.path.join(a.dir, NAME)
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-fp'))
        if baks:
            shutil.copy2(baks[-1], path)
            print(f'{NAME}: restored {os.path.basename(baks[-1])}')
        else:
            print(f'{NAME}: no -fp backup, left alone')
        return 0
    raw, cur = _read(path)
    if TAG in cur:
        print(f'{NAME}: already installed')
        return 0
    _, old = _read(os.path.join(HERE, 'orig', NAME))
    _, new = _read(os.path.join(HERE, 'patch', NAME))
    out, bad = patch_text(cur, old, new)
    if out is None:
        print(f'{NAME}: a block did not match exactly once on this PC; nothing written.\n---\n{bad}')
        return 1
    if b'\r\n' in raw:
        out = out.replace('\n', '\r\n')
    tmp = path + '.tmp-fp'
    with open(tmp, 'wb') as f:
        f.write(out.encode('utf-8'))
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        os.remove(tmp)
        print(f'{NAME}: patched copy does not compile; nothing written: {e}')
        return 1
    if not a.apply:
        os.remove(tmp)
        print(f'{NAME}: ready (every block found once, compiles).' + ('' if a.check else ' Dry run; add --apply to write.'))
        return 0
    bak = f'{path}.bak-{time.strftime("%Y%m%d-%H%M")}-fp'
    shutil.copy2(path, bak)
    os.replace(tmp, path)
    print(f'{NAME}: applied. Backup {bak}. Restart the Worker when it is idle.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
