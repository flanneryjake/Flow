"""Jarvis lookups: turn "I don't know" into an actual answer.

The local model answers first. If it asks for help (a line "LOOKUP: <query>" or "ASK_CLAUDE: <question>") or gives
up ("I don't know", "I can't browse", ...), resolve() fetches the facts and the model answers again from them:

  1. LOOKUP  -> SearXNG web search on the backup laptop (JARVIS_SEARX_URL, free, no key), then Gemini if search came
               back empty and the topic isn't private.
  2. Claude  -> `claude -p` on this PC (Jake's Claude login) for hard questions, "ask Claude ...", or when search and
               Gemini both came up empty. Slower (tens of seconds), so it's the last resort.

Privacy: clinical / patient / work (Recovery Solutions, Fieldwork Clinical) and money-account questions never go to
Gemini or the web; they go to Claude or stay local. Standard library only.
"""
import html
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

SEARX_URL = os.environ.get('JARVIS_SEARX_URL', 'http://100.90.201.22:8888').rstrip('/')
GEMINI_MODELS = ['gemini-3.5-flash', 'gemini-flash-lite-latest']
GEMINI_API = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
CLAUDE_TIMEOUT = int(os.environ.get('JARVIS_CLAUDE_TIMEOUT', '75'))

LOOKUP_RE = re.compile(r'^\s*LOOKUP:\s*(.+)$', re.I | re.M)
CLAUDE_RE = re.compile(r'^\s*ASK_CLAUDE:\s*(.+)$', re.I | re.M)
GAVE_UP_RE = re.compile(
    r"\b(i\s+(?:really\s+)?(?:don'?t|do not)\s+know|i'?m\s+not\s+sure|no\s+idea|can'?t\s+(?:check|browse|look|access|see)"
    r"|(?:don'?t|do not)\s+have\s+(?:access|the\s+internet|internet|real-?time|live)|without\s+(?:internet|web)\s+access"
    r"|i\s+(?:can'?t|cannot)\s+(?:tell|say)|beyond\s+my\s+knowledge|not\s+in\s+my\s+(?:data|training))\b", re.I)
ASK_CLAUDE_RE = re.compile(r"^\s*(?:hey\s+)?(?:jarvis[,!.:]?\s+)?(?:please\s+)?(?:ask|check\s+with)\s+claude\s+"
                           r"(?!to\b)(?:about\s+)?(.{3,})$", re.I | re.S)
ASK_GEMINI_RE = re.compile(r"^\s*(?:hey\s+)?(?:jarvis[,!.:]?\s+)?(?:please\s+)?ask\s+gemini\s+(?:about\s+)?(.{3,})$",
                           re.I | re.S)
PRIVATE_RE = re.compile(
    r"\b(patient|client|clinical|clinic|diagnos\w*|dsm|icd|methadone|buprenorphine|suboxone|dose|dosage|overdose|"
    r"medicat\w*|prescri\w*|therapy|counsel\w*|hipaa|phi|recovery\s+solutions|fieldwork|intake|treatment\s+plan|"
    r"bank\s+account|account\s+number|routing|password|ssn|social\s+security)\b", re.I)
BANNED_RE = re.compile(r"(?:,\s*)?\b(kid|kiddo|wicked|southie|pal|buddy)\b", re.I)
OLD_HOME_RE = re.compile(r"[^.!?]*\b(south\s+end|southie|the\s+bean)\b[^.!?]*[.!?]?\s*", re.I)
SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"])')
# Exact-fact questions a 9B model gets confidently wrong (counts, records, years, prices, winners): always check them.
FACT_Q_RE = re.compile(
    r"\b(how\s+(?:many|much|old|tall|far|long\s+ago)|what\s+year|which\s+year|when\s+(?:did|was|were|is|does)|"
    r"who\s+(?:won|wins|is\s+the|was\s+the|invented|wrote|owns|holds)|record\s+for|the\s+most|price\s+of|cost\s+of|"
    r"championships?|titles?|population|score|banned|legal\s+in|released?|latest\s+version|"
    r"deathtouch|first\s+strike|double\s+strike|trample|lifelink|hexproof|indestructible|"
    r"commander\s+tax|mana\s+value)\b", re.I)
STOP = set('the a an is are was were what when where who how why do does did to of in on for at it its and or me my '
           'i you your tonight today tomorrow please jarvis hey can could tell'.split())


def is_private(text):
    return bool(PRIVATE_RE.search(text or ''))


