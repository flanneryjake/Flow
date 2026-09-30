"""Jarvis task queue on GitHub Issues (replaces the Notion Tasks board).

One private repo holds everything:
  * each card is an issue; its labels carry the state (see LABELS below)
  * every Worker run is its own comment, so run history and minutes per card are kept
  * each PC has one pinned "Health: <machine>" issue that its watchdog rewrites

Both PCs, the phone hub and cloud Claude sessions all read and write the same repo. The tailnet is only
used for an instant nudge: after an approval, `approve` POSTs to each Worker's /wake URL (JARVIS_WAKE_URLS),
so the card starts at once instead of on the next poll.

Standard library only (Python 3.8+). Config comes from environment variables:
  GITHUB_TASKS_TOKEN  fine-grained token limited to the tasks repo (Issues: read/write, Metadata: read)
  JARVIS_TASKS_REPO   owner/name, default flanneryjake/jarvis-tasks
  JARVIS_WAKE_URLS    optional, comma-separated, e.g. http://homebase:8790/wake,http://rig:8790/wake

Use as a library (import ghq) or from the command line: python ghq.py --help
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = 'https://api.github.com'
REPO = os.environ.get('JARVIS_TASKS_REPO', 'flanneryjake/jarvis-tasks')
MACHINES = ['homebase', 'rig', 'laptop', 'pi']

# name -> (color, description). Exactly one status:* label per open card; a closed issue is Done.
LABELS = {
    'status:inbox':      ('ededed', 'New, not reviewed yet'),
    'status:staged':     ('fbca04', 'Ready for Jake to approve'),
    'status:approved':   ('0e8a16', 'Approved: a Worker may run it'),
    'status:working':    ('5319e7', 'A Worker has claimed it and is running it'),
    'status:needs-jake': ('d93f0b', 'Stopped until Jake answers; Workers skip it'),
    'status:snoozed':    ('8b572a', 'Parked on purpose; Workers skip it'),
    'machine:any':       ('c5def5', 'Either PC may run it'),
    'machine:homebase':  ('0e8a16', 'Homebase only'),
    'machine:rig':       ('b60205', 'Rig only'),
    'machine:laptop':    ('1d76db', 'Laptop only'),
    'machine:pi':        ('e99695', 'Pi only'),
    'claimed:homebase':  ('bfdadc', 'Claimed by homebase'),
    'claimed:rig':       ('bfdadc', 'Claimed by rig'),
    'p0':                ('b60205', 'Priority 0 (first)'),
    'p1':                ('d93f0b', 'Priority 1'),
    'p2':                ('cccccc', 'Priority 2'),
    'type:task':         ('1d76db', 'Task'),
    'type:idea':         ('fef2c0', 'Idea'),
    'type:approval':     ('d93f0b', 'Approval request'),
    'type:note':         ('ededed', 'Note'),
    'pin':               ('000000', 'Spend/post/delete: the phone hub asks for the PIN before approving'),
    'owner:jake':        ('f9d0c4', 'Jake does this one'),
    'health':            ('0052cc', 'Machine health issue written by the watchdog'),
    'health:alert':      ('b60205', 'The health issue currently has an alert'),
}
STATUSES = [n for n in LABELS if n.startswith('status:')]
PRIORITY_ORDER = {'p0': 0, 'p1': 1, 'p2': 2}
RUN_MARK = '<!-- jarvis:run -->'
CLAIM_RE = re.compile(r'^<!-- jarvis:claim (\S+) (\S+) -->')
META_RE = re.compile(r'<!-- jarvis:meta (\{.*?\}) -->')


class GitHubError(Exception):
    pass


# ---------------------------------------------------------------------------- HTTP

def _token():
    t = os.environ.get('GITHUB_TASKS_TOKEN')
    if not t:
        raise GitHubError('GITHUB_TASKS_TOKEN is not set (run install-ghq.ps1, or set it as a user environment variable)')
    return t


def request(method, path, body=None, etag=None, graphql=False):
    """Call the GitHub REST API. Returns (status, data, headers). 304 returns (304, None, headers)."""
    url = path if path.startswith('http') else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + _token())
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    req.add_header('User-Agent', 'jarvis-ghq')
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    if etag:
        req.add_header('If-None-Match', etag)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return 304, None, dict(e.headers)
            if e.code in (502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            detail = e.read().decode(errors='replace')[:500]
            raise GitHubError(f'{method} {path} -> {e.code}: {detail}') from None
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise GitHubError(f'{method} {path} -> {e.reason}') from None


def api(method, path, body=None):
    return request(method, path, body)[1]


def paged(path):
    """GET every page of a list endpoint."""
    # Numbered pages rather than the Link header: GitHub's "next" links use /repositories/<id>/ paths,
    # which some proxies refuse.
    path = re.sub(r'([?&])per_page=\d+&?', r'\1', path).rstrip('?&')
    sep = '&' if '?' in path else '?'
    out, page = [], 1
    while True:
        data = request('GET', f'{path}{sep}per_page=100&page={page}')[1] or []
        out.extend(data)
        if len(data) < 100:
            return out
        page += 1


def repo_path(suffix=''):
    return f'/repos/{REPO}{suffix}'


# ---------------------------------------------------------------------------- helpers

def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def label_names(issue):
    return [l['name'] if isinstance(l, dict) else l for l in issue.get('labels', [])]


def status_of(issue):
    if issue.get('state') == 'closed':
        return 'done'
    for n in label_names(issue):
        if n.startswith('status:'):
            return n.split(':', 1)[1]
    return 'inbox'


def meta_of(issue):
    m = META_RE.search(issue.get('body') or '')
    return json.loads(m.group(1)) if m else {}


def set_status(number, status, extra_add=(), extra_remove=()):
    """Swap the status:* label (and optional others) in one read-modify-write."""
    issue = api('GET', repo_path(f'/issues/{number}'))
    names = [n for n in label_names(issue) if not n.startswith('status:') and n not in extra_remove]
    names += [f'status:{status}'] + [n for n in extra_add if n not in names]
    api('PUT', repo_path(f'/issues/{number}/labels'), {'labels': names})


def comment(number, text):
    return api('POST', repo_path(f'/issues/{number}/comments'), {'body': text})


def wake():
    """Nudge each Worker over the tailnet. Best effort: polling still picks the card up if this fails."""
    for url in filter(None, (u.strip() for u in os.environ.get('JARVIS_WAKE_URLS', '').split(','))):
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=b'{}', method='POST',
                                   headers={'Content-Type': 'application/json'}), timeout=5).read()
        except Exception as e:  # noqa: BLE001 - a sleeping rig is normal
            print(f'wake {url}: {e}', file=sys.stderr)


# ---------------------------------------------------------------------------- setup

def setup():
    """Create or update every label, and the pinned health issue for homebase and rig. Safe to re-run."""
    existing = {l['name']: l for l in paged(repo_path('/labels'))}
    for name, (color, desc) in LABELS.items():
        if name in existing:
            if existing[name]['color'] != color or (existing[name].get('description') or '') != desc:
                api('PATCH', repo_path('/labels/' + urllib.parse.quote(name, safe='')),
                    {'new_name': name, 'color': color, 'description': desc})
        else:
            api('POST', repo_path('/labels'), {'name': name, 'color': color, 'description': desc})
    for m in ('homebase', 'rig'):
        health_issue(m, create=True)
    print(f'Labels and health issues ready in {REPO}.')


# ---------------------------------------------------------------------------- queue

_ETAG_CACHE = {}


def ready(machine, use_etag_file=None):
    """Approved, unclaimed, open cards this machine may run, best first.

    With use_etag_file, the list is cached and re-fetched with If-None-Match, so an unchanged queue costs
    nothing against the API rate limit. Polling every 60 s is fine either way."""
    path = repo_path('/issues?state=open&labels=status:approved&per_page=100&sort=created&direction=asc')
    cache = {}
    if use_etag_file and os.path.exists(use_etag_file):
        try:
            with open(use_etag_file, encoding='utf-8') as f:
                cache = json.load(f)
        except ValueError:
            cache = {}
    status, data, headers = request('GET', path, etag=cache.get('etag'))
    if status == 304:
        data = cache.get('data', [])
    elif use_etag_file:
        slim = [{'number': i['number'], 'title': i['title'], 'labels': label_names(i),
                 'created_at': i['created_at'], 'pull_request': i.get('pull_request')} for i in data]
        with open(use_etag_file, 'w', encoding='utf-8') as f:
            json.dump({'etag': headers.get('ETag') or headers.get('etag'), 'data': slim}, f)
        data = slim
    out = []
    for i in data:
        names = label_names(i)
        if i.get('pull_request') or any(n.startswith('claimed:') for n in names):
            continue
        if not ({'machine:any', f'machine:{machine}'} & set(names)) and any(n.startswith('machine:') for n in names):
            continue
        prio = min([PRIORITY_ORDER[n] for n in names if n in PRIORITY_ORDER] or [3])
        out.append({'number': i['number'], 'title': i['title'], 'labels': names, 'priority': prio,
                    'created_at': i['created_at']})
    out.sort(key=lambda c: (c['priority'], c['created_at']))
    return out


def claim(number, machine):
    """Claim a card. Returns True if this machine won it.

    Two Workers can race for a machine:any card, so the claim is a comment: both post one, then the earliest
    claim comment since the card's last run wins and the loser deletes its own. Only the winner moves labels."""
    nonce = uuid.uuid4().hex[:10]
    mine = comment(number, f'<!-- jarvis:claim {machine} {nonce} -->\nClaimed by **{machine}** at {now_iso()}')
    comments = paged(repo_path(f'/issues/{number}/comments'))
    since_last_run = []
    for c in comments:
        body = c.get('body') or ''
        if body.startswith(RUN_MARK):
            since_last_run = []
        elif CLAIM_RE.match(body):
            since_last_run.append(c)
    winner = since_last_run[0] if since_last_run else None
    if not winner or winner['id'] != mine['id']:
        api('DELETE', repo_path(f'/issues/comments/{mine["id"]}'))
        return False
    set_status(number, 'working', extra_add=[f'claimed:{machine}'])
    return True


