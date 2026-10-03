"""Jarvis (TARS persona) chat service on the 5060 laptop.

POST /chat {"text": "..."}  -> {"reply": "...", "humor": 60, "filed": 123 | null}
GET  /health                -> {"ok": true, ...}
GET  /history?n=20          -> last n turns (since the last chat reset)
POST /chat/reset            -> app's "New chat": later turns start fresh (summary and Jake facts kept)

Talks to the Ollama model "jarvis-tars" (Modelfile in this folder, built on baby-jarvis). Every turn is appended
to history.jsonl; each call feeds the last ~20 turns plus a rolling summary of everything older (summary.json).
Task and status questions get a FACTS block read from GitHub (flanneryjake/jarvis-tasks: open cards by status and
machine, what is being worked on, cards touched in the asked-for window, the machine health issues) plus the
laptop Worker heartbeat and job log. GitHub access is read-only except one thing: "start researching X" or
"tell Claude X" files a status:inbox card (for:claude on Claude asks); that is decided here in code, never by
the model.
"How's the rig", "restart searxng" (then "confirm") and the like go to the cross-connect helpers on each PC
(fleet.py, signed with this machine's key); restarts always wait for "confirm".

Listens on 127.0.0.1:8790; `tailscale serve` publishes it on the tailnet at http://100.85.255.99:8790 (tailscaled
handles the inbound side, so no Windows Firewall rule is needed). TARS_BIND=100.85.255.99 binds the tailnet IP
directly instead. Standard library only. Token: GITHUB_TASKS_TOKEN (user environment variable).
"""
import datetime as dt
import http.server
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import winreg

import actions
import fleet
import home
import live
import lookup

try:   # house-rules cards (Flow jarvis/knowledge); optional
    sys.path.insert(0, os.environ.get('JARVIS_KNOWLEDGE', r'C:\Jarvis\knowledge'))
    import knowledge
except Exception:  # noqa: BLE001
    knowledge = None

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, 'history.jsonl')
SUMMARY = os.path.join(HERE, 'summary.json')
STATE = os.path.join(HERE, 'state.json')
CHAT_RESET = os.path.join(HERE, 'chat-reset.json')
LOG = os.path.join(HERE, 'tars.log')
MODEL = os.environ.get('TARS_MODEL', 'jarvis-ironman:latest')
SUMMARY_MODEL = os.environ.get('TARS_SUMMARY_MODEL', 'tars:latest')
OLLAMA = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
BIND = os.environ.get('TARS_BIND', '127.0.0.1')
PORT = int(os.environ.get('TARS_PORT', '8790'))
REPO = os.environ.get('JARVIS_TASKS_REPO', 'flanneryjake/jarvis-tasks')
WINDOW = 20                 # turns fed verbatim
SUMMARIZE_EVERY = 10        # fold older turns into the summary once this many have dropped out of the window
HEARTBEAT = r'C:\Jarvis\laptop-worker.heartbeat'
JOBS_LOG = os.path.join(os.path.expanduser('~'), 'JarvisAgent', 'logs', 'laptop-jobs.jsonl')
# Turns and memory from before the Iron Man persona went live carry the old Boston voice; the model never sees them.
PERSONA_SINCE = os.environ.get('JARVIS_PERSONA_SINCE', '2026-10-02T18:30:00')   # after the #952 test chatter

model_lock = threading.Lock()
file_lock = threading.Lock()


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
            os.replace(LOG, LOG + '.old')
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(f'{dt.datetime.now():%m/%d %H:%M:%S} {msg}\n')
    except OSError:
        pass


def read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- history / summary

def load_turns():
    out = []
    try:
        with open(HISTORY, encoding='utf-8') as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def chat_since():
    """Turns before this are out of the conversation: the persona cutoff or the app's last "New chat"."""
    return max(PERSONA_SINCE, read_json(CHAT_RESET, {}).get('since', ''))


def append_turn(role, text, **extra):
    with file_lock, open(HISTORY, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'at': dt.datetime.now().isoformat(timespec='seconds'), 'role': role, 'text': text,
                            **extra}, ensure_ascii=False) + '\n')


