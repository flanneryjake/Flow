import unittest

import cards

MARK = '<!-- jarvis:nodecmd {"target": "junk", "cmd": "restart_service", "args": {"name": "searxng"}} -->'


class FakeGH:
    def __init__(self, issues, comments=None):
        self.issues, self.comments, self.calls = issues, comments or {}, []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == 'GET' and path.endswith('/comments?per_page=100'):
            return self.comments.get(int(path.split('/issues/')[1].split('/')[0]), [])
        if method == 'GET':
            return self.issues
        return {}

    def posted(self):
        return [b['body'] for m, p, b in self.calls if m == 'POST']

    def closed(self):
        return [p for m, p, b in self.calls if m == 'PATCH' and b['state'] == 'closed']


class CardsTest(unittest.TestCase):
    def setUp(self):
        self.ran = []

    def call(self, target, cmd, args):
        self.ran.append((target, cmd, args))
        return {'ok': True, 'node': target, 'cmd': cmd, 'summary': 'restarted searxng'}

    def test_runs_once_comments_once_closes(self):
        gh = FakeGH([{'number': 7, 'body': 'please\n' + MARK}])
        self.assertEqual(cards.run_node_cards(gh, self.call, log=lambda m: None), 1)
        self.assertEqual(self.ran, [('junk', 'restart_service', {'name': 'searxng'})])
        self.assertEqual(len(gh.posted()), 1)
        self.assertIn('restarted searxng', gh.posted()[0])
        self.assertEqual(gh.closed(), ['/repos/flanneryjake/jarvis-tasks/issues/7'])

    def test_existing_result_is_never_rerun(self):
        gh = FakeGH([{'number': 7, 'body': MARK}], {7: [{'body': cards.RESULT_MARK + '\nRan'}]})
        cards.run_node_cards(gh, self.call, log=lambda m: None)
        self.assertEqual(self.ran, [])
        self.assertEqual(gh.posted(), [])
        self.assertEqual(len(gh.closed()), 1)

    def test_restart_pc_and_junk_refused_without_calling(self):
        for body in ['<!-- jarvis:nodecmd {"target": "junk", "cmd": "restart_pc"} -->', 'no marker',
                     '<!-- jarvis:nodecmd {"target": "mars", "cmd": "status"} -->',
                     '<!-- jarvis:nodecmd {not json} -->']:
            gh = FakeGH([{'number': 3, 'body': body}])
            cards.run_node_cards(gh, self.call, log=lambda m: None)
            self.assertIn('Not run', gh.posted()[0], body)
        self.assertEqual(self.ran, [])

    def test_refusal_from_agent_is_reported(self):
        gh = FakeGH([{'number': 9, 'body': MARK}])
        cards.run_node_cards(gh, lambda t, c, a: {'ok': False, 'error': 'rate limit: 10 min apart'}, log=lambda m: None)
        self.assertIn('did not run: rate limit', gh.posted()[0])

    def test_unreachable_exception_reported(self):
        def boom(t, c, a):
            raise OSError('down')
        gh = FakeGH([{'number': 9, 'body': MARK}])
        cards.run_node_cards(gh, boom, log=lambda m: None)
        self.assertIn('Not run: OSError', gh.posted()[0])


if __name__ == '__main__':
    unittest.main()