OUTCOMES = ('done', 'needs-jake', 'failed', 'timeout', 'waiting-usage', 'released')


def log_run(number, machine, outcome, started=None, ended=None, summary='', log_tail='', model=''):
    """Record one Worker run as its own comment, then move the card on.

    done           -> issue closed as completed
    needs-jake     -> status:needs-jake (Workers stop re-running it until Jake answers)
    waiting-usage  -> back to status:approved (the card is fine; the account hit its limit)
    released       -> back to status:approved (Worker let go without running, e.g. shutting down)
    failed/timeout -> back to status:approved once; a second failed/timeout run in a row -> needs-jake
    """
    if outcome not in OUTCOMES:
        raise ValueError(f'outcome must be one of {OUTCOMES}')
    ended = ended or now_iso()
    minutes = ''
    if started:
        try:
            a = dt.datetime.fromisoformat(started.replace('Z', '+00:00'))
            b = dt.datetime.fromisoformat(ended.replace('Z', '+00:00'))
            minutes = f'{(b - a).total_seconds() / 60:.1f}'
        except ValueError:
            pass
    meta = {'machine': machine, 'outcome': outcome, 'started': started, 'ended': ended, 'minutes': minutes,
            'model': model}
    text = [RUN_MARK, f'<!-- jarvis:runmeta {json.dumps(meta)} -->',
            f'**Run on {machine}: {outcome}**' + (f' ({minutes} min)' if minutes else '') +
            (f' · {model}' if model else '')]
    if summary:
        text += ['', summary.strip()]
    if log_tail:
        tail = log_tail.strip()[-6000:]
        text += ['', '<details><summary>Log tail</summary>', '', '```', tail.replace('```', "'''"), '```',
                 '', '</details>']
    comment(number, '\n'.join(text))

    remove = [f'claimed:{m}' for m in MACHINES]
    if outcome == 'done':
        issue = api('GET', repo_path(f'/issues/{number}'))
        names = [n for n in label_names(issue) if n not in remove and not n.startswith('status:')]
        api('PUT', repo_path(f'/issues/{number}/labels'), {'labels': names})
        api('PATCH', repo_path(f'/issues/{number}'), {'state': 'closed', 'state_reason': 'completed'})
        return 'done'
    if outcome == 'needs-jake':
        set_status(number, 'needs-jake', extra_remove=remove)
        return 'needs-jake'
    if outcome in ('failed', 'timeout'):
        runs = [r for r in runs_of(number) if r.get('outcome') not in ('released', 'waiting-usage')]
        if len(runs) >= 2 and all(r.get('outcome') in ('failed', 'timeout') for r in runs[-2:]):
            set_status(number, 'needs-jake', extra_remove=remove)
            comment(number, 'Stopped after two failed runs in a row. Split the card or add detail, then '
                            'approve it again.')
            return 'needs-jake'
    set_status(number, 'approved', extra_remove=remove)
    return 'approved'


