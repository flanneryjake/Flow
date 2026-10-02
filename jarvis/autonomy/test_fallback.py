"""Tests for fallback.py. Run: python -m unittest test_fallback"""
import os
import tempfile
import unittest

import autotask
import fallback
from fakeghq import ago, fake_ghq

LIMITED = """[19:01] claimed #40
[19:02] Claude: You've hit your usage limit
[19:02] WAITING: usage resets 8pm (America/New_York)
"""
RIG_LOCAL = ['status:approved', 'machine:rig', 'model:local']


class FallbackTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop('JARVIS_AUTO_OFF', None)
        os.environ['JARVIS_STATE_DIR'] = tempfile.mkdtemp()
        os.environ['JARVIS_LOG_DIR'] = tempfile.mkdtemp()

    def test_detect(self):
        self.assertEqual(fallback.detect(LIMITED), (True, '8pm (America/New_York)'))
        self.assertEqual(fallback.detect('all fine\n**Run on rig: done**'), (False, ''))
        self.assertFalse(fallback.detect(LIMITED + '[20:01] claimed #41\n')[0])   # it reset and ran again

    def test_allowed_only_local_research_or_next_project_on_rig(self):
        st = {'mode': 'local-fallback', 'machine': 'rig'}
        self.assertTrue(fallback.allowed({'title': 'Research PETG vs PLA', 'labels': RIG_LOCAL}, st))
        self.assertTrue(fallback.allowed({'title': 'Draft the next project brief', 'labels': RIG_LOCAL}, st))
        self.assertFalse(fallback.allowed({'title': 'Research PETG vs PLA',
                                           'labels': ['status:approved', 'machine:rig']}, st))   # needs Claude
        self.assertFalse(fallback.allowed({'title': 'Fix the hub login', 'labels': RIG_LOCAL}, st))
        self.assertFalse(fallback.allowed({'title': 'Research X', 'labels': ['machine:any', 'model:local']}, st))
        self.assertTrue(fallback.allowed({'title': 'Fix the hub login', 'labels': []}, {'mode': 'normal'}))

    def test_limit_with_empty_lane_files_drafts_and_stays_awake(self):
        g = fake_ghq([{'number': 1, 'title': 'Card sorter robot', 'labels': ['type:idea', 'status:staged'],
                       'created_at': ago(5)}])
        autotask._ghq = lambda: g
        r = fallback.check(LIMITED, 'rig', ghq=g)
        self.assertEqual(r['mode'], 'local-fallback')
        self.assertEqual(r['resets'], '8pm (America/New_York)')
        self.assertEqual(len(r['filed']), 2)
        titles = [f['title'] for f in r['filed']]
        self.assertTrue(titles[0].startswith('Draft the next project brief'))
        self.assertEqual(titles[1], 'Research (local model): Card sorter robot')
        for f in r['filed']:
            labels = g.issues[f['number']]['labels']
            self.assertIn('model:local', labels)
            self.assertIn('machine:rig', labels)
        self.assertFalse(r['can_sleep'])
        self.assertIn('stays awake', r['say'])
        self.assertEqual(fallback.load_state()['mode'], 'local-fallback')

    def test_second_check_does_not_refile(self):
        g = fake_ghq(); autotask._ghq = lambda: g
        fallback.check(LIMITED, 'rig', ghq=g)
        r = fallback.check(LIMITED, 'rig', ghq=g)
        self.assertEqual(r['filed'], [])
        self.assertEqual(len(g.issues), 1)

    def test_kill_switch_files_nothing_and_rig_may_sleep(self):
        os.environ['JARVIS_AUTO_OFF'] = '1'
        g = fake_ghq(); autotask._ghq = lambda: g
        r = fallback.check(LIMITED, 'rig', ghq=g)
        self.assertEqual((r['filed'], r['can_sleep']), ([], True))
        self.assertIn('may sleep', r['say'])

    def test_back_to_normal_clears_state(self):
        g = fake_ghq([{'number': 2, 'title': 'Tidy', 'labels': ['status:approved', 'machine:rig']}])
        fallback.save_state({'mode': 'local-fallback'})
        r = fallback.check('[20:01] claimed #2', 'rig', ghq=g)
        self.assertEqual(r['mode'], 'normal')
        self.assertEqual(r['queued'], [2])
        self.assertFalse(r['can_sleep'])
        self.assertEqual(fallback.load_state()['mode'], 'normal')


if __name__ == '__main__':
    unittest.main()