def maybe_summarize():
    """Fold turns that fell out of the 20-turn window into summary.json (background thread)."""
    turns = load_turns()
    s = read_json(SUMMARY, {'summary': '', 'upto': 0})
    if s.get('at', '') < PERSONA_SINCE:    # never fold the old Boston memory into the new one
        s = {'summary': '', 'upto': next((i for i, t in enumerate(turns) if t.get('at', '') >= PERSONA_SINCE),
                                         len(turns))}
    cut = len(turns) - WINDOW
    if cut - s['upto'] < SUMMARIZE_EVERY:
        return
    chunk = '\n'.join(f"{t['at'][5:16]} {'Jake' if t['role'] == 'user' else 'Jarvis'}: {t['text']}"
                      for t in turns[s['upto']:cut])
    prompt = ('Update the running memory of conversations between Jake and his home AI Jarvis. Keep facts, '
              'decisions, requests, preferences, promises and open questions, with dates. Drop small talk. '
              'At most 180 words, plain sentences.\n\nCURRENT MEMORY:\n' + (s['summary'] or '(empty)') +
              '\n\nNEW TURNS:\n' + chunk[-12000:] + '\n\nUPDATED MEMORY:')
    try:
        new = ollama_chat([{'role': 'user', 'content': prompt}], model=SUMMARY_MODEL, temperature=0.2,
                          num_predict=400)
        write_json(SUMMARY, {'summary': new.strip(), 'upto': cut, 'at': dt.datetime.now().isoformat(timespec='seconds')})
        log(f'summary updated through turn {cut}')
    except Exception as e:  # noqa: BLE001
        log(f'summary failed: {e}')


# ----------------------------------------------------------------------------- Ollama

def ollama_chat(messages, model=MODEL, temperature=None, num_predict=300):
    opts = {'num_ctx': 8192, 'num_predict': num_predict}
    if temperature is not None:
        opts['temperature'] = temperature
    body = {'model': model, 'stream': False, 'think': False, 'messages': messages, 'options': opts,
            'keep_alive': '24h'}
    req = urllib.request.Request(f'{OLLAMA}/api/chat', data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with model_lock, urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode('utf-8'))
    text = resp.get('message', {}).get('content', '')
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S)
    return re.sub(r'[*#`]+', '', text).strip()


# ----------------------------------------------------------------------------- GitHub (read-only + filing)

def gh_token():
    t = os.environ.get('GITHUB_TASKS_TOKEN')
    if not t:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
                t = winreg.QueryValueEx(k, 'GITHUB_TASKS_TOKEN')[0]
        except OSError:
            t = None
    return t


def gh(method, path, body=None):
    req = urllib.request.Request('https://api.github.com' + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header('Authorization', 'Bearer ' + (gh_token() or ''))
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'jarvis-tars')
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b'null')


_cache = {}


def gh_cached(path, ttl=60):
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    data = gh('GET', path)
    _cache[path] = (time.time(), data)
    return data


def labels(issue):
    return [l['name'] for l in issue.get('labels', [])]


def status_of(issue):
    if issue.get('state') == 'closed':
        return 'done'
    return next((n.split(':', 1)[1] for n in labels(issue) if n.startswith('status:')), 'inbox')


def machine_of(issue):
    return next((n.split(':', 1)[1] for n in labels(issue) if n.startswith('machine:')), 'any')


def local_time(iso):
    try:
        return dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone().strftime('%m/%d %H:%M')
    except ValueError:
        return iso


def open_cards():
    out, page = [], 1
    while page <= 5:
        data = gh_cached(f'/repos/{REPO}/issues?state=open&per_page=100&page={page}')
        out += [i for i in data if not i.get('pull_request')]
        if len(data) < 100:
            break
        page += 1
    return out


