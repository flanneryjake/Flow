"""apply_crash_pause.py on both forks (WE_SNAPSHOTS=<dir with 5060/agent and rig/agent>), then drive the patched
finish() early paths and helpers with stubs."""
import ast, os, shutil, subprocess, sys, tempfile, time
from datetime import datetime
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')
NAMES = ('is_real_limit', '_crashed', '_limit_fallback_hours', 'reset_hours', 'claude_failed', 'finish', '_block')


def load(fork):
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(SNAP, fork, 'agent', 'agent.py'), d)
    assert subprocess.run([sys.executable, os.path.join(HERE, 'apply_crash_pause.py'), '--agent', d]).returncode == 0
    src = open(os.path.join(d, 'agent.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in NAMES) or
            (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in ('REAL_LIMIT_MARKERS', 'CRASH_SKIP_MIN',
             'CRASH_STREAK_REST', 'FAIL_MARKERS', 'CLI_ONLY_LINE', 'NO_TASK_MARKERS') for t in n.targets))]
    calls = []
    ns = {'time': time, 'datetime': datetime, 'STATE': {}, 'BLOCKED': {'until': 0, 'why': ''}, 'LOST_CLAIM': {},
          'ME': fork, 'log': lambda *a: None, 'notify': lambda *a: calls.append(('notify',) + a),
          'gh_log': lambda c, o, **k: calls.append(('gh_log', o, k.get('summary', ''))),
          'update': lambda *a, **k: calls.append(('update',)), 'calls': calls,
          'BUDGET': type('B', (), {'record_limit_hit': lambda self, *a: None})()}
    exec(compile(ast.Module(body=keep, type_ignores=[]), 'agent.py', 'exec'), ns)
    shutil.rmtree(d)
    return ns


def forks():
    if not SNAP:
        pytest.skip('set WE_SNAPSHOTS')
    return [load('rig'), load('5060')]


def test_real_limit_messages_pause():
    for ns in forks():
        for why in ("Claude AI usage limit reached|1790990000", "You've hit your limit · resets 3:10pm",
                    "Error: Not logged in · Please run /login", "5-hour limit reached ∙ resets 7pm"):
            assert ns['is_real_limit'](why), why


def test_crashes_and_card_text_do_not_pause():
    for ns in forks():
        for why in ("exit code 1", "empty output", "It looks like you haven't given me a task.",
                    "API Error: 500 Internal server error", "I fixed the GitHub rate limit retry in ghq.py",
                    "The rate limit handling was reviewed; usage limit lines are gone.", "x" * 400):
            assert not ns['is_real_limit'](why), why


def test_finish_on_crash_keeps_queue_running():
    for ns in forks():
        ns['STATE'].update(last_rc=1)
        ns['STATE'].pop('crash_streak', None)
        ns['calls'].clear()
        assert ns['finish']({'number': 690, 'id': 'x', 'task': 't'}, 'API Error: 500', 'log.txt') is None
        assert ns['BLOCKED']['until'] < time.time()                     # no global pause
        assert ns['calls'][0][:2] == ('gh_log', 'failed')
        assert ns['LOST_CLAIM'][690] > time.time() + 25 * 60             # this card waits ~30 min


def test_three_crashes_rest_30_min_not_5_h():
    for ns in forks():
        ns['STATE'].update(last_rc=1, crash_streak=0)
        for _ in range(3):
            ns['finish']({'number': 1, 'id': 'x', 'task': 't'}, 'boom', 'log.txt')
        left = ns['BLOCKED']['until'] - time.time()
        assert 25 * 60 < left <= 30 * 60


def test_real_limit_still_blocks_and_parks():
    for ns in forks():
        ns['STATE'].update(last_rc=1, crash_streak=0)
        ns['BLOCKED'].update(until=0)
        ns['calls'].clear()
        ns['finish']({'number': 2, 'id': 'y', 'task': 't'}, "You've hit your limit · resets 3:10pm", 'log.txt')
        assert ns['BLOCKED']['until'] > time.time()
        assert any(c[:2] == ('gh_log', 'waiting-usage') for c in ns['calls'])


def test_fallback_hours():
    for ns in forks():
        now = datetime(2026, 10, 3, 14, 0)
        assert ns['reset_hours']('usage limit reached', now) == 1
        assert abs(ns['reset_hours'](f'Claude AI usage limit reached|{int(now.timestamp()) + 7200}', now) - 2.1) < 0.01
        assert 58 < ns['reset_hours']('weekly limit reached · resets Oct 6', now) < 59
        assert ns['reset_hours']('Please run /login', now) == 1
