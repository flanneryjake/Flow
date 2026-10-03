"""Tars voice commands for the cross-connect helpers (phase 4a): check on and restart the other machines.

Recognised from Jake's words with regexes (like home.py and actions.py), never chosen by the model:
  - "how's the rig", "check on the junk laptop", "5060 status"   -> signed `status` call to that machine's node agent
  - "how are the machines", "check all the PCs"                 -> `status` on 5060, rig and junk
  - "restart searxng", "restart tars on the 5060"                -> `restart_service`, after Jake says "confirm"
  - "restart the junk laptop"                                    -> `restart_pc` (graceful, 60 s), after "confirm"

Restarts always ask first and run only if Jake says "confirm" within 60 seconds. What may run on which machine,
and how often, is decided by that machine's agent from approvals.json; a refusal is read back as-is. The calls are
signed with the 5060's own key through jarvis/crossconnect/client.py (C:\\Jarvis\\crossconnect). Standard library
only here; client.py needs the `cryptography` package, which the 5060 already has.
"""
import os
import re
import sys
import time

CROSSCONNECT = os.environ.get('JARVIS_CROSSCONNECT', r'C:\Jarvis\crossconnect')
CONFIRM_S = 60

NODES = {   # spoken name -> node name in approvals.json
    'rig': 'rig', 'big pc': 'rig', 'jarvis': 'rig', 'desktop': 'rig',
    '5060': '5060', 'homebase': '5060', 'home base': '5060', 'tars': '5060', 'laptop': '5060',
    'junk laptop': 'junk', 'junk': 'junk', 'junky pos': 'junk', 'junky': 'junk', 'backup': 'junk',
    'backup laptop': 'junk', 'hal': 'hal9000', 'hal9000': 'hal9000', 'hal 9000': 'hal9000', 'pi': 'hal9000',
}
SAY = {'rig': 'the rig', '5060': 'the 5060', 'junk': 'the junk laptop', 'hal9000': 'Hal'}
SERVICE_HOME = {   # service -> (node, name in that node's config) when Jake doesn't say which machine
    'searxng': ('junk', 'searxng'), 'search': ('junk', 'searxng'),
    'home assistant': ('junk', 'home-assistant'), 'mosquitto': ('junk', 'mosquitto'),
    'wake relay': ('junk', 'wake-relay'), 'tars': ('5060', 'tars'), 'hub': ('5060', 'hub'),
    'jellyfin': ('rig', 'jellyfin'),
}
ALL = ('5060', 'rig', 'junk')

_NODE = '|'.join(sorted((re.escape(k) for k in NODES), key=len, reverse=True))
STATUS_RE = re.compile(rf"\b(?:how(?:'s| is| are)|check(?:\s+on)?|status\s+(?:of|on)|health\s+of)\s+(?:the\s+)?"
                       rf"(?P<node>{_NODE})\b(?!\s+(?:online|offline|up|down|on|off|awake|asleep)\b)|"
                       rf"\b(?P<node2>{_NODE})\s+(?:status|health)\b", re.I)
ALL_RE = re.compile(r"\b(?:how(?:'s| is| are)|check(?:\s+on)?|status\s+of)\s+(?:all\s+)?(?:the\s+|my\s+)?"
                    r"(?:machines|pcs|computers|fleet)\b", re.I)
_PC = '|'.join(sorted((re.escape(k) for k in NODES if k not in ('tars', 'jarvis')), key=len, reverse=True))
RESTART_PC_RE = re.compile(rf"\b(?:restart|reboot)\s+(?:the\s+)?(?P<node>{_PC})(?:\s+(?:pc|computer|machine))?"
                           r"\s*[.!?]*$", re.I)
RESTART_SVC_RE = re.compile(rf"\brestart\s+(?:the\s+)?(?P<svc>[a-z][a-z0-9 -]{{1,30}}?)"
                            rf"(?:\s+(?:on|at)\s+(?:the\s+)?(?P<node>{_NODE}))?\s*[.!?]*$", re.I)
CONFIRM_RE = re.compile(r"^\s*(?:jarvis[,.]?\s+)?(?:confirm(?:ed)?|yes,?\s+(?:do it|confirm)|do it)\s*[.!]*\s*$", re.I)
CANCEL_RE = re.compile(r"^\s*(?:jarvis[,.]?\s+)?(?:cancel|never ?mind|no,?\s+don'?t|abort)\b", re.I)

_pending = {}   # the one restart waiting for "confirm": {"cmd", "node", "args", "say", "at"}


