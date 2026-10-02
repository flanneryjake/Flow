"""Tests for dedupe.py and clock.py. Run: python -m unittest test_dedupe"""
import datetime as dt
import unittest

import clock
import dedupe
from fakeghq import ago, runmeta

UTC = dt.timezone.utc


class DedupeTest(unittest.TestCase):
    def test_title_normalization_ignores_tag_case_punctuation(self):
        issues = [{'number': 9, 'title': '[QOL] Smart "Do Not Disturb" mode!', 'labels': []}]
        self.assertEqual(dedupe.check('smart do not disturb mode', '', issues)[:2], (9, 'duplicate'))

    def test_different_cards_pass(self):
        issues = [{'number': 9, 'title': 'MTG bulk value flagger', 'body': 'Goal: flag cards over $1'}]
        self.assertIsNone(dedupe.check('Printer watchdog', 'Goal: poll the printer every 5 minutes', issues))

    def test_jaccard_threshold(self):
        a = dedupe.tokens('read the flashforge log and list every jam error')
        self.assertEqual(dedupe.jaccard(a, a), 1.0)
        self.assertLess(dedupe.jaccard(a, dedupe.tokens('write a poem about printers')), 0.2)

    def test_closed_long_ago_is_ignored(self):
        issues = [{'number': 1, 'title': 'Same', 'state': 'closed', 'closed_at': ago(8)}]
        self.assertIsNone(dedupe.check('Same', '', issues))
        issues[0]['closed_at'] = ago(6)
        self.assertEqual(dedupe.check('Same', '', issues)[0], 1)

    def test_goal_line_wins_over_first_paragraph(self):
        self.assertEqual(dedupe.goal_of('Background stuff\n\nGoal: do X\nSpawned from #3'), 'do X')
        self.assertEqual(dedupe.goal_of('<!-- jarvis:meta {} -->\nFirst para\n\nSecond'), 'First para')

    def test_selftest_without_by_hand_failure_is_allowed(self):
        hist = [{'number': 4, 'title': 'Printer selftest', 'state': 'closed', 'closed_at': ago(30)}]
        comments = {4: [runmeta('failed', extra='Traceback: KeyError status')]}
        self.assertIsNone(dedupe.check('Printer selftest 2', '', [], comments_of=lambda n: comments.get(n, []),
                                       selftest_history=hist))

    def test_selftest_by_hand_failure_rejected(self):
        hist = [{'number': 4, 'title': 'Printer selftest', 'state': 'closed', 'closed_at': ago(30)}]
        comments = {4: ['**Needs Jake:** press the button on the printer to accept the LAN connection']}
        hit = dedupe.check('Printer selftest 2', '', [], comments_of=lambda n: comments.get(n, []),
                           selftest_history=hist)
        self.assertEqual(hit[:2], (4, 'rejected'))

    def test_pull_requests_and_health_ignored(self):
        issues = [{'number': 1, 'title': 'Health: rig', 'labels': ['health']},
                  {'number': 2, 'title': 'X', 'pull_request': {}}]
        self.assertIsNone(dedupe.check('Health: rig', '', issues))
        self.assertIsNone(dedupe.check('X', '', issues))


class ClockTest(unittest.TestCase):
    def test_reset_is_8pm_eastern_daylight(self):
        # 2026-10-02 15:00 UTC = 11:00 EDT -> last reset 2026-10-01 20:00 EDT = 2026-10-02 00:00 UTC
        now = dt.datetime(2026, 10, 2, 15, tzinfo=UTC)
        self.assertEqual(clock.iso(clock.last_reset(now)), '2026-10-02T00:00:00Z')
        self.assertEqual(clock.iso(clock.next_reset(now)), '2026-10-03T00:00:00Z')

    def test_reset_is_8pm_eastern_standard(self):
        now = dt.datetime(2026, 12, 15, 2, tzinfo=UTC)   # 21:00 EST on the 14th
        self.assertEqual(clock.iso(clock.last_reset(now)), '2026-12-15T01:00:00Z')

    def test_just_before_reset(self):
        now = dt.datetime(2026, 10, 2, 23, 59, tzinfo=UTC)  # 19:59 EDT
        self.assertEqual(clock.iso(clock.last_reset(now)), '2026-10-02T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