def facts(text):
    """The FACTS block for a task / status / history question. Empty string when nothing applies."""
    lines = []
    now = dt.datetime.now().astimezone()
    hours = window_hours(text)
    try:
        cards = open_cards()
        work = [i for i in cards if 'health' not in labels(i)]
        by_status = {}
        for i in work:
            by_status.setdefault(status_of(i), []).append(i)
        lines.append('Open cards by status: ' + ', '.join(f'{k} {len(v)}' for k, v in sorted(by_status.items())))
        if not by_status.get('working'):
            lines.append('Being worked on right now: nothing (no card is claimed).')
        for st in ('working', 'needs-jake'):
            for i in by_status.get(st, [])[:8]:
                claimed = [n.split(':', 1)[1] for n in labels(i) if n.startswith('claimed:')]
                lines.append(f'{st.upper()}: #{i["number"]} {i["title"]} (machine {machine_of(i)}'
                             + (f', claimed by {claimed[0]}' if claimed else '') + ')')
        appr = by_status.get('approved', [])
        for m in ('laptop', 'rig', 'homebase', 'any'):
            mine = [i for i in appr if machine_of(i) == m]
            if mine:
                lines.append(f'Approved and waiting for {m} ({len(mine)}): ' +
                             '; '.join(f'#{i["number"]} {i["title"][:70]}' for i in mine[:5]))
        inbox = by_status.get('inbox', [])
        if inbox:
            lines.append(f'Inbox ({len(inbox)}), newest: ' +
                         '; '.join(f'#{i["number"]} {i["title"][:60]}' for i in inbox[:4]))
        for i in cards:
            if 'health' in labels(i):
                body = i.get('body') or ''
                seen = re.search(r'Last check-in:\*\*\s*(\S+)', body)
                warn = re.search(r'> \[!WARNING\]\s*\n> (.+)', body)
                lines.append(f'{i["title"]}' + (f', last check-in {local_time(seen.group(1))}' if seen else '') +
                             (f', ALERT: {warn.group(1)[:150]}' if warn else ''))
        since = (now - dt.timedelta(hours=hours)).astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        recent = [i for i in gh_cached(f'/repos/{REPO}/issues?state=all&sort=updated&direction=desc'
                                       f'&since={since}&per_page=60') if not i.get('pull_request')
                  and 'health' not in labels(i)]
        closed = [i for i in recent if i.get('closed_at') and i['closed_at'] >= since]
        created = [i for i in recent if i['created_at'] >= since]
        lines.append(f'Across all machines (Claude sessions, rig, homebase, this laptop) in the last {hours} hours: {len(closed)} cards finished, {len(created)} cards created, '
                     f'{len(recent)} cards touched.')
        for i in closed[:12]:
            lines.append(f'FINISHED {local_time(i["closed_at"])}: #{i["number"]} {i["title"][:80]} ({machine_of(i)})')
        created.sort(key=lambda i: i['created_at'], reverse=True)
        for i in [i for i in created if i not in closed][:10]:
            lines.append(f'CREATED {local_time(i["created_at"])}: #{i["number"]} {i["title"][:80]} '
                         f'(now {status_of(i)}, {machine_of(i)})')
    except Exception as e:  # noqa: BLE001
        lines.append(f'(GitHub could not be read just now: {type(e).__name__})')
        log(f'github read failed: {e}')
    try:
        with open(HEARTBEAT, encoding='utf-8') as f:
            hb = f.read().strip()
        lines.append(f'This laptop\'s Worker right now: {hb.replace("worker: ", "")}')
    except OSError:
        pass
    try:
        cutoff = (dt.datetime.now() - dt.timedelta(hours=hours)).isoformat(timespec='seconds')
        jobs = []
        with open(JOBS_LOG, encoding='utf-8') as f:
            for line in f:
                j = json.loads(line)
                if j.get('at', '') >= cutoff:
                    jobs.append(f'{j["at"][5:16].replace("T", " ")} {j["input"]["title"][:70]} -> {j["route"]}')
        if jobs:
            lines.append('Laptop Worker jobs in that window: ' + '; '.join(jobs[-8:]))
    except (OSError, ValueError, KeyError):
        pass
    chats = [t for t in load_turns() if t['role'] == 'user' and
             t['at'] >= (dt.datetime.now() - dt.timedelta(hours=hours)).isoformat(timespec='seconds')]
    if chats:
        lines.append(f'Jake talked to you {len(chats)} times in that window; topics: ' +
                     '; '.join(c['text'][:50] for c in chats[-6:]))
    return '\n'.join(lines)


def window_hours(text):
    t = text.lower()
    m = re.search(r'(?:last|past)\s+(\d{1,3})\s*(?:hours|hrs|hr|h)\b', t)
    if m:
        return max(1, min(int(m.group(1)), 168))
    m = re.search(r'(?:last|past)\s+(\d{1,2})\s*days?\b', t)
    if m:
        return min(int(m.group(1)) * 24, 168)
    if 'yesterday' in t or 'last night' in t or 'overnight' in t:
        return 24
    if 'today' in t or 'this morning' in t:
        return max(1, dt.datetime.now().hour + 1)
    if 'this week' in t:
        return 168
    return 12


