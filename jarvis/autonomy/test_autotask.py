"""Tests for autotask.py against an in-memory fake of ghq. Run: python -m unittest test_autotask"""
import os
import sys
import types
import unittest

import autotask


def fake_ghq(existing=()):
    g = types.SimpleNamespace()
    g.issues = {i['number']: i for i in existing}
    g.comments, g.woke = [], 0

    class GitHubError(Exception):
        pass
    g.GitHubError = GitHubError
    g.repo_path = lambda s='': '/repos/x/y' + s
    g.label_names = lambda i: [l['name'] if isinstance(l, dict) else l for l in i.get('labels', [])]
    g.now_iso = lambda: '2026-09-30T20:00:00Z'

    def paged(path):
        out = list(g.issues.values())
        if 'labels=auto' in path:
            out = [i for i in out if 'auto' in g.label_names(i)]
        if 'state=open' in path:
            out = [i for i in out if i.get('state', 'open') == 'open']
        return out
    g.paged = paged

    def api(method, path, body=None):
        if method == 'GET':
            return g.issues[int(path.rsplit('/', 1)[1])]
        if path.endswith('/labels'):
            return {}
        raise AssertionError(path)
    g.api = api

    def new_card(title, body='', machine='any', priority=None, status='staged', card_type='task', pin=False,
                 spawned_from=None, extra_labels=()):
        n = max(g.issues, default=0) + 1
        if spawned_from:
            body = (body + f'\n\nSpawned from #{spawned_from}').strip()
        labels = [f'status:{status}', f'machine:{machine}'] + list(extra_labels) + (['pin'] if pin else [])
        g.issues[n] = {'number': n, 'title': title, 'body': body, 'labels': labels, 'state': 'open',
                       'created_at': '2026-09-30T20:00:00Z'}
        return n
    g.new_card = new_card
    g.comment = lambda n, t: g.comments.append((n, t))

    def wake():
        g.woke += 1
    g.wake = wake
    return g


class AutotaskTest(unittest.TestCase):
    def setUp(self):
        for k in ('JARVIS_AUTO_OFF', 'JARVIS_AUTO_PER_DAY', 'JARVIS_AUTO_DEPTH'):
            os.environ.pop(k, None)

    def use(self, g):
        autotask._ghq = lambda: g

    def test_free_card_is_approved_and_wakes(self):
        g = fake_ghq(); self.use(g)
        n, status, why = autotask.propose('Test ff_printer.py against the real printer', machine='rig')
        self.assertEqual(status, 'approved')
        self.assertIn('auto', g.issues[n]['labels'])
        self.assertIn('status:approved', g.issues[n]['labels'])
        self.assertEqual(g.woke, 1)
        self.assertTrue(g.comments[0][1].startswith('Auto-approved'))

    def test_pin_card_is_staged_with_pin_label(self):
        g = fake_ghq(); self.use(g)
        n, status, why = autotask.propose('Publish Fieldwork Clinical product #1 on Gumroad')
        self.assertEqual(status, 'staged')
        self.assertIn('pin', g.issues[n]['labels'])
        self.assertNotIn('auto', g.issues[n]['labels'])
        self.assertEqual(g.woke, 0)

    def test_duplicate_returns_existing(self):
        g = fake_ghq([{'number': 7, 'title': 'Test ff_printer.py against the real printer!', 'labels': []}])
        self.use(g)
        self.assertEqual(autotask.propose('test ff_printer.py against the real printer')[:2], (7, 'duplicate'))

    def test_daily_limit(self):
        os.environ['JARVIS_AUTO_PER_DAY'] = '1'
        g = fake_ghq([{'number': 1, 'title': 'x', 'labels': ['auto'], 'created_at': '2099-01-01T00:00:00Z'}])
        self.use(g)
        n, status, why = autotask.propose('Wire Phase 1/2 output straight in')
        self.assertEqual(status, 'staged')
        self.assertIn('daily auto limit', why)

    def test_chain_depth_limit(self):
        os.environ['JARVIS_AUTO_DEPTH'] = '2'
        g = fake_ghq([
            {'number': 1, 'title': 'a', 'labels': ['auto'], 'body': ''},
            {'number': 2, 'title': 'b', 'labels': ['auto'], 'body': 'Spawned from #1'},
        ])
        self.use(g)
        n, status, why = autotask.propose('Follow-up c', spawned_from=2)
        self.assertEqual(status, 'staged')
        self.assertIn('chained', why)

    def test_follow_up_of_jake_approved_card_is_approved(self):
        g = fake_ghq([{'number': 1, 'title': 'a', 'labels': ['status:approved'], 'body': ''}])
        self.use(g)
        self.assertEqual(autotask.propose('Follow-up c', spawned_from=1)[1], 'approved')

    def test_kill_switch(self):
        os.environ['JARVIS_AUTO_OFF'] = '1'
        g = fake_ghq(); self.use(g)
        self.assertEqual(autotask.propose('Wire Phase 1/2 output straight in')[1], 'staged')

    def test_check_cli_needs_no_token(self):
        os.environ.pop('GITHUB_TASKS_TOKEN', None)
        self.assertEqual(autotask.main(['check', 'Buy filament']), 0)


if __name__ == '__main__':
    unittest.main()
