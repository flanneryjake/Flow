"""Decide which guardrail tier a task card needs before it runs.

Cards are gated by their *objective*, not by every word in them. The title says what the card is for, so a
PIN or ask-once word there holds the card. The same words in the body (background, "before anything is
bought", a step list) only add a watch note: the run itself still can't send, post, delete or buy, because
those tools are denied to unattended runs and the step stops with `NEEDS PIN: ...`. That is what lets
routine cards run without Jake while real money/publish/email/delete work still waits for the PIN.

Tiers match jarvis/guardrails/policy.json: free, free_logged, ask_once, pin (stricter wins).

    from classify import classify
    tier, reasons = classify(title, body)

CLI: python classify.py "title" ["body"]   (prints JSON)
"""
import json
import re
import sys

TIERS = ['free', 'free_logged', 'ask_once', 'pin']

# Words that turn an action into its opposite ("don't publish", "before anything is bought").
_NEGATION = re.compile(
    r"\b(no|not|never|without|don'?t|do not|before (?:anything|it|we)?\s*(?:is|are|gets?)?|until|instead of|"
    r"draft(?:s|ed|ing)?|propos(?:e|al|ed)|plan(?:s|ned)?|idea|research|review|compare|estimate|cost(?:ed)?)\b"
    r"[^.;:\n]{0,40}$", re.I)

# kind -> patterns. Each pattern should describe *doing* the thing, not mentioning it.
PIN_RULES = {
    'spend-money': [
        r'\b(buy|purchase|order|pay for|pay|checkout|subscribe to|upgrade to (?:a )?paid|renew)\b(?! (?:attention|off)\b)',
        r'\b(add|enter|use) (?:a |the |my )?(credit card|debit card|payment method)\b',
    ],
    'post-external': [
        r"\b(send|email|e-mail|text|dm|message)\b(?:(?!\bto\b)[^.\n]){0,40}\bto (?!(?:the |my |jake(?:'s|s)? )?(?:jake|me|myself|rig|homebase|hub|worker|phone|notion|github|drive|google drive|calendar|inbox|queue|board|folder|repo|printer|pi|laptop|claude|gemini|ollama|jarvis|a file|file|disk)\b)\w+",
        r'\b(email|e-mail|text|dm|message)\b (?:the |a |our |my )?(clients?|customers?|patients?|someone|people|list|subscribers)\b',
        r'\b(post|tweet|share)\b[^.\n]{0,40}?\b(?:to|on) (twitter|x|instagram|facebook|reddit|linkedin|tiktok|youtube|social|etsy|gumroad|shopify|ebay|the store)',
        r'\b(publish|go live|make (?:it )?public|launch the store|list (?:it|them|the \w+) (?:on|for sale))\b',
        r'\b(submit|send) (?:the )?(application|order|payment|form)\b',
    ],
    'delete-other': [
        r'\b(delete|wipe|erase|purge|format)\b[^.\n]{0,30}?\b(files|data|photos|emails|drive|disk|account|backups?)\b',
        r'\brm -rf\b|\bRemove-Item\b[^\n]*-Recurse',
    ],
    'credentials': [
        r'\b(change|reset|rotate|disable|revoke)\b[^.\n]{0,25}?\b(password|2fa|mfa|api key|token|tailscale auth|key expiry|defender)\b',
        r'\b(create|open|sign up for) (?:an? |the )?(new )?account\b',
    ],
    'expose-service': [
        r'\b(funnel|port[- ]forward|open (?:a )?port|expose\b[^.\n]{0,30}\binternet)\b',
    ],
    'merge-main': [
        r'\bmerge\b[^.\n]{0,30}\b(?:into |to )?main\b',
    ],
}

ASK_ONCE_RULES = {
    'scheduled-task': [r'\b(scheduled task|task scheduler|Register-ScheduledTask|startup (?:entry|folder)|windows service)\b'],
    'install-software': [r'\b(install|uninstall|winget|choco|pip install|npm install)\b'],
    'system-settings': [r'\b(firewall|power plan|registry|group policy|tailscale (?:dns|acl|settings)|override local dns)\b'],
}

FREE_LOGGED_RULES = {
    'training-data': [r'\btraining[ -]?data\b|\btraining_intake\b|\bfine[- ]?tun'],
    'model-rebuild': [r'\bollama create\b'],
    'git-claude-branch': [r'\b(push|open) (?:a )?(?:draft )?(?:pr|pull request|branch)\b'],
}


def _hits(text, rules):
    found = []
    for kind, pats in rules.items():
        for p in pats:
            for m in re.finditer(p, text, re.I):
                if _NEGATION.search(text[:m.start()][-60:]):
                    continue
                found.append((kind, m.group(0)))
                break
    return found


def classify(title, body='', labels=()):
    """Return (tier, reasons). reasons is a list of 'kind: matched words' strings, holds first."""
    title = title or ''
    body = body or ''
    labels = {l.lower() for l in labels}
    if 'pin' in labels:
        return 'pin', ['label: pin']

    pin_title = _hits(title, PIN_RULES)
    if pin_title:
        return 'pin', [f'{k}: "{w}"' for k, w in pin_title]

    ask_title = _hits(title, ASK_ONCE_RULES)
    notes = [f'watch (body, run-time guard applies) {k}: "{w}"' for k, w in _hits(body, PIN_RULES)]
    if ask_title:
        return 'ask_once', [f'{k}: "{w}"' for k, w in ask_title] + notes

    logged = _hits(title + '\n' + body, FREE_LOGGED_RULES)
    if logged:
        return 'free_logged', [f'{k}: "{w}"' for k, w in logged] + notes
    return 'free', notes


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    tier, reasons = classify(argv[0], argv[1] if len(argv) > 1 else '')
    print(json.dumps({'tier': tier, 'reasons': reasons}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
