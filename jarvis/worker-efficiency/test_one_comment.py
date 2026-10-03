"""apply_one_comment.py on both forks' ghq.py (WE_SNAPSHOTS=<dir with 5060/ghq and rig/ghq>), then drive claim(),
progress() and _log_run() against a fake GitHub and count new comments."""
import importlib.util, os, shutil, subprocess, sys, tempfile
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get('WE_SNAPSHOTS', '')


def load(fork):
    d = tempfile.mkdtemp()
    for f in os.listdir(os.path.join(SNAP, fork, 'ghq')):
        if f.endswith('.py'):
            shutil.copy(os.path.join(SNAP, fork, 'ghq', f), d)
    assert subprocess.run([sys.executable, os.path.join(HERE, 'apply_one_comment.py'), '--ghq', d]).returncode == 0
    spec = importlib.util.spec_from_file_location(f'ghq_e8_{fork}', os.path.join(d, 'ghq.py'))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


class FakeGitHub:
    def __init__(self, g):
        self.comments, self.calls, self.next_id = [], [], 100
        self.issue = {'state': 'open', 'number': 7, 'labels': [{'name': 'status:approved'}]}
        g.api = self.api
        g.paged = lambda path: iter(list(self.comments)) if '/comments' in path else iter([])
        g.set_status = lambda n, s, extra_add=(), extra_remove=(): self.calls.append(('status', s))
        g._loopnet = lambda: None
        g.catch_jake_text = lambda *a, **k: None
        if hasattr(g, '_repeat_guard_ok'):
            g._repeat_guard_ok = lambda n, i: True

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        if method == 'POST' and path.endswith('/comments'):
            self.next_id += 1
            c = {'id': self.next_id, 'body': body['body'], 'created_at': '2026-10-03T18:00:00Z'}
            self.comments.append(c)
            return c
        if method == 'PATCH' and '/issues/comments/' in path:
            cid = int(path.rsplit('/', 1)[1])
            hit = [c for c in self.comments if c['id'] == cid]
            if not hit:
                raise RuntimeError('404')
            hit[0]['body'] = body['body']
            return hit[0]
        if method == 'DELETE':
            cid = int(path.rsplit('/', 1)[1])
            self.comments = [c for c in self.comments if c['id'] != cid]
            return {}
        return self.issue


@pytest.mark.parametrize('fork', ['rig', '5060'])
def test_one_new_comment_per_run(fork):
    g = load(fork)
    gh = FakeGitHub(g)
    assert g.claim(7, fork) is True
    assert len(gh.comments) == 1 and g.RUN_COMMENT[7] == gh.comments[0]['id']
    if hasattr(g, 'progress'):
        g.progress(7, fork, 'Running for 30 s')
        g.progress(7, fork, 'Running for 60 s', gh.comments[0]['id'])
        assert len(gh.comments) == 1 and 'Running for 60 s' in gh.comments[0]['body']
    g._log_run(7, fork, 'failed', started='2026-10-03T18:00:00Z', ended='2026-10-03T18:05:00Z',
               summary='Crashed', model='claude:opus')
    assert len(gh.comments) == 1
    body = gh.comments[0]['body']
    assert body.startswith(g.RUN_MARK) and '"outcome": "failed"' in body and 7 not in g.RUN_COMMENT
    assert g.runs_of(7)[-1]['outcome'] == 'failed'


@pytest.mark.parametrize('fork', ['rig', '5060'])
def test_falls_back_to_new_comment(fork):
    g = load(fork)
    gh = FakeGitHub(g)
    assert g.claim(7, fork) is True
    gh.comments.clear()                      # claim comment deleted by hand
    g._log_run(7, fork, 'done', started='2026-10-03T18:00:00Z')
    assert len(gh.comments) == 1 and gh.comments[0]['body'].startswith(g.RUN_MARK)


@pytest.mark.parametrize('fork', ['rig', '5060'])
def test_next_claim_still_wins(fork):   # the edited run record ends the old claim for the race check
    g = load(fork)
    gh = FakeGitHub(g)
    assert g.claim(7, fork) is True
    g._log_run(7, fork, 'failed', started='2026-10-03T18:00:00Z')
    gh.issue['labels'] = [{'name': 'status:approved'}]
    assert g.claim(7, fork) is True and len(gh.comments) == 2


def test_off_switch(monkeypatch):
    monkeypatch.setenv('JARVIS_ONE_COMMENT', 'off')
    g = load('5060')
    gh = FakeGitHub(g)
    assert g.claim(7, '5060') is True
    g._log_run(7, '5060', 'done', started='2026-10-03T18:00:00Z')
    assert len(gh.comments) == 2
