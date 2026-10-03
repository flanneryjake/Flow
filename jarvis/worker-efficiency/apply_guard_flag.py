"""Rig fix (10/03 evening, #1451 install): the self-edit guard must not revert a fix Claude applied mid-card.

Before: the rig agent.py snapshots the guarded Worker code when a card starts and, when the card ends, puts back
any file that changed. A fix applied live while that card was running (Claude's RC install of #1451 at 18:53,
during card #1060) was reverted at 18:56 as if #1060 had made it, and had to be applied a second time.

After: same rule as the 5060 guard. Whoever applies a live Worker fix touches C:\\Jarvis\\audit\\fixes-running.flag
first; if that flag was touched during the card (mtime >= card start - 60 s), code_restore logs it and reverts
nothing. Cards themselves never touch the flag (worker_script_guard limits them to their own folder). This
installer touches the flag before it writes, so its own edit is safe too.

    python apply_guard_flag.py --agent <rig folder with agent.py> [--check | --revert]

Backup: agent.py.bak-<stamp>-gf. Keeps the file's line endings. Restart the Worker afterwards, while it is idle.
"""
import argparse
import glob
import os
import shutil
import sys
import time

TAG = 'guard-flag 10/03'
FLAG = r'C:\Jarvis\audit\fixes-running.flag'

ANCHOR = ('    import difflib\n'
          '    changed = []\n'
          '    for p in sorted(set(snap) | set(_guarded_files())):\n')
REPL = ('    import difflib\n'
        '    # %s: a live fix applied during this card (it touches the flag first) is not the card\'s edit\n'
        '    flag = os.environ.get("JARVIS_FIX_FLAG", r"%s")\n'
        '    t0 = STATE.get("run_t0") or 0\n'
        '    if t0 and os.path.exists(flag) and os.path.getmtime(flag) >= t0 - 60:\n'
        '        log(f"self-edit guard: {flag} was touched during this card - live fix in progress, not reverting")\n'
        '        return ""\n'
        '    changed = []\n'
        '    for p in sorted(set(snap) | set(_guarded_files())):\n' % (TAG, FLAG))


def plan(path):
    raw = open(path, 'rb').read()
    s = raw.decode('utf-8').replace('\r\n', '\n')
    if TAG in s:
        return raw, None, 'already installed'
    n = s.count(ANCHOR)
    if n != 1:
        return raw, None, f'anchor found {n} times, expected 1 (the start of code_restore)'
    out = s.replace(ANCHOR, REPL, 1)
    if b'\r\n' in raw:
        out = out.replace('\n', '\r\n')
    return raw, out.encode('utf-8'), None


def touch_flag(flag):
    try:
        os.makedirs(os.path.dirname(flag), exist_ok=True)
        with open(flag, 'a'):
            pass
        os.utime(flag, None)
    except OSError as e:
        print(f'note: could not touch {flag}: {e}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent', required=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--flag', default=os.environ.get('JARVIS_FIX_FLAG', FLAG))
    a = ap.parse_args(argv)
    path = os.path.join(a.agent, 'agent.py')
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-gf'))
        if baks:
            touch_flag(a.flag)
            shutil.copy2(baks[-1], path)
            print(f'agent.py: restored {os.path.basename(baks[-1])}')
        return 0
    raw, new, why = plan(path)
    print('agent.py: ' + (why or 'ready'))
    if why:
        return 0 if why == 'already installed' else 1
    if a.check:
        return 0
    touch_flag(a.flag)
    stamp = time.strftime('%Y%m%d-%H%M')
    shutil.copy2(path, f'{path}.bak-{stamp}-gf')
    with open(path, 'wb') as f:
        f.write(new)
    print(f'Installed (backup agent.py.bak-{stamp}-gf). Restart the Jarvis Worker on the rig while it is idle.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
