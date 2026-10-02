"""Tests for classify.py, built from real cards on the Tasks board (2026-09-30) plus PIN cases.
Run: python -m unittest test_classify   (from jarvis/autonomy)"""
import unittest
from classify import classify

FREE = [
    ('Pi: swap local-only brain for ask_jarvis.py (rig brain) with Pi fallback', ''),
    ('Connect to the Flashforge printer, set up remote power/status, slice + start the Mault print', ''),
    ('MTG Phase 3: deck builder from owned bulk (Commander + 1v1), export to Moxfield', ''),
    ('MTG Phase 2: scanner/sorter tower (proposal: Mault or similar, parts + cost)',
     'Claude Code drafts the costed parts proposal before anything is bought.'),
    ('[Autonomy] Smart Printer Watchdog', 'Create a background loop that polls the printer queue status every 5 minutes.'),
    ('[Income] Passive Income: Automated Case Study Generator', 'format them into a standard PDF template for the storefront'),
    ("[QOL] Smart 'Do Not Disturb' mode that wakes only for critical alerts", ''),
    ('[Income] MTG Bulk Value Flagger', "Export flagged list to a 'Ready to List' folder for eBay"),
    ('[Income] Automated Fieldwork Clinical Product Pipeline',
     "Create a Notion database stage for 'Draft', 'Review', and 'Published'."),
    ('Wire Phase 1/2 output straight in', ''),
    ('Test ff_printer.py against the real printer', ''),
    ('Send the plate file to the printer', ''),
    ('Send a job to homebase to print plate 5', ''),
    ('GATE TEST 2: email Jake a test', ''),
    ('Draft the Etsy listing copy for product #1', ''),
    ('Build native Hub v3 screens instead of the Live tab', ''),
    ('[Income] Payday budget pinger: rig LLM weekly spend summary', ''),
    ('[QOL] Automated budget tracker that flags spending against next paycheck', ''),
    ('Reply to Jake with the test results', ''),
    ('Forward the printer log to homebase', ''),
    ('Send the report to Drive and progress to Notion', ''),
]
ASK_ONCE = [
    ('Move AdGuard Home to homebase (junky laptop), then drop Cloudflare from Tailscale DNS', ''),
    ('Uninstall AdGuard from the rig after 2 weeks', ''),
    ('Replace polling loops with a scheduled task', ''),
    ('Install Orca-Flashforge on homebase', ''),
]
PIN = [
    ('Publish Fieldwork Clinical product #1 on Gumroad', ''),
    ('Buy a spool of PETG filament', ''),
    ('Email the facilitator guide to Dr. Smith', ''),
    ('Post the launch announcement on Instagram', ''),
    ("Delete Jake's old photos from the drive", ''),
    ('Merge PR 9 into main', ''),
    ('Turn on Tailscale Funnel for the hub', ''),
    ('Reset the Notion API token', ''),
    ('Spend $20 on ads', ''),
    ('Reply to the landlord', ''),
    ('Forward the invoice to accounting', ''),
    ('Send Dana the proposal', ''),
    ('Send them the signed lease', ''),
    ('Anything', ''),  # via label
]


class ClassifyTest(unittest.TestCase):
    def test_free(self):
        for t, b in FREE:
            with self.subTest(t=t):
                self.assertIn(classify(t, b)[0], ('free', 'free_logged'), classify(t, b))

    def test_ask_once(self):
        for t, b in ASK_ONCE:
            with self.subTest(t=t):
                self.assertEqual(classify(t, b)[0], 'ask_once', classify(t, b))

    def test_pin(self):
        for t, b in PIN[:-1]:
            with self.subTest(t=t):
                self.assertEqual(classify(t, b)[0], 'pin', classify(t, b))
        self.assertEqual(classify('Anything', '', labels=['pin'])[0], 'pin')

    def test_training_is_logged(self):
        self.assertEqual(classify('[Autonomy] Local LLM fine-tuning pipeline for Jarvis using Jake\'s notes')[0],
                         'free_logged')

    def test_negated_title_does_not_hold(self):
        self.assertEqual(classify("Prepare product #1 but don't publish it yet")[0], 'free')

    def test_body_pin_words_are_notes_not_holds(self):
        tier, reasons = classify('Prep storefront assets', 'Later we will publish the store on Shopify.')
        self.assertEqual(tier, 'free')
        self.assertTrue(any(r.startswith('watch') for r in reasons))


if __name__ == '__main__':
    unittest.main()
