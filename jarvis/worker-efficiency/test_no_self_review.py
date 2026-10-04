"""apply_no_self_review.py on rig snapshots (WE_SNAPSHOTS=<dir with rig/agent and rig/ghq>)."""
import os, re, shutil, subprocess, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')


def _setup():
    d = tempfile.mkdtemp()
    ag, gq = os.path.join(d, 'agent'), os.path.join(d, 'ghq')
    os.makedirs(ag); os.makedirs(gq)
    shutil.copy(os.path.join(SNAP, 'rig', 'agent', 'agent.py'), ag)
    shutil.copy(os.path.join(SNAP, 'rig', 'ghq', 'ghq.py'), gq)
    run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, 'apply_no_self_review.py'), '--agent', ag,
                                     '--ghq', gq, '--flag', os.path.join(d, 'flag')] + list(a),
                                    capture_output=True, text=True)
    return d, ag, gq, run


def _fn(src, name, g):
    i = src.index(f'def {name}(')
    j = src.index('\ndef ', i + 5)
    exec(src[i:j], g)
    return g[name]


def test_check_apply_twice_revert_and_compile():
    d, ag, gq, run = _setup()
    assert run('--check').returncode == 0
    r = run()
    assert r.returncode == 0, r.stdout + r.stderr
    for p in (os.path.join(ag, 'agent.py'), os.path.join(gq, 'ghq.py')):
        compile(open(p, encoding='utf-8').read(), p, 'exec')
    assert 'already installed' in run().stdout
    assert run('--revert').returncode == 0
    for sub, f in (('agent', 'agent.py'), ('ghq', 'ghq.py')):
        assert open(os.path.join(SNAP, 'rig', sub, f), 'rb').read() == open(os.path.join(d, sub, f), 'rb').read()


def _finish_env(ag):
    src = open(os.path.join(ag, 'agent.py'), encoding='utf-8').read()
    calls = []
    ghq = types.SimpleNamespace(
        api=lambda m, p, b=None: calls.append(('api', m, b)) or {'labels': [{'name': 'status:working'}, {'name': 'p2'},
                                                                          {'name': 'claimed:rig'}]},
        repo_path=lambda p: p, label_names=lambda i: [x['name'] for x in i['labels']],
        comment=lambda n, b, dedupe=True: calls.append(('comment', n, b)))
    g = {'os': os, 'STATE': {}, 'ghq': ghq, 'log': lambda *a: None, 'MAX_PASSES_PER_NIGHT': 3,
         'gh_log': lambda c, outcome, **k: calls.append(('log', outcome)), 'file_followup': lambda *a: None}
    for name in ('_needs_claude_qc', '_park_for_claude_qc', 'gh_finish'):
        _fn(src, name, g)
    return g, calls


def test_local_pass_parks_for_claude_qc():
    d, ag, gq, run = _setup()
    assert run().returncode == 0
    g, calls = _finish_env(ag)
    g['STATE']['run_model'] = 'local:qwen3.6:35b'
    g['gh_finish']({'number': 5, 'id': 'gh-5', 'labels': []}, 'out', 'sum', [], None, [], {})
    assert ('log', 'done') not in calls and ('log', 'released') in calls
    put = [c for c in calls if c[0] == 'api' and c[1] == 'PUT'][0][2]['labels']
    assert 'status:snoozed' in put and 'needs-claude-review' in put and 'claimed:rig' not in put
    assert any(c[0] == 'comment' and 'Claude QC' in c[2] for c in calls)


def test_claude_pass_still_closes_and_off_switch():
    d, ag, gq, run = _setup()
    assert run().returncode == 0
    g, calls = _finish_env(ag)
    g['gh_finish']({'number': 6, 'id': 'gh-6', 'labels': []}, 'out', 'sum', [], None, [], {})
    assert ('log', 'done') in calls
    g2, calls2 = _finish_env(ag)
    g2['STATE']['run_model'] = 'local:x'
    os.environ['JARVIS_SELF_REVIEW'] = 'allow'
    try:
        g2['gh_finish']({'number': 7, 'id': 'gh-7', 'labels': ['model:local']}, 'o', 's', [], None, [], {})
    finally:
        del os.environ['JARVIS_SELF_REVIEW']
    assert ('log', 'done') in calls2


def test_send_back_uses_claude_only():
    d, ag, gq, run = _setup()
    assert run().returncode == 0
    s = open(os.path.join(gq, 'ghq.py'), encoding='utf-8').read()
    assert re.search(r'else \[ask_claude\]\)', s)
    assert '[ask_ollama, ask_claude] if os.environ.get("JARVIS_SELF_REVIEW"' in s
