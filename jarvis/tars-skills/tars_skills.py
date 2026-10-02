"""Tars skills: the three small jobs that make Tars the rig's assistant.

  check_rig_output(text, ...)  item 5: after a rig job, check the draft (crisis line, required sections,
                               leftover placeholders, word count) and write the one-line hub notification.
  route_request(text)          item 6: front door. A hub/phone request is answered on the spot if it is small,
                               or comes back as a rig job or a Claude card.
  ha_intent(text, entities)    item 7: turn a spoken lights/climate request into one Home Assistant service
                               call. Locks, alarms, garage doors and covers are never produced.

Every function calls Tars (tars:latest on the laptop's Ollama) and checks what it says; the hard
checks (crisis line, placeholders, sections, allowed HA services) are plain code, so a wrong model answer can't
let a bad draft or an unsafe service call through. If the laptop is unreachable, each function still returns a
result with "model": null (the checker falls back to code-only checks and a template notification; the router
and HA intent return route "rig" / ok false so the caller uses its existing path).

Prompts match the Tars training rows (kit/training/tars on the tars-kit branch of jarvis-outputs), so what the model
sees in production is what it is being trained on.

Standard library only. Config from environment variables:
  TARS_OLLAMA_URL    default http://127.0.0.1:11434 (homebase: http://laptop-4150egrs:11434)
  TARS_MODEL         default tars:latest
CLI:
  python tars_skills.py check draft.md --sections "Objectives,Closing" --max-words 3000
  python tars_skills.py route "turn off the porch light"
  python tars_skills.py ha "set the bedroom to 68" --entities entities.json
"""
import argparse
import ast
import json
import operator
import os
import re
import sys
import time
import urllib.request

