r"""Jake gate for a Worker PC's ghq.py (10/03): every path onto Jake's lists asks jake_gate.decide() first.
Built from the live, tested 5060 ghq.py. Installs jake_gate.py next to ghq.py and wires the chokepoints:
  ask_jake (gate first) - set_status(..., 'needs-jake') - jake_step - _attach_jake_step - ready() skips for:claude.
A 'claude' verdict parks the card: status:snoozed + for:claude + needs-claude-review, one comment, its To-Do step cleared.

  python apply_jake_gate.py                  dry run on C:\Jarvis\ghq (says what it would change)
  python apply_jake_gate.py --check          exit 0 only if every anchor is found once and it compiles
  python apply_jake_gate.py --apply          backup ghq.py.bak-<stamp>-gate, copy jake_gate.py, compile check
  python apply_jake_gate.py --revert         put the newest -gate backup back (jake_gate.py stays; it is inert)
  python apply_jake_gate.py --dir D:\x\ghq   another folder
Off switch after applying: JARVIS_JAKE_GATE=off. Write while the Worker is idle (ghq.py is under the self-edit guard),
then restart the Worker. Test: python -m pytest -q test_jake_gate.py (next to ghq.py).
"""
import argparse
import datetime as dt
import glob
import os
import py_compile
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HELPERS = '# ---------------------------------------------------------------------------- the Jake gate (10/03)\n# Jake: "more tasks are looping ... we need a better filter". Every path onto Jake\'s lists (needs-jake, a To-Do step,\n# a needs-jake card) asks jake_gate.decide() first. \'claude\' -> the card is parked for Claude (status:snoozed + for:claude\n# + needs-claude-review, one comment, its To-Do step cleared) and never reaches Jake. See jake_gate.py for the rules.\nFOR_CLAUDE = \'for:claude\'\n_GATE_OK = {}   # number -> time the gate let it through (so ask_jake -> set_status -> jake_step asks once, not 3x)\n\n\ndef _gate_mod():\n    try:\n        here = os.path.dirname(os.path.abspath(__file__))\n        if here not in sys.path:\n            sys.path.insert(0, here)\n        import jake_gate\n        return jake_gate\n    except Exception:  # noqa: BLE001\n        return None\n\n\ndef park_for_claude(number, why, issue=None):\n    """Off Jake\'s lists, into the Claude queue: snoozed + for:claude + needs-claude-review, claims and todo-tab removed."""\n    issue = issue or api(\'GET\', repo_path(f\'/issues/{number}\'))\n    drop = (\'todo-tab\', \'looped\', \'triage\', \'preapproved\')\n    keep = [n for n in label_names(issue) if not n.startswith((\'claimed:\', \'status:\')) and n not in drop]\n    names = keep + [\'status:snoozed\'] + [x for x in (FOR_CLAUDE, \'needs-claude-review\') if x not in keep]\n    api(\'PUT\', repo_path(f\'/issues/{number}/labels\'), {\'labels\': names})\n    comment(number, f\'{globals().get(\'TRIAGE_MARK\', \'<!-- jarvis:triage -->\')}\\nParked for Claude, not Jake (Jake gate): {why}. It waits in the Claude queue \'\n                    f\'(/api/claude-queue); no Worker takes it.\\n{RESET_MARK}\', queue=False, dedupe=True)\n\n\ndef gate_to_jake(number, reason=\'\', step=None, issue=None, pending=True, source=\'\'):\n    """True = this may go on Jake\'s list. False = it was parked for Claude here. Fails closed toward Claude."""\n    if os.environ.get(\'JARVIS_JAKE_GATE\', \'on\') == \'off\' or not number:\n        return True\n    if time.time() - _GATE_OK.get(number, 0) < 120:\n        return True\n    g = _gate_mod()\n    if g is None:\n        return True   # gate file missing: behave as before (the kit installs it next to ghq.py)\n    try:\n        issue = issue or api(\'GET\', repo_path(f\'/issues/{number}\'))\n        cs = paged(repo_path(f\'/issues/{number}/comments\'))\n        verdict, why = g.decide(number, issue, label_names(issue), cs, reason, step, pending=pending, source=source)\n    except Exception as e:  # noqa: BLE001\n        verdict, why = \'claude\', f\'gate error ({type(e).__name__})\'\n    if verdict == \'jake\':\n        _GATE_OK[number] = time.time()\n        return True\n    try:\n        park_for_claude(number, why, issue)\n    except Exception as e:  # noqa: BLE001\n        print(f\'jake gate: could not park #{number}: {e}\', file=sys.stderr)\n    return False\n\n\n'


