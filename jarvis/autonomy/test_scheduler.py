"""Tests for scheduler.py. Run: python -m unittest test_scheduler"""
import contextlib
import io
import os
import tempfile
import unittest

import autotask
import scheduler
from fakeghq import ago, fake_ghq


def card(n, title, labels=('status:approved', 'machine:rig'), body='', days=1):
    return {'number': n, 'title': title, 'labels': list(labels), 'body': body, 'created_at': ago(days)}


LAPTOP = ('status:approved', 'machine:laptop')


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop('JARVIS_AUTO_OFF', None)
        os.environ['JARVIS_LOG_DIR'] = tempfile.mkdtemp()

    def test_kinds(self):
        self.assertEqual(scheduler.kind_of(card(1, '[Income] Automated Case Study Generator')), 'income')
        self.assertEqual(scheduler.kind_of(card(1, 'Make the Etsy template pack')), 'income')
        self.assertEqual(scheduler.kind_of(card(1, 'diagnose: rig worker idle with cards waiting')), 'fix')
        self.assertEqual(scheduler.kind_of(card(1, 'Research cheaper filament options')), 'research')
        self.assertEqual(scheduler.kind_of(card(1, 'Tidy the downloads folder')), 'chore')
        self.assertEqual(scheduler.kind_of(card(1, 'Printer selftest', body='Jake has to load the filament')),
                         'selftest-human')

    def test_order_income_fix_research_chore_selftest(self):
        issues = [card(1, 'GATE TEST 2: plug in the USB hub by hand'), card(2, 'Tidy the downloads folder'),
                  card(3, 'Research cheaper filament options'), card(4, 'Fix the hub login loop'),
                  card(5, '[Income] Fieldwork product pipeline')]
        p = scheduler.plan(issues, cap_left=10)
        order = [u['numbers'][0] for u in p['today']]
        self.assertEqual(order[-1], 1)
        self.assertEqual(set(order[:2]), {4, 5})
        self.assertEqual(order[2:4], [3, 2])

    def test_priority_label_breaks_ties(self):
        p = scheduler.plan([card(1, 'Tidy A'), card(2, 'Tidy B', labels=('status:approved', 'p0'))], cap_left=5)
        self.assertEqual(p['today'][0]['numbers'], [2])

    def test_cap_defers_the_rest(self):
        issues = [card(i, f'Tidy thing {i}') for i in range(1, 6)] + [card(9, '[Income] Store page')]
        p = scheduler.plan(issues, cap_left=2)
        self.assertEqual(len(p['today']), 2)
        self.assertEqual(p['today'][0]['numbers'], [9])
        self.assertEqual(len(p['deferred']), 4)
        self.assertTrue(p['reset_at'].endswith('Z'))

    def test_laptop_text_cards_are_batched_into_one_slot(self):
        issues = [card(1, 'Write a thank-you note for the vet', LAPTOP, 'Two warm sentences.'),
                  card(2, 'Draft a packing list for the trip', LAPTOP, 'Weekend, cold weather.'),
                  card(3, 'Run the backup script', LAPTOP, 'python backup.py on C:\\Jarvis'),
                  card(4, 'Tidy the rig desktop')]
        p = scheduler.plan(issues, cap_left=2)
        batches = [u for u in p['today'] + p['deferred'] if u['batch']]
        self.assertEqual(len(batches), 1)
        self.assertEqual(sorted(batches[0]['numbers']), [1, 2])
        self.assertIn('#1: Write a thank-you note', scheduler.batch_body(batches[0]))
        singles = {u['numbers'][0] for u in p['today'] + p['deferred'] if not u['batch']}
        self.assertEqual(singles, {3, 4})

    def test_single_laptop_card_is_not_batched(self):
        p = scheduler.plan([card(1, 'Write a note', LAPTOP)], cap_left=5)
        self.assertFalse(p['today'][0]['batch'])

    def test_candidates_skip_pin_claimed_staged_and_health(self):
        issues = [card(1, 'a', ('status:approved', 'pin')), card(2, 'b', ('status:approved', 'claimed:rig')),
                  card(3, 'c', ('status:staged',)), card(4, 'Health: rig', ('health',)),
                  card(5, 'e', ('status:staged', 'sched:deferred')), card(6, 'f')]
        self.assertEqual(sorted(i['number'] for i in scheduler.candidates(issues)), [5, 6])

    def test_apply_labels_promotes_and_batches(self):
        g = fake_ghq([card(1, 'Write a note', LAPTOP, 'short'), card(2, 'Write a toast', LAPTOP, 'short'),
                      card(3, '[Income] Store page', ('status:staged', 'sched:deferred', 'machine:rig')),
                      card(4, 'Tidy the rig desktop')])
        autotask._ghq = lambda: g
        p = scheduler.plan(list(g.issues.values()), cap_left=2)
        changes = scheduler.apply(g, p)
        self.assertIn('status:approved', g.issues[3]['labels'])
        self.assertIn('sched:today', g.issues[3]['labels'])
        self.assertNotIn('sched:deferred', g.issues[3]['labels'])
        self.assertIn('sched:deferred', g.issues[4]['labels'])
        batch = max(g.issues)
        self.assertTrue(g.issues[batch]['title'].startswith('Laptop batch: 2'))
        self.assertIn('machine:laptop', g.issues[batch]['labels'])
        for n in (1, 2):
            self.assertIn('sched:batched', g.issues[n]['labels'])
            self.assertIn('status:snoozed', g.issues[n]['labels'])
        self.assertTrue(changes)
        self.assertEqual(g.woke, 2)   # one from the batch card's auto-approval, one from apply

    def test_apply_respects_kill_switch(self):
        os.environ['JARVIS_AUTO_OFF'] = '1'
        g = fake_ghq([card(1, 'Tidy')])
        p = scheduler.plan(list(g.issues.values()), cap_left=0)
        self.assertEqual(scheduler.apply(g, p), ['JARVIS_AUTO_OFF=1: nothing applied'])
        self.assertEqual(g.issues[1]['labels'], ['status:approved', 'machine:rig'])

    def test_cli_dry_run(self):
        g = fake_ghq([card(1, 'Tidy')])
        autotask._ghq = lambda: g
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(scheduler.main(['--cap-left', '1']), 0)
        self.assertIn('dry run', out.getvalue())
        self.assertNotIn('sched:today', g.issues[1]['labels'])


if __name__ == '__main__':
    unittest.main()
