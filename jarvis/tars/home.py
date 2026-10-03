"""Home control for Jarvis on Tars, through Home Assistant on the backup laptop (packages/tars_home.yaml).

HA pushes a snapshot to POST /ha every 2 minutes: alarms, what's playing, the listening switch, lights and switches,
plus the private webhook URL home commands go to. So Tars holds no HA token, and the webhook URL lives only in
ha.json next to this file (never logged).

Commands are recognised here in code, not by the model: "play jazz", "turn off the lights", "set an alarm for 6",
"volume 4 in the bedroom", "stop listening". They run as Alexa voice commands on the right Echo. Buying, ordering,
calling and messaging are refused (they wait for Jake's PIN). Standard library only.
"""
import datetime as dt
import json
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, 'ha.json')
HA_WEBHOOKS = os.environ.get('JARVIS_HA_WEBHOOKS', 'http://100.90.201.22:8123/api/webhook/')
ECHOS = {'kitchen': 'media_player.kitchen', 'bedroom': 'media_player.master_bedroom'}
DEFAULT_ECHO = os.environ.get('JARVIS_DEFAULT_ECHO', 'media_player.kitchen')   # the rig mic is in the living room
STALE_S = 15 * 60

LEAD_RE = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+)?(?:jarvis[,!.:]?\s*)?(?:(?:please|can you|could you|would you)\s+)*",
                     re.I)
CMD_RE = re.compile(
    r"^(play|pause|resume|unpause|skip|next song|previous song|shuffle|stop(?: the)? (?:music|song|playing|alarm|timer)"
    r"|stop$|turn (?:it |the music )?(?:up|down)|turn (?:on|off) |switch (?:on|off) |volume|louder|quieter|mute|unmute"
    r"|set (?:an? |the )?(?:alarm|timer|reminder)|cancel (?:my |the |all )*(?:alarms?|timers?|reminders?)|snooze"
    r"|dim |brighten |lights? (?:on|off)|(?:the )?lights? (?:on|off))", re.I)
LISTEN_RE = re.compile(r"^(stop|start|quit|begin)\s+listening\b|^listening\s+(on|off)\b", re.I)
BLOCKED_RE = re.compile(r"\b(buy|order|purchase|reorder|checkout|cart|pay|call|drop in|message|text|send|email|unlock|"
                        r"disarm|delete)\b", re.I)
ROOM_RE = re.compile(r"\s*\b(?:in|on|to)\s+(?:the\s+)?(kitchen|bedroom|master bedroom)(?:\s+echo)?\b", re.I)
ASK_RE = re.compile(r"\b(alarms?|wake me|playing|song|music|listening|lights?|light on|switch(?:es)?|fan|echo)\b", re.I)


def save_snapshot(body):
    """POST /ha from HA. Returns an error string, or '' when stored."""
    url = str((body or {}).get('home_url') or '')
    if not url.startswith(HA_WEBHOOKS):
        return 'home_url must be an HA webhook on the backup laptop'
    body = dict(body, received=dt.datetime.now().astimezone().isoformat(timespec='seconds'))
    tmp = SNAPSHOT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(body, f)
    os.replace(tmp, SNAPSHOT)
    return ''


def snapshot():
    try:
        with open(SNAPSHOT, encoding='utf-8') as f:
            snap = json.load(f)
        age = dt.datetime.now().astimezone() - dt.datetime.fromisoformat(snap['received'])
        snap['stale'] = age.total_seconds() > STALE_S
        return snap
    except (OSError, ValueError, KeyError):
        return {}


def _when(iso):
    try:
        t = dt.datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return None
    return t.strftime('%A %I:%M %p').replace(' 0', ' ')