def runs_of(number):
    out = []
    for c in paged(repo_path(f'/issues/{number}/comments')):
        m = re.search(r'<!-- jarvis:runmeta (\{.*?\}) -->', c.get('body') or '')
        if m:
            out.append(json.loads(m.group(1)))
    return out


def new_card(title, body='', machine='any', priority=None, status='staged', card_type='task', pin=False,
             spawned_from=None, extra_labels=()):
    """Create a card. Workers should leave status as 'staged' so Jake approves it; only the hub and Jake
    create 'approved' cards (the guardrail rules decide which kinds skip approval)."""
    if f'status:{status}' not in LABELS:
        raise ValueError(f'unknown status {status}')
    labels = [f'status:{status}', f'machine:{machine}', f'type:{card_type}'] + list(extra_labels)
    if priority:
        labels.append(priority.lower())
    if pin:
        labels.append('pin')
    if spawned_from:
        body = (body + f'\n\nSpawned from #{spawned_from}').strip()
    issue = api('POST', repo_path('/issues'), {'title': title, 'body': body, 'labels': labels})
    return issue['number']


def approve(number, by='hub'):
    """Approve a card. The phone hub checks the PIN itself before calling this for a card labelled 'pin'."""
    set_status(number, 'approved')
    comment(number, f'Approved via {by} at {now_iso()}')
    wake()