BOARD_TALK_RE = re.compile(r"\b(cards?\s*#?\d+|#\d{2,}|idl(?:e|ing)\b.{0,40}\b(?:5060|laptop|rig)|watchdog|"
                           r"migration|the queue)", re.I)

# Only questions about the board or the machines get FACTS; "sunset tonight" or "how's it going" must not drag the
# queue into small talk.
TASK_WORDS = re.compile(
    r"\b(tasks?|cards?|queue|working on|status of|what (?:are|were|did|have) (?:you|we|they)(?: been)? "
    r"(?:doing|do|done|working)|did (?:you|we) (?:do|get|finish)|progress on|pending|approved|inbox|rig|homebase|"
    r"laptop|tars|hal|pi|health|offline|online|overnight|what's new|whats new|anything new|issue|#\d+|worker|"
    r"jobs?|projects?)\b", re.I)


FILE_RE = re.compile(
    r"^\s*(?:hey\s+)?(?:jarvis[,!.:]?\s+)?(?:please\s+|can you\s+|could you\s+)?"
    r"(start researching|research|tell claude(?: to)?|ask claude to|have claude|remind claude(?: to)?)"
    r"\s*[:,-]?\s+(.{3,})$", re.I | re.S)


def file_card(kind, what, said):
    what = what.strip().rstrip('.!?')
    for_claude = 'claude' in kind.lower()
    title = (f'Claude: {what[:1].upper()}{what[1:]}' if for_claude else f'Research: {what}')[:120]
    # Same ask twice in 10 minutes (an app retry) files once.
    st = read_json(STATE, {})
    last = st.get('last_filed') or {}
    if last.get('title') == title and time.time() - last.get('at', 0) < 600:
        return last['number'], title
    body = (f'Filed by Jarvis (TARS, laptop) from Jake\'s chat at {dt.datetime.now():%Y-%m-%d %H:%M}.\n\n'
            f'Jake said: "{said.strip()}"\n\n' +
            ('Ask for Claude: pick this up and report back in the card.' if for_claude else
             'Research request: needs web research, so Claude or the rig takes it after Jake triages it.'))
    lab = ['status:inbox', 'type:task', 'machine:any'] + (['for:claude'] if for_claude else [])
    issue = gh('POST', f'/repos/{REPO}/issues', {'title': title, 'body': body, 'labels': lab})
    st['last_filed'] = {'title': title, 'number': issue['number'], 'at': time.time()}
    write_json(STATE, st)
    log(f'filed #{issue["number"]} {title}')
    return issue['number'], title


# ----------------------------------------------------------------------------- one chat turn

def _short(title, n=70):
    title = re.sub(r'\s+', ' ', title).strip()
    return title if len(title) <= n else title[:n].rsplit(' ', 1)[0].rstrip(',;:-.') + '…'


TODO_RE = re.compile(r"\b(to-?\s?do|what do i (?:need|have) to do|waiting on me|need(?:s)? me|my list)\b", re.I)


def todo_answer(text):
    """To-do questions: read the board's needs-Jake cards in code. The pinned To-Do page itself isn't readable here."""
    if not TODO_RE.search(text or ''):
        return None
    try:
        items = gh_cached(f'/repos/{REPO}/issues?state=open&labels=status:needs-jake&per_page=20', ttl=300)
    except Exception as e:  # noqa: BLE001
        log(f'todo read failed: {type(e).__name__}')
        return "I can't see your to-do list from here just now. It's on the pinned To-Do page."
    items = [i for i in items if 'pull_request' not in i and not any(   # claimed = a machine is already on it
        n.startswith('claimed') or n == 'status:working' for n in labels(i))]
    if not items:
        return ("Nothing on the task board is waiting on you. Your pinned To-Do page may have a few more; "
                "I can't read that one.")
    top = '; '.join(_short(i['title']) for i in items[:3])
    more = f', and {len(items) - 3} more' if len(items) > 3 else ''
    head = f"{len(items)} board item{'s' if len(items) != 1 else ''} waiting on you, sir: {top}{more}"
    return head + ('' if head.endswith('…') else '.') + ' The pinned To-Do page has the full list.'


def jake_facts():
    """training/jake-facts.md minus its heading and preamble: short standing facts fed every turn."""
    try:
        with open(os.path.join(HERE, 'training', 'jake-facts.md'), encoding='utf-8') as f:
            lines = [l.rstrip() for l in f if l.lstrip().startswith('-')]
        return '\n'.join(lines)[:2500]
    except OSError:
        return ''