URL = os.environ.get('TARS_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
MODEL = os.environ.get('TARS_MODEL', 'tars:latest')
TIMEOUT = 60

# Same text as TARS_SYSTEM in kit/tools/build_seed.py.
TARS_SYSTEM = (
    "You are Tars, the small fast model on Jake's RTX 5060 laptop. You do short jobs fast: classify, route, "
    "extract, reformat, tag, name, summarize logs, write one-line notifications, and pass/fail checks on text you are "
    "given. Same voice as big Jarvis (dry, plain, a little South End) but keep it short. You never author long "
    "documents, code beyond a one-liner, or clinical, legal or money content. If a job is bigger than you, reply with "
    "JSON {\"route\":\"rig\"|\"claude\",\"why\":\"...\",\"handoff\":\"...\"} instead of trying. Escalating is a "
    "correct answer. Never claim you did something you can't do.")


# ----------------------------------------------------------------------------- model call

def _chat(user, schema=None, system=TARS_SYSTEM, max_tokens=400, timeout=TIMEOUT):
    """Returns (text, seconds) or (None, error string)."""
    body = {'model': MODEL, 'stream': False, 'think': False,
            'options': {'temperature': 0.1, 'num_ctx': 4096, 'num_predict': max_tokens},
            'messages': ([{'role': 'system', 'content': system}] if system else []) + [{'role': 'user', 'content': user}]}
    if schema:
        body['format'] = schema
    req = urllib.request.Request(f'{URL}/api/chat', data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode('utf-8'))['message']['content']
        return out.strip(), round(time.time() - t0, 2)
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def _json(text):
    if not text:
        return None
    m = re.search(r'\{.*\}', text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


# ----------------------------------------------------------------------------- item 5: rig output checker

CRISIS = re.compile(r'\b988\b|\b911\b')
PLACEHOLDER = re.compile(
    r'\[(?:insert|todo|tbd|add|placeholder|name|date|xx+)[^\]]{0,60}\]|\{\{[^}]{0,60}\}\}|<(?:insert|todo|tbd)[^>]{0,60}>'
    r'|\bTODO\b|\bTBD\b|\bFIXME\b|lorem ipsum|\bXX+\b', re.I)


def _has_section(text, name):
    pat = re.compile(r'^\s*(?:#{1,6}\s*|\*\*)?\s*(?:\d+[.)]\s*)?' + re.escape(name.strip()) + r'\b', re.I | re.M)
    return bool(pat.search(text))


STOP = {'the', 'and', 'for', 'with', 'this', 'that', 'what', 'about', 'from', 'into', 'your', 'you', 'are', 'its',
        'each', 'any', 'all', 'one', 'two', 'three', 'four', 'five', 'per', 'sentences', 'sentence', 'words', 'word',
        'bullets', 'bullet', 'lines', 'line', 'short', 'brief', 'section', 'include', 'list', 'plus', 'least', 'box',
        'plain', 'language', 'step', 'tied', 'used', 'option', 'suggested', 'wording', 'quotes', 'adds', 'session',
        'length', 'own'}
QUALIFIER = re.compile(r'\s(?:with|as|for|that|tied to|which|where|using|in)\s.*$', re.I)


def _key_words(s):
    s = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', s)          # drop "(2-3 sentences)" style asides
    return {w for w in re.findall(r'[a-z][a-z0-9]+', s.lower()) if len(w) > 2 and w not in STOP}


def _clean_part(p):
    """'3 plain-language goals' -> 'plain-language goals'; 'a WORKSHEET section ...' -> 'WORKSHEET section ...'."""
    p = re.sub(r'^\s*(?:and\s+|or\s+)?(?:\d+|a|an|the|one|two|three|four|five|some)\s+', '', p.strip(), flags=re.I)
    return p.strip(' .:;')


def _headings(text):
    out = []
    for ln in text.splitlines():
        t = ln.strip()
        if re.match(r'#{1,6}\s', t) or re.match(r'\d+[.)]\s+\S', t) or (t.endswith(':') and len(t) < 80) \
                or (t.isupper() and 3 < len(t) < 80):
            out.append(t)
        else:
            m = re.match(r'\*\*([^*]{2,80})\*\*', t)    # "**Goals for this session**", "**Title:** Stress, ..."
            if m:
                out.append(m.group(1))
    return out


def _hit(k, hk):   # same word, or one a prefix of the other ("intro"/"introduction", "worksheet"/"worksheets")
    return any(k == w or (min(len(k), len(w)) >= 4 and (k.startswith(w) or w.startswith(k))) for w in hk)


def _has_heading_like(text, name):
    """An Include: part counts as present when:
      * a heading carries most of its key words ("What this session is about (2-3 sentences)" ->
        "**What this session is about**"), or
      * a heading carries its head noun, the last key word before any "with/as/for/tied to" qualifier
        ("3 plain-language goals" -> "**Goals for this session**", "a WORKSHEET section with ..." -> "### WORKSHEET"), or
      * every quoted phrase in it appears in the text ("a footer with 'If you are in crisis: call or text 988 ...'").
    "title" is satisfied by the H1 or a "Title:" line; "intro"/"overview" by the text right under the H1."""
    name = _clean_part(name)
    if not name or _has_section(text, name):
        return True
    flat = re.sub(r'\s+', ' ', text.lower())
    # Quotes, not apostrophes: "'This week's practice'" and "'Handling difficult moments' (... doesn't ...)".
    quoted = [a or b for a, b in re.findall(r"(?<![A-Za-z])'(.{6,}?)'(?![A-Za-z])|\"([^\"]{6,})\"", name)]
    quoted = [q.split('<')[0].strip() for q in quoted]          # "Adapted from <source> ..." -> "Adapted from"
    quoted = [q for q in quoted if len(q) >= 6]
    if quoted and all(re.sub(r'\s+', ' ', q.lower())[:40] in flat for q in quoted):
        return True
    keys = _key_words(name)
    if not keys:
        return True
    heads = _headings(text)
    if keys <= {'title', 'name'}:
        return any(re.match(r'#\s', h) or h.lower().startswith('title') for h in heads)
    if keys & {'intro', 'introduction', 'overview', 'opening'} and len(keys) <= 2:
        body = re.split(r'^#\s.*$', text, maxsplit=1, flags=re.M)[-1]
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), '')
        if first and not first.startswith('#'):
            return True
    head_keys = [_key_words(h) for h in heads]
    need = max(1, round(len(keys) * 0.6))   # 1 of 1-2 key words, 2 of 3-4, 3 of 5
    if any(sum(_hit(k, hk) for k in keys) >= need for hk in head_keys):
        return True
    core = [w for w in re.findall(r'[a-z][a-z0-9]+', QUALIFIER.sub('', re.sub(r'\([^)]*\)', ' ', name)).lower())
            if len(w) > 2 and w not in STOP]
    return bool(core) and any(_hit(core[-1], hk) for hk in head_keys)