def ask_jake(number, question):
    set_status(number, 'needs-jake', extra_remove=[f'claimed:{m}' for m in MACHINES])
    comment(number, f'**Needs Jake:** {question}')


# ---------------------------------------------------------------------------- health

def health_issue(machine, create=False):
    for i in paged(repo_path('/issues?state=open&labels=health')):
        if i['title'].startswith(f'Health: {machine}'):
            return i['number']
    if not create:
        return None
    issue = api('POST', repo_path('/issues'), {'title': f'Health: {machine}', 'labels': ['health'],
                                              'body': 'Written by the watchdog every 5 minutes.'})
    try:  # pin it so it stays at the top of the Issues tab (GraphQL only; best effort)
        request('POST', '/graphql', {'query': 'mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{number}}}',
                                     'variables': {'id': issue['node_id']}})
    except GitHubError as e:
        print(f'pin failed (fine): {e}', file=sys.stderr)
    return issue['number']


def write_health(machine, fields, snapshot='', alerts=()):
    """Rewrite this machine's health issue. `fields` is an ordered dict of name -> value."""
    number = health_issue(machine, create=True)
    rows = ['| | |', '|---|---|'] + [f'| {k} | {str(v).replace("|", "/")} |' for k, v in fields.items()]
    body = [f'**Last check-in:** {now_iso()}', '']
    if alerts:
        body += ['> [!WARNING]', '> ' + '; '.join(alerts), '']
    body += rows
    if snapshot:
        body += ['', '```', snapshot.strip()[-5000:].replace('```', "'''"), '```']
    state = fields.get('Worker', '')
    title = f'Health: {machine} · {state}' if state else f'Health: {machine}'
    issue = api('GET', repo_path(f'/issues/{number}'))
    names = [n for n in label_names(issue) if n != 'health:alert'] + (['health:alert'] if alerts else [])
    api('PATCH', repo_path(f'/issues/{number}'), {'title': title[:250], 'body': '\n'.join(body), 'labels': names})
    return number


# ---------------------------------------------------------------------------- reports

