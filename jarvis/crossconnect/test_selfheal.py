import os
import tempfile
import unittest

import selfheal


def status(services, restartable=(), paused=False):
    return {'ok': True, 'services': services, 'restartable': list(restartable), 'paused': paused}


class SelfHealTest(unittest.TestCase):
    def setUp(self):
        self.replies, self.calls, self.sent, self.state = {}, [], [], {}

    def call(self, node, cmd, args):
        self.calls.append((node, cmd, args))
        return self.replies.get((node, cmd), {'ok': True})

    def round(self):
        return selfheal.check_once(self.call, self.state, self.sent.append, log=lambda m: None)

    def restarts(self):
        return [c for c in self.calls if c[1] == 'restart_service']

    def test_restarts_only_after_two_down_rounds(self):
        self.replies[('junk', 'status')] = status({'searxng': 'down'}, ['searxng'])
        self.replies[('rig', 'status')] = {'ok': False, 'error': 'unreachable: URLError'}
        self.assertIsNone(self.round())
        self.assertEqual(self.restarts(), [])
        self.assertIn('restarted it', self.round())
        self.assertEqual(self.restarts(), [('junk', 'restart_service', {'name': 'searxng'})])
        self.assertEqual(len(self.sent), 1)

    def test_recovery_resets_count(self):
        self.replies[('junk', 'status')] = status({'searxng': 'down'}, ['searxng'])
        self.round()
        self.replies[('junk', 'status')] = status({'searxng': 'up'}, ['searxng'])
        self.round()
        self.replies[('junk', 'status')] = status({'searxng': 'down'}, ['searxng'])
        self.round()
        self.assertEqual(self.restarts(), [])

    def test_not_restartable_or_paused_never_restarted(self):
        self.replies[('junk', 'status')] = status({'mosquitto': 'down'})
        self.replies[('rig', 'status')] = status({'jellyfin': 'down'}, ['jellyfin'], paused=True)
        for _ in range(3):
            self.round()
        self.assertEqual(self.restarts(), [])

    def test_one_restart_per_round_and_refusal_reported(self):
        self.replies[('rig', 'status')] = status({'jellyfin': 'down'}, ['jellyfin'])
        self.replies[('junk', 'status')] = status({'searxng': 'down'}, ['searxng'])
        self.replies[('rig', 'restart_service')] = {'ok': False, 'error': 'rate limit'}
        self.round()
        self.assertIn('refused: rate limit', self.round())
        self.assertEqual(len(self.restarts()), 1)

    def test_old_agent_without_restartable_does_nothing(self):
        self.replies[('junk', 'status')] = {'ok': True, 'services': {'searxng': 'down'}}
        self.round()
        self.round()
        self.assertEqual(self.restarts(), [])

    def test_state_file_roundtrip(self):
        path = os.path.join(tempfile.mkdtemp(), 'selfheal.json')
        self.assertEqual(selfheal.load_state(path), {})
        selfheal.save_state(path, {'junk:searxng': 1})
        self.assertEqual(selfheal.load_state(path), {'junk:searxng': 1})


if __name__ == '__main__':
    unittest.main()