def _split_top(s, seps):
    """Split on any of seps where they are not inside quotes or parentheses. An apostrophe between two
    letters (week's, doesn't) is not a quote."""
    out, buf, depth, quote, i = [], '', 0, None, 0
    while i < len(s):
        c = s[i]
        apostrophe = c == "'" and s[i - 1:i].isalpha() and s[i + 1:i + 2].isalpha()
        if quote:
            if c == quote and not apostrophe:
                quote = None
        elif c in '\'"' and not apostrophe:
            quote = c
        elif c == '(':
            depth += 1
        elif c == ')':
            depth = max(0, depth - 1)
        elif depth == 0:
            sep = next((x for x in seps if s.startswith(x, i)), None)
            if sep:
                out.append(buf)
                buf, i = '', i + len(sep)
                continue
        buf += c
        i += 1
    out.append(buf)
    return out


def include_sections(job_text):
    """Section names from every "Include: ..." in a job prompt, wherever it sits (homebase's rig prompts put it
    mid-sentence). A list runs to the first sentence end outside quotes and parentheses. It is split on ";" when
    it uses them, else on "," and " and ", never inside quotes or parentheses."""
    names = []
    for m in re.finditer(r'\bInclude:\s*', job_text or '', re.I):
        seg = _split_top(job_text[m.end():], ['. ', '.\n', '\n\n'])[0]
        semi = _split_top(seg, ['; ', ';'])
        parts = semi if len(semi) > 1 else [q for p in _split_top(seg, [', ']) for q in _split_top(p, [' and '])]
        for p in parts:
            p = _clean_part(p)
            if p and len(p) < 400 and p.lower() not in (n.lower() for n in names):
                names.append(p)
    return names


def check_rig_output(text, title='', required_sections=(), crisis_line=True, min_words=None, max_words=None,
                     warn_sections=(), job_prompt='', require_988=None, require_rig_notes=None):
    """Hard checks in code, then Tars writes the hub line. Returns
    {"pass": bool, "problems": [...], "warnings": [...], "words": n, "notify": "one line", "model": seconds|None}.
    Pass the rig job's prompt as job_prompt and it sets the rest the way homebase's Check-RigOutput does:
      * 988 is required on its own (911 alone fails) whenever the prompt mentions 988; otherwise 988 or 911.
      * a missing RIG-NOTES block fails whenever the prompt asks for one.
      * "Include:" sections from the prompt are checked as headings, WARN only.
    required_sections always fail when missing; require_988 / require_rig_notes override the prompt-based default."""
    problems, warnings = [], []
    if require_988 is None:
        require_988 = bool(re.search(r'\b988\b', job_prompt or ''))
    if require_rig_notes is None:
        require_rig_notes = bool(re.search(r'RIG-NOTES', job_prompt or ''))
    warn_sections = list(warn_sections) + [s for s in include_sections(job_prompt) if s not in warn_sections]
    if require_rig_notes and not re.search(r'RIG-NOTES', text):
        problems.append('no RIG-NOTES block')
    wmiss = [s for s in warn_sections if s.strip() and not _has_heading_like(text, s)]
    if wmiss:
        warnings.append('Include: sections not found as headings: ' + ', '.join(wmiss))
    words = len(text.split())
    if require_988 and not re.search(r'\b988\b', text):
        problems.append('no 988 line' + (' (911 alone is not enough)' if re.search(r'\b911\b', text) else ''))
    elif crisis_line and not CRISIS.search(text):
        problems.append('no 988/911 crisis line')
    missing = [s for s in required_sections if s.strip() and not _has_section(text, s)]
    if missing:
        problems.append('missing section' + ('s' if len(missing) > 1 else '') + ': ' + ', '.join(missing))
    ph = sorted({m.group(0).strip() for m in PLACEHOLDER.finditer(text)})
    if ph:
        problems.append('placeholders left: ' + ', '.join(ph[:5]) + (' ...' if len(ph) > 5 else ''))
    if min_words and words < min_words:
        problems.append(f'only {words} words (min {min_words})')
    if max_words and words > max_words:
        problems.append(f'{words} words (max {max_words})')
    ok = not problems
    name = title or 'rig draft'
    fallback = (f'{name}: passed checks ({words} words).' if ok
                else f'{name}: failed checks, {"; ".join(problems)}.')
    if warnings and ok:
        fallback = f'{name}: passed checks ({words} words), warning: {"; ".join(warnings)}.'
    facts = (f'Job: {name}\nResult: {"PASS" if ok else "FAIL"}\nWords: {words}\n'
             + (f'Problems: {"; ".join(problems)}\n' if problems else '')
             + (f'Warnings: {"; ".join(warnings)}\n' if warnings else ''))
    out, secs = _chat('Write the one-line hub notification for this finished rig job. One sentence, no markdown, '
                      'state pass or fail and the problems if any.\n' + facts, max_tokens=80)
    notify = out.splitlines()[0].strip(' "') if out else fallback
    # The line must agree with the code's verdict; if it doesn't, use the template.
    if out and (('fail' in notify.lower()) == ok or not notify):
        notify = fallback
    return {'pass': ok, 'problems': problems, 'warnings': warnings, 'words': words, 'notify': notify[:200],
            'model': secs if out else None}


