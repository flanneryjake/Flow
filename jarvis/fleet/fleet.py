"""Jarvis fleet control: take a PC off the network safely, and keep every role covered when one goes down.

State lives in GitHub, not on any PC, so it survives whichever machine is off:
  * one pinned "Fleet control" issue in the tasks repo. Its body holds each machine's mode:
      active    normal
      paused    stays online, its Worker takes no new cards (finishes the one it is on)
      isolated  paused, finishes the card it is running, then leaves the tailnet
                (`tailscale down`). Internet and Remote Control stay up, so it can always be brought back.
  * one comment per machine on that issue, rewritten by that machine's `tick` every 2 minutes (heartbeat:
    mode it applied, tailnet state, which role ports answer).

Every PC runs `fleet.py tick` from a scheduled task (install-fleet.ps1). A tick:
  1. writes this machine's heartbeat;
  2. applies this machine's mode (tailscale down/up, writes C:\\Jarvis\\fleet\\paused.flag for anything
     local that wants to honour it);
  3. if this machine is the first live Worker, hands back cards still claimed by a machine that is isolated
     or has been silent for 30+ min, so a dead PC never strands a card in status:working.
     A card still claimed by a PC after it has left the tailnet is handed back the same way.

ghq.ready() asks may_take_cards() first, so a paused or isolated Worker picks up nothing new.

Roles and their failover order are in roles.json next to this file. `plan` shows, before anything is
disconnected, which roles move to which machine and which would be left with nobody.

Standard library only. Needs GITHUB_TASKS_TOKEN like ghq.py. CLI: python fleet.py --help
"""
import argparse
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, '..', 'ghq'), r'C:\Jarvis\ghq'):
    if os.path.exists(os.path.join(p, 'ghq.py')) and p not in sys.path:
        sys.path.insert(0, p)
import ghq  # noqa: E402

ROLES_FILE = os.environ.get('JARVIS_ROLES_FILE', os.path.join(HERE, 'roles.json'))
LOCAL_DIR = os.environ.get('JARVIS_FLEET_DIR', r'C:\Jarvis\fleet' if os.name == 'nt' else HERE)
PAUSED_FLAG = os.path.join(LOCAL_DIR, 'paused.flag')
CACHE_FILE = os.path.join(LOCAL_DIR, 'fleet-cache.json')
MODES = ('active', 'paused', 'isolated')
STATE_MARK = '<!-- jarvis:fleet '
STATE_RE = re.compile(r'<!-- jarvis:fleet (\{.*?\}) -->', re.S)
BEAT_RE = re.compile(r'^<!-- jarvis:fleetbeat (\S+) (\{.*?\}) -->', re.S)
# Each PC's Claude guard (jarvis/claude-guard) keeps its own comment on the same issue: Claude process counts.
CLAUDE_RE = re.compile(r'^<!-- jarvis:claudewatch (\S+) (\{.*?\}) -->', re.S)
FRESH_MIN = 6       # a heartbeat newer than this counts as "up" (tick runs every 2 min)
STRANDED_MIN = 30   # claims held by a machine silent this long go back to the queue
CACHE_MAX_S = 300   # may_take_cards() trusts a cached state this long when GitHub can't be reached


def load_roles():
    with open(ROLES_FILE, encoding='utf-8-sig') as f:
        return json.load(f)


def this_machine():
    for p in (r'C:\Jarvis\watchdog\machine.txt', os.path.join(LOCAL_DIR, 'machine.txt')):
        try:
            with open(p, encoding='utf-8-sig') as f:
                name = f.read().strip()
            if name:
                return name
        except OSError:
            pass
    computer = (os.environ.get('COMPUTERNAME') or socket.gethostname()).upper()
    for name, m in load_roles()['machines'].items():
        if m.get('computer', '').upper() == computer:
            return name
    return computer.lower()


