"""Spend the daily auto-approval cap on the cards that matter most.

The cap (JARVIS_AUTO_PER_DAY, 100, resets 8 PM US Eastern) ran out in 30 minutes at 25 because cards ran in
filing order. This ranks the queue and splits it into today / deferred:

  1. income work (templates, store, products, listings) and fix / self-repair work
  2. research
  3. chores (everything else)
  4. selftests (plain ones before the ones that need a human step, which go last)
  p0/p1 labels and age break ties inside a band.

Small pure-text cards for the laptop (no code, no commands, short body) are folded into one combined card, so
five one-paragraph writing jobs cost one slot instead of five. Whatever doesn't fit goes to after the reset.

Candidates: open cards that are status:approved (not claimed, not pin, not owner:jake), plus staged cards that
autotask deferred when the cap ran out (label sched:deferred).

    import scheduler
    p = scheduler.plan(issues, cap_left=40)            # pure, no network
    scheduler.apply(ghq, p)                             # labels sched:today / sched:deferred, files the batch

CLI: python scheduler.py [--cap-left N] [--apply] [--json]
     Dry run by default. --apply is refused while JARVIS_AUTO_OFF=1.
Workers: skip cards labelled sched:deferred or sched:batched (see README).
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import clock  # noqa: E402
from dedupe import BY_HAND, SELFTEST  # noqa: E402

TODAY, DEFERRED, BATCHED = 'sched:today', 'sched:deferred', 'sched:batched'
SCHED_LABELS = {
    TODAY: ('0e8a16', 'Scheduler: run before the next cap reset'),
    DEFERRED: ('cccccc', 'Scheduler: waits for the next cap reset (8 PM ET); Workers skip it'),
    BATCHED: ('cccccc', 'Folded into a combined laptop card; Workers skip it'),
}
BAND_SCORE = {'income': 100, 'fix': 100, 'research': 60, 'chore': 30, 'selftest': 15, 'selftest-human': 0}
PRIO_BONUS = {'p0': 30, 'p1': 15, 'p2': 0}

KIND_RULES = [
    ('fix', re.compile(r'\b(fix|repair|self[- ]repair|diagnose|broken|bug|crash(?:es|ing)?|failing|fails|error|'
                       r'restore|recover|unstick|stuck)\b', re.I)),
    ('income', re.compile(r'\[income\]|\b(income|template|store(?:front)?|shop|etsy|gumroad|shopify|ebay|product|'
                          r'listing|sell|sales|pricing|revenue|client|customer|case study)\b', re.I)),
    ('research', re.compile(r'\b(research|investigate|compare|survey|evaluate|look into|find out|explore|options '
                            r'for|proposal|brief)\b', re.I)),
]
CODE_HINT = re.compile(r'```|`[^`]+`|[A-Za-z]:\\|\b\w+\.(?:py|ps1|js|json|sh|bat)\b|\b(?:pip|npm|git|python|'
                       r'powershell|winget|ollama|ssh|curl)\b', re.I)


def _labels(i):
    return [l['name'] if isinstance(l, dict) else l for l in i.get('labels', [])]


def _status(i):
    if i.get('state') == 'closed':
        return 'done'
    for n in _labels(i):
        if n.startswith('status:'):
            return n.split(':', 1)[1]
    return 'inbox'


def kind_of(issue):
    """income | fix | research | chore | selftest | selftest-human. Title decides; the body only matters for
    'does this selftest need a person'."""
    title = issue.get('title') or ''
    if 'self-repair' in _labels(issue):
        return 'fix'
    if SELFTEST.search(title):
        return 'selftest-human' if BY_HAND.search(title + '\n' + (issue.get('body') or '')) else 'selftest'
    for kind, rx in KIND_RULES:
        if rx.search(title):
            return kind
    return 'chore'


def score(issue, now=None):
    names = _labels(issue)
    s = BAND_SCORE[kind_of(issue)]
    s += max([PRIO_BONUS[n] for n in names if n in PRIO_BONUS] or [0])
    created = clock.parse(issue.get('created_at'))
    if created:
        s += min(10, max(0, ((now or clock.now_utc()) - created).days))   # +1 a day waited, at most +10
    return s


def is_laptop_text(issue, max_body=None):
    """A short card for the laptop with no code or commands in it: safe to batch with others."""
    max_body = max_body or int(os.environ.get('JARVIS_BATCH_MAX_BODY', 800))
    text = (issue.get('title') or '') + '\n' + (issue.get('body') or '')
    return ('machine:laptop' in _labels(issue) and len(issue.get('body') or '') <= max_body
            and not CODE_HINT.search(text) and not kind_of(issue).startswith('selftest'))


def candidates(issues):
    out = []
    for i in issues:
        names = _labels(i)
        if i.get('state', 'open') != 'open' or 'pull_request' in i or 'health' in names:
            continue
        if 'pin' in names or 'owner:jake' in names or BATCHED in names or any(n.startswith('claimed:') for n in names):
            continue
        st = _status(i)
        if st == 'approved' or (st == 'staged' and DEFERRED in names):
            out.append(i)
    return out


def plan(issues, cap_left, now=None, batch_max=None):
    """Pure core. Returns {'cap_left', 'reset_at', 'today': [unit], 'deferred': [unit]}; a unit is
    {'numbers', 'title', 'kind', 'score', 'batch'}. A batch unit costs one slot."""
    now = now or clock.now_utc()
    batch_max = batch_max or int(os.environ.get('JARVIS_BATCH_MAX', 8))
    cards = sorted(candidates(issues), key=lambda i: (-score(i, now), i.get('created_at') or '', i['number']))
    text_cards = [i for i in cards if is_laptop_text(i)]
    units = []
    for k in range(0, len(text_cards), batch_max):
        group = text_cards[k:k + batch_max]
        if len(group) >= 2:   # a lone leftover stays a normal card
            units.append({'numbers': [i['number'] for i in group], 'kind': 'batch', 'batch': True,
                          'score': max(score(i, now) for i in group),
                          'title': f'Laptop batch: {len(group)} short text cards',
                          'members': [{'number': i['number'], 'title': i['title'], 'body': i.get('body') or ''}
                                      for i in group]})
    batched = {n for u in units for n in u['numbers']}
    for i in cards:
        if i['number'] not in batched:
            units.append({'numbers': [i['number']], 'title': i['title'], 'kind': kind_of(i),
                          'score': score(i, now), 'batch': False})
    units.sort(key=lambda u: -u['score'])  # stable: keeps age order inside equal scores
    cap_left = max(0, cap_left)
    return {'cap_left': cap_left, 'reset_at': clock.iso(clock.next_reset(now)),
            'today': units[:cap_left], 'deferred': units[cap_left:]}


def batch_body(unit):
    lines = ['Several short text jobs folded into one card by the scheduler so they cost one slot of the daily '
             'cap. Do each one and post each answer as its own section, headed with its card number. Then close '
             'each original card with a link to this one.', '']
    for m in unit['members']:
        lines += [f'### #{m["number"]}: {m["title"]}', '', (m['body'] or '(no details)').strip(), '']
    return '\n'.join(lines).strip()


def _relabel(ghq, number, add=(), remove=()):
    issue = ghq.api('GET', ghq.repo_path(f'/issues/{number}'))
    names = [n for n in ghq.label_names(issue) if n not in remove]
    names += [n for n in add if n not in names]
    ghq.api('PUT', ghq.repo_path(f'/issues/{number}/labels'), {'labels': names})


def _ensure_labels(ghq):
    for name, (color, desc) in SCHED_LABELS.items():
        try:
            ghq.api('POST', ghq.repo_path('/labels'), {'name': name, 'color': color, 'description': desc})
        except ghq.GitHubError as e:
            if '422' not in str(e):
                raise


def apply(ghq, p, propose=None):
    """Write the plan to the queue. Returns a list of what changed. `propose` defaults to autotask.propose."""
    if os.environ.get('JARVIS_AUTO_OFF') == '1':
        return ['JARVIS_AUTO_OFF=1: nothing applied']
    if propose is None:
        import autotask
        propose = autotask.propose
    _ensure_labels(ghq)
    done = []
    for unit in p['today']:
        if unit['batch']:
            n, status, why = propose(f'{unit["title"]} (#{", #".join(map(str, unit["numbers"]))})',
                                     batch_body(unit), machine='laptop', source='scheduler',
                                     extra_labels=[TODAY])
            done.append(f'batch #{n} {status}: {", ".join(map(str, unit["numbers"]))}')
            if status in ('duplicate', 'rejected'):
                continue
            for m in unit['numbers']:
                ghq.set_status(m, 'snoozed', extra_add=[BATCHED], extra_remove=[TODAY, DEFERRED])
                ghq.comment(m, f'Folded into #{n} by the scheduler; it is answered there.')
            continue
        n = unit['numbers'][0]
        issue = ghq.api('GET', ghq.repo_path(f'/issues/{n}'))
        if ghq.status_of(issue) == 'staged':   # cap-deferred card: its turn has come
            ghq.set_status(n, 'approved', extra_add=[TODAY, 'auto'], extra_remove=[DEFERRED])
            ghq.comment(n, f'Auto-approved by scheduler at {ghq.now_iso()}: deferred by the daily cap, now in '
                           f"today's slots (score {unit['score']}).")
            done.append(f'#{n} approved for today')
        else:
            _relabel(ghq, n, add=[TODAY], remove=[DEFERRED])
            done.append(f'#{n} today')
    for unit in p['deferred']:
        for n in unit['numbers']:
            _relabel(ghq, n, add=[DEFERRED], remove=[TODAY])
            done.append(f'#{n} deferred to {p["reset_at"]}')
    if p['today']:
        ghq.wake()
    return done


def render(p):
    out = [f'Cap left until {p["reset_at"]}: {p["cap_left"]}', '', 'Today:']
    for u in p['today']:
        out.append(f'  {u["score"]:>4}  {u["kind"]:<15} ' + ' '.join(f'#{n}' for n in u['numbers']) + f'  {u["title"]}')
    out += ['', f'Deferred ({len(p["deferred"])}):']
    for u in p['deferred']:
        out.append(f'  {u["score"]:>4}  {u["kind"]:<15} ' + ' '.join(f'#{n}' for n in u['numbers']) + f'  {u["title"]}')
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cap-left', type=int, help='slots left today (default: cap minus auto cards since the reset)')
    ap.add_argument('--apply', action='store_true', help='write labels and file the batch card')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    import autotask
    ghq = autotask._ghq()
    issues = ghq.paged(ghq.repo_path('/issues?state=open'))
    cap_left = a.cap_left
    if cap_left is None:
        cap_left = autotask._limit('JARVIS_AUTO_PER_DAY', autotask.DEFAULT_PER_DAY) - autotask.auto_count_today(ghq)
    p = plan(issues, cap_left)
    print(json.dumps(p, indent=1) if a.json else render(p))
    if a.apply:
        for line in apply(ghq, p):
            print(line)
    else:
        print('\n(dry run; add --apply to label the cards)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