# ----------------------------------------------------------------------------- item 6: front door

# "why" comes first so the model reasons before it picks; with route first it often picked rig and then
# explained why the job was small.
ROUTE_SCHEMA = {'type': 'object', 'required': ['why', 'route', 'handoff'],
                'properties': {'why': {'type': 'string'},
                               'route': {'type': 'string', 'enum': ['laptop', 'rig', 'claude']},
                               'handoff': {'type': 'string'}}}
ROUTE_HINT = (
    "Routes: laptop = you answer it now (status, small talk, jokes, naming, tagging, lists, reformatting, short "
    "summaries of text given, Home Assistant lights/climate). rig = long-form writing over ~400 words, curricula, "
    "guides, handouts, store copy, cover letters, code, clinical documents. claude = web, current facts, accounts, "
    "purchases, email or messages to people, publishing, other PCs.")
# Rule overrides before and after the model: these never stay on the laptop, whatever it says.
TO_CLAUDE = re.compile(
    r'\b(buy|purchase|order (more|new|a|some)|subscribe|pay|checkout|publish|post (it|this|to|on)|tweet|email|'
    r'e-mail|text (him|her|them|my)|message (him|her|them)|send (it|this|a|an|the) |delete|password|api key|'
    r'merge|git push|install|download|search (the web|online|for)|look up|google|'
    r'news|weather|price|website|log ?in|sign in|on the (homebase|rig) pc)\b', re.I)
STATUS = re.compile(r"^\s*(is|are|was|what'?s|how'?s|whats|hows|status|any)\b.{0,60}\b(on|up|down|awake|asleep|running|"
                    r"online|offline|status|doing|working|busy|queued|waiting|left)\b", re.I)
TO_RIG = re.compile(
    r'\b(curriculum|facilitator guide|handout|study guide|cover letter|resume|r[eé]sum[eé]|seo|product (copy|'
    r'description)|lesson plan|essay|paper|report|write (a|an|the|me) (\w+ ){0,3}(guide|plan|script|program|app|'
    r'story|article|post|letter|blog)|blog post|code|python|script|refactor|progress note|treatment plan|'
    r'assessment|\d[\d,]{2,}[- ]?words?|\d+[- ]?pages?|long[- ]form)\b', re.I)


# ----------------------------------------------------------------------------- math (front door)
# The 9B gets arithmetic wrong ("15 percent of 80" -> "Twelve point eight"), so simple math is computed here
# with a small AST walker (no eval) and Tars only phrases the number. Its line is used only if it
# contains the exact result; otherwise a plain template answers.

