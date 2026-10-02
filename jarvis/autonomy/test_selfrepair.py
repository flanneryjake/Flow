"""Tests for selfrepair.py. Run: python -m unittest test_selfrepair"""
import os
import tempfile
import unittest

import autotask
import selfrepair
from fakeghq import fake_ghq

HEALTH = ("Remote Control down, restart failed: run 'claude remote-control' in C:\\Jarvis; "
          "Worker idle with 3 approved cards waiting, last claim 2h ago; Down: hub, ollama")
WORKER_TAIL = """[2026-10-01 10:02:11] claimed #41
Traceback (most recent call last):
  File "C:\\Jarvis\\agent.py", line 88, in run
ModuleNotFoundError: No module named 'serial'
[2026-10-01 10:02:12] token=""" + "ghp_" + "x" * 32 + """ used
"""


class SelfRepairTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop('JARVIS_AUTO_OFF', None)
        os.environ['JARVIS_LOG_DIR'] = tempfile.mkdtemp()

    def test_health_alert_symptoms(self):
        titles = [d['title'] for d in selfrepair.proposals(HEALTH, 'rig')]
        self.assertEqual(titles, ['diagnose: rig remote-control down, restart failed',
                                  'diagnose: rig worker idle with cards waiting',
                                  'diagnose: rig services down: hub, ollama'])

    def test_lone_restart_files_nothing(self):
        self.assertEqual(selfrepair.proposals('Remote Control: restarted just now (task)', 'rig'), [])
        self.assertEqual(selfrepair.proposals('[10:00] Remote Control restarted (direct).\nRemote Control: UP', 'rig'), [])

    def test_restart_loop_does(self):
        text = '\n'.join(['Remote Control restarted (task).'] * 3)
        self.assertEqual([d['title'] for d in selfrepair.proposals(text, 'homebase')],
                         ['diagnose: homebase remote-control restart loop'])

    def test_needs_jake_and_usage_are_not_failures(self):
        self.assertEqual(selfrepair.proposals('Needs Jake: #12 asks which printer; WAITING: usage resets 8pm', 'rig'), [])

    def test_worker_tail_error_and_redaction(self):
        d = selfrepair.proposals(WORKER_TAIL, 'rig')
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]['title'], 'diagnose: rig worker error: modulenotfounderror: no module named serial')
        self.assertNotIn('ghp_', d[0]['body'])

    def test_machine_guessed_from_health_title(self):
        self.assertTrue(selfrepair.proposals('Health: homebase\nCould not read the Tasks board: 401')[0]['title']
                        .startswith('diagnose: homebase cannot read the task queue'))

    def test_run_files_labelled_cards_and_dedupes(self):
        g = fake_ghq(); autotask._ghq = lambda: g
        first = selfrepair.run(HEALTH, 'rig')
        self.assertEqual([r['status'] for r in first], ['approved'] * 3)
        n = first[0]['number']
        for lab in ('self-repair', 'agent:claude', 'p1'):
            self.assertIn(lab, g.issues[n]['labels'])
        again = selfrepair.run(HEALTH, 'rig')
        self.assertEqual([r['status'] for r in again], ['duplicate'] * 3)
        self.assertEqual(len(g.issues), 3)

    def test_kill_switch_files_nothing(self):
        os.environ['JARVIS_AUTO_OFF'] = '1'
        g = fake_ghq(); autotask._ghq = lambda: g
        self.assertEqual({r['status'] for r in selfrepair.run(HEALTH, 'rig')}, {'off'})
        self.assertEqual(g.issues, {})


if __name__ == '__main__':
    unittest.main()