def scrub(reply):
    """Last line of defence for the old Boston voice: drop "kid"/"wicked" and friends, and South End asides."""
    out = OLD_HOME_RE.sub('', reply or '')
    out = BANNED_RE.sub('', out)
    out = re.sub(r'\s+([,.!?])', r'\1', out)
    return re.sub(r'\s{2,}', ' ', out).strip()


def trim(reply, sentences=2):
    """Spoken replies stay short: at most `sentences` sentences."""
    parts = SENTENCE_RE.split((reply or '').strip())
    return ' '.join(parts[:sentences]).strip()


def wants(reply, question):
    """What the first answer is asking for: ('lookup', query) / ('claude', question) / (None, None)."""
    m = CLAUDE_RE.search(reply or '')
    if m:
        return 'claude', m.group(1).strip()
    m = LOOKUP_RE.search(reply or '')
    if m:
        return 'lookup', m.group(1).strip()
    if GAVE_UP_RE.search(reply or ''):
        return 'lookup', question
    return None, None


def direct(text):
    """Jake named the helper himself: ('claude'|'gemini', question) or (None, None)."""
    m = ASK_CLAUDE_RE.match(text or '')
    if m:
        return 'claude', m.group(1).strip()
    m = ASK_GEMINI_RE.match(text or '')
    if m:
        return 'gemini', m.group(1).strip()
    return None, None


# ----------------------------------------------------------------------------- sources

