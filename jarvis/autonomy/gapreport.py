"""Weekly gap report: where the Workers lost time last week, and what to upgrade.

From the cards touched in the last 7 days and the Worker run comments (ghq.log_run's runmeta), it computes:
  * failed / timed-out / held runs by failure reason (usage limit, by-hand step, missing tool, auth, ...)
  * slowest card kinds (average minutes per run)
  * most-held kinds (cards that ended needs-jake or were filed staged for Jake)
and writes a markdown report, plus up to 3 upgrade proposals and 1 new-skill proposal as card drafts. With
--file-cards the drafts are filed through autotask.propose (deduped, so an open upgrade card is not filed
again next week).

    import gapreport
    stats = gapreport.analyze(issues, comments, now)       # pure
    md = gapreport.report(stats); cards = gapreport.proposals(stats)

CLI: python gapreport.py [--days 7] [--out report.md] [--file-cards]
     No cards are filed while JARVIS_AUTO_OFF=1.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import clock  # noqa: E402
from dedupe import BY_HAND  # noqa: E402
from scheduler import kind_of  # noqa: E402

RUNMETA = re.compile(r'<!-- jarvis:runmeta (\{.*?\}) -->')
BAD = ('failed', 'timeout', 'needs-jake')
# reason -> pattern, checked in order against the run comment (summary + log tail)
REASONS = [
    ('usage-limit', re.compile(r'usage limit|usage resets|rate limit', re.I)),
    ('needs-pin', re.compile(r'NEEDS PIN', re.I)),
    ('by-hand step', BY_HAND),
    ('auth/token', re.compile(r'\b(401|403|unauthori[sz]ed|forbidden|token (?:expired|invalid|is not set)|'
                              r'not logged in|login required|credentials?)\b', re.I)),
    ('missing tool', re.compile(r"ModuleNotFoundError|No module named|is not recognized as|command not found|"
                                r"not installed|No such file|cannot find (?:the )?(?:path|file)", re.I)),
    ('network', re.compile(r'ConnectionError|Connection refused|getaddrinfo|unreachable|URLError|DNS|'
                           r'tailscale', re.I)),
    ('local model', re.compile(r'ollama|CUDA|out of memory|\bOOM\b|model not found', re.I)),
    ('printer', re.compile(r'printer (?:offline|not responding|busy)|flashforge.*(?:refused|offline)', re.I)),
    ('vague card', re.compile(r'Split the card|add detail|unclear|ambiguous|not enough (?:detail|info)', re.I)),
]
UPGRADES = {
    'usage-limit': ('Upgrade: send research and drafting to the local model before the Claude limit',
                    'Runs stopped on the Claude usage limit. Route research/drafting cards to the rig\'s local '
                    'model first (fallback.py lane) and keep Claude for code and fixes.'),
    'needs-pin': ('Upgrade: split PIN steps out of routine cards',
                  'Runs stopped with NEEDS PIN. File the PIN step as its own pin card up front so the routine part '
                  'finishes on its own.'),
    'by-hand step': ('Upgrade: mark by-hand steps owner:jake when the card is filed',
                     'Runs failed on a step only a person can do. Detect it at filing time (dedupe.BY_HAND) and '
                     'file that step as an owner:jake card instead of letting a Worker fail on it.'),
    'auth/token': ('Upgrade: check tokens before a Worker claims a card',
                   'Runs failed on auth. Add a token/login preflight to the Worker so it reports once in Machine '
                   'Health instead of failing card after card.'),
    'missing tool': ('Upgrade: install check for tools the cards keep needing',
                     'Runs failed on a missing module or command. List them and add them to install.ps1 (ask_once).'),
    'network': ('Upgrade: retry and report network failures in the Worker',
                'Runs failed on network errors. Retry with backoff and mark the machine unhealthy instead of the card.'),
    'local model': ('Upgrade: guard local model runs (memory, model present)',
                    'Runs failed in the local model. Check the model exists and VRAM is free before starting.'),
    'printer': ('Upgrade: printer status check before print cards run',
                'Print cards failed with the printer offline. Gate them on the printer watchdog status.'),
    'vague card': ('Upgrade: require a Goal line and done-check on new cards',
                   'Runs stopped because the card was unclear. autotask should ask for "Goal:" and "Done when:".'),
}


def _labels(i):
    return [l['name'] if isinstance(l, dict) else l for l in i.get('labels', [])]


def reason_of(text, outcome=''):
    for name, rx in REASONS:
        if rx.search(text or ''):
            return name
    return 'timeout' if outcome == 'timeout' else 'other'


def kind_label(issue):
    """A [Tag] in the title if there is one ("qol", "income"), else scheduler.kind_of."""
    m = re.match(r'\s*\[([^\]]+)\]', issue.get('title') or '')
    return m.group(1).strip().lower() if m else kind_of(issue)


def analyze(issues, comments, now=None, days=7):
    """issues: cards updated in the window. comments: comments in the window (with issue_url). Pure."""
    now = now or clock.now_utc()
    since = now - dt.timedelta(days=days)
    by_num = {i['number']: i for i in issues if 'pull_request' not in i and 'health' not in _labels(i)}
    reasons = collections.Counter()
    reason_cards = collections.defaultdict(list)
    minutes = collections.defaultdict(list)
    held = collections.Counter()
    held_cards = collections.defaultdict(list)
    runs = collections.Counter()
    kinds_seen = collections.Counter()
    held_nums = set()
    for c in comments:
        created = clock.parse(c.get('created_at'))
        if created and created < since:
            continue
        num = int((c.get('issue_url') or '0').rsplit('/', 1)[1])
        issue = by_num.get(num)
        if not issue:
            continue
        body = c.get('body') or ''
        m = RUNMETA.search(body)
        if m:
            meta = json.loads(m.group(1))
            outcome = meta.get('outcome') or ''
            runs[outcome] += 1
            if outcome in BAD:
                r = reason_of(body, outcome)
                reasons[r] += 1
                if num not in reason_cards[r]:
                    reason_cards[r].append(num)
            if outcome == 'done' and meta.get('minutes'):
                minutes[kind_label(issue)].append(float(meta['minutes']))
        if body.startswith('**Needs Jake:**') or body.startswith('Filed for Jake') or \
                (m and json.loads(m.group(1)).get('outcome') == 'needs-jake'):
            held_nums.add(num)
    for num, issue in by_num.items():
        kinds_seen[kind_label(issue)] += 1
        if 'status:needs-jake' in _labels(issue):
            held_nums.add(num)
    for num in held_nums:
        k = kind_label(by_num[num])
        held[k] += 1
        held_cards[k].append(num)
    closed = [i for i in by_num.values() if i.get('state') == 'closed'
              and (clock.parse(i.get('closed_at')) or now) >= since]
    slow = sorted(((k, sum(v) / len(v), len(v)) for k, v in minutes.items()), key=lambda t: -t[1])
    return {
        'since': clock.iso(since), 'until': clock.iso(now), 'days': days,
        'cards': len(by_num), 'closed': len(closed), 'runs': dict(runs),
        'failure_reasons': reasons.most_common(), 'reason_cards': {k: v[:5] for k, v in reason_cards.items()},
        'slowest_kinds': [{'kind': k, 'avg_minutes': round(a, 1), 'runs': n} for k, a, n in slow[:5]],
        'most_held': held.most_common(5), 'held_cards': {k: sorted(v)[:5] for k, v in held_cards.items()},
        'kinds': kinds_seen.most_common(),
    }


def report(stats):
    s = stats
    bad = sum(n for k, n in s['runs'].items() if k in BAD)
    out = [f'# Jarvis gap report, {s["since"][:10]} to {s["until"][:10]}', '',
           f'{s["cards"]} cards touched, {s["closed"]} closed, {sum(s["runs"].values())} Worker runs '
           f'({s["runs"].get("done", 0)} done, {bad} failed/timeout/held).', '', '## Failures by reason', '']
    if s['failure_reasons']:
        out += ['| Reason | Runs | Cards |', '|---|---|---|']
        out += [f'| {r} | {n} | {" ".join("#%d" % c for c in s["reason_cards"].get(r, []))} |'
                for r, n in s['failure_reasons']]
    else:
        out.append('None.')
    out += ['', '## Slowest kinds (done runs)', '']
    if s['slowest_kinds']:
        out += ['| Kind | Avg min | Runs |', '|---|---|---|']
        out += [f'| {k["kind"]} | {k["avg_minutes"]} | {k["runs"]} |' for k in s['slowest_kinds']]
    else:
        out.append('No timed runs.')
    out += ['', '## Most held kinds', '']
    if s['most_held']:
        out += ['| Kind | Held | Cards |', '|---|---|---|']
        out += [f'| {k} | {n} | {" ".join("#%d" % c for c in s["held_cards"].get(k, []))} |'
                for k, n in s['most_held']]
    else:
        out.append('Nothing held.')
    return '\n'.join(out) + '\n'


def proposals(stats):
    """Up to 3 upgrade drafts and 1 new-skill draft: [{'title', 'body', 'card_type', 'kind'}]. Pure."""
    s = stats
    ups = []
    for reason, n in s['failure_reasons']:
        if reason in UPGRADES:
            title, why = UPGRADES[reason]
            cards = ' '.join('#%d' % c for c in s['reason_cards'].get(reason, []))
            ups.append({'title': title, 'body': f'Goal: {why}\n\nSeen {n} time(s) last week: {cards}.',
                        'card_type': 'task', 'kind': 'upgrade'})
    if s['slowest_kinds'] and s['slowest_kinds'][0]['avg_minutes'] >= 20:
        k = s['slowest_kinds'][0]
        ups.append({'title': f'Upgrade: make {k["kind"]} cards faster',
                    'body': f'Goal: cut the average {k["kind"]} run ({k["avg_minutes"]} min over {k["runs"]} '
                            'runs) by splitting the cards, caching setup, or sending them to the local model.',
                    'card_type': 'task', 'kind': 'upgrade'})
    if s['most_held']:
        k, n = s['most_held'][0]
        cards = ' '.join('#%d' % c for c in s['held_cards'].get(k, []))
        ups.append({'title': f'Upgrade: fewer holds on {k} cards',
                    'body': f'Goal: {n} {k} card(s) waited on Jake last week ({cards}). Find what they asked and '
                            'answer it once in the guardrail policy or the card template.',
                    'card_type': 'task', 'kind': 'upgrade'})
    seen, upgrades = set(), []
    for u in ups:
        if u['title'] not in seen:
            seen.add(u['title'])
            upgrades.append(u)
    out = upgrades[:3]
    base = s['most_held'][0][0] if s['most_held'] else (s['kinds'][0][0] if s['kinds'] else None)
    if base:
        out.append({'title': f'New skill: {base} playbook',
                    'body': f'Goal: write a skill (SKILL.md plus any scripts) for the steps {base} cards repeat, so '
                            'the Worker follows it instead of rediscovering them each run. Use last week\'s '
                            f'{base} cards as examples.',
                    'card_type': 'idea', 'kind': 'skill'})
    return out


def gather(ghq, days=7, now=None):
    now = now or clock.now_utc()
    since = clock.iso(now - dt.timedelta(days=days))
    issues = ghq.paged(ghq.repo_path(f'/issues?state=all&since={since}'))
    comments = ghq.paged(ghq.repo_path(f'/issues/comments?since={since}'))
    return analyze(issues, comments, now, days)


def file_cards(drafts, propose=None):
    if os.environ.get('JARVIS_AUTO_OFF') == '1':
        return [{'title': d['title'], 'number': None, 'status': 'off', 'why': 'JARVIS_AUTO_OFF=1'} for d in drafts]
    if propose is None:
        import autotask
        propose = autotask.propose
    out = []
    for d in drafts:
        n, status, why = propose(d['title'], d['body'], card_type=d['card_type'], source='gapreport')
        out.append({'title': d['title'], 'number': n, 'status': status, 'why': why})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--out', help='write the markdown report here')
    ap.add_argument('--file-cards', action='store_true', help='file the proposals through autotask')
    a = ap.parse_args(argv)
    import autotask
    stats = gather(autotask._ghq(), a.days)
    md = report(stats)
    drafts = proposals(stats)
    md += '\n## Proposals\n\n' + '\n'.join(f'- **{d["title"]}**: {d["body"].splitlines()[0]}' for d in drafts) + '\n'
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(md)
    print(md)
    if a.file_cards:
        print(json.dumps(file_cards(drafts), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