def _call(node, cmd, args=None):
    if CROSSCONNECT not in sys.path:
        sys.path.insert(0, CROSSCONNECT)
    import client   # noqa: E402  (lazy: Tars still starts on a machine without the helper)
    return client.call(node, cmd, args or {})


def _service_words(name):
    return name.replace('-', ' ')


def describe(node, out):
    """One spoken sentence for a status reply."""
    who = SAY.get(node, node)
    if not out.get('ok'):
        err = str(out.get('error', ''))
        if err.startswith('unreachable'):
            return (f"I can't reach {who}'s helper. "
                    + ("It's probably asleep, which is normal for the rig." if node == 'rig' else
                       "It may be off or off the tailnet."))
        return f"{who[0].upper() + who[1:]} turned me down, sir: {err}."
    svcs = out.get('services') or {}
    down = [_service_words(k) for k, v in svcs.items() if v == 'down']
    up = [_service_words(k) for k, v in svcs.items() if v == 'up']
    mem, bits = out.get('memory') or {}, []
    if mem.get('free_gb') is not None and mem.get('total_gb'):
        bits.append(f"{mem['free_gb']:g} of {mem['total_gb']:g} GB of memory free")
    if out.get('disk_free_gb') is not None:
        bits.append(f"{out['disk_free_gb']:g} GB of disk free")
    if out.get('uptime_h') is not None:
        bits.append(f"up {round(out['uptime_h'])} hours")
    head = (f"{who[0].upper() + who[1:]} needs a look: {_join(down)} {'is' if len(down) == 1 else 'are'} down"
            if down else f"{who[0].upper() + who[1:]} is fine, sir")
    tail = (f"; {_join(up)} up" if up and down else f": {_join(up)} up" if up else '')
    paused = ' It is paused in fleet control.' if out.get('paused') else ''
    return head + tail + (', ' + ', '.join(bits) if bits else '') + '.' + paused


def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1] if items else ''


def _ask(cmd, node, args, say):
    _pending.clear()
    _pending.update(cmd=cmd, node=node, args=args, say=say, at=time.time())
    return f"{say[0].upper() + say[1:]}? Say \"confirm\" within a minute and I'll do it."


def _run_pending():
    p = dict(_pending)
    _pending.clear()
    out = _call(p['node'], p['cmd'], p['args'])
    if out.get('ok'):
        if p['cmd'] == 'restart_pc':
            return f"Done. {SAY[p['node']][0].upper() + SAY[p['node']][1:]} restarts in one minute."
        return f"Done, sir: {out.get('summary') or p['say']}."
    err = str(out.get('error', 'no reason given'))
    if err.startswith('unreachable'):
        return f"I couldn't reach {SAY.get(p['node'], p['node'])}'s helper, so nothing was restarted."
    return f"{SAY.get(p['node'], p['node'])[0].upper() + SAY.get(p['node'], p['node'])[1:]} refused: {err}."


def act(text, log=print):
    """Run a fleet command if Jake's words ask for one. Returns the spoken reply, or None."""
    t = (text or '').strip()
    try:
        if _pending and time.time() - _pending['at'] > CONFIRM_S:
            _pending.clear()
        if _pending and CONFIRM_RE.match(t):
            log(f"fleet: confirmed {_pending['cmd']} on {_pending['node']}")
            return _run_pending()
        if _pending and CANCEL_RE.match(t):
            _pending.clear()
            return 'Cancelled. Nothing was restarted.'
        if ALL_RE.search(t):
            return ' '.join(describe(n, _call(n, 'status')) for n in ALL)
        m = STATUS_RE.search(t)
        if m:
            node = NODES[(m.group('node') or m.group('node2')).lower()]
            return describe(node, _call(node, 'status'))
        m = RESTART_PC_RE.search(t)
        if m:
            node = NODES[m.group('node').lower()]
            return _ask('restart_pc', node, {}, f"restart {SAY[node]}")
        m = RESTART_SVC_RE.search(t)
        if m:
            svc = m.group('svc').strip().lower()
            if m.group('node'):
                node, name = NODES[m.group('node').lower()], svc.replace(' ', '-')
            elif svc in SERVICE_HOME:
                node, name = SERVICE_HOME[svc]
            else:
                return None   # "restart the movie" and the like: not ours
            return _ask('restart_service', node, {'name': name}, f"restart {_service_words(name)} on {SAY[node]}")
    except Exception as e:  # noqa: BLE001
        log(f'fleet failed: {type(e).__name__}: {str(e)[:160]}')
        return "I couldn't reach the machine helpers just now. It's in the Tars log."
    return None
