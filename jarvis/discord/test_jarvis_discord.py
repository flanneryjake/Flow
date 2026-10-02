import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jarvis_discord as jd  # noqa: E402


def issue(n, status, extra=()):
    return {'number': n, 'title': f'Card {n}', 'labels': [{'name': f'status:{status}'}] + [{'name': x} for x in extra]}


class Tests(unittest.TestCase):
    def test_split_short(self):
        self.assertEqual(jd.split_message('hi'), ['hi'])
        self.assertEqual(jd.split_message(''), ['(no reply)'])

    def test_split_long_on_lines(self):
        text = '\n'.join('line %d %s' % (i, 'x' * 50) for i in range(100))
        parts = jd.split_message(text)
        self.assertTrue(all(len(p) <= jd.MAX_LEN for p in parts))
        self.assertEqual(' '.join(' '.join(parts).split()), ' '.join(text.split()))

    def test_split_no_spaces(self):
        parts = jd.split_message('a' * 5000)
        self.assertEqual([len(p) for p in parts], [1900, 1900, 1200])

    def test_clean_prompt(self):
        self.assertEqual(jd.clean_prompt('<@123> what is   up', 123), 'what is up')
        self.assertEqual(jd.clean_prompt('hey <@!123>', 123), 'hey')

    def test_should_answer(self):
        self.assertTrue(jd.should_answer(True, False, None, True, False))
        self.assertTrue(jd.should_answer(False, True, 'general', True, False))
        self.assertTrue(jd.should_answer(False, False, 'Jarvis', True, False))
        self.assertFalse(jd.should_answer(False, False, 'general', True, False))
        self.assertFalse(jd.should_answer(False, True, 'jarvis', False, False))
        self.assertFalse(jd.should_answer(True, True, 'jarvis', True, True))

    def test_card_events(self):
        issues = [issue(1, 'staged'), issue(2, 'needs-jake'), issue(3, 'approved'), dict(issue(4, 'staged'), pull_request={})]
        ev, seen = jd.card_events(issues, {})
        self.assertEqual([(n, s) for n, s, _ in ev], [(1, 'staged'), (2, 'needs-jake')])
        self.assertEqual(seen, {'1': 'staged', '2': 'needs-jake'})
        ev, seen2 = jd.card_events(issues, seen)
        self.assertEqual(ev, [])
        ev, _ = jd.card_events([issue(1, 'needs-jake')], seen2)
        self.assertEqual([(n, s) for n, s, _ in ev], [(1, 'needs-jake')])

    def test_read_token_strips_prefix(self):
        os.environ['DISCORD_BOT_TOKEN'] = 'Bot abc.def '
        try:
            self.assertEqual(jd.read_token(), 'abc.def')
        finally:
            del os.environ['DISCORD_BOT_TOKEN']


if __name__ == '__main__':
    unittest.main()
