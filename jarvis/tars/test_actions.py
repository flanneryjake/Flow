import json
import os
import tempfile
import unittest

import actions


def cfg_with(tmp):
    cfg = actions.config()
    cfg['media'] = {'inbox': [os.path.join(tmp, 'in')], 'movies': os.path.join(tmp, 'Movies'),
                    'tv': os.path.join(tmp, 'TV'), 'min_mb': 0}
    return cfg


STATUS = json.dumps({'Self': {'HostName': 'LAPTOP-4150EGRS', 'Online': False},
                     'Peer': {'a': {'HostName': 'DESKTOP-VLLDDM4', 'Online': False},
                              'b': {'HostName': 'DESKTOP-5VE3C77', 'Online': True}}})


class ActionsTest(unittest.TestCase):
    def setUp(self):
        actions.LOG = os.path.join(tempfile.mkdtemp(), 'actions.log')

    def test_destination(self):
        cfg = cfg_with('/m')
        self.assertEqual(actions.destination('The.Bear.S03E04.1080p.WEB-DL.x264.mkv', cfg),
                         (os.path.join('/m/TV', 'The Bear', 'Season 03'), 'The Bear - S03E04.mkv'))
        self.assertEqual(actions.destination('Dune.Part.Two.2024.2160p.UHD.BluRay.x265.mkv', cfg),
                         (os.path.join('/m/Movies', 'Dune Part Two (2024)'), 'Dune Part Two (2024).mkv'))
        self.assertIsNone(actions.destination('home_video.mp4', cfg)[0])

    def test_file_media_moves_never_overwrites(self):
        tmp = tempfile.mkdtemp()
        cfg = cfg_with(tmp)
        os.makedirs(os.path.join(tmp, 'in', 'dl'))
        for fn in ('Show.Name.S01E02.mkv', 'Show.Name.S01E02.en.srt', 'Heat.1995.mkv', 'Heat.1995.Sample.mkv',
                   'notes.txt'):
            open(os.path.join(tmp, 'in', 'dl', fn), 'w').write('x')
        os.makedirs(os.path.join(tmp, 'Movies', 'Heat (1995)'))
        open(os.path.join(tmp, 'Movies', 'Heat (1995)', 'Heat (1995).mkv'), 'w').write('old')
        moved, skipped = actions.file_media(cfg)
        self.assertEqual(moved, ['Show Name - S01E02.mkv'])
        self.assertTrue(os.path.exists(os.path.join(tmp, 'TV', 'Show Name', 'Season 01', 'Show Name - S01E02.en.srt')))
        self.assertEqual(open(os.path.join(tmp, 'Movies', 'Heat (1995)', 'Heat (1995).mkv')).read(), 'old')
        self.assertTrue(os.path.exists(os.path.join(tmp, 'in', 'dl', 'Heat.1995.mkv')))      # clash: left alone
        self.assertTrue(os.path.exists(os.path.join(tmp, 'in', 'dl', 'notes.txt')))
        self.assertIn('already there', skipped[0])

    def test_tailnet(self):
        cfg = actions.config()
        run = lambda *a: (0, STATUS)
        self.assertEqual(actions.tailnet_status('is the rig online', cfg, run), 'The rig is offline.')
        self.assertIn('Online: ', actions.tailnet_status('which machines are up', cfg, run))
        self.assertIn('answered in 12', actions.ping('ping the rig', cfg, lambda *a: (0, 'pong from rig via DERP in 12ms')))

    def test_posts_stay_on_the_tailnet(self):
        with self.assertRaises(PermissionError):
            actions._post('https://example.com/hook', {})

    def test_notify_and_routing(self):
        sent = []
        cfg = actions.config()
        self.assertEqual(actions.notify('"dinner is ready"', cfg, post=lambda u, b, h=None: sent.append(b)),
                         'Sent to your phone.')
        self.assertEqual(sent[0]['body'], 'dinner is ready')
        self.assertIsNone(actions.act('what is the capital of peru'))
        self.assertIsNone(actions.act('are the lights on'))
        self.assertTrue(actions.NOTIFY_RE.search('send a ping to my phone saying hi'))
        self.assertTrue(actions.WAKE_RE.search('jarvis wake up the rig'))


if __name__ == '__main__':
    unittest.main()
