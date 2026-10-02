"""Let Jarvis file and start its own cards without Jake approving each one.

`ghq.new_card` files every card as staged, so today every follow-up, fix and idea Jarvis thinks of waits for a
tap on the phone. This wraps it: a card whose objective is tier free or free_logged (classify.py) is filed
straight into status:approved with an `auto` label and a comment saying which rule let it through. Anything
ask_once or pin is filed staged, exactly as before, with the reason in a comment so the hub can show it.

Limits that keep it from running away (all overridable by environment variable):
  JARVIS_AUTO_PER_DAY   auto-approved cards per UTC day, default 25; after that cards are filed staged
  JARVIS_AUTO_DEPTH     how many auto cards may chain off one another, default 3
  JARVIS_AUTO_OFF       set to 1 to file everything staged (the kill switch; the hub can set it)
Duplicates (an open card with the same normalized title) are not filed again; the existing number is returned.

    import autotask
    number, status, why = autotask.propose('Test ff_printer.py against the printer', body, machine='rig',
                                           spawned_from=12)

CLI: python autotask.py propose "title" [--body TEXT] [--machine rig] [--priority p1] [--spawned-from 12]
     python autotask.py check "title" [--body TEXT]      (classify only, files nothing)
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, '..', 'ghq'), r'C:\Jarvis\ghq'):
    if p not in sys.path:
        sys.path.append(p)

from classify import classify  # noqa: E402

AUTO_LABEL = 'auto'
AUTO_LABEL_SPEC = ('0e8a16', 'Filed and approved by Jarvis under the guardrail policy')
SPAWN_RE = re.compile(r'Spawned from #(\d+)')


def _ghq():
    import ghq  # imported lazily so `check` works on a PC without the token
    return ghq


def _norm(title):
    return re.sub(r'[^a-z0-9]+', ' ', (title or '').lower()).strip()


def _limit(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def find_duplicate(title, open_issues):
    want = _norm(title)
    for i in open_issues:
        if 'pull_request' not in i and _norm(i.get('title')) == want:
            return i['number']
    return None


def auto_count_today(ghq):
    since = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT00:00:00Z')
    issues = ghq.paged(ghq.repo_path(f'/issues?state=all&labels={AUTO_LABEL}&since={since}'))
    return sum(1 for i in issues if (i.get('created_at') or '') >= since)


def auto_depth(ghq, number, cap):
    """How many auto cards are in the spawn chain ending at `number` (stops counting at cap)."""
    depth = 0
    seen = set()
    while number and number not in seen and depth < cap:
        seen.add(number)
        issue = ghq.api('GET', ghq.repo_path(f'/issues/{number}'))
        if AUTO_LABEL not in ghq.label_names(issue):
            break
        depth += 1
        m = SPAWN_RE.search(issue.get('body') or '')
        number = int(m.group(1)) if m else None
    return depth


def ensure_label(ghq):
    try:
        ghq.api('POST', ghq.repo_path('/labels'),
                {'name': AUTO_LABEL, 'color': AUTO_LABEL_SPEC[0], 'description': AUTO_LABEL_SPEC[1]})
    except ghq.GitHubError as e:
        if '422' not in str(e):  # 422 = already exists
            raise


def decide(title, body='', labels=(), *, auto_today=0, depth=0):
    """Pure decision: returns (status, why). No network."""
    tier, reasons = classify(title, body, labels)
    why = f'tier {tier}' + (f' ({"; ".join(reasons)})' if reasons else '')
    if os.environ.get('JARVIS_AUTO_OFF') == '1':
        return 'staged', why + '; auto-approval is switched off'
    if tier not in ('free', 'free_logged'):
        return 'staged', why
    if auto_today >= _limit('JARVIS_AUTO_PER_DAY', 25):
        return 'staged', why + f'; daily auto limit of {_limit("JARVIS_AUTO_PER_DAY", 25)} reached'
    if depth >= _limit('JARVIS_AUTO_DEPTH', 3):
        return 'staged', why + f'; {depth} auto cards already chained, a person should look'
    return 'approved', why


def propose(title, body='', machine='any', priority=None, card_type='task', spawned_from=None, source='Jarvis'):
    """File a card, approving it when policy allows. Returns (number, status, why); status 'duplicate' when an
    open card with the same title already exists."""
    ghq = _ghq()
    dup = find_duplicate(title, ghq.paged(ghq.repo_path('/issues?state=open')))
    if dup:
        return dup, 'duplicate', 'an open card with the same title exists'
    depth = auto_depth(ghq, spawned_from, _limit('JARVIS_AUTO_DEPTH', 3)) if spawned_from else 0
    status, why = decide(title, body, auto_today=auto_count_today(ghq), depth=depth)
    tier = why.split()[1]
    extra = [AUTO_LABEL] if status == 'approved' else []
    if status == 'approved':
        ensure_label(ghq)
    number = ghq.new_card(title, body, machine=machine, priority=priority, status=status, card_type=card_type,
                          pin=(tier == 'pin'), spawned_from=spawned_from, extra_labels=extra)
    verdict = 'Auto-approved' if status == 'approved' else 'Filed for Jake'
    ghq.comment(number, f'{verdict} by {source} at {ghq.now_iso()}: {why}.')
    if status == 'approved':
        ghq.wake()
    return number, status, why


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('propose', 'check'):
        s = sub.add_parser(name)
        s.add_argument('title')
        s.add_argument('--body', default='')
        if name == 'propose':
            s.add_argument('--machine', default='any')
            s.add_argument('--priority')
            s.add_argument('--type', default='task', dest='card_type')
            s.add_argument('--spawned-from', type=int)
            s.add_argument('--source', default='Jarvis')
    a = ap.parse_args(argv)
    if a.cmd == 'check':
        status, why = decide(a.title, a.body)
        print(json.dumps({'would_file_as': status, 'why': why}))
        return 0
    number, status, why = propose(a.title, a.body, a.machine, a.priority, a.card_type, a.spawned_from, a.source)
    print(json.dumps({'number': number, 'status': status, 'why': why}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
