"""Tests for gapreport.py. Run: python -m unittest test_gapreport"""
import os
import tempfile
import unittest

import autotask
import gapreport
from fakeghq import ago, fake_ghq, runmeta


def week():
    issues = [
        {'number': 1, 'title': '[Income] Case study generator', 'labels': [], 'state': 'closed',
         'closed_at': ago(1), 'updated_at': ago(1)},
        {'number': 2, 'title': '[Income] Etsy listing copy', 'labels': ['status:needs-jake'], 'updated_at': ago(2)},
        {'number': 3, 'title': 'Printer selftest', 'labels': ['status:approved'], 'updated_at': ago(2)},
        {'number': 4, 'title': 'Research PETG vs PLA', 'labels': [], 'state': 'closed', 'closed_at': ago(3),
         'updated_at': ago(3)},
        {'number': 5, 'title': 'Health: rig', 'labels': ['health'], 'updated_at': ago(0)},
    ]
    comments = {
        1: [runmeta('failed', extra="ModuleNotFoundError: No module named 'fpdf'"), runmeta('done', '45.0')],
        2: [runmeta('needs-jake', extra='NEEDS PIN: publish the listing')],
        3: [runmeta('failed', extra='Jake has to load the filament by hand'),
            runmeta('failed', extra='usage limit reached'), runmeta('timeout')],
        4: [runmeta('done', '6.0')],
        5: [runmeta('failed', extra='ignored')],
    }
    return issues, comments


class GapReportTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop('JARVIS_AUTO_OFF', None)
        os.environ['JARVIS_LOG_DIR'] = tempfile.mkdtemp()
        issues, comments = week()
        self.g = fake_ghq(issues, comments)
        autotask._ghq = lambda: self.g
        self.stats = gapreport.gather(self.g)

    def test_counts(self):
        s = self.stats
        self.assertEqual(s['cards'], 4)
        self.assertEqual(s['closed'], 2)
        self.assertEqual(dict(s['failure_reasons']), {'missing tool': 1, 'needs-pin': 1, 'by-hand step': 1,
                                                      'usage-limit': 1, 'timeout': 1})
        self.assertEqual(s['slowest_kinds'][0], {'kind': 'income', 'avg_minutes': 45.0, 'runs': 1})
        self.assertEqual(s['most_held'][0], ('income', 1))
        self.assertEqual(s['held_cards']['income'], [2])

    def test_report_markdown(self):
        md = gapreport.report(self.stats)
        self.assertIn('# Jarvis gap report', md)
        self.assertIn('| missing tool | 1 | #1 |', md)
        self.assertIn('| income | 45.0 | 1 |', md)

    def test_proposals_three_upgrades_one_skill(self):
        d = gapreport.proposals(self.stats)
        self.assertEqual([x['kind'] for x in d], ['upgrade'] * 3 + ['skill'])
        self.assertEqual(d[-1]['title'], 'New skill: income playbook')

    def test_old_comments_ignored(self):
        self.g.comments[4][0]['created_at'] = ago(10)
        s = gapreport.gather(self.g)
        self.assertNotIn('research', [k['kind'] for k in s['slowest_kinds']])

    def test_file_cards_dedupes_week_to_week(self):
        d = gapreport.proposals(self.stats)
        first = gapreport.file_cards(d)
        self.assertTrue(all(r['number'] for r in first))
        again = gapreport.file_cards(d)
        self.assertEqual({r['status'] for r in again}, {'duplicate'})

    def test_kill_switch(self):
        os.environ['JARVIS_AUTO_OFF'] = '1'
        before = len(self.g.issues)
        self.assertEqual({r['status'] for r in gapreport.file_cards(gapreport.proposals(self.stats))}, {'off'})
        self.assertEqual(len(self.g.issues), before)

    def test_empty_week(self):
        s = gapreport.analyze([], [])
        self.assertIn('None.', gapreport.report(s))
        self.assertEqual(gapreport.proposals(s), [])


if __name__ == '__main__':
    unittest.main()