def searx(query, n=5, timeout=12):
    url = f'{SEARX_URL}/search?' + urllib.parse.urlencode({'q': query, 'format': 'json', 'safesearch': 1})
    req = urllib.request.Request(url, headers={'User-Agent': 'jarvis-tars', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode('utf-8', 'replace'))
    lines = []
    for a in (data.get('answers') or [])[:2]:
        lines.append(f'Answer box: {a if isinstance(a, str) else a.get("answer", "")}'[:400])
    for b in (data.get('infoboxes') or [])[:1]:
        lines.append(f'Infobox {b.get("infobox", "")}: {b.get("content", "")}'[:500])
    results = (data.get('results') or [])[:n]
    for res in results:
        lines.append(f'- {res.get("title", "")}: {res.get("content", "")} ({res.get("url", "")})'[:400])
    # Snippets are often just page titles ("Sunrise and sunset times in ..."); read the top pages for the actual answer.
    for res in results[:2]:
        try:
            ex = excerpt(page_text(res.get('url', '')), query)
        except Exception:  # noqa: BLE001
            continue
        if ex:
            lines.append(f'From {res.get("url", "")}: {ex}')
    return '\n'.join(l for l in lines if l.strip())


def page_text(url, timeout=8, limit=400_000):
    if not url.startswith(('http://', 'https://')):
        return ''
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (jarvis-tars)', 'Accept': 'text/html'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if 'html' not in (r.headers.get('Content-Type') or 'html'):
            return ''
        raw = r.read(limit).decode('utf-8', 'replace')
    raw = re.sub(r'(?is)<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>', ' ', raw)
    raw = re.sub(r'(?s)<[^>]+>', ' ', raw)
    return html.unescape(re.sub(r'\s+', ' ', raw)).strip()


def excerpt(text, query, limit=900):
    """The page sentences that best match the query, in page order."""
    words = {w for w in re.findall(r'[a-z0-9]+', query.lower()) if w not in STOP and len(w) > 1}
    if not text or not words:
        return ''
    sents = [x for x in re.split(r'(?<=[.!?])\s+', text) if 20 <= len(x) <= 400]
    scored = sorted(((sum(w in x.lower() for w in words) + 0.5 * bool(re.search(r'\d', x)), i)
                     for i, x in enumerate(sents)), reverse=True)
    keep = sorted(i for sc, i in scored[:4] if sc >= max(1, len(words) / 3))
    return ' '.join(sents[i] for i in keep)[:limit]


def gemini(question, timeout=25):
    key = os.environ.get('GEMINI_API_KEY') or _user_env('GEMINI_API_KEY')
    if not key:
        raise RuntimeError('no GEMINI_API_KEY')
    prompt = ('Answer this for a voice assistant in at most 3 short plain sentences. Be specific and factual. If it '
              'depends on today\'s date or live data you do not have, say what you do know and what to check.\n\n'
              f'Question: {question}')
    last = None
    for model in GEMINI_MODELS:
        body = {'contents': [{'parts': [{'text': prompt}]}]}
        req = urllib.request.Request(GEMINI_API.format(model=model), data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            return ''.join(p.get('text', '') for p in data['candidates'][0]['content']['parts']).strip()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f'gemini failed: {last}')


def claude(question, timeout=CLAUDE_TIMEOUT):
    exe = shutil.which('claude') or shutil.which('claude.cmd') or os.path.expanduser(r'~\.local\bin\claude.exe')
    if not exe or not os.path.exists(exe) and not shutil.which(exe):
        raise RuntimeError('claude CLI not found')
    prompt = ('You are helping Jarvis, Jake\'s home voice assistant, answer him. Find the actual answer (search the '
              'web if it needs current facts). Reply with only the answer, in at most 4 short plain sentences, no '
              f'markdown.\n\nJake asked: {question}')
    prompt = ' '.join(prompt.split())     # claude.cmd is a batch file: anything after a newline is dropped
    p = subprocess.run([exe, '-p', prompt, '--output-format', 'text', '--allowedTools', 'WebSearch', 'WebFetch'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                       cwd=os.path.dirname(os.path.abspath(__file__)),
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    out = (p.stdout or '').strip()
    if p.returncode != 0 or not out:
        raise RuntimeError(f'claude exit {p.returncode}: {(p.stderr or "")[:200]}')
    return out


def _user_env(name):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
            return winreg.QueryValueEx(k, name)[0]
    except (ImportError, OSError):
        return None


# ----------------------------------------------------------------------------- the whole lookup

def find(kind, query, private=False, log=print):
    """Gather facts for `query`. Returns (source, facts) or (None, '') when every source failed."""
    order = ['claude'] if kind == 'claude' else (['claude'] if private else ['searx', 'gemini', 'claude'])
    if kind == 'gemini':
        order = ['claude'] if private else ['gemini', 'searx', 'claude']
    for src in order:
        try:
            facts = {'searx': searx, 'gemini': gemini, 'claude': claude}[src](query)
        except Exception as e:  # noqa: BLE001
            log(f'lookup {src} failed: {type(e).__name__}: {str(e)[:160]}')
            continue
        if facts and not GAVE_UP_RE.search(facts[:200]):
            log(f'lookup {src} ok ({len(facts)} chars) for: {query[:80]}')
            return src, facts[:3000]
    return None, ''


def resolve(text, first_reply, answer_with, log=print, today='', check_facts=True):
    """Return the final spoken reply. `answer_with(note)` re-asks the local model with a NOTE added to its context."""
    kind, query = direct(text)
    asked_by_name = bool(kind)
    checking = False
    if not kind:
        kind, query = wants(first_reply, text)
    if not kind and check_facts and FACT_Q_RE.search(text or '') and not is_private(text):
        kind, query = 'lookup', text      # check the model's answer against the web before saying it
        checking = True
    if not kind:
        return trim(scrub(first_reply))
    src, facts = find(kind, query, private=is_private(text) or is_private(query), log=log)
    if not src:
        return ("I couldn't get a straight answer from the web or from Claude just now, sir. "
                "Say \"tell Claude to look into it\" and I'll put it on the board for tonight.")
    if asked_by_name and src in ('claude', 'gemini'):   # Jake asked that helper: speak its answer as-is
        return scrub(re.sub(r'\s+', ' ', facts))[:600]
    label = {'searx': 'a web search', 'gemini': 'Gemini', 'claude': 'Claude'}[src]
    note = (f'LOOKUP RESULT from {label} for "{query}":\n{facts}\n\nAnswer Jake now from this result in 1-3 spoken '
            'sentences, in character. Give the actual answer; mention the source only if it matters. Do not output '
            'LOOKUP or ASK_CLAUDE again and do not say you don\'t know. Only state numbers, times, scores and dates '
            'that appear in the result; if the exact one isn\'t there, say what the result does show and that you '
            'couldn\'t confirm the rest. Work out "yesterday" or "last night" from the dates in the result'
            + (f' (today is {today})' if today else '') + '.')
    try:
        reply = answer_with(note)
    except Exception as e:  # noqa: BLE001
        log(f'restating lookup failed: {e}')
        reply = ''
    if not reply or LOOKUP_RE.search(reply) or CLAUDE_RE.search(reply):
        reply = re.sub(r'\s+', ' ', facts.split('\n- ')[0])[:400]   # speak the source's own answer
    if src == 'claude':
        return scrub(reply)
    return trim(scrub(reply), 1 if checking else 2)   # a checked fact is one sentence: extras are where errors live
