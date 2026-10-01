"""Baby Jarvis laptop Worker: runs laptop-class cards from the Notion Tasks board on the local model.

Runs on LAPTOP-4150EGRS against Ollama's baby-jarvis:latest (qwen3.5:9b, 4k context). It only produces text:
it never runs commands, installs, spends, posts or deletes, so every action it takes is in the guardrails'
Free tier (local model, task cards, files under C:\\Jarvis\\outputs).

Which cards: Status = Approved, Auto-executable ticked, Claimed by empty, Machine = laptop (the same gate the
other Workers use, with this machine's name). Each card is routed with the offload rules
(kit/training/offload/offload-rules.md in flanneryjake/jarvis-outputs):
  * laptop-class (short classify / extract / reformat / triage / check / status job): Baby Jarvis does it,
    the result goes on the card and in C:\\Jarvis\\outputs\\laptop, the card goes to Done.
  * rig-class (long-form, code, planning, clinical, > ~3,000 tokens in): Machine -> rig, back to Approved and
    unclaimed, with Baby Jarvis' handoff note. It does not wake the rig for one job.
  * Claude-class (web, accounts, purchases, posting, email, other PCs) or any card with an Approval code (PIN):
    Machine -> Any, back to Approved and unclaimed, so a Claude Worker picks it up under its own PIN rules.
Two failed runs in a row put the card back to Staged with "NEEDS JAKE" in Notes, and log a "needs Jake:" line
that the watchdog shows on the Machine Health row.

Standard library only. Config from user environment variables: NOTION_TOKEN (required),
BABY_JARVIS_MODEL (default baby-jarvis:latest), OLLAMA_URL (default http://127.0.0.1:11434).
Run: pythonw laptop_worker.py            (normal, started by laptop_watchdog.py)
     python laptop_worker.py --once      (one pass, then exit)
     python laptop_worker.py --dry-run   (show what it would claim, change nothing)
"""
import argparse
import ctypes
import datetime as dt
import http.server
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import winreg

MACHINE = 'laptop'
TASKS_DB = '7c1c59e927644dfba461c88a67dbd32c'
MODEL = os.environ.get('BABY_JARVIS_MODEL', 'baby-jarvis:latest')
OLLAMA = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
POLL_SEC = 60
PORT = 8791                     # 127.0.0.1 only: single-instance lock, GET /health, POST /wake
MAX_IN_TOKENS = 3000            # offload rule 1: input must fit in ~3,000 tokens
MAX_OUT_WORDS = 650             # ~600-word answers; longer means it should have been a rig job
MODEL_TIMEOUT = 240
FAILS_BEFORE_JAKE = 2

HOME = os.path.expanduser('~')
BASE = os.path.join(HOME, 'JarvisAgent')
LOG_DIR = os.path.join(BASE, 'logs')
AGENT_LOG = os.path.join(LOG_DIR, 'agent.log')          # the watchdog reads "needs Jake:" lines from here
JOBS_LOG = os.path.join(LOG_DIR, 'laptop-jobs.jsonl')   # one line per job: input, route, output (future training)
STATE = os.path.join(BASE, 'laptop-worker-state.json')
HEARTBEAT = r'C:\Jarvis\worker.heartbeat'               # the watchdog's Worker column reads this
OUT_DIR = r'C:\Jarvis\outputs\laptop'

# Cards whose wording asks for a PIN-tier or Claude-only action never run here (GUARDRAILS.md).
CLAUDE_WORDS = re.compile(
    r'\b(buy|purchase|order (more|new)|subscribe|subscription|pay(ment)?|checkout|shopify|publish|post (it|to|on)|'
    r'tweet|(send|write)( an?)? e-?mail|e-?mail (him|her|them|it|jake|the|back)|send (it |a |an )?(to|message)|text (him|her|them)|delete|remove files|password|api key|'
    r'token|credential|merge|push to|git push|install|download|browse|search the web|look up online|website|'
    r'login|log in|sign in)\b', re.I)
