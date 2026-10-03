import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import lookup


class Searx(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({'results': [{'title': 'Sunset Plymouth', 'content': 'Sunset today is 6:21 PM.',
                                        'url': 'https://example.com'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)


class LookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), Searx)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        lookup.SEARX_URL = 'http://127.0.0.1:%d' % cls.srv.server_port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_scrub_removes_boston_voice(self):
        self.assertEqual(lookup.scrub("Anytime, kid."), "Anytime.")
        self.assertEqual(lookup.scrub("That's wicked fast, buddy."), "That's fast.")
        self.assertEqual(lookup.scrub("Kidney beans are cheap."), "Kidney beans are cheap.")

    def test_wants(self):
        self.assertEqual(lookup.wants('LOOKUP: sunset Plymouth', 'q'), ('lookup', 'sunset Plymouth'))
        self.assertEqual(lookup.wants('ASK_CLAUDE: plan my week', 'q'), ('claude', 'plan my week'))
        self.assertEqual(lookup.wants("Sorry, I don't have internet access.", 'when is sunset'),
                         ('lookup', 'when is sunset'))
        self.assertEqual(lookup.wants('Canberra, sir.', 'q'), (None, None))

    def test_direct(self):
        self.assertEqual(lookup.direct('Jarvis, ask Claude what a Pi 5 draws'), ('claude', 'what a Pi 5 draws'))
        self.assertEqual(lookup.direct('ask claude to order filament'), (None, None))   # that one files a card
        self.assertEqual(lookup.direct('ask gemini about PETG temps'), ('gemini', 'PETG temps'))

    def test_private_never_leaves_for_web_or_gemini(self):
        with mock.patch.object(lookup, 'searx') as s, mock.patch.object(lookup, 'gemini') as g, \
                mock.patch.object(lookup, 'claude', return_value='Use the clinic protocol.') as c:
            src, facts = lookup.find('lookup', 'buprenorphine dose for a patient', private=True, log=lambda m: None)
        self.assertEqual(src, 'claude')
        s.assert_not_called()
        g.assert_not_called()
        c.assert_called_once()

    def test_resolve_uses_search_and_answers_again(self):
        seen = {}

        def answer_with(note):
            seen['note'] = note
            return 'Sunset is at 6:21 this evening, kid.'
        out = lookup.resolve('when is sunset', 'LOOKUP: sunset Plymouth MA', answer_with, log=lambda m: None)
        self.assertIn('6:21 PM', seen['note'])
        self.assertEqual(out, 'Sunset is at 6:21 this evening.')

    def test_resolve_falls_back_when_everything_fails(self):
        with mock.patch.object(lookup, 'searx', side_effect=OSError), \
                mock.patch.object(lookup, 'gemini', side_effect=RuntimeError), \
                mock.patch.object(lookup, 'claude', side_effect=RuntimeError):
            out = lookup.resolve('who won', "I don't know.", lambda n: 'x', log=lambda m: None)
        self.assertIn('tell Claude to look into it', out)
        self.assertNotIn("don't know", out)

    def test_plain_answer_passes_through(self):
        self.assertEqual(lookup.resolve('capital of australia', 'Canberra, sir.', lambda n: 'x',
                                        log=lambda m: None), 'Canberra, sir.')

    def test_direct_claude_answer_is_spoken_as_is(self):
        with mock.patch.object(lookup, 'claude', return_value='About 2.5 to 3 watts at idle.\n'):
            out = lookup.resolve('ask Claude what a Pi 5 draws at idle', 'whatever',
                                 lambda n: "I haven't caught that question.", log=lambda m: None)
        self.assertEqual(out, 'About 2.5 to 3 watts at idle.')

    def test_claude_prompt_is_one_line(self):
        seen = {}

        def fake_run(args, **kw):
            seen['prompt'] = args[2]
            return mock.Mock(returncode=0, stdout='ok', stderr='')
        with mock.patch.object(lookup.shutil, 'which', return_value='/bin/true'), \
                mock.patch.object(lookup.subprocess, 'run', side_effect=fake_run):
            lookup.claude('line one\nline two')
        self.assertNotIn('\n', seen['prompt'])
        self.assertIn('line one line two', seen['prompt'])


    def test_exact_fact_questions_get_checked(self):
        asked = []
        with mock.patch.object(lookup, 'find', side_effect=lambda k, q, **kw: asked.append(q) or ('searx', 'x')):
            lookup.resolve('how many championships do the celtics have', 'Eighteen, two ahead of the Lakers.',
                           lambda note: 'Eighteen, sir.', log=lambda m: None)
            lookup.resolve('i am so tired', 'Early night, then.', lambda note: '', log=lambda m: None)
        self.assertEqual(asked, ['how many championships do the celtics have'])


    def test_recent_questions_get_the_year(self):
        asked = []
        with mock.patch.object(lookup, 'find', side_effect=lambda k, q, **kw: asked.append(q) or ('searx', 'x')):
            lookup.resolve('who won the most recent super bowl', 'LOOKUP: most recent super bowl winner',
                           lambda note: 'ok', log=lambda m: None, today='Friday October 02 2026')
        self.assertEqual(asked, ['most recent super bowl winner 2026'])


if __name__ == '__main__':
    unittest.main()
