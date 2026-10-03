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
import hashlib
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


class RateLimited(GitHubError):
    """GitHub said slow down (secondary or primary rate limit). Nothing is sent until the cooldown ends."""


# ---------------------------------------------------------------------------- HTTP

def _token():
    t = os.environ.get('GITHUB_TASKS_TOKEN')
    if not t:
        raise GitHubError('GITHUB_TASKS_TOKEN is not set (run install-ghq.ps1, or set it as a user environment variable)')
    return t


# merged 10/2 from Flow 8e1c251
# A rate-limit cooldown is shared by every process on this PC through a small file, so a blocked Worker,
# hub and watchdog all wait it out instead of each hammering GitHub (which extends the block).
STATE_DIR = os.environ.get('JARVIS_STATE_DIR') or (
    os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'Jarvis') if os.name == 'nt'
    else os.path.join(os.path.expanduser('~'), '.jarvis'))
COOLDOWN_FILE = os.path.join(STATE_DIR, 'github-cooldown.json')
WRITE_GAP = 1.0  # GitHub asks for at least 1 s between content writes from one client
_LAST_WRITE = [0.0]


def _state_read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _state_write(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f'{path}.{os.getpid()}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass  # best effort: the in-process cooldown still applies


_COOLDOWN = [0.0, 0]  # until (epoch s), consecutive strikes


def cooldown_left():
    """Seconds until GitHub may be called again (0 when not cooling down)."""
    disk = _state_read(COOLDOWN_FILE, {})
    until = max(_COOLDOWN[0], float(disk.get('until') or 0))
    return max(0.0, until - time.time())


def _start_cooldown(headers, detail):
    """Back off after a 403/429 rate-limit answer: Retry-After if given, the primary reset time if that is the
    limit, else 60 s doubling per strike in a row (max 15 min)."""
    disk = _state_read(COOLDOWN_FILE, {})
    strikes = int(disk.get('strikes') or 0) + 1 if time.time() - float(disk.get('until') or 0) < 600 else 1
    wait = 0
    retry_after = headers.get('Retry-After') or headers.get('retry-after')
    if retry_after and str(retry_after).isdigit():
        wait = int(retry_after)
    elif str(headers.get('X-RateLimit-Remaining') or headers.get('x-ratelimit-remaining')) == '0':
        wait = int(headers.get('X-RateLimit-Reset') or headers.get('x-ratelimit-reset') or 0) - int(time.time())
    wait = min(max(wait, 60 * 2 ** (strikes - 1)), 900)
    until = time.time() + wait
    _COOLDOWN[:] = [until, strikes]
    _state_write(COOLDOWN_FILE, {'until': until, 'strikes': strikes, 'why': detail[:200],
                                 'set': now_iso(), 'pid': os.getpid()})
    return wait


def _is_rate_limit(code, detail, headers):
    if code == 429:
        return True
    if code != 403:
        return False
    return 'rate limit' in detail.lower() or str(headers.get('X-RateLimit-Remaining') or
                                                 headers.get('x-ratelimit-remaining')) == '0'




# loop net (Flow claude/loopnet 94b4b13, 10/03)
def _loopnet():
    """The loop net module (loopnet.py next to this file), or None if it's missing or switched off. Never raises."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import loopnet
        return loopnet if loopnet.enabled() else None
    except Exception:  # noqa: BLE001
        return None


_ISSUE_PATH_RE = re.compile(r'/issues/(\d+)(?:/|$)')


def _breaker_alert(number, count):
    try:   # one push through the hub (localhost on homebase; elsewhere the alert is just the ledger row)
        req = urllib.request.Request('http://127.0.0.1:8770/api/notify', method='POST', data=json.dumps({
            'title': f'GitHub breaker: #{number}', 'source': 'loopnet', 'url': f'/#inbox/review/{number}',
            'body': f'Needs Jake: #{number} made {count} GitHub writes this hour, so writes to it are paused until the '
                    'hour turns over. It is probably looping.'}).encode(), headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:  # noqa: BLE001
        pass


def _breaker(path):
    m = _ISSUE_PATH_RE.search(path)
    ln = _loopnet() if m else None
    if not ln:
        return
    try:
        ln.note_write(int(m.group(1)), alert=_breaker_alert)
    except ln.BreakerOpen as e:
        raise GitHubError(str(e))
    except Exception:  # noqa: BLE001 - a broken breaker file must never stop GitHub writes
        pass


def request(method, path, body=None, etag=None, graphql=False):
    """Call the GitHub REST API. Returns (status, data, headers). 304 returns (304, None, headers).
    Raises RateLimited, without calling GitHub, while a rate-limit cooldown is running."""
    left = cooldown_left()
    if left > 0:
        raise RateLimited(f'GitHub rate limit: waiting {int(left)} s more before {method} {path}')
    if method != 'GET':
        _breaker(path)   # loop net (10/03): too many writes on one card this hour -> refuse (GitHubError)
        gap = WRITE_GAP - (time.time() - _LAST_WRITE[0])
        if gap > 0:
            time.sleep(gap)
        _LAST_WRITE[0] = time.time()
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
            if _is_rate_limit(e.code, detail, dict(e.headers)):
                wait = _start_cooldown(dict(e.headers), detail)
                raise RateLimited(f'GitHub rate limit on {method} {path}: pausing GitHub calls for {wait} s') from None
            raise GitHubError(f'{method} {path} -> {e.code}: {detail}') from None
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise GitHubError(f'{method} {path} -> {e.reason}') from None


def api(method, path, body=None):
    return request(method, path, body)[1]


_PAGE_CACHE = {}  # url -> (etag, data): repeat list calls send If-None-Match; a 304 is free and returns this


def paged(path):
    """GET every page of a list endpoint. Each page is re-fetched with If-None-Match, so an unchanged list
    costs nothing against the rate limit (304s are free). (Flow b629c62)"""
    # Numbered pages rather than the Link header: GitHub's "next" links use /repositories/<id>/ paths,
    # which some proxies refuse.
    path = re.sub(r'([?&])per_page=\d+&?', r'\1', path).rstrip('?&')
    sep = '&' if '?' in path else '?'
    out, page = [], 1
    while True:
        url = f'{path}{sep}per_page=100&page={page}'
        etag, cached = _PAGE_CACHE.get(url, (None, None))
        status, data, headers = request('GET', url, etag=etag)
        if status == 304 and cached is not None:
            data = cached
        else:
            data = data or []
            tag = headers.get('ETag') or headers.get('etag')
            if tag:
                if len(_PAGE_CACHE) > 500:
                    _PAGE_CACHE.clear()
                _PAGE_CACHE[url] = (tag, data)
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


OUTBOX_FILE = os.path.join(STATE_DIR, 'github-outbox.json')
SENT_FILE = os.path.join(STATE_DIR, 'github-sent.json')
REPEAT_WINDOW = 6 * 3600  # the same text on the same card within 6 h is a loop, not news


def _comment_key(number, text):
    # Timestamps and claim nonces differ between otherwise identical comments, so drop them from the key.
    norm = re.sub(r'\d{4}-\d\d-\d\dT[\d:.+Z-]+|\b[0-9a-f]{10}\b', '', text)
    norm = re.sub(r'\s+', ' ', norm).strip()
    return f'{number}:' + hashlib.sha1(norm.encode()).hexdigest()[:12]


def _remember(key):
    sent = {k: v for k, v in _state_read(SENT_FILE, {}).items() if time.time() - v < REPEAT_WINDOW}
    sent[key] = time.time()
    _state_write(SENT_FILE, sent)


def flush_outbox(limit=5):
    """Post comments queued during a rate-limit cooldown, oldest first. Called before each new comment."""
    box = _state_read(OUTBOX_FILE, [])
    if not box or cooldown_left() > 0:
        return 0
    sent = 0
    while box and sent < limit:
        item = box[0]
        try:
            api('POST', repo_path(f'/issues/{item["number"]}/comments'), {'body': item['text']})
            _remember(item['key'])
        except RateLimited:
            break
        except GitHubError as e:
            print(f'outbox: dropped comment for #{item["number"]}: {e}', file=sys.stderr)
        box.pop(0)
        sent += 1
        _state_write(OUTBOX_FILE, box)
    return sent


def comment(number, text, queue=True, dedupe=True):
    """Post a comment. Returns the new comment, or None when it was skipped as a repeat or queued.

    dedupe: the same text on the same card again within 6 h is skipped (that is a bounce loop, and loops are
    what tripped GitHub's content-creation limit on 10/02). queue: during a rate-limit cooldown the comment is
    saved and posted once the cooldown ends, instead of raising. Claims and progress comments pass
    queue=False, dedupe=False because the caller needs the comment id."""
    key = _comment_key(number, text)
    if dedupe:
        sent = _state_read(SENT_FILE, {})
        if time.time() - float(sent.get(key) or 0) < REPEAT_WINDOW:
            return None
    try:
        flush_outbox()
        out = api('POST', repo_path(f'/issues/{number}/comments'), {'body': text})
    except RateLimited:
        if not queue:
            raise
        box = _state_read(OUTBOX_FILE, [])
        if not any(i.get('key') == key for i in box):
            box.append({'number': number, 'text': text, 'key': key, 'queued': now_iso()})
            _state_write(OUTBOX_FILE, box[-200:])
        return None
    if dedupe:
        _remember(key)
    return out


_LAST_WAKE = [0.0]


def wake(force=False):
    """Nudge each Worker over the tailnet. Best effort: polling still picks the card up if this fails.
    At most once a minute unless force (Jake's `now` cards always wake at once). (Flow b629c62)"""
    if not force and time.time() - _LAST_WAKE[0] < 60:
        return
    _LAST_WAKE[0] = time.time()
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
    if not fleet_allows(machine):
        return []  # paused or disconnected from the phone app's Fleet panel (fleet/fleet.py)
    # ready() fetch = Flow 06cf1b7 (10/2): newest-updated first, so any change to the approved set (a new approval, a
    # claim, an edit) changes page 1. Page 1 goes out with If-None-Match: an unchanged queue is a 304, which GitHub
    # doesn't count against the rate limit. Only a changed page 1 pays for the remaining pages. The fleet_allows
    # check above and the machine filter / sort below are rig-local.
    path = repo_path('/issues?state=open&labels=status:approved&sort=updated&direction=desc')
    cache = {}
    if use_etag_file and os.path.exists(use_etag_file):
        try:
            with open(use_etag_file, encoding='utf-8') as f:
                cache = json.load(f)
        except ValueError:
            cache = {}
    status, data, headers = request('GET', path + '&per_page=100&page=1', etag=cache.get('etag'))
    if status == 304 and 'data' in cache:
        data = cache['data']
    else:
        data, page = list(data or []), 1
        while len(data) == 100 * page:
            page += 1
            data += request('GET', path + f'&per_page=100&page={page}')[1] or []
        data = [{'number': i['number'], 'title': i['title'], 'labels': label_names(i),
                 'created_at': i['created_at'], 'pull_request': i.get('pull_request')} for i in data]
        if use_etag_file:
            with open(use_etag_file, 'w', encoding='utf-8') as f:
                json.dump({'etag': headers.get('ETag') or headers.get('etag'), 'data': data}, f)
    defer = fleet_defers(machine)
    out = []
    for i in data:
        names = label_names(i)
        if i.get('pull_request') or any(n.startswith('claimed:') for n in names):
            continue
        if REVIEW_LABEL in names:
            continue  # E1: in Claude review; only Jake's tap or Claude's triage (approve() strips the label) frees it
        if defer and f'machine:{machine}' not in names:
            continue  # the rig (first in roles.json's worker order) is up and takes the shared cards
        if not ({'machine:any', f'machine:{machine}'} & set(names)) and any(n.startswith('machine:') for n in names):
            continue
        prio = min([PRIORITY_ORDER[n] for n in names if n in PRIORITY_ORDER] or [3])
        out.append({'number': i['number'], 'title': i['title'], 'labels': names, 'priority': prio,
                    'created_at': i['created_at']})
    out.sort(key=lambda c: (c['priority'], c['created_at']))
    return out


def fleet_allows(machine):
    """False when the Fleet panel has this machine paused or disconnected. True if fleet.py isn't installed."""
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fleet'), r'C:\Jarvis\fleet'):
        if os.path.exists(os.path.join(p, 'fleet.py')):
            if p not in sys.path:
                sys.path.insert(0, p)
            break
    try:
        import fleet
    except ImportError:
        return True
    return fleet.may_take_cards(machine)


def fleet_defers(machine):
    """True when a Worker ahead of this one in roles.json is live (rig first, 10/02): this machine then runs only
    cards pinned to it and leaves machine:any cards to that Worker. False if fleet.py isn't installed."""
    if not fleet_allows(machine):
        return False
    try:
        import fleet
        return bool(getattr(fleet, 'defer_shared_to', lambda m: None)(machine))
    except ImportError:
        return False


def claim(number, machine):
    """Claim a card. Returns True if this machine won it.

    Two Workers can race for a machine:any card, so the claim is a comment: both post one, then the earliest
    claim comment since the card's last run wins and the loser deletes its own. Only the winner moves labels."""
    current = api('GET', repo_path(f'/issues/{number}'))
    if current.get('state') != 'open' or status_of(current) != 'approved' or \
            any(n.startswith('claimed:') for n in label_names(current)):
        return False  # the cached queue was stale: someone else has it, or it was snoozed or closed
    nonce = uuid.uuid4().hex[:10]
    mine = comment(number, f'<!-- jarvis:claim {machine} {nonce} -->\nClaimed by **{machine}** at {now_iso()}',
                   queue=False, dedupe=False)
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
    ln = _loopnet()
    if ln:
        ln.record(number, 'claimed', machine=machine)
    return True


OUTCOMES = ('done', 'needs-jake', 'failed', 'timeout', 'waiting-usage', 'released')


def log_run(number, machine, outcome, started=None, ended=None, summary='', log_tail='', model=''):
    """Record one Worker run (see _log_run), then the needs-Jake catcher (Jake 10/03) reads the result: if Claude verifies
    it says Jake must do something, the card gets a To-Do step and leaves the queue. Runs after the status move, so a
    run is never interrupted."""
    result = _log_run(number, machine, outcome, started, ended, summary, log_tail, model)
    if outcome not in ('done', 'needs-jake'):   # done = closed; needs-jake already gets its step from ask_jake/_log_run
        catch_jake_text(number, f'{summary}\n{(log_tail or "")[-1500:]}', 'run')
    return result


def catch_jake_text(number, text, source='run'):
    """needsjake pipeline (regex -> Tars/Jarvis -> Claude verifies -> park). Never raises."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import needsjake
        return needsjake.process(sys.modules[__name__], number, text, source)
    except Exception as e:  # noqa: BLE001 - the catcher must never break a Worker
        print(f'needs-jake catcher #{number}: {e}', file=sys.stderr)
        return None


def _log_run(number, machine, outcome, started=None, ended=None, summary='', log_tail='', model=''):
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
    # Never deduped (Flow da1510f): each run record counts toward the two-failures rule, and two failed runs often read alike.
    comment(number, '\n'.join(text), dedupe=False)

    remove = [f'claimed:{m}' for m in MACHINES]
    if outcome == 'done':
        issue = api('GET', repo_path(f'/issues/{number}'))
        names = [n for n in label_names(issue) if n not in remove and not n.startswith('status:')]
        api('PUT', repo_path(f'/issues/{number}/labels'), {'labels': names})
        api('PATCH', repo_path(f'/issues/{number}'), {'state': 'closed', 'state_reason': 'completed'})
        return 'done'
    if outcome == 'needs-jake':
        set_status(number, 'needs-jake', extra_remove=remove)
        if os.environ.get('JARVIS_JAKE_STEPS', 'on') != 'off':
            _attach_jake_step(number, summary or 'The Worker stopped this card for you.')
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
        if META_FOLLOWUP_RE.search(title):
            return None  # "stop re-approving #N"-style cards: snooze/send_back handle loops now
        if open_followups(spawned_from) >= MAX_FOLLOWUPS:
            return None  # this card already has its share of open follow-ups; finish those first (Flow 186485a)
        dup = similar_open_child(spawned_from, title)
        if dup:
            return dup
        body = (body + f'\n\nSpawned from #{spawned_from}').strip()
    labels, gate_note = focus_gate(labels, spawned_from)
    if gate_note:
        body = (body + '\n\n' + gate_note).strip()
    issue = api('POST', repo_path('/issues'), {'title': title, 'body': body, 'labels': labels})
    if gate_note:
        FOCUS_EXILED.add(issue['number'])
    return issue['number']


# focus gate (Jake 10/03, same as the 5060's): only 4 projects run (project:homebase, handsfree, brains, income).
# A new staged / approved / inbox card with no project:* label takes its parent's project label; with none to
# inherit it is filed status:snoozed + exile instead, so it never reaches Jake's approvals or a Worker.
FOCUS_STATUSES = ('staged', 'approved', 'inbox')
FOCUS_EXEMPT = {'todo-tab', 'owner:jake', 'from:jake', 'now', 'life', 'health', 'fleet', 'project', 'type:project'}
FOCUS_EXILED = set()   # cards new_card exiled in this process: autotask must not report them auto-approved or wake


def focus_gate(labels, spawned_from=None):
    """-> (labels, note). note is '' when the card may be filed as asked (one GET of the parent at most)."""
    status = next((n.split(':', 1)[1] for n in labels if n.startswith('status:')), '')
    if (status not in FOCUS_STATUSES or any(n.startswith('project:') for n in labels)
            or FOCUS_EXEMPT & set(labels)):
        return labels, ''
    if spawned_from:
        try:
            inherit = [n for n in label_names(api('GET', repo_path(f'/issues/{spawned_from}')))
                       if n.startswith('project:')]
        except GitHubError:
            inherit = []   # parent unreadable: exile rather than let an unscoped card through
        if inherit:
            return labels + inherit[:1], ''
    keep = [n for n in labels if not n.startswith('status:') and n not in ('auto', 'sched:deferred', 'exile')]
    return keep + ['status:snoozed', 'exile'], (
        f'> Focus gate (10/03): filed as snoozed + exile instead of {status} because it has no project:* label '
        f'(only homebase, handsfree, brains and income run now).')



# merged 10/2 from Flow 4e6085d (follow-up dedupe in new_card)
# Follow-ups about the loop itself, which snooze_until / send_back now handle. Never filed as cards.
META_FOLLOWUP_RE = re.compile(r're-?approv|stop (re-?)?running|pause (re-?)?approval|keeps? (looping|bouncing)', re.I)


def _norm(t):
    words = re.findall(r'[a-z0-9]+', t.lower())
    stop = {'the', 'a', 'an', 'to', 'for', 'of', 'and', 'on', 'in', 'card', 'let', 'allow', 'make', 'add', 'when'}
    return {w[:-1] if len(w) > 4 and w.endswith('s') else w for w in words} - stop


MAX_FOLLOWUPS = int(os.environ.get('JARVIS_MAX_FOLLOWUPS_PER_CARD', '2'))  # open follow-ups one card may have


def similar_open_child(parent, title, threshold=0.6, sibling_threshold=0.4):
    """An open card with a similar title, or None. Siblings (spawned from the same parent) match more loosely,
    because a Worker re-running a card rewords the same follow-up. Two search calls (search has its own quota)."""
    want = _norm(title)
    words = ' '.join(sorted(want, key=len, reverse=True)[:4])
    for q, limit in ((f'repo:{REPO} is:issue is:open "Spawned from #{parent}" in:body', sibling_threshold),
                     (f'repo:{REPO} is:issue is:open {words} in:title', threshold)):
        try:
            items = api('GET', '/search/issues?per_page=50&q=' + urllib.parse.quote(q)).get('items', [])
        except GitHubError:
            continue  # search down: file it rather than lose it
        for i in items:
            have = _norm(i['title'])
            if want and have and len(want & have) / len(want | have) >= limit:
                return i['number']
    return None


def open_followups(parent):
    """How many open cards were spawned from `parent` (one search call)."""
    q = f'repo:{REPO} is:issue is:open "Spawned from #{parent}" in:body'
    try:
        return api('GET', '/search/issues?per_page=1&q=' + urllib.parse.quote(q)).get('total_count', 0)
    except GitHubError:
        return 0


REVIEW_LABEL = 'needs-claude-review'


def review_release_ok(by):
    """E1: who may take a card out of Claude review. Jake's own tap, or Claude's loop triage. Nothing automatic."""
    b = str(by or '').lower()
    return (b in ('phone', 'jake') or 'triage' in b or 'claude' in b
            or os.environ.get('JARVIS_E1_GUARD', 'on').lower() in ('off', '0', 'false', 'no'))


def approve(number, by='hub'):
    """Approve a card. The phone hub checks the PIN itself before calling this for a card labelled 'pin'.
    Returns False (and changes nothing on GitHub) when E1 refuses it: see review_release_ok."""
    current = api('GET', repo_path(f'/issues/{number}'))
    # E1 (Jake 10/03): a card the loop net sent to Claude review can't be pushed back into the queue by automation.
    # #690: a board wipe re-approved it 8 times in 90 minutes and tripped the GitHub breaker twice.
    in_review = REVIEW_LABEL in label_names(current)
    if in_review and not review_release_ok(by):
        ln = _loopnet()
        if ln:
            ln.record(number, 'approve-blocked', by=str(by))
        return False
    if (current.get('state') == 'open' and status_of(current) == 'approved' and not in_review
            and not any(n.startswith('claimed:') for n in label_names(current))):
        return  # a double tap or a retried request: already approved, so no second label write or comment
    # Flow da1510f: a 'working' card is approved again on purpose (Jake re-approving a card whose Worker crashed
    # mid-run): clear the stale claim, and reopen a closed card so ready() sees it again.
    set_status(number, 'approved', extra_remove=[f'claimed:{m}' for m in MACHINES] +
               ([REVIEW_LABEL, 'triage'] if in_review else []))
    if current.get('state') == 'closed':
        api('PATCH', repo_path(f'/issues/{number}'), {'state': 'open'})
    ln = _loopnet()
    tag = ln.daily_tag(number) if ln else ''   # NN-YYYYMMDD: a friendly daily label, never the identity
    comment(number, f'Approved via {by} at {now_iso()}' + (f" (today's tag {tag})" if tag else ''), dedupe=False)
    if ln:
        ln.record(number, 'approved', by=by)
    wake()


def ask_jake(number, question, step=None):
    set_status(number, 'needs-jake', extra_remove=[f'claimed:{m}' for m in MACHINES])
    comment(number, f'**Needs Jake:** {question}')
    # Then the To-Do item (place, steps, one link or a question): see "Jake steps" below. (Flow 8f9962f, PR #48)
    if os.environ.get('JARVIS_JAKE_STEPS', 'on') != 'off':
        if step:
            try:
                jake_step(number, step)
            except Exception:  # noqa: BLE001 - never let the To-Do item break the hand-off
                pass
        else:
            _attach_jake_step(number, question)


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


# ---------------------------------------------------------------------------- merged 10/2 from Flow 571878e
# (claude/snooze-until-file): snooze + approval-loop triage only; everything else above is the rig's copy.
SNOOZE_RE = re.compile(r'<!-- jarvis:snooze (\{.*?\}) -->')
OUTPUT_RE = re.compile(r'^\*\*Output file \(full path\):\*\* `[^`]*`\n?(Write the finished file to exactly this path.*)?$', re.M)
OUTPUT_ROOT = os.environ.get('JARVIS_OUTPUT_ROOT', r'C:\Jarvis\outputs-repo\cards')


# ---------------------------------------------------------------------------- snooze until a file exists
#
# An approved card that can't start because an input file isn't there yet (another card makes it, or a print
# hasn't finished) must not bounce back to Jake: Jake already approved it. The Worker snoozes it on the file
# instead, and every poll `wake_snoozed()` puts it back to approved the moment the file shows up.

def _is_full_path(path):
    return bool(re.match(r'^[A-Za-z]:[\\/]', path) or path.startswith('\\\\') or path.startswith('/'))


def clean_path(path):
    """A file path as written by a model, minus quotes and any trailing description: `scan.csv (raw output)`
    or `scan.csv - the export` would otherwise be watched literally and never show up. (Flow PR #40)"""
    p = str(path).strip().strip('"`\'').strip()
    p = re.sub(r'\s+\([^()]*\)$', '', p)
    p = re.sub(r'\s+[-\u2013\u2014]\s+.*$', '', p)
    return p.strip().strip('"`\'')


def parent_of(issue_or_number):
    """The card this one was spawned from ("Spawned from #N" in the body), or None."""
    issue = issue_or_number if isinstance(issue_or_number, dict) else api('GET', repo_path(f'/issues/{issue_or_number}'))
    m = re.search(r'Spawned from #(\d+)', issue.get('body') or '')
    return int(m.group(1)) if m else None


def full_output_path(name, producer):
    """A full path for a bare file name, in the producing card's own folder under OUTPUT_ROOT."""
    name = re.split(r'[\\/]', clean_path(name))[-1]
    sep = '\\' if '\\' in OUTPUT_ROOT else '/'
    return sep.join([OUTPUT_ROOT.rstrip('\\/'), f'card-{producer}', name])


def set_output_path(producer, path, waiting=None):
    """Edit the producing card's body so it writes its output to exactly `path` (replacing any earlier line)."""
    issue = api('GET', repo_path(f'/issues/{producer}'))
    body = OUTPUT_RE.sub('', issue.get('body') or '').rstrip()
    line = f'**Output file (full path):** `{path}`'
    note = f'\nWrite the finished file to exactly this path' + (f'; #{waiting} is snoozed until it exists.' if waiting else '.')
    api('PATCH', repo_path(f'/issues/{producer}'), {'body': f'{body}\n\n{line}{note}'.strip()})
    comment(producer, f'Output path set to `{path}`' + (f' so #{waiting} can start when it exists.' if waiting else '.'))
    if issue.get('state') == 'closed':
        # Already ran, but nobody knows where its file went. Reopen it (Jake approved it once already) to put
        # the file at the full path: copy it there if it exists elsewhere, otherwise make it again.
        api('PATCH', repo_path(f'/issues/{producer}'), {'state': 'open'})
        set_status(producer, 'approved')
        comment(producer, f'Reopened only to put its output at `{path}`. If the file already exists somewhere '
                          'else, copy it there instead of redoing the work.')
        wake()


SNOOZE_KINDS = ('file', 'card', 'machine', 'time')
MACHINE_FRESH_MIN = 10   # a PC counts as online if its health issue checked in this recently
MAX_SNOOZES = 3          # a card snoozed this many times and still blocked goes to Jake with the history


def _parse_iso(text):
    t = dt.datetime.fromisoformat(str(text).strip().replace('Z', '+00:00'))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# merged 10/2 from Flow f63d242 + c3a07a8: Windows hostnames and tailnet names -> fleet names. The 5060 claims as
# "homebase" since 2026-10-02; the junk laptop is the backup box (Home Assistant, relay).
HOST_ALIASES = {'laptop-4150egrs': 'homebase', '5060': 'homebase', 'laptop': 'homebase',
                'desktop-vllddm4': 'rig', 'desktop-5ve3c77': 'backup', 'junk': 'backup', 'jarvis-pi': 'pi'}
SNOOZE_MACHINES = MACHINES + ['backup']
# Health issue names each fleet name may still be reporting under (the 5060's watchdog wrote "laptop" until
# its Health identity moved to "homebase"); the freshest check-in wins.
HEALTH_NAMES = {'homebase': ['homebase', 'laptop']}


def machine_name(name):
    """A fleet name for a PC given as a fleet name, hostname or tailnet name (any case, with or without the
    tailnet domain). Raises ValueError if it isn't a known PC."""
    n = str(name).strip().lower().split('.')[0]
    n = HOST_ALIASES.get(n, n)
    if n not in SNOOZE_MACHINES:
        raise ValueError(f'unknown machine {name}')
    return n


def snooze_until(number, kind, value, machine, reason='', producer=None):
    """Park a card (status:snoozed) until a condition is met; never asks Jake. Returns the value watched.

      file     value is a full path (or a bare name, see snooze_until_file) that must exist on `machine`
      card     value is a card number that must be closed (done)
      machine  value is a PC name ('rig', 'homebase', ...) whose health issue must have checked in recently
      time     value is an ISO time (UTC if no zone) that must have passed

    `machine` is the PC whose Worker checks the condition (for a file, the PC the file lands on)."""
    if kind in GATE_KINDS:   # kind 'all' / 'any' (the if / and / or gate, 10/03): value = list of {kind, value}
        return snooze_until_gate(number, kind, value, machine, reason)
    if kind not in SNOOZE_KINDS:
        raise ValueError(f'snooze kind must be one of {SNOOZE_KINDS}')
    # rig 10/2: never snooze on something that is already true, or the next sweep wakes it and it loops
    # (#431 waited on "rig online" from the rig; #433 on a file its own run had already written). Callers
    # (send_back, the budget guard) treat ValueError as "ask Jake instead".
    if kind == 'machine' and machine_name(value) == machine_name(machine):
        raise ValueError(f'waits for {machine}, which is the PC snoozing it (already online)')
    if kind in ('file', 'card', 'time'):
        here = None
        try:
            here = machine_name(os.environ.get('JARVIS_MACHINE') or os.environ.get('COMPUTERNAME', ''))
        except ValueError:
            pass
        probe = {'kind': kind, 'value': value}
        if kind == 'card':
            probe['value'] = int(str(value).lstrip('#'))
        if (kind != 'file' or (here and machine_name(machine) == here and _is_full_path(clean_path(value)))) \
                and condition_met(dict(probe, value=clean_path(probe['value']) if kind == 'file' else probe['value'])):
            raise ValueError(f'{kind} condition `{value}` is already met')
    if kind == 'file':
        value = clean_path(value)
        if not _is_full_path(value):
            producer = producer or parent_of(number)
            if not producer:
                raise ValueError(f'#{number}: "{value}" is not a full path and the card has no parent to edit; '
                                 'pass a full path or producer=<card that makes the file>')
            value = full_output_path(value, producer)
            set_output_path(producer, value, waiting=number)
        what = f'this file exists on {machine}: `{value}`'
    elif kind == 'card':
        value = int(str(value).lstrip('#'))
        if value == number:
            raise ValueError('a card cannot wait on itself')
        what = f'#{value} is done'
        producer = producer or value
    elif kind == 'machine':
        value = machine_name(value)
        if value in MACHINES:   # 'backup' runs no Worker and has no machine: label, so it is never relabeled
            # "Run this on the rig" means the card belongs to that PC: move it there, or the snoozing Worker
            # (machine:any) claims it again the moment it wakes. If that PC is already up, nothing to wait for.
            try:   # local (5060): a relabel problem (incl. RateLimited) must never lose the snooze itself
                issue = api('GET', repo_path(f'/issues/{number}'))
                keep = [n for n in label_names(issue) if not n.startswith(('machine:', 'claimed:'))]
                if machine_online(value):
                    keep = [n for n in keep if not n.startswith('status:')] + ['status:approved', f'machine:{value}']
                    api('PUT', repo_path(f'/issues/{number}/labels'), {'labels': keep})
                    comment(number, f'Moved to **{value}**, which is online now.' + (f'\n\n{reason.strip()}' if reason else ''))
                    wake()
                    return value
                api('PUT', repo_path(f'/issues/{number}/labels'), {'labels': keep + [f'machine:{value}']})
            except GitHubError as e:
                print(f'snooze_until #{number}: could not move it to {value}: {e}', file=sys.stderr)
        what = f'**{value}** is online'
    else:
        value = _parse_iso(value).replace(microsecond=0).isoformat()
        what = f'{value}'
    meta = {'kind': kind, 'value': value, 'machine': machine, 'since': now_iso(), 'producer': producer}
    if kind == 'file':
        meta['path'] = value
    set_status(number, 'snoozed', extra_remove=[f'claimed:{m}' for m in MACHINES] + ['resume'])
    comment(number, f'<!-- jarvis:snooze {json.dumps(meta)} -->\n**Snoozed until {what}**' +
                    (f'\n\n{reason.strip()}' if reason else '') +
                    (f'\n\nMade by #{producer}.' if producer and kind == 'file' else '') +
                    '\n\nIt goes back to approved by itself when that happens. No action needed from Jake.', dedupe=False)
    return value


GATE_KINDS = ('all', 'any')


def _gate_condition(number, c, machine):
    """Check and normalise one condition of an all/any gate. Returns (condition, already_met)."""
    kind = str(c.get('kind') or '').strip()
    value = c.get('value')
    if kind in GATE_KINDS:   # nesting: all of [file X, any of [card #3, after 9 PM]]
        subs = [_gate_condition(number, s, machine) for s in (value or [])]
        if not subs:
            raise ValueError(f'empty {kind} gate')
        met = all(m for _, m in subs) if kind == 'all' else any(m for _, m in subs)
        return {'kind': kind, 'value': [s for s, _ in subs]}, met
    if kind not in SNOOZE_KINDS:
        raise ValueError(f'gate condition kind must be one of {SNOOZE_KINDS + GATE_KINDS}')
    if kind == 'file':
        value = clean_path(value)
        if not _is_full_path(value):
            raise ValueError(f'gate file condition needs a full path, got "{value}"')
        return {'kind': 'file', 'value': value}, os.path.exists(value)
    if kind == 'card':
        value = int(str(value).lstrip('#'))
        if value == number:
            raise ValueError('a card cannot wait on itself')
        return {'kind': 'card', 'value': value}, api('GET', repo_path(f'/issues/{value}')).get('state') == 'closed'
    if kind == 'machine':
        value = machine_name(value)
        return {'kind': 'machine', 'value': value}, False
    when = _parse_iso(value).replace(microsecond=0)
    return {'kind': 'time', 'value': when.isoformat()}, when <= dt.datetime.now(dt.timezone.utc)


def _gate_text(c):
    if c['kind'] in GATE_KINDS:
        return '(' + (' AND ' if c['kind'] == 'all' else ' OR ').join(_gate_text(s) for s in c['value']) + ')'
    return {'file': 'file `{}`', 'card': '#{} done', 'machine': '{} online', 'time': 'after {}'}[c['kind']].format(c['value'])


def snooze_until_gate(number, kind, conditions, machine, reason=''):
    """The if / and / or gate: park a card until ALL (kind 'all') or ANY (kind 'any') of `conditions` are met, e.g.
        snooze_until(42, 'all', [{'kind': 'file', 'value': r'D:\\out\\photos.csv'},
                                 {'kind': 'any', 'value': [{'kind': 'card', 'value': 12}, {'kind': 'time', 'value': '2026-10-04T01:00Z'}]}],
                     'rig', 'waits on the photo export and either card #12 or 9 PM')
    Never asks Jake; wake_snoozed() re-approves it when the gate opens. Returns the stored gate."""
    gate, met = _gate_condition(number, {'kind': kind, 'value': list(conditions or [])}, machine)
    if met:
        raise ValueError(f'the gate {_gate_text(gate)} is already open, so there is nothing to wait for')
    meta = {'kind': gate['kind'], 'value': gate['value'], 'machine': machine, 'since': now_iso(), 'producer': None}
    set_status(number, 'snoozed', extra_remove=[f'claimed:{m}' for m in MACHINES] + ['resume'])
    comment(number, f'<!-- jarvis:snooze {json.dumps(meta)} -->\n**Snoozed until {_gate_text(gate)}**' +
                    (f'\n\n{reason.strip()}' if reason else '') +
                    '\n\nIt goes back to approved by itself when that happens. No action needed from Jake.', dedupe=False)
    ln = _loopnet()
    if ln:
        ln.record(number, 'snoozed', reason, machine, gate=_gate_text(gate))
    return gate


def snooze_until_file(number, path, machine, reason='', producer=None):
    """Park a card until `path` exists on `machine`; never asks Jake.

    If `path` is only a file name, the producing card (`producer`, else this card's parent) is edited to write
    to a full path, and the snooze watches that path. Returns the full path watched."""
    return snooze_until(number, 'file', path, machine, reason, producer)


def snooze_of(number):
    """The latest snooze record on a card ({kind, value, machine, since, producer}) or None."""
    found = None
    for c in paged(repo_path(f'/issues/{number}/comments')):
        m = SNOOZE_RE.search(c.get('body') or '')
        if m:
            found = json.loads(m.group(1))
    if found and 'kind' not in found:  # written before kinds existed
        found.update(kind='file', value=found.get('path'))
    return found


_SNOOZE_CACHE = {}  # issue number -> (updated_at, snooze record): comments are only re-read when a card changes


def snoozed(machine=None):
    """Open snoozed cards with what each waits on (kind None = snoozed by hand). Used by the hub too.
    One list call per 100 snoozed cards; a card's comments are read only when it changed since last time."""
    out = []
    for i in paged(repo_path('/issues?state=open&labels=status:snoozed')):
        if i.get('pull_request'):
            continue
        hit = _SNOOZE_CACHE.get(i['number'])
        if hit and hit[0] == i['updated_at']:
            s = hit[1]
        else:
            s = snooze_of(i['number']) or {}
            _SNOOZE_CACHE[i['number']] = (i['updated_at'], s)
        if machine and s.get('machine') not in (None, machine):
            continue
        out.append({'number': i['number'], 'title': i['title'], 'kind': s.get('kind'), 'value': s.get('value'),
                    'path': s.get('path'), 'machine': s.get('machine'), 'since': s.get('since'),
                    'producer': s.get('producer'), 'labels': label_names(i)})
    return out


def machine_online(name, fresh_min=MACHINE_FRESH_MIN):
    """True if any Health issue this PC reports under checked in within `fresh_min` minutes."""
    for health_name in HEALTH_NAMES.get(name, [name]):
        number = health_issue(health_name)
        if not number:
            continue
        m = re.search(r'\*\*Last check-in:\*\* (\S+)', api('GET', repo_path(f'/issues/{number}')).get('body') or '')
        if m and (dt.datetime.now(dt.timezone.utc) - _parse_iso(m.group(1))).total_seconds() < fresh_min * 60:
            return True
    return False


def condition_met(s, exists=os.path.exists, closed=None):
    """`closed`: optional set of recently closed card numbers, so a sweep checks every card condition with one call."""
    kind, value = s.get('kind'), s.get('value')
    if kind in GATE_KINDS:   # the if / and / or gate: nested conditions
        results = (condition_met(c, exists, closed) for c in (value or []))
        return all(results) if kind == 'all' else any(results)
    if kind == 'file':
        return bool(value) and (exists(value) or exists(clean_path(value)))  # older records kept descriptions
    if kind == 'card':
        if closed is not None:
            return int(value) in closed
        return api('GET', repo_path(f'/issues/{value}')).get('state') == 'closed'
    if kind == 'machine':
        return machine_online(value)
    if kind == 'time':
        return dt.datetime.now(dt.timezone.utc) >= _parse_iso(value)
    return False  # snoozed by hand


def wake_snoozed(machine, exists=os.path.exists):
    """Call once per poll. Any card this machine watches whose condition is now met goes back to approved.
    Cards snoozed by hand (no condition) are left alone. Returns the numbers woken."""
    # rig 10/2: a card parked by hand on top of an older condition snooze (label repeat-hold) stays parked
    mine = [s for s in snoozed(machine) if s['kind'] and s['machine'] == machine
            and 'repeat-hold' not in (s.get('labels') or [])]
    closed = None
    def _waits_on_card(c):
        return c.get('kind') == 'card' or (c.get('kind') in GATE_KINDS and any(_waits_on_card(x) for x in c.get('value') or []))
    if any(_waits_on_card(s) for s in mine):
        since = min(s['since'] or now_iso() for s in mine if _waits_on_card(s))
        since = (_parse_iso(since) - dt.timedelta(days=7)).isoformat()  # also catch cards closed just before
        closed = {i['number'] for i in paged(repo_path('/issues?state=closed&since=' + urllib.parse.quote(since)))}
    woken = []
    for s in mine:
        if condition_met(s, exists, closed):
            set_status(s['number'], 'approved')
            comment(s['number'], f'Snooze condition met ({s["kind"]}: `{s["value"]}`): back to approved at {now_iso()}.')
            woken.append(s['number'])
    if woken:
        wake()
    return woken


SNOOZE_LINE_RE = re.compile(r'^\s*SNOOZE_UNTIL:\s*(.+?)\s*$', re.M)


def parse_snooze_line(text):
    """The Worker's `SNOOZE_UNTIL: <what> | <reason> [| producer=#N]` line (the last one in `text`), as
    {kind, value, reason, producer}, or None. <what> is card:<n>, machine:<name>, time:<ISO>, file:<path>, or a
    bare path or file name (a Windows drive letter like C: is a path, not a kind)."""
    found = SNOOZE_LINE_RE.findall(text or '')
    if not found:
        return None
    parts = [x.strip() for x in found[-1].split('|')]
    what, reason, producer = parts[0].strip('"`'), '', None
    for x in parts[1:]:
        m = re.match(r'producer\s*=\s*#?(\d+)$', x)
        if m:
            producer = int(m.group(1))
        else:
            reason = (reason + ' ' + x).strip()
    kind, value = 'file', what
    m = re.match(r'^(\w{2,}):(.+)$', what)
    if m and m.group(1).lower() in SNOOZE_KINDS:
        kind, value = m.group(1).lower(), m.group(2).strip().strip('"`')
    return {'kind': kind, 'value': value, 'reason': reason, 'producer': producer}


# ---------------------------------------------------------------------------- approval-loop triage
#
# Jake approves a card, the Worker kicks it back as "needs Jake", he approves again, it bounces again. Before
# any card goes back to Jake, the Worker asks a model (the local one first, Claude if that fails) what the card
# is really waiting on. Only a blocker that truly is Jake (a decision, a PIN, a purchase, a login, something
# physical) goes to him; anything else is snoozed on that condition.

TRIAGE_KINDS = ('jake',) + SNOOZE_KINDS
TRIAGE_PROMPT = """You are triaging a Jarvis task card that a Worker wants to send back to Jake for approval.
Jake already approved it{loop}. Work out what the card is actually waiting on.

Answer "jake" ONLY if nothing but Jake can unblock it: a decision or preference only he can make, a PIN,
spending money, posting or sending something outside, deleting something, a password or login, or a
physical action (plug in, load filament, press a button). Otherwise pick what it waits on:
  file     a file that another card, a print, a sync or a download will produce (value: full path, or file name)
  card     another card that has to finish first (value: its number)
  machine  a PC that has to be online: {machines} (value: its name)
  time     a time it can't start before, e.g. a usage reset (value: ISO time, UTC)

Card #{number}: {title}
{body}

Why the Worker stopped it this time:
{reason}

History (oldest first):
{history}

Reply with one JSON object and nothing else:
{{"kind": "jake|file|card|machine|time", "value": "...", "machine": "PC that will see the file (file only)", "why": "one sentence: what it waits on and why it kept looping"}}"""


def bounce_history(number):
    """Every time the card went to Jake, was approved, or was snoozed, oldest first."""
    out = []
    for c in paged(repo_path(f'/issues/{number}/comments')):
        b = c.get('body') or ''
        when = c.get('created_at', '')
        if b.startswith('**Needs Jake:**'):
            out.append({'at': when, 'event': 'needs-jake', 'text': b[len('**Needs Jake:**'):].strip()[:400]})
        elif b.startswith('Approved via'):
            out.append({'at': when, 'event': 'approved', 'text': b[:80]})
        elif SNOOZE_RE.search(b):
            out.append({'at': when, 'event': 'snoozed', 'text': re.sub(r'<!--.*?-->\n?', '', b).strip()[:300]})
        elif b.startswith(RUN_MARK) and '"outcome": "needs-jake"' in b:
            out.append({'at': when, 'event': 'run needs-jake',
                        'text': re.sub(r'<!--.*?-->\n?', '', b).split('<details>')[0].strip()[:400]})
    return out


def triage_prompt(number, reason):
    issue = api('GET', repo_path(f'/issues/{number}'))
    hist = bounce_history(number)
    bounces = sum(1 for h in hist if h['event'] in ('needs-jake', 'run needs-jake'))
    loop = f', and it has already been sent back to him {bounces} time(s)' if bounces else ''
    lines = '\n'.join(f"- {h['at']} {h['event']}: {h['text']}" for h in hist[-12:]) or '- (none)'
    return TRIAGE_PROMPT.format(loop=loop, machines='homebase (the 5060), rig, pi, backup (the junk laptop)', number=number, title=issue['title'],
                                body=(issue.get('body') or '').strip()[:3000], reason=reason.strip()[:1500],
                                history=lines)


def parse_triage(text):
    """The model's JSON verdict, checked. Raises ValueError if it is unusable."""
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        raise ValueError('no JSON in triage answer')
    v = json.loads(m.group(0))
    if v.get('kind') not in TRIAGE_KINDS:
        raise ValueError(f'bad kind {v.get("kind")!r}')
    if v['kind'] != 'jake' and not str(v.get('value') or '').strip():
        raise ValueError('missing value')
    return v


def ask_ollama(prompt, model=None, url=None, timeout=180):
    model = model or os.environ.get('JARVIS_TRIAGE_MODEL', 'baby-jarvis')
    url = url or os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434') + '/api/generate'
    body = json.dumps({'model': model, 'prompt': prompt, 'stream': False, 'format': 'json', 'think': False,
                       'keep_alive': '24h', 'options': {'temperature': 0, 'num_ctx': 8192}}).encode()
    req = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())['response']


def ask_claude(prompt, timeout=300):
    import subprocess
    exe = os.environ.get('CLAUDE_EXE', 'claude')
    r = subprocess.run([exe, '-p', '--output-format', 'text'], input=prompt, capture_output=True, text=True,
                       timeout=timeout, encoding='utf-8', errors='replace')
    return r.stdout


TRIAGE_MARK = '<!-- jarvis:triage -->'


def park_for_triage(number, machine, reason, verdict=None):
    """LOOP: snooze + unclaim the card and label it for the hub's triage lane (triage + needs-claude-review).
    Jake is not asked. Works from any PC: it is only labels and one comment on GitHub."""
    issue = api('GET', repo_path(f'/issues/{number}'))
    keep = [n for n in label_names(issue) if not n.startswith(('claimed:', 'status:'))]
    api('PUT', repo_path(f'/issues/{number}/labels'),
        {'labels': keep + ['status:snoozed'] + [x for x in ('triage', 'needs-claude-review') if x not in keep]})
    v = verdict or {}
    comment(number, f'{TRIAGE_MARK}\nLoop net: #{number} came back for Jake again ({v.get("bounces", "?")} time(s) before'
                    + (', same reason' if v.get('same_reason') else '') + '), so it went to Claude for loop triage instead '
                    f'of to Jake. Worker on {machine} said: {str(reason).strip()[:500]}', dedupe=False)


def send_back(number, machine, reason, models=None):
    """Use instead of ask_jake whenever a Worker would send a card back to Jake.

    Asks the models in turn (default: local Ollama, then Claude) what the card waits on. Snoozes it on that, or
    asks Jake only when the blocker really is him, or when the card has already been snoozed MAX_SNOOZES times
    and is still stuck, or no model gave a usable answer. Returns ('snoozed', kind, value) or ('needs-jake',)."""
    # Loop net (Jake 10/03): a card that already came back for Jake once never reaches him again; it is parked for
    # Claude's loop triage instead (the hub's triage lane picks up the `triage` label within 2 minutes).
    ln = _loopnet()
    if ln:
        try:
            v = ln.gate(number, machine, reason)
        except Exception:  # noqa: BLE001
            v = {'verdict': 'first'}
        if v.get('verdict') == 'loop':
            park_for_triage(number, machine, reason, v)
            return ('loop',)
    # needs-Jake catcher (Jake 10/03): if Claude verifies the reason really says Jake must act, it goes straight to his
    # To-Do (step + parked) instead of the snooze triage below.
    c = catch_jake_text(number, reason, 'send_back')
    if c and str(c.get('result', '')).startswith(('verified', 'held')):
        return ('needs-jake',) if c['result'].startswith('verified') else ('held',)
    models = models if models is not None else [ask_ollama, ask_claude]
    hist = bounce_history(number)
    if sum(1 for h in hist if h['event'] == 'snoozed') >= MAX_SNOOZES:
        ask_jake(number, f'{reason}\n\nThis card has been snoozed {MAX_SNOOZES} times and is still stuck, so it '
                         'needs a look. Snooze history is above.')
        return ('needs-jake',)
    prompt, verdict, errors = triage_prompt(number, reason), None, []
    for ask in models:
        try:
            verdict = parse_triage(ask(prompt))
            break
        except Exception as e:  # noqa: BLE001 - a down model falls through to the next one
            errors.append(f'{getattr(ask, "__name__", "model")}: {e}')
    if verdict and verdict['kind'] == 'machine':
        try:
            if machine_name(verdict.get('value')) == machine_name(machine):
                errors.append(f'triage said wait for {machine}, which is this PC (already online): asking Jake')
                verdict = dict(verdict, kind='jake')
        except ValueError:
            pass
    if verdict and verdict['kind'] != 'jake':
        try:
            value = snooze_until(number, verdict['kind'], verdict['value'],
                                 verdict.get('machine') if verdict['kind'] == 'file' and verdict.get('machine') in MACHINES
                                 else machine, f'{verdict.get("why", "")}\n\nWorker said: {reason}'.strip())
            return ('snoozed', verdict['kind'], value)
        except (ValueError, GitHubError) as e:
            errors.append(f'snooze: {e}')
    why = (verdict or {}).get('why')
    ask_jake(number, reason + (f'\n\nTriage: {why}' if why else '') +
             (f'\n\n<!-- triage errors: {"; ".join(errors)[:500]} -->' if errors else ''))
    return ('needs-jake',)


# ---------------------------------------------------------------------------- Jake steps (the To-Do list)
#
# Whenever a card stops for Jake (ask_jake, a needs-jake run, send_back), it also gets ONE structured
# "Jake step": where he does it, what to do, why, the steps and one link, or a question to answer. The phone
# app's To-Do tab lists these grouped by place; a tick or an answer there comments on the card and sends it on.
# The step is a hidden marker comment, so the card's history stays readable and nothing else has to change.

# merged 10/03 from Flow claude/loopnet 8f9962f (PR #48). The rig never writes RESET_MARK, but approvals and
# questions written by other PCs do, so jake_step_of() still honours it.
RESET_MARK = '<!-- jarvis:reset -->'
JAKE_RE = re.compile(r'<!-- jarvis:jake (\{.*?\}) -->', re.S)
JAKE_PLACES = ('rig', '5060', 'phone', 'homework')
JAKE_AFTER = ('approve', 'close')
JAKE_PROMPT = """A Jarvis task card has stopped because it needs Jake. Turn the reason into ONE clear step for his
To-Do list. Jake has ADHD: plain words, verb first, no jargon, no card ids in the steps, nothing he does not need.

Card #{number}: {title}
{body}

Why it needs Jake:
{reason}

Places: "rig" (big desktop PC), "5060" (the homebase laptop), "phone" (anything done in a browser, an app or an
answer), "homework" (school). Pick where the hands-on part happens. Answers and decisions are "phone".

Reply with one JSON object and nothing else:
{{"place": "rig|5060|phone|homework", "title": "verb-first, under 70 characters",
  "why": "one sentence: what it unlocks", "mins": 5,
  "steps": ["3 to 5 short steps, only when he has to do something"],
  "button": {{"label": "2-4 words", "url": "https://... the exact page, only if one exists in the text above"}},
  "ask": "the question to answer, only when the card needs an answer instead of an action",
  "after": "approve if his step lets the card run again, close if the card is only his task"}}"""


def _clean_step(raw, issue, reason):
    """Check a model's step and fill what is missing, so a bad answer still gives a usable To-Do item."""
    s = raw if isinstance(raw, dict) else {}
    title = str(s.get('title') or issue.get('title') or 'Card needs you').strip()[:90]
    place = str(s.get('place') or '').strip().lower()
    if place not in JAKE_PLACES:
        place = 'phone'
    step = {'place': place, 'title': title,
            'why': str(s.get('why') or reason or '').strip().split('\n')[0][:200]}
    try:
        mins = int(s.get('mins') or 0)
        if 0 < mins <= 240:
            step['mins'] = mins
    except (TypeError, ValueError):
        pass
    steps = [str(x).strip()[:200] for x in (s.get('steps') or []) if str(x).strip()][:6]
    if steps:
        step['steps'] = steps
    b = s.get('button') or {}
    if isinstance(b, dict) and str(b.get('url') or '').startswith(('https://', 'http://')):
        step['button'] = {'label': str(b.get('label') or 'Open').strip()[:30], 'url': str(b['url']).strip()}
    ask = str(s.get('ask') or '').strip()
    if ask:
        step['ask'] = ask[:500]
    if not steps and not ask:
        step['ask'] = (reason or title).strip()[:500]  # nothing to do by hand: it must be a question
    step['after'] = s.get('after') if s.get('after') in JAKE_AFTER else 'approve'
    if 'pin' in label_names(issue):
        step['pin'] = True  # the To-Do tab asks for the PIN before it approves
    return step


def make_jake_step(number, reason, models=None):
    """Ask a model (local first, then Claude) to turn `reason` into a Jake step. Never raises."""
    issue = api('GET', repo_path(f'/issues/{number}'))
    prompt = JAKE_PROMPT.format(number=number, title=issue.get('title', ''),
                                body=(issue.get('body') or '').strip()[:2000], reason=(reason or '').strip()[:1500])
    for ask in (models if models is not None else [ask_ollama, ask_claude]):
        try:
            m = re.search(r'\{.*\}', ask(prompt) or '', re.S)
            if m:
                return _clean_step(json.loads(m.group(0)), issue, reason)
        except Exception:  # noqa: BLE001 - a down model falls through to the next one, then to the fallback
            continue
    return _clean_step({}, issue, reason)


def jake_step(number, step):
    """Attach a Jake step to a card (the newest one wins). `step` is checked first."""
    issue = api('GET', repo_path(f'/issues/{number}'))
    step = _clean_step(step, issue, step.get('why', '') if isinstance(step, dict) else '')
    step['since'] = now_iso()
    comment(number, f'<!-- jarvis:jake {json.dumps(step)} -->\n**On Jake\'s To-Do ({step["place"]}):** '
                    f'{step["title"]}', dedupe=False)
    return step


def jake_step_of(number):
    """The card's current Jake step, or None. A step written before the card's last approval no longer counts."""
    current = None
    for c in paged(repo_path(f'/issues/{number}/comments')):
        b = c.get('body') or ''
        m = JAKE_RE.search(b)
        if m:
            try:
                current = json.loads(m.group(1))
            except ValueError:
                pass
        elif b.startswith('Approved via') or (RESET_MARK in b and not b.startswith('**Needs Jake:**')):
            current = None
    return current


def _attach_jake_step(number, reason):
    try:
        jake_step(number, make_jake_step(number, reason))
    except Exception:  # noqa: BLE001 - the card is already with Jake; a missing To-Do item must not break that
        pass


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
    s = sub.add_parser('jake-step', help="write a card's To-Do item for Jake (from a reason, or --json)")
    s.add_argument('number', type=int)
    s.add_argument('reason', nargs='?', default='')
    s.add_argument('--json', help='a ready step: {"place", "title", "why", "steps", "button", "ask", "after"}')
    s = sub.add_parser('jake-backfill', help='give every needs-jake card without a To-Do item one')
    s.add_argument('--dry-run', action='store_true')
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
        elif a.cmd == 'jake-step':
            step = json.loads(a.json) if a.json else make_jake_step(a.number, a.reason)
            print(json.dumps(jake_step(a.number, step), indent=1))
        elif a.cmd == 'jake-backfill':
            for i in paged(repo_path('/issues?state=open&labels=status:needs-jake&per_page=100')):
                if i.get('pull_request') or jake_step_of(i['number']):
                    continue
                hist = [h for h in bounce_history(i['number']) if h['event'] in ('needs-jake', 'run needs-jake')]
                reason = hist[-1]['text'] if hist else (i.get('body') or i['title'])
                print(f"#{i['number']} {i['title'][:60]}")
                if not a.dry_run:
                    _attach_jake_step(i['number'], reason)
        elif a.cmd == 'health':
            with open(a.json, encoding='utf-8-sig') as f:
                h = json.load(f)
            print(write_health(a.machine, h.get('fields', {}), h.get('snapshot', ''), h.get('alerts') or []))
        elif a.cmd == 'usage':
            for t in usage(a.days):
                print(f"#{t['number']:<5} {t['minutes']:7.1f} min  {t['runs']} runs  {t['outcomes']}")
        elif a.cmd == 'wake':
            wake(force=True)
    except GitHubError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
