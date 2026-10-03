"""Rig fix (audit thread, 10/03): one To-Do step per ask, not two.

A needs-jake run attached a Jake step in _log_run, then send_back -> ask_jake attached a second one about 10 s later,
so the card showed two items on Jake's To-Do. The 5060 was fixed the same way on 10/03: ask_jake now attaches a step
only when the card has no current one (ghq.jake_step_of; an approval clears the old step, so a new ask after a
re-approval still gets its step). An explicit `step=` is always written.

    python apply_one_step.py --ghq C:\\Jarvis\\ghq [--check | --revert]

Backup: ghq.py.bak-<stamp>-os. Keeps the file's line endings. Restart the Worker afterwards.
"""
import argparse
import glob
import os
import shutil
import sys
import time

TAG = 'one-step 10/03'
ANCHOR = '        else:\n            _attach_jake_step(number, question)\n'
REPL = ('        elif not _current_step(number):   # %s: _log_run may have attached it already\n'
        '            _attach_jake_step(number, question)\n'
        '\n'
        '\n'
        'def _current_step(number):\n'
        '    try:\n'
        '        return jake_step_of(number)\n'
        '    except Exception:  # noqa: BLE001 - unknown: attach, as before\n'
        '        return None\n' % TAG)


def plan(path):
    raw = open(path, 'rb').read()
    s = raw.decode('utf-8').replace('\r\n', '\n')
    if TAG in s:
        return raw, None, 'already installed'
    if 'def jake_step_of(' not in s:
        return raw, None, 'jake_step_of() not found'
    n = s.count(ANCHOR)
    if n != 1:
        return raw, None, f'anchor found {n} times, expected 1 (ask_jake\'s _attach_jake_step)'
    out = s.replace(ANCHOR, REPL, 1)
    if b'\r\n' in raw:
        out = out.replace('\n', '\r\n')
    return raw, out.encode('utf-8'), None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--ghq', required=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    path = os.path.join(a.ghq, 'ghq.py')
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-os'))
        if baks:
            shutil.copy2(baks[-1], path)
            print(f'ghq.py: restored {os.path.basename(baks[-1])}')
        return 0
    raw, new, why = plan(path)
    print('ghq.py: ' + (why or 'ready'))
    if why:
        return 0 if why == 'already installed' else 1
    if a.check:
        return 0
    stamp = time.strftime('%Y%m%d-%H%M')
    shutil.copy2(path, f'{path}.bak-{stamp}-os')
    with open(path, 'wb') as f:
        f.write(new)
    print(f'Installed (backup ghq.py.bak-{stamp}-os). Restart the Jarvis Worker on this PC.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