def humor():
    return int(read_json(STATE, {}).get('humor', 60))


def chat(text):
    text = (text or '').strip()[:4000]
    if not text:
        return {'reply': "I didn't catch that.", 'humor': humor(), 'filed': None}
    append_turn('user', text)
    note, filed = '', None

    m = re.search(r'\bhumou?r(?:\s+setting)?(?:\s+(?:to|at|is|=))?\s*(\d{1,3})\s*(?:%|percent)?', text, re.I)
    if m and not FILE_RE.match(text):
        val = max(0, min(100, int(m.group(1))))
        st = read_json(STATE, {})
        st['humor'] = val
        write_json(STATE, st)
        note = f'Jake just changed your humor setting to {val}%. Confirm it in one short line, in character.'

    fm = FILE_RE.match(text)
    if fm:
        try:
            filed, title = file_card(fm.group(1), fm.group(2), text)
            note = (f'The server filed GitHub card #{filed} "{title}" in the inbox'
                    + (' for Claude' if 'claude' in fm.group(1).lower() else '') +
                    '. Confirm that in one or two lines, with the card number. Do not say the work is done.')
        except Exception as e:  # noqa: BLE001
            log(f'filing failed: {e}')
            note = ('Filing the card FAILED (GitHub error). Tell Jake it did not get filed and he should try again '
                    'or tell Claude directly.')

    done = None if (fm or note) else (fleet.act(text, log=log) or home.act(text, log=log) or
                                      actions.act(text, log=log) or
                                      home.alarm_answer(text) or todo_answer(text))
    if done:   # "play jazz", "lights off", "set an alarm for 6": run it through HA, no model needed
        append_turn('assistant', done)
        return {'reply': done, 'humor': humor(), 'filed': None}

    fx = facts(text) if (TASK_WORDS.search(text) and not fm) else ''
    since = chat_since()
    turns = [t for t in load_turns()[:-1] if t.get('at', '') >= since][-WINDOW:]
    if not TASK_WORDS.search(text):   # small talk: earlier board chatter ("card 256 finished") stays out of it
        turns = [t for t in turns if not BOARD_TALK_RE.search(t['text'])]
    mem = read_json(SUMMARY, {})
    summary = mem.get('summary', '') if mem.get('at', '') >= PERSONA_SINCE else ''
    now = dt.datetime.now()
    part = ('night' if now.hour < 5 or now.hour >= 22 else 'morning' if now.hour < 12 else
            'afternoon' if now.hour < 17 else 'evening')
    ctx = [f'Now: {now:%A} {part}, {now:%B} {now.day}, {now.year}, {now:%I:%M %p}'.replace(' 0', ' ') +
           f' (yesterday was {now - dt.timedelta(days=1):%A %B %d}). Humor setting: {humor()}%.']
    jf = jake_facts()
    if jf:
        ctx.append('About Jake (standing background, NOT what is happening now; only LIVE and FACTS say what is '
                   'happening now; don\'t recite it):\n' + jf)
    if summary:
        ctx.append('Memory of earlier conversations: ' + summary)
    hf = '' if fm else home.facts(text)
    lv = '' if fm else '\n'.join(x for x in (live.facts(text, log=log), hf) if x)
    if lv:
        ctx.append('LIVE (fresh data; answer from it, do not LOOKUP these):\n' + lv)
    kn = '' if (fm or hf or knowledge is None) else knowledge.context_for(text)
    if kn:
        ctx.append(kn)
    if fx:
        ctx.append('FACTS (live from the task board and this laptop; answer from these, cite card numbers, '
                   'never invent others. "We" and "you" mean the whole Jarvis setup, so say which machine or Claude did what; '
                   'WORKING means claimed and running now):\n' + fx)
    if note:
        ctx.append('NOTE: ' + note)
    msgs = [{'role': 'user' if t['role'] == 'user' else 'assistant', 'content': t['text']} for t in turns]
    msgs.append({'role': 'user', 'content': '[context]\n' + '\n'.join(ctx) + '\n[/context]\n\nJake: ' + text})
    try:
        reply = ollama_chat(msgs, num_predict=350 if fx else 220)
        if not fm:   # LOOKUP / ASK_CLAUDE / "I don't know" -> fetch the facts and answer again
            def answer_with(found):
                more = msgs[:-1] + [{'role': 'user', 'content': msgs[-1]['content'].replace(
                    '[/context]', 'NOTE: ' + found + '\n[/context]')}]
                return ollama_chat(more, num_predict=260)
            if hf:   # alarms / what's playing: the HA snapshot is the only truth; never web-search it
                reply = lookup.trim(lookup.scrub(reply))
                if lookup.LOOKUP_RE.search(reply) or lookup.CLAUDE_RE.search(reply) or lookup.GAVE_UP_RE.search(reply):
                    reply = lookup.trim(hf.replace('\n', ' '))
            else:
                reply = lookup.resolve(text, reply, answer_with, log=log, today=f'{now:%A %B %d %Y}',
                                       check_facts=not (fx or lv))   # board / LIVE answers are already grounded
        else:
            reply = lookup.scrub(reply)
    except Exception as e:  # noqa: BLE001
        log(f'model failed: {e}')
        reply = ("My language model isn't answering right now. " +
                 (f'I did file card #{filed}. ' if filed else '') + 'Give me a minute and try again.')
    if not reply:
        reply = 'I have nothing useful to add, which is rare and slightly embarrassing.'
    append_turn('assistant', reply, **({'filed': filed} if filed else {}))
    threading.Thread(target=maybe_summarize, daemon=True).start()
    return {'reply': reply, 'humor': humor(), 'filed': filed}


