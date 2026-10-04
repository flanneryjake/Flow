"""apply_one_step.py on the rig's ghq.py (WE_SNAPSHOTS=<dir with rig/ghq>): ask_jake adds no second step."""
import importlib.util, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')


def test_ask_jake_skips_second_step():
    d = tempfile.mkdtemp()
    for f in os.listdir(os.path.join(SNAP, 'rig', 'ghq')):
        if f.endswith('.py'):
            shutil.copy(os.path.join(SNAP, 'rig', 'ghq', f), d)
    for args in ([os.path.join(HERE, 'apply_worker_efficiency.py'), '--ghq', d, '--agent', os.path.join(SNAP, 'rig', 'agent'), '--check'],
                 [os.path.join(HERE, 'apply_one_comment.py'), '--ghq', d],
                 [os.path.join(HERE, 'apply_one_step.py'), '--ghq', d]):
        assert subprocess.run([sys.executable] + args).returncode == 0
    spec = importlib.util.spec_from_file_location('ghq_os', os.path.join(d, 'ghq.py'))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    attached = []
    g.set_status = lambda *a, **k: None
    g.comment = lambda *a, **k: None
    g._attach_jake_step = lambda n, q: attached.append(n)
    g.jake_step_of = lambda n: {'place': 'rig'}
    g.ask_jake(5, 'run the script')
    assert attached == []
    g.jake_step_of = lambda n: None
    g.ask_jake(5, 'run the script')
    assert attached == [5]
    shutil.rmtree(d)
