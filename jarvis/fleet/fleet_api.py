"""Fleet endpoints for the phone hub (server.py). Framework-free so it drops into any handler:

    import fleet_api
    res = fleet_api.handle(method, path, body_dict)   # None when the path isn't a fleet path
    if res is not None:
        status, payload = res
        ...send payload as JSON with that status...

  GET  /api/fleet                         machines, modes, liveness, who serves each role
  GET  /api/fleet/plan?machine=rig        what disconnecting it would move and leave uncovered
  POST /api/fleet/mode {machine, mode: active|paused|isolated, hours?, reason?, force?}
       Pausing or disconnecting a machine that would leave a role with nobody returns 409 with the plan,
       unless force is true. The panel shows the plan and asks before sending force.
"""
import json
import os
import re
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import fleet  # noqa: E402

_cache = {'at': 0, 'snap': None}
CACHE_S = 20


def _snapshot(fresh=False):
    if fresh or not _cache['snap'] or time.time() - _cache['at'] > CACHE_S:
        _cache.update(snap=_with_claude(fleet.snapshot()), at=time.time())
    return _cache['snap']


_claude = {'at': 0, 'data': {}}
CLAUDE_CACHE_S = 120   # the Claude guard writes every 5 min


def _with_claude(snap):
    """Each PC's Claude guard counts (jarvis/claude-guard) for the panel. A fleet.py that already reads them
    fills machine['claude'] itself; an older or locally changed fleet.py doesn't, so read the guard's
    comments on the Fleet control issue here, at most every 2 minutes."""
    machines = snap.get('machines') or {}
    if not machines or all('claude' in m for m in machines.values()):
        return snap
    if time.time() - _claude['at'] > CLAUDE_CACHE_S:
        data = {}
        try:
            rx = re.compile(r'^<!-- jarvis:claudewatch (\S+) (\{.*?\}) -->', re.S)
            if snap.get('issue'):
                for c in fleet.ghq.paged(fleet.ghq.repo_path(f'/issues/{snap["issue"]}/comments')):
                    m = rx.match(c.get('body') or '')
                    if m:
                        try:
                            data[m.group(1)] = json.loads(m.group(2))
                        except ValueError:
                            pass
        except Exception:  # noqa: BLE001 - counts are optional; never break the panel over them
            data = _claude['data']
        _claude.update(at=time.time(), data=data)
    for name, m in machines.items():
        cw = _claude['data'].get(name)
        if cw:
            age = fleet._age_min(cw.get('at')) if cw.get('at') else None
            cw = dict(cw, minutes_ago=None if age is None else round(age, 1))
        m['claude'] = cw
    return snap


def handle(method, path, body=None):
    url = urllib.parse.urlparse(path)
    if not url.path.startswith('/api/fleet'):
        return None
    q = dict(urllib.parse.parse_qsl(url.query))
    try:
        if method == 'GET' and url.path == '/api/fleet':
            return 200, _snapshot(fresh=q.get('fresh') == '1')
        if method == 'GET' and url.path == '/api/fleet/plan':
            return 200, fleet.plan(q.get('machine', ''), _snapshot())
        if method == 'POST' and url.path == '/api/fleet/mode':
            body = body or {}
            machine, mode = body.get('machine', ''), body.get('mode', '')
            if mode in ('paused', 'isolated') and not body.get('force'):
                pl = fleet.plan(machine, _snapshot(fresh=True))
                if not pl['safe']:
                    return 409, {'error': 'would leave a role uncovered', 'plan': pl}
            entry = fleet.set_mode(machine, mode, by='phone', reason=body.get('reason', ''),
                                   hours=body.get('hours'))
            return 200, {'ok': True, 'entry': entry, 'fleet': _snapshot(fresh=True)}
        return 404, {'error': 'unknown fleet endpoint'}
    except ValueError as e:
        return 400, {'error': str(e)}
    except fleet.ghq.GitHubError as e:
        return 502, {'error': f'GitHub: {e}'}
