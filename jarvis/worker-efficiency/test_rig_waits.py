"""apply_rig_waits.py (with apply_wait_met.py) on the rig's budget_guard.py (WE_SNAPSHOTS=<dir with rig/agent>)."""
import importlib.util, os, shutil, subprocess, sys, tempfile
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')


@pytest.fixture()
def bg():
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(SNAP, 'rig', 'agent', 'budget_guard.py'), d)
    for tool in ('apply_wait_met.py', 'apply_rig_waits.py'):
        assert subprocess.run([sys.executable, os.path.join(HERE, tool), '--agent', d]).returncode == 0
    spec = importlib.util.spec_from_file_location('bg_rw', os.path.join(d, 'budget_guard.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    calls = []

    class G:
        def parse_snooze_line(self, out): return None
        def snooze_until(self, n, kind, value, machine, reason='', producer=None): calls.append(('snooze', n, kind, value))
        def park_for_triage(self, n, machine, why): calls.append(('triage', n, why))
        def ask_jake(self, n, why): calls.append(('jake', n))

    m.AG.update(ghq=G(), ME='rig', gh_log=lambda c, o, **k: calls.append(('log', o)), log=lambda *a: None,
                file_followup=lambda *a: None)
    m.ORIG['gh_finish'] = lambda *a: calls.append(('orig',))
    m._load = lambda: {}
    m._save = lambda d: None
    m.calls = calls
    yield m
    shutil.rmtree(d)


def test_selfpatch_goes_to_claude_review(bg):
    bg._gh_finish({'number': 609}, 'x', 's', ['Apply autosleep-can-sleep.patch to the rig\'s agent.py and restart the '
                                               'Worker'], None, [], {})
    assert ('log', 'released') in bg.calls and any(c[0] == 'triage' and c[1] == 609 for c in bg.calls)
    assert ('orig',) not in bg.calls and not any(c[0] == 'jake' for c in bg.calls)


def test_other_needs_unchanged(bg):
    bg._gh_finish({'number': 5}, 'x', 's', ['Tap Yes on the UAC prompt at the rig'], None, [], {})
    assert bg.calls == [('orig',)]


def test_waiting_checkpoint_snoozes(bg):
    bg._gh_finish({'number': 795}, 'x', 's', [], ['4 of 10 pass-3 files are written and the other 6 are still queued'],
                  [], {})
    assert bg.calls[0] == ('orig',) and bg.calls[1][:3] == ('snooze', 795, 'time')


def test_working_checkpoint_does_not_snooze(bg):
    bg._gh_finish({'number': 7}, 'x', 's', [], ['Built the parser; tests next'], [], {})
    assert bg.calls == [('orig',)]


def test_revert_identical():
    d = tempfile.mkdtemp()
    src = os.path.join(SNAP, 'rig', 'agent', 'budget_guard.py')
    shutil.copy(src, d)
    run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, 'apply_rig_waits.py'), '--agent', d] + list(a))
    assert run().returncode == 0 and run('--revert').returncode == 0
    assert open(src, 'rb').read() == open(os.path.join(d, 'budget_guard.py'), 'rb').read()