def _parse_time(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except ValueError:
        return None


def _fresh(at, minutes):
    age = _age_min(at) if at else None
    return age is not None and age < minutes


def _age_min(s):
    t = _parse_time(s)
    if not t:
        return None
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 60


# ---------------------------------------------------------------------------- GitHub state

def fleet_issue(create=False):
    for i in ghq.paged(ghq.repo_path('/issues?state=open&labels=fleet')):
        if i['title'].startswith('Fleet control'):
            return i
    if not create:
        return None
    try:
        ghq.api('POST', ghq.repo_path('/labels'), {'name': 'fleet', 'color': '0052cc',
                                                   'description': 'Fleet control: which PCs are online'})
    except ghq.GitHubError:
        pass  # already exists
    issue = ghq.api('POST', ghq.repo_path('/issues'), {
        'title': 'Fleet control', 'labels': ['fleet'], 'body': render_body({'machines': {}})})
    try:
        ghq.request('POST', '/graphql', {'query': 'mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{number}}}',
                                         'variables': {'id': issue['node_id']}})
    except ghq.GitHubError:
        pass
    return issue


def state_of(issue):
    m = STATE_RE.search((issue or {}).get('body') or '')
    state = json.loads(m.group(1)) if m else {}
    state.setdefault('machines', {})
    # An isolation with an end time lapses on its own, so a forgotten one can't keep a PC off forever.
    for name, s in state['machines'].items():
        if s.get('mode') != 'active' and s.get('until'):
            age = _age_min(s['until'])
            if age is not None and age >= 0:
                s.update(mode='active', expired=True)
    return state


def mode_of(state, machine):
    return (state['machines'].get(machine) or {}).get('mode', 'active')


def render_body(state):
    roles = load_roles()
    rows = ['| Machine | Mode | Since | By | Reason |', '|---|---|---|---|---|']
    for name in roles['machines']:
        s = state['machines'].get(name) or {}
        mode = s.get('mode', 'active')
        if s.get('until') and mode != 'active':
            mode += f" until {s['until']}"
        rows.append(f"| {name} | {mode} | {s.get('since', '')} | {s.get('by', '')} | "
                    f"{str(s.get('reason', '')).replace('|', '/')} |")
    return '\n'.join([
        'Which Jarvis PCs are online. Set from the phone app (System > Fleet) or `fleet.py`.',
        'To bring a PC back by hand from GitHub: edit this issue and change its `"mode"` in the JSON line to '
        '`"active"`. The PC rejoins within 2 minutes.', '', *rows, '',
        f'{STATE_MARK}{json.dumps(state, sort_keys=True)} -->'])


def set_mode(machine, mode, by='cli', reason='', hours=None):
    roles = load_roles()
    if machine not in roles['machines']:
        raise ValueError(f'unknown machine {machine!r}; known: {", ".join(roles["machines"])}')
    if mode not in MODES:
        raise ValueError(f'mode must be one of {MODES}')
    issue = fleet_issue(create=True)
    state = state_of(issue)
    entry = {'mode': mode, 'since': ghq.now_iso(), 'by': by, 'reason': reason}
    if hours and mode != 'active':
        until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=float(hours))
        entry['until'] = until.replace(microsecond=0).isoformat()
    state['machines'][machine] = entry
    ghq.api('PATCH', ghq.repo_path(f'/issues/{issue["number"]}'), {'body': render_body(state)})
    ghq.comment(issue['number'], f'{machine} set to **{mode}** by {by}' + (f': {reason}' if reason else '') +
                (f' (until {entry["until"]})' if entry.get('until') else ''))
    return entry


def heartbeats(issue, claude=None):
    """machine -> {'at': iso, ...heartbeat fields, 'comment_id': id}. Pass a dict as `claude` to also get each
    machine's Claude guard line (counts, caps, over) from the same comment pages."""
    out = {}
    for c in ghq.paged(ghq.repo_path(f'/issues/{issue["number"]}/comments')):
        if claude is not None:
            cm = CLAUDE_RE.match(c.get('body') or '')
            if cm:
                try:
                    claude[cm.group(1)] = json.loads(cm.group(2))
                except ValueError:
                    pass
                continue
        m = BEAT_RE.match(c.get('body') or '')
        if m:
            beat = json.loads(m.group(2))
            beat['comment_id'] = c['id']
            beat['at'] = c.get('updated_at') or beat.get('at')
            out[m.group(1)] = beat
    return out