def usage(days=7):
    """Minutes of Worker time per card over the last `days` days, heaviest first."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).replace(microsecond=0).isoformat()
    totals = {}
    for c in paged(repo_path(f'/issues/comments?since={urllib.parse.quote(since)}')):
        m = re.search(r'<!-- jarvis:runmeta (\{.*?\}) -->', c.get('body') or '')
        if not m:
            continue
        meta = json.loads(m.group(1))
        num = int(c['issue_url'].rsplit('/', 1)[1])
        t = totals.setdefault(num, {'number': num, 'runs': 0, 'minutes': 0.0, 'outcomes': {}})
        t['runs'] += 1
        t['minutes'] += float(meta.get('minutes') or 0)
        t['outcomes'][meta.get('outcome')] = t['outcomes'].get(meta.get('outcome'), 0) + 1
    return sorted(totals.values(), key=lambda t: -t['minutes'])


# ---------------------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('setup', help='create labels and health issues (safe to re-run)')
    s = sub.add_parser('ready', help='list cards this machine may run')
    s.add_argument('--machine', required=True)
    s.add_argument('--cache', help='ETag cache file (makes idle polls free)')
    s = sub.add_parser('claim')
    s.add_argument('number', type=int)
    s.add_argument('--machine', required=True)
    s = sub.add_parser('log', help='record a run and move the card on')
    s.add_argument('number', type=int)
    s.add_argument('--machine', required=True)
    s.add_argument('--outcome', required=True, choices=OUTCOMES)
    s.add_argument('--started')
    s.add_argument('--ended')
    s.add_argument('--summary', default='')
    s.add_argument('--log-file')
    s.add_argument('--model', default='')
    s = sub.add_parser('new')
    s.add_argument('title')
    s.add_argument('--body', default='')
    s.add_argument('--machine', default='any', choices=['any'] + MACHINES)
    s.add_argument('--priority', choices=['p0', 'p1', 'p2', 'P0', 'P1', 'P2'])
    s.add_argument('--status', default='staged', choices=[x.split(':')[1] for x in STATUSES])
    s.add_argument('--type', default='task', choices=['task', 'idea', 'approval', 'note'])
    s.add_argument('--pin', action='store_true')
    s.add_argument('--spawned-from', type=int)
    s = sub.add_parser('approve')
    s.add_argument('number', type=int)
    s.add_argument('--by', default='hub')
    s = sub.add_parser('ask', help='stop a card until Jake answers')
    s.add_argument('number', type=int)
    s.add_argument('question')
    s = sub.add_parser('health', help='rewrite this machine\'s health issue from a JSON file')
    s.add_argument('--machine', required=True)
    s.add_argument('--json', required=True, help='{"fields": {...}, "alerts": [...], "snapshot": "..."}')
    s = sub.add_parser('usage', help='Worker minutes per card')
    s.add_argument('--days', type=int, default=7)
    sub.add_parser('wake', help='nudge the Workers over the tailnet')
    a = p.parse_args(argv)

    try:
        if a.cmd == 'setup':
            setup()
        elif a.cmd == 'ready':
            print(json.dumps(ready(a.machine, a.cache), indent=1))
        elif a.cmd == 'claim':
            won = claim(a.number, a.machine)
            print('claimed' if won else 'lost')
            return 0 if won else 3
        elif a.cmd == 'log':
            tail = ''
            if a.log_file and os.path.exists(a.log_file):
                with open(a.log_file, encoding='utf-8', errors='replace') as f:
                    tail = f.read()
            print(log_run(a.number, a.machine, a.outcome, a.started, a.ended, a.summary, tail, a.model))
        elif a.cmd == 'new':
            print(new_card(a.title, a.body, a.machine, a.priority, a.status, a.type, a.pin, a.spawned_from))
        elif a.cmd == 'approve':
            approve(a.number, a.by)
        elif a.cmd == 'ask':
            ask_jake(a.number, a.question)
        elif a.cmd == 'health':
            with open(a.json, encoding='utf-8-sig') as f:
                h = json.load(f)
            print(write_health(a.machine, h.get('fields', {}), h.get('snapshot', ''), h.get('alerts') or []))
        elif a.cmd == 'usage':
            for t in usage(a.days):
                print(f"#{t['number']:<5} {t['minutes']:7.1f} min  {t['runs']} runs  {t['outcomes']}")
        elif a.cmd == 'wake':
            wake()
    except GitHubError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
