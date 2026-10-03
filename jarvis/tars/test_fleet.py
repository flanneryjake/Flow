import unittest

import fleet

FINE = {'ok': True, 'node': 'junk', 'cmd': 'status', 'services': {'home-assistant': 'up', 'searxng': 'up'},
        'memory': {'total_gb': 7.9, 'free_gb': 2.8}, 'disk_free_gb': 146.0, 'uptime_h': 22.7, 'paused': False}


class FleetTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.replies = {}
        fleet._pending.clear()

        def fake(node, cmd, args=None):
            self.calls.append((node, cmd, args or {}))
            return self.replies.get((node, cmd), dict(FINE, node=node, cmd=cmd))
        fleet._call = fake

    def test_status_phrases(self):
        for said, node in [("How's the rig?", 'rig'), ('check on the junk laptop', 'junk'),
                           ('5060 status', '5060'), ('how is tars', '5060'), ('status of the backup laptop', 'junk')]:
            self.calls.clear()
            self.assertIn('is fine, sir', fleet.act(said), said)
            self.assertEqual(self.calls, [(node, 'status', {})], said)

    def test_leaves_online_questions_to_actions(self):
        self.assertIsNone(fleet.act('is the rig online'))
        self.assertIsNone(fleet.act('how is the rig online'))
        self.assertIsNone(fleet.act('play some jazz'))
        self.assertEqual(self.calls, [])

    def test_all_machines(self):
        reply = fleet.act('how are the machines')
        self.assertEqual([c[0] for c in self.calls], ['5060', 'rig', 'junk'])
        self.assertIn('The rig is fine', reply)

    def test_down_and_unreachable(self):
        self.replies[('junk', 'status')] = dict(FINE, services={'searxng': 'down', 'mosquitto': 'up'})
        self.assertIn('needs a look, sir: searxng is down; mosquitto up', fleet.act('check on the junk laptop'))
        self.replies[('rig', 'status')] = {'ok': False, 'error': 'unreachable: URLError'}
        self.assertIn('probably asleep', fleet.act("how's the rig"))

    def test_restart_needs_confirm(self):
        reply = fleet.act('restart searxng')
        self.assertIn('Say "confirm"', reply)
        self.assertEqual(self.calls, [])
        self.assertIn('Done, sir', fleet.act('confirm'))
        self.assertEqual(self.calls, [('junk', 'restart_service', {'name': 'searxng'})])
        self.assertIsNone(fleet.act('confirm'))   # used up

    def test_restart_named_machine_and_refusal(self):
        fleet.act('restart home assistant on the junk laptop')
        self.replies[('junk', 'restart_service')] = {'ok': False, 'error': "'home-assistant' is not a restartable service on junk"}
        self.assertIn('refused, sir', fleet.act('Confirm.'))

    def test_restart_pc(self):
        self.assertIn('Restart the junk laptop?', fleet.act('restart the junk laptop'))
        self.assertIn('restarts in one minute', fleet.act('yes, do it'))
        self.assertEqual(self.calls, [('junk', 'restart_pc', {})])

    def test_restart_tars_is_a_service_not_a_pc(self):
        fleet.act('restart tars')
        fleet.act('confirm')
        self.assertEqual(self.calls, [('5060', 'restart_service', {'name': 'tars'})])

    def test_cancel_and_expiry(self):
        fleet.act('restart the junk laptop')
        self.assertIn('Cancelled', fleet.act('never mind'))
        fleet.act('restart the junk laptop')
        fleet._pending['at'] -= 120
        self.assertIsNone(fleet.act('confirm'))
        self.assertEqual(self.calls, [])

    def test_unknown_service_is_not_ours(self):
        self.assertIsNone(fleet.act('restart the movie'))
        self.assertIsNone(fleet.act('restart'))


if __name__ == '__main__':
    unittest.main()