def write_heartbeat(issue, machine, beat, existing=None):
    ports = ', '.join(f"{k} {'up' if v else 'down'}" for k, v in beat.get('ports', {}).items()) or 'none checked'
    body = (f'<!-- jarvis:fleetbeat {machine} {json.dumps(beat, sort_keys=True)} -->\n'
            f'**{machine}** checked in at {beat["at"]} · mode {beat["mode"]} · tailnet {beat["tailnet"]} · {ports}')
    if existing:
        ghq.api('PATCH', ghq.repo_path(f'/issues/comments/{existing}'), {'body': body})
    else:
        ghq.comment(issue['number'], body)


# ---------------------------------------------------------------------------- views

def snapshot():
    """Everything the panel shows: each machine's mode and liveness, and who serves each role."""
    roles = load_roles()
    issue = fleet_issue()
    state = state_of(issue)
    claude = {}
    beats = heartbeats(issue, claude) if issue else {}
    machines = {}
    for name, m in roles['machines'].items():
        b = beats.get(name) or {}
        age = _age_min(b['at']) if b.get('at') else None
        cw = claude.get(name)
        if cw:
            cw_age = _age_min(cw.get('at')) if cw.get('at') else None
            cw = dict(cw, minutes_ago=None if cw_age is None else round(cw_age, 1))
        machines[name] = {
            'label': m.get('label', name), 'mode': mode_of(state, name),
            'state': state['machines'].get(name) or {},
            'last_seen': b.get('at'), 'minutes_ago': None if age is None else round(age, 1),
            'up': age is not None and age < FRESH_MIN,
            'tailnet': b.get('tailnet', 'unknown'), 'ports': b.get('ports', {}),
            'applied': b.get('mode'),
            'claude': cw,
        }
    return {'issue': issue and issue['number'], 'machines': machines,
            'roles': coverage(roles, machines), 'checked_at': ghq.now_iso()}


def serving(roles, machines, role, exclude=()):
    """Machines able to serve `role` right now, in failover order."""
    r = roles['roles'][role]
    out = []
    for name in r['order']:
        m = machines.get(name) or {}
        if name in exclude or m.get('mode') != 'active' or not m.get('up'):
            continue
        port = str(r.get('port') or '')
        if port and m.get('ports', {}).get(port) is False:
            continue
        out.append(name)
    return out


def coverage(roles, machines, exclude=()):
    out = {}
    for role, r in roles['roles'].items():
        live = serving(roles, machines, role, exclude)
        out[role] = {'label': r.get('label', role), 'order': r['order'], 'serving': live[0] if live else None,
                     'standby': live[1:], 'note': r.get('note', '')}
    return out


def plan(machine, snap=None):
    """What happens if `machine` is taken off: role by role, who takes over and what is left uncovered."""
    snap = snap or snapshot()
    roles = load_roles()
    before = snap['roles']
    after = coverage(roles, snap['machines'], exclude=(machine,))
    moves, uncovered = [], []
    for role, b in before.items():
        if machine not in roles['roles'][role]['order']:
            continue
        a = after[role]
        if b['serving'] != machine:
            continue  # it isn't serving this role right now, so nothing changes
        if a['serving']:
            moves.append({'role': role, 'label': b['label'], 'to': a['serving']})
        else:
            uncovered.append({'role': role, 'label': b['label'], 'note': b['note']})
    held = [c for c in claimed_cards() if c['machine'] == machine]
    pinned = pinned_cards(machine)
    warnings = []
    if machine == this_hub(snap):
        warnings.append('This PC serves the phone app. Once it leaves the tailnet the app will not load; bring it '
                        'back from the Fleet control issue on GitHub or by asking Claude in the project.')
    return {'machine': machine, 'moves': moves, 'uncovered': uncovered, 'warnings': warnings,
            'running_cards': [c['number'] for c in held], 'waiting_pinned_cards': pinned,
            'safe': not uncovered}


def this_hub(snap):
    return snap['roles'].get('hub', {}).get('serving')


def claimed_cards():
    out = []
    for i in ghq.paged(ghq.repo_path('/issues?state=open&labels=status:working')):
        for n in ghq.label_names(i):
            if n.startswith('claimed:'):
                out.append({'number': i['number'], 'title': i['title'], 'machine': n.split(':', 1)[1]})
    return out


def pinned_cards(machine):
    return [i['number'] for i in ghq.paged(ghq.repo_path(
        f'/issues?state=open&labels=status:approved,machine:{machine}'))]


# ---------------------------------------------------------------------------- the Worker gate

