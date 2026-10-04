"""E10 (Jake's 10/03 email): the Worker works approved cards by priority, focus project and phase.
See queue_order.py for the order.

    python apply_queue_order.py --agent <folder with agent.py> [--check | --revert]

Copies queue_order.py next to agent.py and changes agent.py's queue sort to use it (both forks; on the 5060 the card
dict also gets its labels). Every anchor must match exactly once, or nothing is written. Backup:
agent.py.bak-<stamp>-qo. Keeps the file's line endings. Restart the Worker afterwards.
"""
import argparse
import glob
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = 'queue-order 10/03'

HELPER = '''def _queue_key(c):
    """E10 (%s): now > parked > resume > p0 > p1 > p2 > none > p3, focus projects and early
    phases first (queue_order.py). Any problem falls back to the old p0 > p1 > p2 order."""
    try:
        import queue_order
        return (0,) + queue_order.key(c, STATE.get("parked"))
    except Exception:
        return (1, c["id"] != STATE.get("parked"), c["priority"] or "P9")


def approved_for(machine):''' % TAG

COMMON = [('\ndef approved_for(machine):', '\n' + HELPER)]
RIG = [('        cards.sort(key=lambda c: (c["id"] != STATE.get("parked"), c["priority"] or "P9"))\n',
        '        cards.sort(key=_queue_key)   # E10: see queue_order.py\n')]
H5060 = [('        cards.sort(key=lambda c: (not c.get("now"), c["id"] != STATE.get("parked"), not c.get("resume"),\n'
          '                                  c["priority"] or "P9"))\n',
          '        cards.sort(key=_queue_key)   # E10: see queue_order.py\n'),
         ('            "resume": "resume" in names}\n',
          '            "resume": "resume" in names, "labels": names}\n')]


def edits_for(s):
    return COMMON + (H5060 if 'not c.get("resume"),\n' in s else RIG)


def plan(path):
    raw = open(path, 'rb').read()
    s = raw.decode('utf-8').replace('\r\n', '\n')
    if TAG in s:
        return raw, None, 'already installed'
    out = s
    for anchor, repl in edits_for(s):
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
    mod = os.path.join(a.agent, 'queue_order.py')
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-qo'))
        if baks:
            shutil.copy2(baks[-1], path)
            print(f'agent.py: restored {os.path.basename(baks[-1])}')
        if os.path.exists(mod):
            os.remove(mod)
            print('queue_order.py: removed')
        return 0
    raw, new, why = plan(path)
    print('agent.py: ' + (why or 'ready'))
    if why == 'already installed':
        if not a.check:
            shutil.copy2(os.path.join(HERE, 'queue_order.py'), mod)
            print('queue_order.py refreshed.')
        return 0
    if why:
        print('Nothing written.')
        return 1
    if a.check:
        return 0
    stamp = time.strftime('%Y%m%d-%H%M')
    shutil.copy2(path, f'{path}.bak-{stamp}-qo')
    shutil.copy2(os.path.join(HERE, 'queue_order.py'), mod)
    with open(path, 'wb') as f:
        f.write(new)
    print(f'Installed (backup agent.py.bak-{stamp}-qo). Restart the Jarvis Worker on this PC.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
