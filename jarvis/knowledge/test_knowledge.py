import os
import re
import tempfile
import unittest

import knowledge as k


class Cards(unittest.TestCase):
    def setUp(self):
        self.cards = k.load()

    def test_every_card_is_complete(self):
        self.assertGreaterEqual(len(self.cards), 10)
        for c in self.cards:
            self.assertTrue(c.get('summary'), c['name'])
            self.assertIsNotNone(c['re'], c['name'])
            self.assertLess(len(c['body']), 1200, c['name'])

    def test_no_money_figures_or_secrets(self):
        for c in self.cards:
            text = c['body'] + c['summary']
            self.assertNotRegex(text, r'\$\d', c['name'])
            self.assertNotRegex(text.lower(), r'ghp_|github_pat_|password:|api[_ ]key', c['name'])

    def test_no_boston_voice(self):
        for c in self.cards:
            self.assertNotRegex((c['body'] + c['summary']).lower(), r'\bkid\b|wicked|southie', c['name'])

    def test_matching(self):
        cases = {
            "what's on my to-do list": 'todo',
            'should I go to Walmart for groceries': 'groceries',
            'start the next print plate': 'printing',
            'is the rig asleep': 'machines',
            'how much can I spend before payday': 'budget',
            'write a relapse handout': 'clinical',
            'tailor my resume for this job': 'jobs',
        }
        for q, want in cases.items():
            got = [c['name'] for c in k.select(q, self.cards)]
            self.assertIn(want, got, q)

    def test_small_talk_gets_nothing(self):
        for q in ('good morning', 'tell me a joke', 'what time is sunset'):
            self.assertEqual(k.context_for(q, self.cards), '', q)

    def test_select_caps(self):
        q = 'rig print plates budget groceries to-do card'
        self.assertLessEqual(len(k.select(q, self.cards)), 2)


class Modelfile(unittest.TestCase):
    def test_block_added_then_replaced(self):
        block = k.summary()
        once = k.merge_system('You are JARVIS.', block)
        self.assertTrue(once.startswith('You are JARVIS.'))
        twice = k.merge_system(once, block.replace('short form', 'SHORT'))
        self.assertEqual(twice.count(k.START), 1)
        self.assertIn('SHORT', twice)

    def test_modelfile_shape(self):
        mf = k.modelfile('jarvis-pre-skills', 'Persona """quoted"""', k.summary())
        self.assertTrue(mf.startswith('FROM jarvis-pre-skills\nSYSTEM """'))
        self.assertEqual(mf.count('"""'), 2)


class Install(unittest.TestCase):
    def test_install_backs_up_changed(self):
        with tempfile.TemporaryDirectory() as dest:
            first = dict(k.install_skills(dest))
            self.assertIn('jarvis-pc-fleet', first)
            self.assertTrue(all(v == 'added' for v in first.values()))
            p = os.path.join(dest, 'jarvis-pc-fleet', 'SKILL.md')
            with open(p, 'w') as f:
                f.write('old')
            again = dict(k.install_skills(dest))
            self.assertEqual(again['jarvis-pc-fleet'], 'updated')
            self.assertEqual(again['jarvis-dispatch'], 'same')
            self.assertTrue(any(n.startswith('SKILL.md.bak-') for n in os.listdir(os.path.dirname(p))))


class Skills(unittest.TestCase):
    def test_skill_front_matter(self):
        for name in os.listdir(k.SKILLS):
            p = os.path.join(k.SKILLS, name, 'SKILL.md')
            if not os.path.isfile(p):
                continue
            with open(p, encoding='utf-8') as f:
                t = f.read()
            m = re.match(r'^---\nname: (.+)\ndescription: (.+)\n---\n', t)
            self.assertIsNotNone(m, name)
            self.assertEqual(m.group(1), name)
            self.assertNotRegex(m.group(2), r'[<>]', name)


if __name__ == '__main__':
    unittest.main()