RIG_WORDS = re.compile(
    r'\b(curriculum|facilitator guide|handout|study guide|cover letter|resume|r[eé]sum[eé]|seo guide|'
    r'product (copy|description)s?|write (the |a )?(code|script|program|app)|unit tests?|refactor|research)\b', re.I)

SYSTEM = """You are Baby Jarvis, the small local model on Jake's RTX 5060 laptop, working a task card from the queue.
You only have the text on the card: no internet, no files, no other PCs, no tools. Your answer is read by a script
and then by Jake, so it must be a single JSON object and nothing else.

Do the job yourself ONLY when all of these hold:
1. The answer fits in about 600 words, or is JSON / a list.
2. The job is one of: classify, route, extract, reformat, shorten, tag, name, check, triage, summarize given text,
   write a one-line notification or status line, or answer from text you were given.
3. Being slightly wrong is cheap and checkable.
4. You are not authoring clinical, counseling, legal, financial or publish-ready content.

Hand it to the rig (the big Jarvis) when: long-form writing (over ~400 words: curricula, guides, handouts, store
product copy, study guides, cover letters, resumes), code beyond a one-line fix, multi-step planning, research
synthesis across documents, or any clinical/counseling content a customer or patient will read.

Hand it to Claude when it needs the web, current facts, citations, accounts, purchases, email or messages to
people, publishing, GitHub, or another PC.

Escalating is a correct answer, not a failure. Never "try anyway" on rig-class work.

Reply with exactly this JSON shape:
{"route": "laptop" | "rig" | "claude",
 "why": "one short sentence",
 "result": "the finished work when route is laptop, else empty",
 "handoff": "when route is rig or claude: one paragraph task card for them, else empty"}
Plain text inside the strings, no markdown headings. Do not claim you did anything outside this answer."""

SCHEMA = {
    'type': 'object',
    'properties': {
        'route': {'type': 'string', 'enum': ['laptop', 'rig', 'claude']},
        'why': {'type': 'string'},
        'result': {'type': 'string'},
        'handoff': {'type': 'string'},
    },
    'required': ['route', 'why', 'result', 'handoff'],
}

wake_event = threading.Event()
status = {'state': 'starting', 'since': dt.datetime.now().isoformat(timespec='seconds'),
          'started_epoch': time.time()}   # the watchdog restarts the Worker when its code is newer than this


# ----------------------------------------------------------------------------- logging / state

def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [{MACHINE}] {redact(msg)}"
    try:
        if os.path.exists(AGENT_LOG) and os.path.getsize(AGENT_LOG) > 2_000_000:
            os.replace(AGENT_LOG, AGENT_LOG + '.old')
        with open(AGENT_LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass
    if sys.stdout and sys.stdout.isatty():
        print(line)


def redact(s):
    return re.sub(r'(ntn_|secret_|sk-ant-|sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{16,}', '<redacted>', str(s))


def heartbeat(worker_line):
    status['state'] = worker_line
    status['since'] = dt.datetime.now().isoformat(timespec='seconds')
    try:
        os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
        with open(HEARTBEAT, 'w', encoding='utf-8') as f:
            f.write(f"worker: {worker_line}\n")
    except OSError:
        pass


def load_state():
    try:
        with open(STATE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'fails': {}}


def save_state(st):
    os.makedirs(BASE, exist_ok=True)
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1)