def facts(text):
    """LIVE lines about the apartment for questions like "when's my alarm" or "what's playing". '' if not relevant."""
    if not ASK_RE.search(text or ''):
        return ''
    snap = snapshot()
    if not snap:
        return 'Home Assistant has not reported in yet, so home state is unknown.'
    lines = []
    for room, key in (('bedroom', 'next_alarm_bedroom'), ('kitchen', 'next_alarm_kitchen')):
        w = _when(snap.get(key))
        if w:
            lines.append(f'Next alarm on the {room} Echo: {w}.')
    if not lines:   # Alexa Media reports "unknown" both when nothing is set and when it can't read the alarms
        lines.append('No upcoming alarm is showing on the Echos (Home Assistant shows none, or cannot read them right '
                     'now). Work-day alarms (Tue-Sat) are set automatically at 9 PM the night before.')
    for ent, e in (snap.get('echos') or {}).items():
        room = 'bedroom' if 'bedroom' in ent else 'kitchen'
        if e.get('state') == 'playing' and e.get('title'):
            lines.append(f'{room.title()} Echo is playing "{e["title"]}"' + (f' by {e["artist"]}' if e.get('artist')
                                                                               else '') + '.')
        else:
            lines.append(f'{room.title()} Echo is {e.get("state") or "unknown"}.')
    lines.append('Jarvis listening (rig mic) is ' + ('on.' if snap.get('listening') else 'off.'))
    devs = snap.get('devices') or {}
    if devs:
        lines.append('Lights and switches: ' + '; '.join(devs.values()) + '.')
    if snap.get('stale'):
        lines.append('(Home Assistant has not reported for over 15 minutes; this may be out of date.)')
    return '\n'.join(lines)


ALARM_Q_RE = re.compile(r"\b(alarms?|wake me|wake-up|wakeup)\b", re.I)
WORK_DAYS = {1, 2, 3, 4, 5}   # Tue-Sat; HA's morning package sets them at 9 PM the night before


def alarm_answer(text, now=None):
    """Alarm questions are answered here from the HA snapshot, never by the model. None if not an alarm question."""
    if not ALARM_Q_RE.search(text or '') or parse(text)[0]:
        return None
    snap = snapshot()
    if not snap:
        return "Home Assistant hasn't checked in with me yet, so I can't see the Echo alarms."
    times = []
    for room, key in (('bedroom', 'next_alarm_bedroom'), ('kitchen', 'next_alarm_kitchen')):
        try:
            times.append((dt.datetime.fromisoformat(snap.get(key)).astimezone(), room))
        except (TypeError, ValueError):
            pass
    if times:
        t, room = min(times)
        return f"Your next alarm is {_when(t.isoformat())} on the {room} Echo."
    now = now or dt.datetime.now().astimezone()
    if (now + dt.timedelta(days=1)).weekday() in WORK_DAYS:
        if now.hour < 21:
            return "Nothing's set on the Echos yet. Tomorrow's work alarms go on at 9 tonight."
        return ("I can't see tomorrow's alarms on the Echos right now. They should have gone on at 9; "
                "say \"set an alarm for 4\" if you'd like to be sure.")
    return "No alarms are showing on the Echos. Tomorrow's a day off."


def _post(url, body, timeout=8):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def parse(text):
    """('alexa', echo, command) / ('listening', on, None) / ('blocked', None, None) / (None, None, None)."""
    t = LEAD_RE.sub('', text or '').strip().rstrip('.!?')
    m = LISTEN_RE.match(t)
    if m:
        word = (m.group(1) or m.group(2)).lower()
        return 'listening', word in ('start', 'begin', 'on'), None
    if not CMD_RE.match(t):
        return None, None, None
    if BLOCKED_RE.search(t):
        return 'blocked', None, None
    echo = DEFAULT_ECHO
    room = ROOM_RE.search(t)
    if room:
        echo = ECHOS['kitchen' if 'kitchen' in room.group(1).lower() else 'bedroom']
        t = ROOM_RE.sub('', t).strip()
    if re.fullmatch(r'stop', t, re.I):
        t = 'stop'
    return 'alexa', echo, t


def act(text, post=_post, log=print):
    """Run a home command. Returns the spoken reply, or None when `text` isn't a home command."""
    kind, a, b = parse(text)
    if not kind:
        return None
    if kind == 'blocked':
        return "That one waits for your PIN. I don't buy, order, call or message on my own."
    url = snapshot().get('home_url')
    if not url:
        return "I can't reach Home Assistant just yet. It hasn't checked in with me."
    body = {'kind': 'listening', 'on': a} if kind == 'listening' else {'kind': 'alexa', 'echo': a, 'command': b}
    try:
        post(url, body)
    except Exception as e:  # noqa: BLE001
        log(f'home command failed: {type(e).__name__}')
        return "Home Assistant didn't answer. The backup laptop may be napping."
    log(f'home: {kind} {a} {b or ""}'.strip())
    if kind == 'listening':
        return 'Listening, sir.' if a else "Understood. I'll stop listening."
    room = 'bedroom' if 'bedroom' in a else 'kitchen'
    return 'Right away, sir.' if room == 'kitchen' else 'Done. On the bedroom Echo.'