_WORDS = [(r'\bmultiplied by\b|\btimes\b|\bx\b(?=\s*[\d(])', '*'), (r'\bdivided by\b|\bover\b', '/'),
          (r'\bplus\b|\badded to\b', '+'), (r'\bminus\b|\bless\b', '-'), (r'\bsquared\b', '**2'),
          (r'\bto the power of\b', '**')]
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _safe_eval(expr):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 12:
                raise ValueError('exponent too large')
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError('not simple arithmetic')
    return ev(ast.parse(expr, mode='eval'))


def _fmt(x):
    if isinstance(x, float):
        x = round(x, 4)
        if x == int(x):
            x = int(x)
    return f'{x:,}' if isinstance(x, int) else f'{x:,.4f}'.rstrip('0').rstrip('.')


def solve_math(text):
    """Returns (expression shown, result string) for a simple arithmetic or percent question, else None."""
    t = text.lower().replace(',', '').strip().rstrip('?.! ')
    t = re.sub(r"^(hey jarvis,?\s*)?(what'?s|what is|whats|calculate|compute|how much is|work out)\s+", '', t)
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)\s+of\s+\$?(\d+(?:\.\d+)?)', t)
    if m:
        p, n = float(m.group(1)), float(m.group(2))
        return f'{_fmt(p)}% of {_fmt(n)}', _fmt(p * n / 100)
    m = re.fullmatch(r'\$?(\d+(?:\.\d+)?)\s+is what (?:percent|%) of\s+\$?(\d+(?:\.\d+)?)', t)
    if m and float(m.group(2)):
        return f'{m.group(1)} as a percent of {m.group(2)}', _fmt(float(m.group(1)) / float(m.group(2)) * 100) + '%'
    m = re.fullmatch(r'(?:a\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent)\s+tip on\s+\$?(\d+(?:\.\d+)?)', t)
    if m:
        tip = float(m.group(1)) * float(m.group(2)) / 100
        return f'{m.group(1)}% tip on ${m.group(2)}', f'${tip:,.2f} (total ${tip + float(m.group(2)):,.2f})'
    expr = t.replace('$', '')
    for pat, rep in _WORDS:
        expr = re.sub(pat, rep, expr)
    expr = expr.replace('^', '**').replace('×', '*').replace('÷', '/')
    if not re.fullmatch(r'[\d\s.+\-*/%()]+', expr) or not re.search(r'\d\s*(\*\*|[+\-*/%])\s*[\d(]', expr):
        return None
    try:
        val = _safe_eval(expr.strip())
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
        return None
    return re.sub(r'\s+', ' ', expr.strip()), _fmt(val)


def _math_answer(text, solved):
    shown, result = solved
    out, secs = _chat(f"Jake asked: {text}\nThe exact answer, already computed, is {result}. Reply in one short "
                      f"sentence that states {result} exactly as written. Do not redo the math.", system=None,
                      max_tokens=60)
    line = out.splitlines()[0].strip() if out else ''
    if result.split(' ')[0] not in line:
        line = f'{shown} = {result}.'
    return line, secs if out else None


HA_CMD = re.compile(r'\b(turn|switch|dim|brighten|set|put)\b.{0,40}\b(lights?|lamps?|thermostat|heat|ac|air|'
                    r'temperature|degrees)\b|\b(lights?|lamps?)\s+(on|off)\b', re.I)
HA_LATER = re.compile(r'\b(at \d|in \d+ ?(min|minutes|hours?)|tonight|tomorrow|every|schedule|when|after|before|until)\b',
                      re.I)