def may_take_cards(machine):
    """False when this machine is paused or isolated. Fails open: if GitHub can't be read and there is no
    recent cached answer, the Worker keeps working rather than stalling the whole queue."""
    try:
        with open(CACHE_FILE, encoding='utf-8') as f:
            cache = json.load(f)
        if cache.get('machine') == machine and _fresh(cache.get('at'), CACHE_MAX_S / 60):
            return cache.get('mode', 'active') == 'active'
    except (OSError, ValueError):
        pass
    if os.path.exists(PAUSED_FLAG):
        return False
    try:
        mode = mode_of(state_of(fleet_issue()), machine)
    except ghq.GitHubError:
        return True
    _write_cache(machine, mode)
    return mode == 'active'


def _write_cache(machine, mode):
    try:
        os.makedirs(LOCAL_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'machine': machine, 'mode': mode, 'at': ghq.now_iso()}, f)
    except OSError:
        pass


# ---------------------------------------------------------------------------- tick (runs on each PC)

def _port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', int(port)), timeout=2):
            return True
    except OSError:
        return False


def _tailscale(*args):
    exe = 'tailscale'
    for p in (r'C:\Program Files\Tailscale\tailscale.exe', r'C:\Program Files (x86)\Tailscale\tailscale.exe'):
        if os.path.exists(p):
            exe = p
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def tailscale_up():
    """`tailscale up`, keeping the PC's existing settings. When the PC has non-default settings (unattended mode,
    routes...) a bare `up` refuses and prints the full command to use instead; run that one."""
    code, out = _tailscale('up')
    if code != 0 and 'non-default' in out:
        m = re.search(r'^\s*tailscale up( .*)$', out, re.M)
        if m:
            code, out = _tailscale('up', *m.group(1).split())
    return code, out


def tailnet_state():
    code, out = _tailscale('status', '--json')
    if code != 0 and not out.startswith('{'):
        return 'unknown'
    try:
        return 'up' if json.loads(out).get('BackendState') == 'Running' else 'down'
    except ValueError:
        return 'unknown'


def _local_worker_busy(machine):
    return any(c['machine'] == machine for c in claimed_cards())


def tick(machine=None, dry_run=False):
    machine = machine or this_machine()
    roles = load_roles()
    issue = fleet_issue(create=not dry_run)
    state = state_of(issue)
    mode = mode_of(state, machine)
    log = [f'{ghq.now_iso()} {machine} mode={mode}']
    _write_cache(machine, mode)

    # 1. Pause flag for anything local (the Worker also checks GitHub through may_take_cards()).
    if mode == 'active':
        if os.path.exists(PAUSED_FLAG) and not dry_run:
            os.remove(PAUSED_FLAG)
    elif not dry_run:
        os.makedirs(LOCAL_DIR, exist_ok=True)
        with open(PAUSED_FLAG, 'w', encoding='utf-8') as f:
            f.write(f'{mode} since {(state["machines"].get(machine) or {}).get("since", "")}\n')

    # 2. Network. Isolation waits for this machine's running card to finish or be handed back, so a card is
    #    never cut off mid-run with its claim still on it.
    net = tailnet_state()
    if mode == 'isolated' and net != 'down':
        if _local_worker_busy(machine):
            log.append('isolated: waiting for the running card before leaving the tailnet')
        elif not dry_run:
            code, out = _tailscale('down')
            log.append(f'tailscale down -> {code} {out[:200]}')
            net = tailnet_state()
    elif mode != 'isolated' and net == 'down':
        if not dry_run:
            code, out = tailscale_up()
            log.append(f'tailscale up -> {code} {out[:200]}')
            net = tailnet_state()

    # 3. Heartbeat.
    ports = {}
    for r in roles['roles'].values():
        if machine in r['order'] and r.get('port'):
            ports[str(r['port'])] = _port_open(r['port'])
    beat = {'at': ghq.now_iso(), 'mode': mode, 'tailnet': net, 'ports': ports}
    beats = heartbeats(issue) if issue else {}
    if not dry_run and issue:
        write_heartbeat(issue, machine, beat, (beats.get(machine) or {}).get('comment_id'))
    beats[machine] = dict(beat, at=beat['at'])

    # 4. Hand back cards stranded on a machine that is isolated or silent. Only the first live Worker does it,
    #    so two PCs never release the same card twice.
    machines = {n: {'mode': mode_of(state, n), 'up': n == machine or _fresh(beats.get(n, {}).get('at'), FRESH_MIN),
                    'ports': beats.get(n, {}).get('ports', {})} for n in roles['machines']}
    leader = (serving(roles, machines, 'worker') or [None])[0]
    if leader == machine:
        for card in claimed_cards():
            other = card['machine']
            if other == machine:
                continue
            # An isolated PC finishes its running card before it leaves the tailnet, so its claim only counts
            # as stranded once it has actually left (or gone silent).
            b = beats.get(other, {})
            silent = _age_min(b['at']) if b.get('at') else None
            gone = mode_of(state, other) == 'isolated' and b.get('tailnet') == 'down'
            if not (gone or (silent is not None and silent >= STRANDED_MIN)):
                continue
            why = 'it left the tailnet' if gone else f'it has been silent for {silent:.0f} min'
            log.append(f'releasing #{card["number"]} from {other} ({why})')
            if not dry_run:
                ghq.log_run(card['number'], other, 'released',
                            summary=f'Handed back to the queue by {machine}: {why}.')
    print('\n'.join(log))
    return log