# ----------------------------------------------------------------------------- HTTP

class Handler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self._send(204, None)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.rstrip('/') in ('', '/health'):
            self._send(200, {'ok': True, 'model': MODEL, 'turns': len(load_turns()), 'humor': humor()})
        elif u.path.rstrip('/') == '/history':
            n = int((urllib.parse.parse_qs(u.query).get('n') or ['20'])[0])
            since = chat_since()
            turns = [t for t in load_turns() if t.get('at', '') >= since]
            self._send(200, {'turns': turns[-max(1, min(n, 200)):]})
        else:
            self._send(404, {'error': 'try POST /chat, GET /health, GET /history'})

    def do_POST(self):
        if self.path.rstrip('/') == '/ha':   # Home Assistant's 2-minute home snapshot
            try:
                n = int(self.headers.get('Content-Length') or 0)
                err = home.save_snapshot(json.loads(self.rfile.read(min(n, 100_000)).decode('utf-8', 'replace')))
                return self._send(400 if err else 200, {'error': err} if err else {'ok': True})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {'error': type(e).__name__})
        if self.path.rstrip('/') == '/chat/reset':   # the app's "New chat" button
            since = dt.datetime.now().isoformat(timespec='microseconds')
            write_json(CHAT_RESET, {'since': since})
            log(f'chat reset at {since}')
            return self._send(200, {'ok': True, 'since': since})
        if self.path.rstrip('/') != '/chat':
            return self._send(404, {'error': 'try POST /chat'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(min(n, 100_000)).decode('utf-8', 'replace')
            try:
                body = json.loads(raw) if raw.strip() else {}
            except ValueError:
                body = {'text': raw}
            text = body.get('text') or body.get('message') or body.get('q') or ''
            self._send(200, chat(text))
        except Exception as e:  # noqa: BLE001
            log(f'request failed: {type(e).__name__}: {e}')
            self._send(500, {'error': f'{type(e).__name__}'})

    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode('utf-8') if obj is not None else b''
        self.send_response(code)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        if obj is not None:
            self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        if b:
            self.wfile.write(b)

    def log_message(self, *a):
        pass


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--ask':      # quick local test: python tars_server.py --ask "hi"
        print(chat(' '.join(sys.argv[2:]))['reply'])
        return 0
    while True:   # at logon the tailnet IP may not exist yet; keep trying
        try:
            srv = http.server.ThreadingHTTPServer((BIND, PORT), Handler)
            break
        except OSError as e:
            if getattr(e, 'winerror', None) == 10048 or e.errno in (98, 10048):
                return 0          # another copy already has the port
            log(f'bind {BIND}:{PORT} failed ({e}); retrying in 30 s')
            time.sleep(30)
    log(f'TARS listening on {BIND}:{PORT} (model {MODEL})')
    srv.serve_forever()


if __name__ == '__main__':
    sys.exit(main())
