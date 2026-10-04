"""apply_wait_met.py on the rig's budget_guard.py (WE_SNAPSHOTS=<dir with rig/agent>), then drive _gh_finish."""
import importlib.util, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')


def test_already_met_wait_goes_to_triage():
    d = tempfile.mkdtemp()
    src = os.path.join(SNAP, 'rig', 'agent', 'budget_guard.py')
    shutil.copy(src, d)
    run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, 'apply_wait_met.py'), '--agent', d] + list(a))
    assert run('--check').returncode == 0 and run().returncode == 0
    spec = importlib.util.spec_from_file_location('bg_wm', os.path.join(d, 'budget_guard.py'))
    bg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bg)
    calls = []

    class G:
        def parse_snooze_line(self, out): return {'kind': 'card', 'value': '987', 'reason': 'x'}
        def snooze_until(self, *a): raise ValueError('card condition `987` is already met')
        def park_for_triage(self, n, m, why): calls.append(('triage', n, why))
        def ask_jake(self, n, why): calls.append(('jake', n, why))

    bg.AG.update(ghq=G(), ME='rig', gh_log=lambda *a, **k: calls.append(('log',)), log=lambda *a: None,
                 file_followup=lambda *a: None)
    bg._load = lambda: {}
    bg._gh_finish({'number': 982}, 'SNOOZE_UNTIL: card:987 | x', 'summary', [], None, [], 0)
    assert ('jake', 982) not in [c[:2] for c in calls]
    assert any(c[0] == 'triage' and c[1] == 982 and 'already met' in c[2] for c in calls)
    assert run('--revert').returncode == 0
    assert open(src, 'rb').read() == open(os.path.join(d, 'budget_guard.py'), 'rb').read()