def route_request(text, context=''):
    """Returns {"route": laptop|rig|claude, "why", "handoff", "answer", "model"}. "answer" is set only for laptop.
    context: optional current state for status questions (e.g. the Machine Health snapshot lines, Worker queue),
    so "is the rig on" is answered on the laptop from what the caller knows. Simple arithmetic and percent
    questions are computed in code (solve_math) and the answer carries the exact result."""
    t = text.strip()
    if HA_CMD.search(t):
        if HA_LATER.search(t):
            return {'route': 'claude', 'why': 'A scheduled or conditional home action needs an HA automation.',
                    'handoff': t, 'answer': '', 'model': None}
        r = ha_intent(t)
        return {'route': 'laptop', 'why': 'Home Assistant command.', 'handoff': '', 'ha': r, 'model': r['model'],
                'answer': '' if r['ok'] else f"I can't do that one by voice: {r['why']}"}
    solved = solve_math(t)
    if solved:
        line, secs = _math_answer(t, solved)
        return {'route': 'laptop', 'why': 'Arithmetic, computed in code.', 'handoff': '', 'answer': line,
                'result': solved[1], 'model': secs}
    ctx = f"Current state you can answer status questions from:\n{context.strip()[:2500]}\n" if context else ''
    if len(t) / 3.5 > 3000:
        return {'route': 'rig', 'why': 'Too long for the laptop.', 'handoff': t[:300], 'answer': '', 'model': None}
    out, secs = _chat(f"{ROUTE_HINT}\n{ctx}Classify this request and route it: '{t}'", schema=ROUTE_SCHEMA,
                      max_tokens=200)
    ans = _json(out) or {}
    route = ans.get('route') if ans.get('route') in ('laptop', 'rig', 'claude') else None
    why, handoff = ans.get('why', ''), ans.get('handoff', '')
    if route is None:
        return {'route': 'rig', 'why': f'Tars unavailable ({secs}); default path.', 'handoff': t,
                'answer': '', 'model': None}
    if route != 'claude' and TO_CLAUDE.search(t):
        route, why, handoff = 'claude', 'Needs web, accounts or an outside action.', handoff or t
    elif route == 'laptop' and TO_RIG.search(t):
        route, why, handoff = 'rig', 'Long-form or code; rig work.', handoff or t
    elif route != 'laptop' and context and STATUS.search(t) and not TO_RIG.search(t):
        route, why, handoff = 'laptop', 'Status question, answered from the state given.', ''
    answer = ''
    if route == 'laptop':
        # Answer in the normal Tars voice (the Modelfile's own system prompt).
        a, s2 = _chat(ctx + t if ctx else t, system=None, max_tokens=250)
        answer = a or ''
        if a and (_json(a) or {}).get('route') in ('rig', 'claude'):   # it changed its mind mid-answer
            j = _json(a)
            route, why, handoff, answer = j['route'], j.get('why', why), j.get('handoff', t), ''
        if not a:
            route, why, handoff = 'rig', 'Tars did not answer.', t
    return {'route': route, 'why': why, 'handoff': handoff if route != 'laptop' else '', 'answer': answer,
            'model': secs}


# ----------------------------------------------------------------------------- item 7: Home Assistant

ALLOWED_SERVICES = {
    'light.turn_on', 'light.turn_off', 'light.toggle',
    'climate.set_temperature', 'climate.set_hvac_mode', 'climate.turn_on', 'climate.turn_off',
}
BLOCKED = re.compile(r'\b(lock|unlock|alarm|arm|disarm|garage|door|gate|cover|blind|shade|camera|siren|oven|stove)\b', re.I)
HA_SYSTEM = (
    "You turn one spoken Home Assistant request into one service call as JSON. Allowed services only: "
    "light.turn_on, light.turn_off, light.toggle, climate.set_temperature, climate.set_hvac_mode, climate.turn_on, "
    "climate.turn_off. target is {\"area_id\": \"<room>\"} (snake_case room name) or {\"entity_id\": \"<id>\"} "
    "when the known entities list has a match. data only when needed: {\"temperature\": n}, {\"hvac_mode\": "
    "\"heat|cool|heat_cool|off\"}, {\"brightness_pct\": n}. Examples:\n"
    "turn off the living room lights -> {\"service\":\"light.turn_off\",\"target\":{\"area_id\":\"living_room\"}}\n"
    "set the bedroom to 68 -> {\"service\":\"climate.set_temperature\",\"target\":{\"area_id\":\"bedroom\"},"
    "\"data\":{\"temperature\":68}}\n"
    "dim the office lights to 30 percent -> {\"service\":\"light.turn_on\",\"target\":{\"area_id\":\"office\"},"
    "\"data\":{\"brightness_pct\":30}}\n"
    "turn the heat off downstairs -> {\"service\":\"climate.set_hvac_mode\",\"target\":{\"area_id\":\"downstairs\"},"
    "\"data\":{\"hvac_mode\":\"off\"}}")
