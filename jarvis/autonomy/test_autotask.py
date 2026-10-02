"""Tests for autotask.py against an in-memory fake of ghq. Run: python -m unittest test_autotask"""
import os
import sys
import tempfile
import unittest

import autotask
from fakeghq import ago, fake_ghq, runmeta


class AutotaskTest(unittest.TestCase):
    def setUp(self):
        for k in ('JARVIS_AUTO_OFF', 'JARVIS_AUTO_PER_DAY', 'JARVIS_AUTO_DEPTH', 'JARVIS_DEDUPE_JACCARD'):
            os.environ.pop(k, None)
        self.logdir = tempfile.mkdtemp()
        os.environ['JARVIS_LOG_DIR'] = self.logdir

    def use(self, g):
        autotask._ghq = lambda: g

    def test_loop_meta_card_is_dropped(self):
        g = fake_ghq(); self.use(g)
        n, status, why = autotask.propose('Pause re-approval of card #23')
        self.assertEqual((n, status), (None, 'dropped'))
        self.assertEqual(g.issues, {}) if isinstance(g.issues, dict) else self.assertFalse(g.issues)
        self.assertEqual(g.posted, [])

    def test_new_card_refusal_is_dropped(self):
        g = fake_ghq(); self.use(g)
        g.new_card = lambda *a, **k: None
        n, status, why = autotask.propose('Test ff_printer.py against the real printer')
        self.assertEqual((n, status), (None, 'dropped'))
        self.assertEqual(g.posted, [])

    def test_ghq_side_duplicate_is_not_commented(self):
        g = fake_ghq(); self.use(g)
        g.new_card = lambda *a, **k: 41
        g.LAST_NEW_CARD_WAS_NEW = False
        n, status, why = autotask.propose('Test ff_printer.py against the real printer')
        self.assertEqual((n, status), (41, 'duplicate'))
        self.assertEqual(g.posted, [])

    def test_burst_sees_card_filed_moments_ago(self):
        g = fake_ghq(); self.use(g)
        a = autotask.propose('Test ff_printer.py against the real printer')
        b = autotask.propose('Test ff_printer.py against the real printer')
        self.assertEqual(b[1], 'duplicate')
        self.assertEqual(b[0], a[0])

    def test_free_card_is_approved_and_wakes(self):
        g = fake_ghq(); self.use(g)
        n, status, why = autotask.propose('Test ff_printer.py against the real printer', machine='rig')
        self.assertEqual(status, 'approved')
        self.assertIn('auto', g.issues[n]['labels'])
        self.assertIn('status:approved', g.issues[n]['labels'])
        self.assertEqual(g.woke, 1)
        self.assertTrue(g.posted[0][1].startswith('Auto-approved'))

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
        self.assertIn('sched:deferred', g.issues[n]['labels'])

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

    def test_recently_closed_duplicate_is_rejected_and_logged(self):
        g = fake_ghq([{'number': 3, 'title': '[QOL] Printer queue watchdog', 'state': 'closed',
                       'closed_at': ago(3), 'updated_at': ago(3)}])
        self.use(g)
        n, status, why = autotask.propose('Printer queue watchdog')
        self.assertEqual((n, status), (3, 'duplicate'))
        self.assertEqual(len(g.issues), 1)
        with open(os.path.join(self.logdir, 'autotask-rejects.jsonl')) as f:
            self.assertIn('Printer queue watchdog', f.read())

    def test_old_closed_card_does_not_block(self):
        g = fake_ghq([{'number': 3, 'title': 'Printer queue watchdog', 'state': 'closed',
                       'closed_at': ago(40), 'updated_at': ago(40)}])
        self.use(g)
        self.assertEqual(autotask.propose('Printer queue watchdog')[1], 'approved')

    def test_near_identical_goal_is_duplicate(self):
        g = fake_ghq([{'number': 4, 'title': 'Summarize printer errors',
                       'body': 'Goal: read the Flashforge log and list every jam and heater error this week'}])
        self.use(g)
        n, status, why = autotask.propose('Summarize the printer errors',
                                          'Goal: read the Flashforge log and list every jam and heater error this week.')
        self.assertEqual((n, status), (4, 'duplicate'))
        self.assertIn('near-identical', why)

    def test_selftest_that_failed_by_hand_is_rejected(self):
        g = fake_ghq([{'number': 5, 'title': 'GATE TEST 2: printer selftest', 'state': 'closed',
                       'closed_at': ago(40), 'updated_at': ago(40)}],
                     comments={5: [runmeta('failed', extra='Jake has to load the filament by hand first.')]})
        self.use(g)
        n, status, why = autotask.propose('GATE TEST 3: printer selftest')
        self.assertEqual((n, status), (5, 'rejected'))
        self.assertEqual(len(g.issues), 1)

    def test_extra_labels_pass_through(self):
        g = fake_ghq(); self.use(g)
        n = autotask.propose('Look at the rig log', extra_labels=['agent:claude'])[0]
        self.assertIn('agent:claude', g.issues[n]['labels'])

    def test_check_cli_needs_no_token(self):
        os.environ.pop('GITHUB_TASKS_TOKEN', None)
        self.assertEqual(autotask.main(['check', 'Buy filament']), 0)


if __name__ == '__main__':
    unittest.main()
