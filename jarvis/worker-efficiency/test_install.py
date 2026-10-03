"""Install into copies of the 3 AM snapshots of both forks, then drive the patched ghq.claim() with a fake GitHub."""
import importlib.util, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')   # folder with 5060/{ghq,agent} and rig/{ghq,agent} copies of the live files


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_fork(fork):
    d = tempfile.mkdtemp()
    for sub in ('ghq', 'agent'):
        shutil.copytree(os.path.join(SNAP, fork, sub), os.path.join(d, sub))
    cmd = [sys.executable, os.path.join(HERE, 'apply_worker_efficiency.py'), '--ghq', os.path.join(d, 'ghq'),
           '--agent', os.path.join(d, 'agent')]
    assert subprocess.run(cmd).returncode == 0
    sys.path.insert(0, os.path.join(d, 'ghq'))
    try:
        g = _load(os.path.join(d, 'ghq', 'ghq.py'), f'ghq_{fork}')
        sys.modules[g.__name__] = g
        calls = []
        issue = {'state': 'open', 'labels': [{'name': 'status:approved'}], 'number': 690}
        hist = [{'created_at': '2026-10-03T08:00:00Z', 'user': {'login': 'clauderigassist-cell'},
                 'body': '**Needs Jake:** run add-startup-trigger.ps1\n<!-- jarvis:reset -->'},
                {'created_at': '2026-10-03T08:10:00Z', 'user': {'login': 'flanneryjake'},
                 'body': 'Approved via phone at x\n<!-- jarvis:reset -->'}]
        g.api = lambda m, p, b=None: (calls.append((m, p)), issue)[1]
        g.paged = lambda p: iter(hist)
        g.request = lambda *a, **k: (200, {'data': {'repository': {'issue': {'lastEditedAt': None}}}}, {})
        g.set_status = lambda n, s, extra_add=(), extra_remove=(): calls.append(('status', s))
        g.comment = lambda n, t, **k: calls.append(('comment', t[:40]))
        assert g.claim(690, 'rig') is False
        assert ('status', 'needs-jake') in calls
        assert not any(c[0] == 'comment' and 'jarvis:claim' in c[1] for c in calls)   # no claim comment posted
        return True
    finally:
        sys.path.remove(os.path.join(d, 'ghq'))
        shutil.rmtree(d, ignore_errors=True)


def test_both_forks():
    if not SNAP:
        import pytest
        pytest.skip('set WE_SNAPSHOTS')
    assert run_fork('5060') and run_fork('rig')