def stay_awake(on):
    """The laptop sleeps after 30 min without input; hold it awake while a card runs (ES_CONTINUOUS|ES_SYSTEM_REQUIRED)."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
    except Exception:
        pass


def stamp():
    return f"{MACHINE} {dt.datetime.now():%m/%d %H:%M}"


# ----------------------------------------------------------------------------- Notion

def notion_token():
    t = os.environ.get('NOTION_TOKEN')
    if not t:   # a task started before setx still sees the user's registry value
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
                t = winreg.QueryValueEx(k, 'NOTION_TOKEN')[0]
        except OSError:
            t = None
    return t


def notion(method, path, body=None):
    req = urllib.request.Request(
        f'https://api.notion.com/v1/{path}', method=method,
        data=json.dumps(body).encode('utf-8') if body is not None else None,
        headers={'Authorization': f'Bearer {notion_token()}', 'Notion-Version': '2022-06-28',
                 'Content-Type': 'application/json; charset=utf-8'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f'Notion {method} {path}: {e.code} {e.read()[:300]!r}') from None
        except (urllib.error.URLError, socket.timeout) as e:
            if attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f'Notion {method} {path}: {e}') from None


def plain(rich):
    return ''.join(x.get('plain_text', '') for x in rich or [])


def rt(text):
    """Notion rich_text, split into the 2,000-character pieces the API allows."""
    text = text or ''
    return [{'type': 'text', 'text': {'content': text[i:i + 2000]}} for i in range(0, max(len(text), 1), 2000)][:50]


def prop_text(page, name):
    p = page['properties'].get(name) or {}
    t = p.get('type')
    if t in ('rich_text', 'title'):
        return plain(p[t])
    if t == 'select':
        return (p['select'] or {}).get('name', '')
    if t == 'checkbox':
        return p['checkbox']
    return ''


def ready_cards():
    q = notion('POST', f'databases/{TASKS_DB}/query', {
        'page_size': 20,
        'filter': {'and': [
            {'property': 'Status', 'select': {'equals': 'Approved'}},
            {'property': 'Auto-executable', 'checkbox': {'equals': True}},
            {'property': 'Claimed by', 'rich_text': {'is_empty': True}},
            {'property': 'Machine', 'select': {'equals': MACHINE}},
        ]},
        'sorts': [{'property': 'Priority', 'direction': 'ascending'},
                  {'timestamp': 'created_time', 'direction': 'ascending'}],
    })
    return q.get('results', [])


def page_body(page_id, limit_chars=16000):
    out, cursor = [], None
    while True:
        r = notion('GET', f'blocks/{page_id}/children?page_size=100' + (f'&start_cursor={cursor}' if cursor else ''))
        for b in r.get('results', []):
            t = b.get('type')
            data = b.get(t) or {}
            if isinstance(data, dict) and 'rich_text' in data:
                prefix = {'bulleted_list_item': '- ', 'numbered_list_item': '- ', 'to_do': '- [ ] ',
                          'heading_1': '# ', 'heading_2': '## ', 'heading_3': '### '}.get(t, '')
                out.append(prefix + plain(data['rich_text']))
        if not r.get('has_more') or sum(len(x) for x in out) > limit_chars:
            break
        cursor = r.get('next_cursor')
    return '\n'.join(out)[:limit_chars]


def update(page_id, props):
    return notion('PATCH', f'pages/{page_id}', {'properties': props})


def append_blocks(page_id, heading, text):
    blocks = [{'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': rt(heading)}}]
    for para in [p for p in (text or '').split('\n') if p.strip()][:90]:
        blocks.append({'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rt(para)}})
    notion('PATCH', f'blocks/{page_id}/children', {'children': blocks})


# ----------------------------------------------------------------------------- Ollama

def ollama_up():
    try:
        with urllib.request.urlopen(f'{OLLAMA}/api/tags', timeout=5) as r:
            names = [m['name'] for m in json.loads(r.read()).get('models', [])]
        return MODEL in names or MODEL.split(':')[0] in [n.split(':')[0] for n in names]
    except Exception:
        return False


def ask_model(card_text):
    body = {
        'model': MODEL, 'stream': False, 'think': False, 'format': SCHEMA,
        'options': {'temperature': 0.2, 'num_ctx': 4096, 'num_predict': 1100},
        'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': card_text}],
    }
    req = urllib.request.Request(f'{OLLAMA}/api/chat', data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT) as r:
        resp = json.loads(r.read().decode('utf-8'))
    raw = resp.get('message', {}).get('content', '')
    m = re.search(r'\{.*\}', raw, re.S)
    if not m:
        raise ValueError(f'model gave no JSON: {raw[:200]!r}')
    try:
        ans = json.loads(m.group(0))
    except json.JSONDecodeError:
        if resp.get('done_reason') == 'length' or (resp.get('eval_count') or 0) >= body['options']['num_predict'] - 5:
            # Ran out of room mid-answer: the job is bigger than the laptop, not a failure.
            return {'route': 'rig', 'why': "answer ran past the laptop's output limit", 'result': '', 'handoff': '',
                    'seconds': round(time.time() - t0, 1), 'tokens_out': resp.get('eval_count')}
        raise
    if ans.get('route') not in ('laptop', 'rig', 'claude'):
        raise ValueError(f'bad route {ans.get("route")!r}')
    ans['seconds'] = round(time.time() - t0, 1)
    ans['tokens_out'] = resp.get('eval_count')
    return ans


# ----------------------------------------------------------------------------- one card

def est_tokens(s):
    return int(len(s) / 3.5) + 1


def pre_route(title, notes, body, approval_code):
    """Routes decided by rule before the model sees the card. Returns (route, why) or (None, None)."""
    text = f'{title}\n{notes}\n{body}'
    if approval_code:
        return 'claude', 'card carries an Approval code (PIN action); the laptop never runs PIN cards'
    m = CLAUDE_WORDS.search(f'{title}\n{notes}')
    if m:
        return 'claude', f'asks for something the laptop cannot do ("{m.group(0)}")'
    if est_tokens(text) > MAX_IN_TOKENS:
        return 'rig', f'input is ~{est_tokens(text)} tokens, over the laptop\'s ~{MAX_IN_TOKENS}'
    m = RIG_WORDS.search(title)
    # Only when the card asks to make one ("write the B12 facilitator guide"), not when it merely names one
    # ("tag these files as curriculum or handout").
    if m and re.search(r'\b(write|draft|create|make|build|generate|produce|rewrite|expand)\b', title, re.I):
        return 'rig', f'rig-class job ("{m.group(0)}")'
    return None, None


def handoff(page, route, why, note):
    target = 'rig' if route == 'rig' else 'Any'
    notes = prop_text(page, 'Notes')
    new_notes = f'Laptop handoff to {"the rig" if route == "rig" else "Claude"}: {why}' + (f'\n{notes}' if notes else '')
    if note:
        append_blocks(page['id'], f'Baby Jarvis handoff ({stamp()})', note)
    update(page['id'], {
        'Machine': {'select': {'name': target}},
        'Status': {'select': {'name': 'Approved'}},
        'Claimed by': {'rich_text': []},
        'Notes': {'rich_text': rt(new_notes)},
        'Agent log': {'rich_text': rt(f'{stamp()} escalated to {target}: {why}')},
    })


def decide(title, notes, body, pin, task_log):
    """Rule check, then Baby Jarvis. Returns (route, why, answer-or-None) and writes the task log."""
    route, why = pre_route(title, notes, body, pin)
    ans = None
    if route is None:
        card = f'TASK: {title}\n' + (f'NOTES: {notes}\n' if notes else '') + (f'DETAILS:\n{body}\n' if body else '')
        ans = ask_model(card)
        route, why = ans['route'], ans.get('why', '').strip()
        if route == 'laptop':
            result = (ans.get('result') or '').strip()
            if not result:
                route, why = 'rig', 'Baby Jarvis returned an empty result'
            elif len(result.split()) > MAX_OUT_WORDS:
                route, why = 'rig', f'answer ran {len(result.split())} words, too long for a laptop job'
    with open(task_log, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'title': title, 'route': route, 'why': why, 'answer': ans}, indent=1))
    return route, why, ans


def save_output(title, key, result):
    os.makedirs(OUT_DIR, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:50] or 'card'
    out = os.path.join(OUT_DIR, f'{dt.date.today():%Y%m%d}-{slug}-{str(key)[:6]}.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(f'# {title}\n\n{result.strip()}\n')
    return out


def run_card(page, st, dry):
    pid = page['id']
    title = prop_text(page, 'Task') or '(untitled)'
    notes = prop_text(page, 'Notes')
    code = prop_text(page, 'Approval code')
    if dry:
        log(f'dry run: would claim "{title}"')
        return
    # Claim, then read back: if another process stamped it in between, leave it.
    me = stamp()
    update(pid, {'Claimed by': {'rich_text': rt(me)}, 'Status': {'select': {'name': 'In progress'}}})
    if prop_text(notion('GET', f'pages/{pid}'), 'Claimed by') != me:
        log(f'lost the claim on "{title}", skipping')
        return
    heartbeat(f'Working: {title[:80]}')
    stay_awake(True)
    log(f'claimed "{title}"')
    started = time.time()
    task_log = os.path.join(LOG_DIR, f'task-{pid[:8]}.log')
    try:
        body = page_body(pid)
        route, why, ans = decide(title, notes, body, code, task_log)
        if route == 'laptop':
            out = save_output(title, pid, ans['result'])
            append_blocks(pid, f'Baby Jarvis result ({stamp()})', ans['result'])
            update(pid, {'Status': {'select': {'name': 'Done'}},
                         'Agent log': {'rich_text': rt(f'{stamp()} done on Baby Jarvis in {ans["seconds"]}s. '
                                                       f'Result on this page and in {out}')}})
            log(f'done "{title}" in {time.time() - started:.0f}s -> {out}')
        else:
            handoff(page, route, why, (ans or {}).get('handoff', ''))
            log(f'escalated "{title}" to {route}: {why}')
        st['fails'].pop(pid, None)
        record(title, notes, body, route, why, ans)
    except Exception as e:
        n = st['fails'].get(pid, 0) + 1
        st['fails'][pid] = n
        err = f'{type(e).__name__}: {e}'
        log(f'failed "{title}" (try {n}): {err}')
        try:
            with open(task_log, 'a', encoding='utf-8') as f:
                f.write('\n' + traceback.format_exc())
        except OSError:
            pass
        props = {'Claimed by': {'rich_text': []},
                 'Agent log': {'rich_text': rt(f'{stamp()} failed (try {n}): {err[:400]}')}}
        if n >= FAILS_BEFORE_JAKE:
            props['Status'] = {'select': {'name': 'Staged'}}
            props['Notes'] = {'rich_text': rt(f'NEEDS JAKE: the laptop Worker failed this {n} times ({err[:200]}). '
                                              f'Re-approve to retry, or set Machine to rig.' + (f'\n{notes}' if notes else ''))}
            log(f'needs Jake: "{title}" failed {n} times on the laptop, moved back to Staged')
            st['fails'].pop(pid, None)
        else:
            props['Status'] = {'select': {'name': 'Approved'}}
        try:
            update(pid, props)
        except Exception as e2:
            log(f'could not release "{title}": {e2}')
    finally:
        stay_awake(False)
        save_state(st)


def record(title, notes, body, route, why, ans):
    try:
        with open(JOBS_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'at': dt.datetime.now().isoformat(timespec='seconds'), 'model': MODEL,
                                'input': {'title': title, 'notes': notes, 'body': body[:4000]},
                                'route': route, 'why': why,
                                'result': (ans or {}).get('result', ''), 'handoff': (ans or {}).get('handoff', ''),
                                'seconds': (ans or {}).get('seconds')}, ensure_ascii=False) + '\n')
    except OSError:
        pass


# ----------------------------------------------------------------------------- loop

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._send(200, status)

    def do_POST(self):
        if self.path.rstrip('/') == '/wake':
            wake_event.set()
            self._send(200, {'ok': True})
        else:
            self._send(404, {'error': 'unknown path'})

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def start_server():
    """Binding the port is the single-instance lock. Returns False if another copy already holds it."""
    try:
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    except OSError:
        return False
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return True


# ----------------------------------------------------------------------------- GitHub queue (ghq)
# The board is moving to GitHub Issues in flanneryjake/jarvis-tasks (Flow jarvis/ghq). JARVIS_QUEUE picks the
# source: notion, github, or both (default, while cards live on both boards during the cutover). On GitHub the laptop takes only open
# status:approved issues labelled machine:laptop; a "pin" label counts like an Approval code.

def _ghq():
    if r'C:\Jarvis\ghq' not in sys.path:
        sys.path.insert(0, r'C:\Jarvis\ghq')
    if not os.environ.get('GITHUB_TASKS_TOKEN'):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
                os.environ['GITHUB_TASKS_TOKEN'] = winreg.QueryValueEx(k, 'GITHUB_TASKS_TOKEN')[0]
        except OSError:
            pass
    import ghq
    return ghq


def gh_ready():
    g = _ghq()
    return [c for c in g.ready(MACHINE, use_etag_file=os.path.join(BASE, 'ghq-ready-cache.json'))
            if f'machine:{MACHINE}' in c['labels']]


def gh_run_card(card, st, dry):
    g = _ghq()
    n, title = card['number'], card['title']
    key = f'gh{n}'
    if dry:
        log(f'dry run: would claim GitHub #{n} "{title}"')
        return
    if not g.claim(n, MACHINE):
        log(f'lost the claim on GitHub #{n}, skipping')
        return
    heartbeat(f'Working: #{n} {title[:80]}')
    stay_awake(True)
    log(f'claimed GitHub #{n} "{title}"')
    started = g.now_iso()
    try:
        issue = g.api('GET', g.repo_path(f'/issues/{n}'))
        body = re.sub(r'<!--.*?-->', '', issue.get('body') or '', flags=re.S).strip()[:16000]
        route, why, ans = decide(title, '', body, 'pin' in card['labels'], os.path.join(LOG_DIR, f'task-{key}.log'))
        if route == 'laptop':
            out = save_output(title, key, ans['result'])
            g.log_run(n, MACHINE, 'done', started=started, model=MODEL,
                      summary=f"{ans['result'].strip()}\n\n_Done on Baby Jarvis in {ans['seconds']}s; also saved to {out}._")
            log(f'done GitHub #{n} -> {out}')
        else:
            target = 'rig' if route == 'rig' else 'any'
            note = (ans or {}).get('handoff', '')
            g.log_run(n, MACHINE, 'released', started=started, model=MODEL,
                      summary=f'Laptop handoff to {"the rig" if route == "rig" else "Claude"}: {why}' +
                              (f'\n\n{note}' if note else ''))
            g.set_status(n, 'approved', extra_add=[f'machine:{target}'], extra_remove=[f'machine:{MACHINE}'])
            log(f'escalated GitHub #{n} to {target}: {why}')
        record(title, '', body, route, why, ans)
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        log(f'failed GitHub #{n} "{title}": {err}')
        try:
            if g.log_run(n, MACHINE, 'failed', started=started, summary=err[:1500]) == 'needs-jake':
                log(f'needs Jake: GitHub #{n} "{title}" failed twice on the laptop')
        except Exception as e2:
            log(f'could not release GitHub #{n}: {e2}')
    finally:
        stay_awake(False)


QUEUE = os.environ.get('JARVIS_QUEUE', 'both').lower()


def one_pass(st, dry=False):
    if not ollama_up():
        heartbeat(f'IDLE-REASON: ollama-down {MODEL} not reachable at {OLLAMA}')
        log(f'Ollama/{MODEL} not reachable; waiting (the watchdog restarts Ollama)')
        return
    ran = False
    if QUEUE in ('notion', 'both'):
        if not notion_token():
            if QUEUE == 'notion':
                heartbeat('IDLE-REASON: no-token NOTION_TOKEN is not set for this user')
                return
        else:
            for page in ready_cards():
                run_card(page, st, dry)
                ran = True
    if QUEUE in ('github', 'both'):
        for card in gh_ready():
            gh_run_card(card, st, dry)
            ran = True
    heartbeat('IDLE-REASON: no-cards ' + ('queue done' if ran else 'nothing approved for the laptop'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if not a.dry_run and not start_server():
        print('another laptop Worker is already running', file=sys.stderr)
        return 0
    st = load_state()
    log(f'laptop Worker started (model {MODEL}, poll {POLL_SEC}s)')
    while True:
        try:
            one_pass(st, a.dry_run)
        except Exception as e:
            log(f'pass error: {type(e).__name__}: {e}')
            heartbeat(f'IDLE-REASON: error {str(e)[:120]}')
        if a.once or a.dry_run:
            return 0
        wake_event.wait(POLL_SEC)
        wake_event.clear()


if __name__ == '__main__':
    sys.exit(main())
