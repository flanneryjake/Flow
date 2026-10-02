"""Stop Jarvis filing the same card twice (28 duplicates had to be closed by hand on 2026-10-01).

Before autotask.propose files a card, `check` compares it with every open card and every card closed in the
last 7 days. It is rejected when:
  * the normalized title matches (case, punctuation and a leading [Tag] ignored), or
  * the title + goal tokens are near-identical (Jaccard >= 0.8, JARVIS_DEDUPE_JACCARD), or
  * it is a selftest and the same selftest already failed for a by-hand reason (someone has to plug, press,
    log in, load paper...). Re-filing it just fails again; it needs Jake, not another run.
Every rejection is logged (stderr, plus JARVIS_LOG_DIR/autotask-rejects.jsonl when that folder exists).

    import dedupe
    hit = dedupe.check(title, body, issues, comments_of=fn)   # None, or (number, 'duplicate'|'rejected', why)

CLI: python dedupe.py "title" [--body TEXT]   (checks against the live queue, files nothing)
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import clock  # noqa: E402

WINDOW_DAYS = 7
STOP = set('a an the and or of to for in on at by with from into is are be it this that its as via then '
           'jarvis card task'.split())
SELFTEST = re.compile(r'\b(self[- ]?test|gate test|smoke test|test (?:run|pass)|selftest)\b', re.I)
# A step only a person can do. Shared with scheduler.py (selftests that need a human go last).
BY_HAND = re.compile(
    r"\b(by hand|manual(?:ly)?(?: step)?|physical(?:ly)?|in person|needs? (?:a )?(?:human|person)|"
    r"jake (?:has|needs|must) to|needs jake to|(?:un)?plug|press (?:the )?(?:\w+ )?button|power[- ]?cycle|"
    r"load (?:the )?(?:filament|paper|spool)|insert (?:the |a |an )?(?:card|cable|usb|sd card|disk|drive|paper|key)|scan the qr|approve (?:it )?on (?:the |his )?phone|"
    r"2fa prompt|log ?in (?:on|to) (?:the )?\w+ (?:by hand|yourself)|turn (?:it|the \w+) on)\b", re.I)
FAILED = re.compile(r'"outcome": "(failed|timeout|needs-jake)"|\*\*Needs Jake:\*\*|NEEDS PIN|Stopped after two failed',
                    re.I)


def norm_title(title):
    t = re.sub(r'^\s*(\[[^\]]*\]\s*)+', '', title or '')
    return re.sub(r'[^a-z0-9]+', ' ', t.lower()).strip()


def tokens(text):
    return {w for w in re.findall(r'[a-z0-9]+', (text or '').lower()) if len(w) > 1 and w not in STOP}


def goal_of(body):
    """The card's goal: a 'Goal:' line if there is one, else the first paragraph (no HTML comments, no
    'Spawned from')."""
    body = re.sub(r'<!--.*?-->', '', body or '', flags=re.S)
    m = re.search(r'^\s*\**goal\**\s*:\s*(.+)$', body, re.I | re.M)
    if m:
        return m.group(1)[:300]
    for para in re.split(r'\n\s*\n', body):
        para = para.strip()
        if para and not para.startswith('Spawned from'):
            return para[:300]
    return ''


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def is_selftest(title):
    return bool(SELFTEST.search(title or ''))


def failed_by_hand(texts, labels=()):
    """True when these texts (issue body + comments) show a failed/held run whose reason is a by-hand step."""
    blob = '\n'.join(t or '' for t in texts)
    if 'owner:jake' in labels and FAILED.search(blob):
        return True
    return bool(FAILED.search(blob) and BY_HAND.search(blob))


def _threshold():
    try:
        return float(os.environ.get('JARVIS_DEDUPE_JACCARD', 0.8))
    except ValueError:
        return 0.8


def _label_names(i):
    return [l['name'] if isinstance(l, dict) else l for l in i.get('labels', [])]


def recent(issues, now=None, days=WINDOW_DAYS):
    """Open issues plus those closed within `days` (pull requests and health issues dropped)."""
    now = now or clock.now_utc()
    cutoff = now - dt.timedelta(days=days)
    out = []
    for i in issues:
        if 'pull_request' in i or 'health' in _label_names(i):
            continue
        if i.get('state', 'open') == 'closed':
            closed = clock.parse(i.get('closed_at') or i.get('updated_at'))
            if not closed or closed < cutoff:
                continue
        out.append(i)
    return out


def check(title, body='', issues=(), now=None, comments_of=None, selftest_history=()):
    """None when the card is new; else (existing_number, 'duplicate' | 'rejected', reason).

    issues: open + recently closed cards (filtered again here to the 7-day window).
    comments_of: optional fn(number) -> [comment bodies], used only for selftest history.
    selftest_history: older closed cards to search for earlier selftest failures (any age)."""
    want = norm_title(title)
    want_tok = tokens(title) | tokens(goal_of(body))
    limit = _threshold()
    for i in recent(issues, now):
        state = 'open' if i.get('state', 'open') == 'open' else 'closed'
        if want and norm_title(i.get('title')) == want:
            return i['number'], 'duplicate', f'same title as #{i["number"]} ({state})'
        have = tokens(i.get('title')) | tokens(goal_of(i.get('body')))
        if len(want_tok) >= 3 and len(have) >= 3:
            score = jaccard(want_tok, have)
            if score >= limit:
                return i['number'], 'duplicate', f'near-identical goal to #{i["number"]} ({state}, J={score:.2f})'
    if is_selftest(title):
        key = tokens(re.sub(r'\d+', ' ', norm_title(title)))
        seen = set()
        for i in list(issues) + list(selftest_history):
            if i['number'] in seen or 'pull_request' in i or not is_selftest(i.get('title')):
                continue
            seen.add(i['number'])
            if jaccard(key, tokens(re.sub(r'\d+', ' ', norm_title(i.get('title'))))) < 0.6:
                continue
            texts = [i.get('body') or ''] + (list(comments_of(i['number'])) if comments_of else [])
            if failed_by_hand(texts, _label_names(i)):
                return i['number'], 'rejected', (f'selftest #{i["number"]} already failed for a by-hand reason; '
                                                 'it needs Jake, not another run')
    return None


def log_reject(title, number, status, reason, source='autotask'):
    rec = {'at': clock.iso(clock.now_utc()), 'source': source, 'title': title, 'existing': number,
           'status': status, 'reason': reason}
    print(f'dedupe: not filing "{title}": {reason}', file=sys.stderr)
    folder = os.environ.get('JARVIS_LOG_DIR') or (r'C:\Jarvis\logs' if os.path.isdir(r'C:\Jarvis\logs') else '')
    if folder and os.path.isdir(folder):
        try:
            with open(os.path.join(folder, 'autotask-rejects.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec) + '\n')
        except OSError as e:
            print(f'dedupe: could not write reject log: {e}', file=sys.stderr)
    return rec


_CACHE = {'at': 0.0, 'issues': None, 'src': None}
CACHE_S = 120  # one open + recently-closed listing per process every 2 minutes, not one per proposed card


def gather(ghq, title, now=None):
    """Fetch what `check` needs from the queue: (issues, comments_of, selftest_history). The open and recently
    closed list is cached for CACHE_S seconds per process; remember() adds cards filed meanwhile."""
    import time
    now = now or clock.now_utc()
    if _CACHE['issues'] is None or _CACHE['src'] is not ghq or time.time() - _CACHE['at'] > CACHE_S:
        since = clock.iso(now - dt.timedelta(days=WINDOW_DAYS))
        issues = ghq.paged(ghq.repo_path('/issues?state=open'))
        issues += ghq.paged(ghq.repo_path(f'/issues?state=closed&since={since}'))
        _CACHE.update(at=time.time(), issues=issues, src=ghq)
    issues = list(_CACHE['issues'])
    history = ghq.paged(ghq.repo_path('/issues?state=closed')) if is_selftest(title) else []

    def comments_of(n):
        return [c.get('body') or '' for c in ghq.paged(ghq.repo_path(f'/issues/{n}/comments'))]
    return issues, comments_of, history


def remember(issue):
    """Add a card just filed to the cached list, so the next proposal in the same burst sees it."""
    if _CACHE['issues'] is not None:
        _CACHE['issues'].append(issue)


def forget_cache():
    _CACHE.update(at=0.0, issues=None, src=None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('title')
    ap.add_argument('--body', default='')
    a = ap.parse_args(argv)
    import autotask
    issues, comments_of, history = gather(autotask._ghq(), a.title)
    hit = check(a.title, a.body, issues, comments_of=comments_of, selftest_history=history)
    print(json.dumps({'new': hit is None} if hit is None else
                     {'new': False, 'existing': hit[0], 'status': hit[1], 'reason': hit[2]}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
