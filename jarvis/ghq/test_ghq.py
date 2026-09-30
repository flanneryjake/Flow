"""Tests for ghq against an in-memory fake of the GitHub REST API. Run from this folder: python -m unittest"""
import datetime as dt
import hashlib
import itertools
import json
import os
import re
import tempfile
import unittest
import urllib.parse

os.environ.setdefault('GITHUB_TASKS_TOKEN', 'test')
os.environ['JARVIS_WAKE_URLS'] = ''
import ghq  # noqa: E402

R = '/repos/' + ghq.REPO


class FakeGitHub:
    """Just enough of the Issues API for ghq: issues, labels, comments, list filters, pages and ETags."""

    def __init__(self):
        self.issues, self.comments, self.labels = {}, {}, {}
        self.num, self.cid, self.tick = itertools.count(1), itertools.count(1000), itertools.count()
        self.hook = None

    def _ts(self):
        return (dt.datetime(2026, 9, 30) + dt.timedelta(seconds=next(self.tick))).isoformat() + 'Z'

    @staticmethod
    def _out(i):
        return dict(i, labels=[{'name': n} for n in i['labels']])

    @staticmethod
    def _page(items, q):
        per, page = int(q.get('per_page', 30)), int(q.get('page', 1))
        return items[(page - 1) * per:page * per]

    def __call__(self, method, path, body=None, etag=None, graphql=False):
        if self.hook:
            self.hook(method, path, body)
        u = urllib.parse.urlparse(path)
        q, p = dict(urllib.parse.parse_qsl(u.query)), u.path
        if p == '/graphql':
            return 200, {'data': {}}, {}
        if p == R + '/labels':
            if method == 'GET':
                return 200, self._page(list(self.labels.values()), q), {}
            self.labels[body['name']] = dict(body)
            return 201, body, {}
        m = re.fullmatch(R + r'/labels/(.+)', p)
        if m:
            self.labels[urllib.parse.unquote(m.group(1))].update(body)
            return 200, {}, {}
        if p == R + '/issues' and method == 'POST':
            n = next(self.num)
            self.issues[n] = {'number': n, 'node_id': f'N{n}', 'title': body['title'], 'body': body.get('body', ''),
                              'labels': list(dict.fromkeys(body.get('labels', []))), 'state': 'open',
                              'created_at': self._ts()}
            self.comments[n] = []
            return 201, self._out(self.issues[n]), {}
        if p == R + '/issues':
            items = [i for i in self.issues.values() if q.get('state', 'open') in ('all', i['state'])]
            if 'labels' in q:
                items = [i for i in items if all(l in i['labels'] for l in q['labels'].split(','))]
            out = [self._out(i) for i in self._page(sorted(items, key=lambda i: i['created_at']), q)]
            tag = 'W/"' + hashlib.md5(json.dumps(out, sort_keys=True).encode()).hexdigest() + '"'
            return (304, None, {'ETag': tag}) if etag == tag else (200, out, {'ETag': tag})
        m = re.fullmatch(R + r'/issues/(\d+)', p)
        if m:
            i = self.issues[int(m.group(1))]
            if method == 'PATCH':
                for k, v in body.items():
                    i[k] = list(dict.fromkeys(v)) if k == 'labels' else v
            return 200, self._out(i), {}
        m = re.fullmatch(R + r'/issues/(\d+)/labels', p)
        if m:
            self.issues[int(m.group(1))]['labels'] = list(dict.fromkeys(body['labels']))
            return 200, [], {}
        m = re.fullmatch(R + r'/issues/(\d+)/comments', p)
        if m:
            n = int(m.group(1))
            if method == 'POST':
                c = {'id': next(self.cid), 'body': body['body'], 'created_at': self._ts(),
                     'issue_url': f'https://api.github.com{R}/issues/{n}'}
                self.comments[n].append(c)
                return 201, dict(c), {}
            return 200, [dict(c) for c in self._page(self.comments[n], q)], {}
        m = re.fullmatch(R + r'/issues/comments/(\d+)', p)
        if m:
            cid = int(m.group(1))
            for cs in self.comments.values():
                for c in cs:
                    if c['id'] == cid:
                        if method == 'DELETE':
                            cs.remove(c)
                            return 204, None, {}
                        c['body'] = body['body']
                        return 200, dict(c), {}
            raise ghq.GitHubError(f'{method} {path} -> 404')
        if p == R + '/issues/comments':
            everything = sorted((c for cs in self.comments.values() for c in cs), key=lambda c: c['id'])
            return 200, self._page(everything, q), {}
        raise AssertionError(f'fake GitHub has no {method} {path}')


