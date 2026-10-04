"""Rig fix (Jake 10/04 00:39Z: "I do not want an ai model evaluating itself ... I want audits, quality control,
and peer review" through Claude). No model gives the final verdict on its own work on the rig Worker.

Two holes closed:
1. agent.py gh_finish: a pass the local model did (local lane, run_model "local:...", or a model:local card) used
   to close the card as done with "Not reviewed by Claude". Now the card is released and parked for Claude QC
   (status:snoozed + needs-claude-review + triage, one comment saying why); the hub's Claude review lane closes it,
   fixes it or sends it on. Claude passes are unchanged.
2. ghq.send_back: the rig's own model (ask_ollama, the same Jarvis that ran the card) used to decide first what a
   stuck card waits on. Now Claude decides (ask_claude only); if Claude can't answer, the old path (jake gate ->
   park) runs as before.

    python apply_no_self_review.py --agent <rig agent folder> --ghq <rig ghq folder> [--check | --revert]

Backups: agent.py.bak-<stamp>-nsr, ghq.py.bak-<stamp>-nsr. Touches fixes-running.flag first. Restart the Worker
while it is idle. Off switch: env JARVIS_SELF_REVIEW=allow (old behaviour for both).
"""
import argparse
import glob
import os
import shutil
import sys
import time

TAG = 'no-self-review 10/04'
FLAG = r'C:\Jarvis\audit\fixes-running.flag'

A_ANCHOR = ('    else:\n'
            '        gh_log(c, "done", summary=summary, tail=output)\n'
            '        STATE["done_today"] = STATE.get("done_today", 0) + 1\n')
A_REPL = ('    elif _needs_claude_qc(c):   # %s: a model never closes its own work; Claude QC does\n'
          '        gh_log(c, "released", summary=f"Local draft finished; waiting for Claude QC before it closes.\\n\\n{summary}",\n'
          '               tail=output)\n'
          '        try:\n'
          '            _park_for_claude_qc(c)\n'
          '            log(f"CLAUDE-QC: #{c.get(\'number\')} parked for Claude review (local model pass)")\n'
          '        except Exception as e:\n'
          '            log(f"CLAUDE-QC: could not park #{c.get(\'number\')}: {e}")\n'
          '    else:\n'
          '        gh_log(c, "done", summary=summary, tail=output)\n'
          '        STATE["done_today"] = STATE.get("done_today", 0) + 1\n' % TAG)
A_HELPERS_ANCHOR = 'def gh_finish(c, output, summary, needs, ckpt, follow, passes):\n'
A_HELPERS = ('def _needs_claude_qc(c):\n'
             '    """%s: True when a local model did this pass, so it may not mark its own work done."""\n'
             '    if os.environ.get("JARVIS_SELF_REVIEW", "").lower() == "allow" or not c.get("number"):\n'
             '        return False\n'
             '    return str(STATE.get("run_model") or "").startswith("local:") or "model:local" in (c.get("labels") or [])\n'
             '\n'
             '\n'
             'def _park_for_claude_qc(c):\n'
             '    n = c["number"]\n'
             '    issue = ghq.api("GET", ghq.repo_path(f"/issues/{n}"))\n'
             '    keep = [x for x in ghq.label_names(issue) if not x.startswith(("claimed:", "status:"))]\n'
             '    ghq.api("PUT", ghq.repo_path(f"/issues/{n}/labels"),\n'
             '            {"labels": keep + ["status:snoozed"] + [x for x in ("needs-claude-review", "triage") if x not in keep]})\n'
             '    ghq.comment(n, f"Claude QC: this pass was done by the local model ({STATE.get(\'run_model\') or \'model:local\'}) "\n'
             '                   "on the rig. No model signs off on its own work (Jake 10/04), so Claude reviews the draft "\n'
             '                   "before this card closes.", dedupe=False)\n'
             '\n'
             '\n' % TAG)

B_ANCHOR = '    models = models if models is not None else [ask_ollama, ask_claude]\n'
B_REPL = ('    # %s: the Worker\'s own model never judges its own stuck card; Claude does\n'
          '    models = models if models is not None else (\n'
          '        [ask_ollama, ask_claude] if os.environ.get("JARVIS_SELF_REVIEW", "").lower() == "allow" else [ask_claude])\n'
          % TAG)


def _read(path):
    raw = open(path, 'rb').read()
    return raw, raw.decode('utf-8').replace('\r\n', '\n')


def _write(raw, s):
    if b'\r\n' in raw:
        s = s.replace('\n', '\r\n')
    return s.encode('utf-8')


def plan_agent(path):
    raw, s = _read(path)
    if TAG in s:
        return raw, None, 'already installed'
    for name, a in (('gh_finish done branch', A_ANCHOR), ('def gh_finish', A_HELPERS_ANCHOR)):
        if s.count(a) != 1:
            return raw, None, f'anchor "{name}" found {s.count(a)} times, expected 1'
    s = s.replace(A_ANCHOR, A_REPL, 1).replace(A_HELPERS_ANCHOR, A_HELPERS + A_HELPERS_ANCHOR, 1)
    return raw, _write(raw, s), None


def plan_ghq(path):
    raw, s = _read(path)
    if TAG in s:
        return raw, None, 'already installed'
    if s.count(B_ANCHOR) != 1:
        return raw, None, f'anchor "send_back models" found {s.count(B_ANCHOR)} times, expected 1'
    if 'import os' not in s:
        return raw, None, 'ghq.py does not import os'
    return raw, _write(raw, s.replace(B_ANCHOR, B_REPL, 1)), None


def touch(flag):
    try:
        os.makedirs(os.path.dirname(flag), exist_ok=True)
        open(flag, 'a').close()
        os.utime(flag, None)
    except OSError as e:
        print(f'note: could not touch {flag}: {e}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent', required=True)
    ap.add_argument('--ghq', required=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--flag', default=os.environ.get('JARVIS_FIX_FLAG', FLAG))
    a = ap.parse_args(argv)
    targets = [(os.path.join(a.agent, 'agent.py'), plan_agent), (os.path.join(a.ghq, 'ghq.py'), plan_ghq)]
    if a.revert:
        touch(a.flag)
        for path, _ in targets:
            baks = sorted(glob.glob(path + '.bak-*-nsr'))
            if baks:
                shutil.copy2(baks[-1], path)
                print(f'{os.path.basename(path)}: restored {os.path.basename(baks[-1])}')
        return 0
    plans, bad = [], False
    for path, fn in targets:
        raw, new, why = fn(path)
        print(f'{os.path.basename(path)}: ' + (why or 'ready'))
        if why and why != 'already installed':
            bad = True
        plans.append((path, new))
    if bad:
        return 1
    if a.check:
        return 0
    todo = [(p, n) for p, n in plans if n is not None]
    if not todo:
        return 0
    touch(a.flag)
    stamp = time.strftime('%Y%m%d-%H%M')
    for path, new in todo:
        shutil.copy2(path, f'{path}.bak-{stamp}-nsr')
        with open(path, 'wb') as f:
            f.write(new)
        print(f'Installed {os.path.basename(path)} (backup .bak-{stamp}-nsr)')
    print('Restart the Jarvis Worker on the rig while it is idle.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
