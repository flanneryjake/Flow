"""Rig fix (audit thread report, #982 10/03): a wait the Worker can't watch goes to Claude's loop triage, not Jake.

Before: when a run ended with SNOOZE_UNTIL on something already true (#982 waited on card 987, which had just
closed) or something that can't be watched, budget_guard._gh_finish asked Jake: "Repeat-pass guard: the run asked
to wait on card `987`, which couldn't be watched ... Comment and approve to run it again." That puts a decision only
Claude can usefully make (what is actually left on the card) on Jake's To-Do.

After: the card is parked for the loop-triage lane with ghq.park_for_triage (status:snoozed + triage +
needs-claude-review, one comment saying why). Jake is not asked. If park_for_triage is missing, it asks Jake as
before.

    python apply_wait_met.py --agent <rig folder with budget_guard.py> [--check | --revert]

Backup: budget_guard.py.bak-<stamp>-wm. Keeps the file's line endings. Restart the Worker afterwards.
"""
import argparse
import glob
import os
import shutil
import sys
import time

TAG = 'wait-met 10/03'

ANCHOR = ('        except Exception as e:   # can\'t watch it: park by hand so it doesn\'t loop, and list it for Jake\n'
          '            ghq.ask_jake(n, f"Repeat-pass guard: the run asked to wait on {snooze[\'kind\']} `{snooze[\'value\']}`, which "\n'
          '                            f"couldn\'t be watched ({e}). Comment and approve to run it again.")\n')
REPL = ('        except Exception as e:   # %s: can\'t watch it -> Claude\'s loop triage, not Jake\n'
        '            why = (f"the run asked to wait on {snooze[\'kind\']} `{snooze[\'value\']}`, which can\'t be watched ({e}). "\n'
        '                   f"If it is already met, decide what is really left on this card.")\n'
        '            if hasattr(ghq, "park_for_triage"):\n'
        '                ghq.park_for_triage(n, AG.get("ME"), why)\n'
        '                log(f"WAIT-MET #{n}: {why[:160]} - parked for loop triage")\n'
        '            else:\n'
        '                ghq.ask_jake(n, f"Repeat-pass guard: {why} Comment and approve to run it again.")\n' % TAG)


def plan(path):
    raw = open(path, 'rb').read()
    s = raw.decode('utf-8').replace('\r\n', '\n')
    if TAG in s:
        return raw, None, 'already installed'
    n = s.count(ANCHOR)
    if n != 1:
        return raw, None, f'anchor found {n} times, expected 1 (the "can\'t watch it" ask_jake in _gh_finish)'
    out = s.replace(ANCHOR, REPL, 1)
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
        baks = sorted(glob.glob(path + '.bak-*-wm'))
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
    shutil.copy2(path, f'{path}.bak-{stamp}-wm')
    with open(path, 'wb') as f:
        f.write(new)
    print(f'Installed (backup budget_guard.py.bak-{stamp}-wm). Restart the Jarvis Worker on the rig.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
