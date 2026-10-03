"""Install the E2/E4 repeat guard and the E7 model-name fix into a Worker's live files (5060 or rig fork).

    python apply_worker_efficiency.py --ghq C:\\Jarvis\\ghq --agent <folder with agent.py> [--check | --revert]

  --check   report which anchors are found; writes nothing
  --revert  restore the newest *.bak-*-we backups this script made

Every anchor must match exactly once in its file, or nothing is written (no partial installs). Backups are
<file>.bak-<stamp>-we next to each file. Already-installed files are skipped. Restart the Worker afterwards (and the
hub on the 5060, which imports ghq too).
"""
import argparse
import glob
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = 'worker-efficiency 10/03'

GHQ_HELPER = '''

def _repeat_guard_ok(number, issue):
    """E2/E4 (Jake's 10/03 email, %s): refuse a claim when the card already asked Jake and nothing changed since.
    repeatguard.py sits next to this file; any problem lets the claim go ahead."""
    try:
        import repeatguard
    except ImportError:
        return True
    return repeatguard.check_before_claim(sys.modules[__name__], number, issue)


def claim(number, machine):''' % TAG

GHQ_EDITS = [
    # (anchor, replacement)
    ('\n\ndef claim(number, machine):', GHQ_HELPER),
    ("        return False  # the cached queue was stale: someone else has it, or it was snoozed or closed\n",
     "        return False  # the cached queue was stale: someone else has it, or it was snoozed or closed\n"
     "    if not _repeat_guard_ok(number, current):\n"
     "        return False  # asked Jake already and nothing changed: handed back to him (repeatguard.py)\n"),
]

AGENT_HELPER = '''def run_model_label(model=None):
    """E7 (%s): the model name every run comment records. None/"" means the Claude CLI default."""
    if model:
        return model
    label = os.environ.get("JARVIS_DEFAULT_MODEL_LABEL")
    if not label:
        try:
            with open(os.path.join(os.path.expanduser("~"), ".claude", "settings.json"), encoding="utf-8") as f:
                label = json.load(f).get("model")
        except Exception:
            label = None
    return "claude:" + (label or "default")


def gh_log(''' % TAG

AGENT_EDITS_COMMON = [
    ('def gh_log(', AGENT_HELPER),
    ('log_tail=tail, model=STATE.get("run_model") or "")',
     'log_tail=tail, model=STATE.get("run_model") or "worker-only")'),   # no model ran (preflight, release, lost race)
    ('    STATE.update(running=c["task"], running_number=n, gh_started=ghq.now_iso())\n',
     '    STATE.update(running=c["task"], running_number=n, gh_started=ghq.now_iso())\n'
     '    STATE["run_model"] = ""   # E7: never carry the previous card\'s model into this run\'s record\n'),
]
AGENT_EDITS_5060 = [('    STATE["run_model"] = model or ""\n', '    STATE["run_model"] = run_model_label(model)\n')]
AGENT_EDITS_RIG = [('    STATE["run_t0"] = t0\n    snap = code_snapshot()\n',
                    '    STATE["run_t0"] = t0\n    STATE["run_model"] = run_model_label(model)\n    snap = code_snapshot()\n')]


def plan(path, edits):
    s = open(path, encoding='utf-8').read()
    if TAG in s:
        return s, None, 'already installed'
    out = s
    for anchor, repl in edits:
        n = out.count(anchor)
        if n != 1:
            return s, None, f'anchor found {n} times, expected 1:\n    {anchor.strip()[:100]}'
        out = out.replace(anchor, repl, 1)
    return s, out, None


def agent_edits(src):
    if 'STATE["run_model"] = model or ""' in src:
        return AGENT_EDITS_COMMON + AGENT_EDITS_5060
    return AGENT_EDITS_COMMON + AGENT_EDITS_RIG


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--ghq', required=True)
    ap.add_argument('--agent', required=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    ghq_py, agent_py = os.path.join(a.ghq, 'ghq.py'), os.path.join(a.agent, 'agent.py')
    guard_dst = os.path.join(a.ghq, 'repeatguard.py')

    if a.revert:
        for f in (ghq_py, agent_py):
            baks = sorted(glob.glob(f + '.bak-*-we'))
            if baks:
                shutil.copy2(baks[-1], f)
                print(f'{os.path.basename(f)}: restored {os.path.basename(baks[-1])}')
        if os.path.exists(guard_dst):
            os.remove(guard_dst)
            print('repeatguard.py: removed')
        return 0

    agent_src = open(agent_py, encoding='utf-8').read()
    jobs = [(ghq_py, GHQ_EDITS), (agent_py, agent_edits(agent_src))]
    results, failed = [], False
    for path, edits in jobs:
        old, new, why = plan(path, edits)
        print(f'{os.path.basename(path)}: ' + (why or 'ready'))
        failed |= bool(why) and why != 'already installed'
        results.append((path, old, new))
    if failed:
        print('Nothing written.')
        return 1
    if a.check:
        return 0
    if all(new is None for _, _, new in results):
        shutil.copy2(os.path.join(HERE, 'repeatguard.py'), guard_dst)   # refresh the module only
        print('Already installed; repeatguard.py refreshed.')
        return 0
    stamp = time.strftime('%Y%m%d-%H%M')
    for path, old, new in results:
        if new is None:
            continue
        with open(f'{path}.bak-{stamp}-we', 'w', encoding='utf-8') as f:
            f.write(old)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new)
    shutil.copy2(os.path.join(HERE, 'repeatguard.py'), guard_dst)
    print(f'Installed (backups *.bak-{stamp}-we). Restart the Jarvis Worker on this PC, and the hub on the 5060.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
