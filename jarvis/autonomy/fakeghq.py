"""In-memory stand-in for ghq, shared by the tests. No network.

    g = fake_ghq([{'number': 7, 'title': 'x', 'labels': ['status:approved']}])
    autotask._ghq = lambda: g
"""
import datetime as dt
import types
import urllib.parse

import clock

NOW = clock.iso(clock.now_utc())


def ago(days=0, hours=0):
    """ISO timestamp `days`/`hours` before now, so test data never goes stale."""
    return clock.iso(clock.now_utc() - dt.timedelta(days=days, hours=hours))


def fake_ghq(existing=(), comments=None):
    g = types.SimpleNamespace()
    g.issues = {}
    for i in existing:
        i = dict(i)
        i.setdefault('state', 'open')
        i.setdefault('labels', [])
        i.setdefault('body', '')
        i.setdefault('created_at', ago(1))
        i.setdefault('updated_at', i['created_at'])
        g.issues[i['number']] = i
    g.comments = {}          # number -> [{'id', 'body', 'created_at', 'issue_url'}]
    for n, bodies in (comments or {}).items():
        for b in bodies:
            _add_comment(g, n, b)
    g.posted, g.woke, g.calls = [], 0, []

    class GitHubError(Exception):
        pass
    g.GitHubError = GitHubError
    g.repo_path = lambda s='': '/repos/x/y' + s
    g.label_names = lambda i: [l['name'] if isinstance(l, dict) else l for l in i.get('labels', [])]
    g.now_iso = lambda: NOW
    g.MACHINES = ['homebase', 'rig', 'laptop', 'pi']

    def status_of(i):
        if i.get('state') == 'closed':
            return 'done'
        for n in g.label_names(i):
            if n.startswith('status:'):
                return n.split(':', 1)[1]
        return 'inbox'
    g.status_of = status_of

    def paged(path):
        g.calls.append(('GET', path))
        base, _, query = path.partition('?')
        q = dict(urllib.parse.parse_qsl(query))
        if base.endswith('/issues/comments'):
            out = [c for cs in g.comments.values() for c in cs]
            if 'since' in q:
                out = [c for c in out if c['created_at'] >= q['since']]
            return out
        if base.endswith('/comments'):
            return list(g.comments.get(int(base.split('/')[-2]), []))
        out = list(g.issues.values())
        state = q.get('state', 'open')
        if state != 'all':
            out = [i for i in out if i.get('state', 'open') == state]
        for lab in filter(None, q.get('labels', '').split(',')):
            out = [i for i in out if lab in g.label_names(i)]
        if 'since' in q:
            out = [i for i in out if (i.get('updated_at') or '') >= q['since']]
        return out
    g.paged = paged

    def api(method, path, body=None):
        g.calls.append((method, path))
        parts = path.split('/')
        if method == 'GET' and parts[-2] == 'issues':
            return g.issues[int(parts[-1])]
        if method == 'POST' and path.endswith('/repos/x/y/labels'):
            return {}
        if path.endswith('/labels') and parts[-3] == 'issues':
            i = g.issues[int(parts[-2])]
            if method == 'PUT':
                i['labels'] = list(body['labels'])
            elif method == 'POST':
                i['labels'] = g.label_names(i) + [l for l in body['labels'] if l not in g.label_names(i)]
            return i['labels']
        if method == 'PATCH' and parts[-2] == 'issues':
            g.issues[int(parts[-1])].update(body)
            return g.issues[int(parts[-1])]
        raise AssertionError(f'unexpected {method} {path}')
    g.api = api

    def set_status(number, status, extra_add=(), extra_remove=()):
        i = g.issues[number]
        names = [n for n in g.label_names(i) if not n.startswith('status:') and n not in extra_remove]
        i['labels'] = names + [f'status:{status}'] + [n for n in extra_add if n not in names]
    g.set_status = set_status

    def new_card(title, body='', machine='any', priority=None, status='staged', card_type='task', pin=False,
                 spawned_from=None, extra_labels=()):
        n = max(g.issues, default=0) + 1
        if spawned_from:
            body = (body + f'\n\nSpawned from #{spawned_from}').strip()
        labels = [f'status:{status}', f'machine:{machine}', f'type:{card_type}'] + list(extra_labels)
        labels += ([priority] if priority else []) + (['pin'] if pin else [])
        g.issues[n] = {'number': n, 'title': title, 'body': body, 'labels': labels, 'state': 'open',
                       'created_at': NOW, 'updated_at': NOW}
        return n
    g.new_card = new_card

    def comment(n, text):
        g.posted.append((n, text))
        return _add_comment(g, n, text)
    g.comment = comment

    def wake():
        g.woke += 1
    g.wake = wake
    return g


def _add_comment(g, n, body):
    if isinstance(body, str):
        body = {'body': body}
    c = {'id': sum(len(v) for v in g.comments.values()) + 1, 'created_at': NOW,
         'issue_url': f'https://api.github.com/repos/x/y/issues/{n}'}
    c.update(body)
    g.comments.setdefault(n, []).append(c)
    return c


def runmeta(outcome, minutes='', machine='rig', extra=''):
    """A Worker run comment the way ghq.log_run writes it."""
    import json
    meta = {'machine': machine, 'outcome': outcome, 'minutes': minutes}
    return f'<!-- jarvis:run -->\n<!-- jarvis:runmeta {json.dumps(meta)} -->\n**Run on {machine}: {outcome}**\n{extra}'