def _func(t, name):
    m = re.search(rf'^def {re.escape(name)}\(', t, re.M)
    if not m:
        raise SystemExit(f'no def {name}()')
    n = re.search(r'^(def |class |[A-Z_]+ = |# -{10,})', t[m.end():], re.M)
    return m.start(), (m.end() + n.start()) if n else len(t)


def _in_func(t, name, old, new, why):
    a, b = _func(t, name)
    body = t[a:b]
    if body.count(old) < 1:
        raise SystemExit(f'{why}: anchor not found in {name}():\n{old[:160]}')
    return t[:a] + body.replace(old, new, 1) + t[b:]


def patch(t):
    if 'def gate_to_jake(' in t:
        raise SystemExit('already has the Jake gate')
    anchor = '\ndef ask_jake(number, question, step=None):\n'
    if t.count(anchor) != 1:
        raise SystemExit('ask_jake not found exactly once')
    t = t.replace(anchor, '\n' + HELPERS + 'def ask_jake(number, question, step=None):\n'
                  "    if not gate_to_jake(number, question, step, source='ask_jake'):\n"
                  "        return 'claude'   # Jake gate: not a hands-only ask -> Claude queue\n", 1)
    t = t.replace('def set_status(number, status, extra_add=(), extra_remove=()):',
                  "def set_status(number, status, extra_add=(), extra_remove=(), why=''):", 1)
    t = _in_func(t, 'set_status', "    issue = api('GET', repo_path(f'/issues/{number}'))\n",
                 "    issue = api('GET', repo_path(f'/issues/{number}'))\n"
                 "    if status == 'needs-jake' and not gate_to_jake(number, why, None, issue, source='set_status'):\n"
                 "        return issue   # Jake gate: parked for Claude instead\n", 'set_status')
    t = _in_func(t, 'jake_step', "    issue = api('GET', repo_path(f'/issues/{number}'))\n",
                 "    issue = api('GET', repo_path(f'/issues/{number}'))\n"
                 "    if not gate_to_jake(number, '', step, issue, source='jake_step'):\n"
                 "        return None  # Jake gate: parked for Claude, no To-Do item\n", 'jake_step')
    t = _in_func(t, '_attach_jake_step', 'def _attach_jake_step(number, reason):\n',
                 'def _attach_jake_step(number, reason):\n'
                 "    if not gate_to_jake(number, reason, source='attach_jake_step'):\n"
                 '        return  # Jake gate: parked for Claude\n', '_attach_jake_step')
    t = _in_func(t, 'ready', "        if i.get('pull_request') or any(n.startswith('claimed:') for n in names):\n            continue\n",
                 "        if i.get('pull_request') or any(n.startswith('claimed:') for n in names):\n            continue\n"
                 "        if 'for:claude' in names:\n            continue  # Jake gate: the Claude queue, not a Worker's\n", 'ready')
    return t


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=r'C:\Jarvis\ghq')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    path = os.path.join(a.dir, 'ghq.py')
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-gate'))
        if baks:
            shutil.copy2(baks[-1], path)
            print(f'ghq.py: restored {os.path.basename(baks[-1])}')
        return 0
    raw = open(path, 'rb').read()
    t = raw.decode('utf-8').replace('\r\n', '\n')
    try:
        new = patch(t)
    except SystemExit as e:
        print(f'ghq.py: {e}')
        return 0 if 'already has' in str(e) else 1
    if b'\r\n' in raw:
        new = new.replace('\n', '\r\n')
    tmp = path + '.tmp-gate'
    with open(tmp, 'wb') as f:
        f.write(new.encode('utf-8'))
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        os.remove(tmp)
        print(f'ghq.py: patched copy does not compile; nothing written: {e}')
        return 1
    if not a.apply:
        os.remove(tmp)
        print('ghq.py: ready (every anchor found, compiles).' + ('' if a.check else ' Dry run; add --apply to write.'))
        return 0
    bak = f'{path}.bak-{dt.datetime.now():%Y%m%d-%H%M}-gate'
    shutil.copy2(path, bak)
    shutil.copy2(os.path.join(HERE, 'jake_gate.py'), os.path.join(a.dir, 'jake_gate.py'))
    os.replace(tmp, path)
    print(f'ghq.py: applied (backup {bak}); jake_gate.py copied next to it. Restart the Worker when it is idle.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