HA_SCHEMA = {'type': 'object', 'required': ['service', 'target'],
             'properties': {'service': {'type': 'string', 'enum': sorted(ALLOWED_SERVICES)},
                            'target': {'type': 'object', 'properties': {'area_id': {'type': 'string'},
                                                                        'entity_id': {'type': 'string'}}},
                            'data': {'type': 'object'}}}


def ha_intent(text, entities=None):
    """entities: optional list of HA entity_ids (and/or area_ids) that exist. Returns
    {"ok": bool, "call": {service, target, data} | None, "why", "model"}. Only lights and climate."""
    if BLOCKED.search(text):
        return {'ok': False, 'call': None, 'model': None,
                'why': 'Locks, alarms, doors and covers are not handled by voice; ask from the app.'}
    hint = ''
    if entities:
        hint = '\nKnown entities and areas: ' + ', '.join(sorted(entities)[:80])
    out, secs = _chat(text + hint, schema=HA_SCHEMA, system=HA_SYSTEM, max_tokens=150)
    call = _json(out)
    if not call:
        return {'ok': False, 'call': None, 'why': f'Tars unavailable or gave no call ({secs}).', 'model': None}
    svc = str(call.get('service', ''))
    target = {k: v for k, v in (call.get('target') or {}).items() if v}
    data = call.get('data') or {}
    if svc not in ALLOWED_SERVICES:
        return {'ok': False, 'call': call, 'why': f'{svc or "no service"} is outside lights and climate.', 'model': secs}
    if not target:
        return {'ok': False, 'call': call, 'why': 'No room or device named.', 'model': secs}
    tid = target.get('entity_id') or target.get('area_id')
    if tid and str(tid).split('.')[0] not in ('light', 'climate') and 'entity_id' in target:
        return {'ok': False, 'call': call, 'why': f'{tid} is not a light or thermostat.', 'model': secs}
    if entities and tid not in entities:
        return {'ok': False, 'call': call, 'why': f'Unknown target {tid}.', 'model': secs}
    if svc == 'climate.set_temperature':
        temp = data.get('temperature')
        if not isinstance(temp, (int, float)) or not 55 <= temp <= 85:
            return {'ok': False, 'call': call, 'why': f'Temperature {temp!r} is outside 55-85.', 'model': secs}
    return {'ok': True, 'call': {'service': svc, 'target': target, **({'data': data} if data else {})},
            'why': '', 'model': secs}


# ----------------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('file')
    c.add_argument('--title', default='')
    c.add_argument('--sections', default='')
    c.add_argument('--no-crisis', action='store_true')
    c.add_argument('--min-words', type=int)
    c.add_argument('--max-words', type=int)
    c.add_argument('--prompt-file', help="the rig job's prompt: sets the 988, RIG-NOTES and Include: checks")
    r = sub.add_parser('route')
    r.add_argument('text')
    h = sub.add_parser('ha')
    h.add_argument('text')
    h.add_argument('--entities', help='JSON file with a list of entity/area ids')
    a = ap.parse_args()
    if a.cmd == 'check':
        prompt = ''
        if a.prompt_file:
            with open(a.prompt_file, encoding='utf-8', errors='replace') as f:
                prompt = f.read()
        with open(a.file, encoding='utf-8', errors='replace') as f:
            res = check_rig_output(f.read(), a.title or os.path.basename(a.file),
                                   [s for s in a.sections.split(',') if s.strip()], not a.no_crisis,
                                   a.min_words, a.max_words, job_prompt=prompt)
    elif a.cmd == 'route':
        res = route_request(a.text)
    else:
        ents = None
        if a.entities:
            with open(a.entities, encoding='utf-8') as f:
                ents = json.load(f)
        res = ha_intent(a.text, ents)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get('pass', res.get('ok', True)) else 1


if __name__ == '__main__':
    sys.exit(main())