class GhqTest(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self._request = ghq.request
        ghq.request = self.gh

    def tearDown(self):
        ghq.request = self._request

    def labels(self, n):
        return self.gh.issues[n]['labels']

    def ready_numbers(self, machine, cache=None):
        return [c['number'] for c in ghq.ready(machine, cache)]

    def card(self, **kw):
        kw.setdefault('status', 'approved')
        return ghq.new_card(kw.pop('title', 'card'), **kw)

    # -------------------------------------------------------------- the README's Worker loop

    def test_basic_flow(self):
        n = ghq.new_card('t', priority='P1')
        self.assertIn('p1', self.labels(n))
        self.assertEqual(ghq.ready('rig'), [])
        ghq.approve(n)
        self.assertEqual(self.ready_numbers('rig'), [n])
        self.assertTrue(ghq.claim(n, 'rig'))
        self.assertIn('status:working', self.labels(n))
        self.assertIn('claimed:rig', self.labels(n))
        self.assertEqual(ghq.ready('homebase'), [])
        self.assertEqual(ghq.log_run(n, 'rig', 'done', started='2026-09-30T10:00:00Z',
                                     ended='2026-09-30T10:30:00Z'), 'done')
        self.assertEqual(self.gh.issues[n]['state'], 'closed')
        self.assertFalse([l for l in self.labels(n) if l.startswith(('status:', 'claimed:'))])
        self.assertIn('(30.0 min)', self.gh.comments[n][-1]['body'])

    def test_machine_filter_and_priority(self):
        a = self.card(machine='rig')
        self.card(machine='homebase')
        b = self.card(priority='p0')
        self.assertEqual(self.ready_numbers('rig'), [b, a])

    def test_ready_over_100_cards(self):
        for i in range(105):
            self.card(title=f'c{i}', machine='homebase')
        last = self.card(machine='rig')
        self.assertEqual(self.ready_numbers('rig'), [last])
        cache = os.path.join(tempfile.mkdtemp(), 'ready.json')
        self.assertEqual(self.ready_numbers('rig', cache), [last])
        other = self.card(machine='rig')  # lands on page 2; page 1 is unchanged
        self.assertEqual(self.ready_numbers('rig', cache), [last, other])

    def test_etag_cache(self):
        n = self.card()
        cache = os.path.join(tempfile.mkdtemp(), 'ready.json')
        self.assertEqual(self.ready_numbers('rig', cache), [n])
        self.assertEqual(self.ready_numbers('rig', cache), [n])  # served from the 304
        ghq.claim(n, 'rig')
        self.assertEqual(self.ready_numbers('rig', cache), [])

    # -------------------------------------------------------------- claim

    def test_claim_race_has_one_winner(self):
        n = self.card()
        result = {}

        def hook(method, path, body):  # rig claims right after homebase posts, before homebase reads back
            if method == 'POST' and path.endswith(f'/issues/{n}/comments') and 'claim homebase' in body['body']:
                self.gh.hook = None
                result['rig'] = ghq.claim(n, 'rig')
        self.gh.hook = hook
        result['homebase'] = ghq.claim(n, 'homebase')
        self.assertEqual(sorted(result.values()), [False, True])

    def test_claim_survives_duplicate_post(self):
        n = self.card()
        fake = self.gh

        def retried(method, path, body=None, etag=None, graphql=False):
            if method == 'POST' and path.endswith('/comments') and 'jarvis:claim' in body['body']:
                fake(method, path, body)  # the first try reached GitHub, but the client saw a 502 and retried
            return fake(method, path, body, etag)
        ghq.request = retried
        self.assertTrue(ghq.claim(n, 'rig'))

    def test_claim_after_ask_jake_and_reapproval(self):
        n = self.card()
        self.assertTrue(ghq.claim(n, 'rig'))
        ghq.ask_jake(n, 'Which printer?')
        self.assertIn('status:needs-jake', self.labels(n))
        self.assertNotIn('claimed:rig', self.labels(n))
        self.assertTrue(self.gh.comments[n][-1]['body'].startswith('**Needs Jake:** Which printer?'))
        ghq.approve(n)
        self.assertEqual(self.ready_numbers('homebase'), [n])
        self.assertTrue(ghq.claim(n, 'homebase'))

    def test_reapprove_after_worker_crash(self):
        n = self.card()
        self.assertTrue(ghq.claim(n, 'rig'))  # ...and the Worker dies without logging a run
        ghq.approve(n)
        self.assertEqual(self.ready_numbers('rig'), [n])
        self.assertTrue(ghq.claim(n, 'rig'))

    def test_claim_from_stale_list_fails(self):
        done = self.card()
        ghq.claim(done, 'rig')
        ghq.log_run(done, 'rig', 'done')
        snoozed = self.card()
        ghq.set_status(snoozed, 'snoozed')
        for n in (done, snoozed):
            self.assertFalse(ghq.claim(n, 'homebase'))
            self.assertFalse([c for c in self.gh.comments[n] if 'claim homebase' in c['body']])
        self.assertEqual(self.gh.issues[done]['state'], 'closed')
        self.assertIn('status:snoozed', self.labels(snoozed))

    # -------------------------------------------------------------- log_run / approve

    def test_two_failures_stop_the_card(self):
        n = self.card()
        ghq.claim(n, 'rig')
        self.assertEqual(ghq.log_run(n, 'rig', 'failed'), 'approved')
        self.assertTrue(ghq.claim(n, 'homebase'))
        self.assertEqual(ghq.log_run(n, 'homebase', 'timeout'), 'needs-jake')
        self.assertIn('status:needs-jake', self.labels(n))

    def test_released_and_waiting_usage_do_not_count(self):
        n = self.card()
        for outcome in ('failed', 'waiting-usage', 'released'):
            ghq.claim(n, 'rig')
            self.assertEqual(ghq.log_run(n, 'rig', outcome), 'approved')
        ghq.claim(n, 'rig')
        self.assertEqual(ghq.log_run(n, 'rig', 'failed'), 'needs-jake')

    def test_reapproval_gives_a_fresh_retry(self):
        n = self.card()
        for _ in range(2):
            ghq.claim(n, 'rig')
            ghq.log_run(n, 'rig', 'failed')
        ghq.approve(n)
        self.assertTrue(ghq.claim(n, 'rig'))
        self.assertEqual(ghq.log_run(n, 'rig', 'failed'), 'approved')

    def test_approve_reopens_a_closed_card(self):
        n = self.card()
        ghq.claim(n, 'rig')
        ghq.log_run(n, 'rig', 'done')
        ghq.approve(n)
        self.assertEqual(self.gh.issues[n]['state'], 'open')
        self.assertEqual(self.ready_numbers('rig'), [n])

    def test_minutes_from_powershell_and_naive_times(self):
        n = self.card()
        ghq.claim(n, 'rig')
        ghq.log_run(n, 'rig', 'released', started='2026-09-30T10:00:00.1234567-04:00',
                    ended='2026-09-30T10:30:00.1234567-04:00')
        self.assertIn('(30.0 min)', self.gh.comments[n][-1]['body'])
        ghq.claim(n, 'rig')
        self.assertEqual(ghq.log_run(n, 'rig', 'released', started='2026-09-30T10:00:00',
                                     ended='2026-09-30T10:06:00'), 'approved')
        self.assertIn('(6.0 min)', self.gh.comments[n][-1]['body'])
        ghq.claim(n, 'rig')
        self.assertEqual(ghq.log_run(n, 'rig', 'released', started=ghq.now_iso().replace('+00:00', '')),
                         'approved')

    # -------------------------------------------------------------- the rest

    def test_new_card(self):
        n = ghq.new_card('a', body='b', spawned_from=3)
        self.assertTrue(self.gh.issues[n]['body'].endswith('Spawned from #3'))
        self.assertIn('status:staged', self.labels(n))
        with self.assertRaises(ValueError):
            ghq.new_card('a', status='bogus')

    def test_health_and_usage(self):
        ghq.setup()
        n = ghq.write_health('rig', {'Worker': 'Working'}, alerts=['x'])
        self.assertIn('health:alert', self.labels(n))
        self.assertTrue(self.gh.issues[n]['title'].startswith('Health: rig'))
        self.assertEqual(ghq.write_health('rig', {'Worker': 'Idle'}), n)
        self.assertNotIn('health:alert', self.labels(n))
        self.assertEqual(len([i for i in self.gh.issues.values() if 'health' in i['labels']]), 2)
        c = self.card()
        ghq.claim(c, 'rig')
        ghq.log_run(c, 'rig', 'done', started='2026-09-30T10:00:00Z', ended='2026-09-30T10:06:00Z')
        top = ghq.usage()[0]
        self.assertEqual((top['number'], top['runs'], top['minutes']), (c, 1, 6.0))


if __name__ == '__main__':
    unittest.main()