# ---------------------------------------------------------------------------- local model lookup

def endpoint(role):
    """Base URL of whichever machine serves `role` now, e.g. http://desktop-vllddm4:11434 for local-llm."""
    roles = load_roles()
    snap = snapshot()
    name = snap['roles'][role]['serving']
    if not name:
        return None
    port = roles['roles'][role].get('port')
    host = roles['machines'][name]['host']
    return f'http://{host}:{port}' if port else host


# ---------------------------------------------------------------------------- CLI

def _print_status(snap):
    print('Machines:')
    for name, m in snap['machines'].items():
        seen = f"{m['minutes_ago']} min ago" if m['minutes_ago'] is not None else 'never'
        print(f"  {name:9} {m['mode']:9} {'up' if m['up'] else 'DOWN':5} tailnet {m['tailnet']:8} seen {seen}")
    print('Roles:')
    for role, r in snap['roles'].items():
        who = r['serving'] or 'NOBODY'
        print(f"  {r['label']:30} {who}" + (f"  (standby {', '.join(r['standby'])})" if r['standby'] else ''))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status', help='modes, liveness and who serves each role')
    s = sub.add_parser('plan', help='what disconnecting a machine would do (changes nothing)')
    s.add_argument('machine')
    for name, helptext in (('pause', 'stop taking new cards, stay online'),
                           ('isolate', 'pause, finish the running card, then leave the tailnet')):
        s = sub.add_parser(name, help=helptext)
        s.add_argument('machine')
        s.add_argument('--reason', default='')
        s.add_argument('--hours', type=float, help='rejoin on its own after this many hours')
        s.add_argument('--force', action='store_true', help='go ahead even if a role is left uncovered')
        s.add_argument('--by', default='cli')
    s = sub.add_parser('rejoin', help='back to active')
    s.add_argument('machine')
    s.add_argument('--by', default='cli')
    s = sub.add_parser('tick', help='run once on this PC (the scheduled task calls this)')
    s.add_argument('--machine')
    s.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('endpoint', help='URL of whoever serves a role now')
    s.add_argument('role')
    a = p.parse_args(argv)
    try:
        if a.cmd == 'status':
            _print_status(snapshot())
        elif a.cmd == 'plan':
            print(json.dumps(plan(a.machine), indent=1))
        elif a.cmd in ('pause', 'isolate'):
            mode = 'paused' if a.cmd == 'pause' else 'isolated'
            pl = plan(a.machine)
            if not pl['safe'] and not a.force:
                print('Not changed. This would leave nobody on: ' +
                      ', '.join(u['label'] for u in pl['uncovered']) + '. Re-run with --force to go ahead.')
                return 4
            print(json.dumps(set_mode(a.machine, mode, a.by, a.reason, a.hours)))
        elif a.cmd == 'rejoin':
            print(json.dumps(set_mode(a.machine, 'active', a.by)))
        elif a.cmd == 'tick':
            tick(a.machine, a.dry_run)
        elif a.cmd == 'endpoint':
            print(endpoint(a.role) or '')
    except (ghq.GitHubError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
