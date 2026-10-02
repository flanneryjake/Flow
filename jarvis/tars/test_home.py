import datetime as dt
import os
import tempfile
import unittest

import home

URL = 'http://100.90.201.22:8123/api/webhook/jarvis_tars_home_test'


class HomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        home.SNAPSHOT = os.path.join(self.tmp, 'ha.json')
        alarm = (dt.datetime.now().astimezone() + dt.timedelta(hours=7)).isoformat()
        self.assertEqual(home.save_snapshot({
            'home_url': URL, 'listening': True, 'next_alarm_bedroom': alarm, 'next_alarm_kitchen': 'unknown',
            'echos': {'media_player.kitchen': {'state': 'playing', 'title': 'Back in Black', 'artist': 'AC/DC'},
                      'media_player.master_bedroom': {'state': 'idle'}},
            'devices': {'light.desk': 'Desk lamp: off'}}), '')

    def test_rejects_foreign_webhook(self):
        self.assertTrue(home.save_snapshot({'home_url': 'http://evil.example/hook'}))

    def test_parse(self):
        self.assertEqual(home.parse('Hey Jarvis, play jazz in the bedroom'),
                         ('alexa', 'media_player.master_bedroom', 'play jazz'))
        self.assertEqual(home.parse('can you turn off the lights'), ('alexa', 'media_player.kitchen',
                                                                     'turn off the lights'))
        self.assertEqual(home.parse('set an alarm for 6 am'), ('alexa', 'media_player.kitchen', 'set an alarm for 6 am'))
        self.assertEqual(home.parse('jarvis stop listening'), ('listening', False, None))
        self.assertEqual(home.parse('order more filament')[0], None)        # not a home command: model + PIN rule
        self.assertEqual(home.parse('play nothing and order paper towels')[0], 'blocked')
        self.assertEqual(home.parse('how is the weather'), (None, None, None))
        self.assertEqual(home.parse('what are you playing at'), (None, None, None))

    def test_act_posts_to_ha(self):
        sent = []
        reply = home.act('play some jazz', post=lambda u, b: sent.append((u, b)), log=lambda m: None)
        self.assertEqual(sent, [(URL, {'kind': 'alexa', 'echo': 'media_player.kitchen', 'command': 'play some jazz'})])
        self.assertTrue(reply.startswith('Right away'))

    def test_act_survives_ha_down(self):
        def boom(u, b):
            raise OSError('down')
        self.assertIn("didn't answer", home.act('pause', post=boom, log=lambda m: None))

    def test_facts(self):
        f = home.facts("when's my alarm")
        self.assertIn('Next alarm on the bedroom Echo', f)
        self.assertIn('"Back in Black" by AC/DC', f)
        self.assertIn('Desk lamp: off', f)
        self.assertEqual(home.facts('capital of australia'), '')


if __name__ == '__main__':
    unittest.main()
