"""Check the Jarvis training set: python check_training.py [folder]. Exits 1 on any failure. Standard library only.

Checks conversations.jsonl, prompts.jsonl and modelfile_examples.txt against the persona and routing rules:
lanes, banned words, no "I don't know" (or any phrase the server treats as giving up), spoken length, routing lines
alone in their message, LOOKUP / ASK_CLAUDE followed by the server's NOTE turn, numbers grounded in the context,
card numbers only from FACTS / the filing NOTE, and (when ../home.py, ../live.py and ../tars_server.py are present)
that every context block is what the server would really build for that text.
"""
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = sys.argv[1] if len(sys.argv) > 1 else HERE

LANES = {'local', 'lookup', 'claude', 'card', 'live', 'home', 'tasks'}
CATEGORIES = {'morning', 'small_talk', 'weather_time', 'news_sports', 'home', 'music', 'printing', 'money',
              'groceries_cooking', 'mtg', 'homelab', 'task_board', 'planning', 'reminders', 'general_knowledge',
              'follow_up', 'vague', 'correction', 'stress'}
BANNED = re.compile(r'\b(kid|kiddo|wicked|southie|pal|buddy|bud|dude|bro|chief|champ|sport|wicked\s+smaht|'
                    r'kehd|pissah|packie|bubbler|the\s+bean)\b', re.I)
# lookup.GAVE_UP_RE: any of these in a first reply makes the server run a web search instead of speaking it.
GAVE_UP = re.compile(
    r"\b(i\s+(?:really\s+)?(?:don'?t|do not)\s+know|i'?m\s+not\s+sure|no\s+idea|can'?t\s+(?:check|browse|look|access|see)"
    r"|(?:don'?t|do not)\s+have\s+(?:access|the\s+internet|internet|real-?time|live)|without\s+(?:internet|web)\s+access"
    r"|i\s+(?:can'?t|cannot)\s+(?:tell|say)|beyond\s+my\s+knowledge|not\s+in\s+my\s+(?:data|training))\b", re.I)
SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"])')   # lookup.SENTENCE_RE; the server keeps 3 sentences
ROUTE_RE = re.compile(r'^(LOOKUP|ASK_CLAUDE):\s*\S')
OLD_ROUTE_RE = re.compile(r'^\s*(HOME|CARD):', re.M)
CTX_RE = re.compile(r'^\[context\]\nNow: .+?\n\[/context\]\n\nJake: (.+)$', re.S)
MARKDOWN_RE = re.compile(r'(\*\*|__|^\s*[-*#]\s|^\s*\d+\.\s|`)', re.M)
EMOJI_RE = re.compile('[\U0001F300-\U0001FAFF☀-➿]')
SIR_RE = re.compile(r'\bsir\b', re.I)
MAX_WORDS, MAX_SENTENCES, MAX_SIR = 60, 3, 0.40
MIN_SIR = 0.30   # Jake 2026-10-03: "sir" in about 30-40% of replies

errors, warnings = [], []


def err(where, msg):
    errors.append(f'{where}: {msg}')


# ----------------------------------------------------------------------------- the server's own trigger regexes

def _load_server():
    tars = os.path.dirname(HERE)
    try:
        sys.path.insert(0, tars)
        import home  # noqa: F401
        import live  # noqa: F401
        src = open(os.path.join(tars, 'tars_server.py'), encoding='utf-8').read()
        found = {}
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') in ('FILE_RE', 'TASK_WORDS'):
                found[node.targets[0].id] = eval(ast.get_source_segment(src, node.value), {'re': re})
        return home, live, found['FILE_RE'], found['TASK_WORDS']
    except Exception as e:  # noqa: BLE001
        warnings.append(f'server modules not loaded ({type(e).__name__}: {e}); skipping context-consistency checks')
        return None


SERVER = _load_server()
NOT_WEATHER = re.compile(r'\b(printer|bed|nozzle|cpu|gpu|pc|laptop|rig)\b', re.I)


def check_context(where, content):
    """The context block must be what tars_server.chat() would build for this text."""
    m = CTX_RE.match(content)
    if not m:
        err(where, 'user message is not "[context]\\nNow: ...\\n[/context]\\n\\nJake: <text>"')
        return None
    text = m.group(1)
    if not SERVER:
        return text
    home, live, file_re, task_words = SERVER
    fm = file_re.match(text)
    if home.parse(text)[0] and not fm and 'humor setting' not in content:
        err(where, f'home command is handled in code and never reaches the model: {text!r}')
    has_live = 'LIVE (fresh data' in content
    want_weather = bool(live.WEATHER_RE.search(text) and not NOT_WEATHER.search(text)) and not fm
    want_sun = bool(live.SUN_RE.search(text)) and not fm
    want_home = bool(home.ASK_RE.search(text)) and not fm
    if ('Weather for ' in content) != want_weather:
        err(where, f'weather block {"missing" if want_weather else "present but the server would not add it"}: {text!r}')
    if ('Sun in ' in content) != want_sun:
        err(where, f'sun block {"missing" if want_sun else "present but the server would not add it"}: {text!r}')
    home_here = bool(re.search(r'Echo|Home Assistant has not reported in yet|No alarms are set', content.split('[/context]')[0]))
    if home_here != want_home:
        err(where, f'home block {"missing" if want_home else "present but the server would not add it"}: {text!r}')
    if has_live != (want_weather or want_sun or want_home):
        err(where, 'LIVE header does not match its contents')
    want_facts = bool(task_words.search(text)) and not fm
    if ('FACTS (live from the task board' in content) != want_facts:
        err(where, f'FACTS {"missing" if want_facts else "present but the server would not add them"}: {text!r}')
    if fm and 'filed GitHub card' not in content and 'Filing the card FAILED' not in content:
        err(where, f'"{fm.group(1)} ..." files a card, so the context needs the filing NOTE: {text!r}')
    return text


# ----------------------------------------------------------------------------- numbers must come from the context

def numbers(text):
    return {int(n) for n in re.findall(r'\d+', text.replace(',', ''))}


def context_numbers(text):
    out = numbers(text)
    for h, _ in re.findall(r'\b(\d{1,2}):(\d{2})\b', text):
        h = int(h)
        out.add(h - 12 if h > 12 else h + 12)
        if h == 0:
            out.add(12)
    return out


def spoken_problems(reply):
    probs = []
    words = len(reply.split())
    if words > MAX_WORDS:
        probs.append(f'{words} words (max {MAX_WORDS})')
    sents = len(SENTENCE_RE.split(reply.strip()))
    if sents > MAX_SENTENCES:
        probs.append(f'{sents} sentences (the server trims to {MAX_SENTENCES})')
    if '\n' in reply:
        probs.append('line break in a spoken reply')
    if MARKDOWN_RE.search(reply) or EMOJI_RE.search(reply):
        probs.append('markdown or emoji in a spoken reply')
    return probs


# ----------------------------------------------------------------------------- conversations.jsonl

def check_conversations(path):
    convs, sir, spoken = [], 0, 0
    lane_count, cat_count, multi = {}, {}, 0
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            where = f'conversations.jsonl:{n}'
            try:
                c = json.loads(line)
            except ValueError as e:
                err(where, f'bad JSON: {e}')
                continue
            where += f' ({c.get("id", "?")})'
            convs.append(c)
            lane, cat, msgs = c.get('lane'), c.get('category'), c.get('messages')
            if lane not in LANES:
                err(where, f'lane {lane!r} not one of {sorted(LANES)}')
            if cat not in CATEGORIES:
                err(where, f'category {cat!r} unknown')
            if not isinstance(msgs, list) or len(msgs) < 2:
                err(where, 'needs a messages list with at least one exchange')
                continue
            lane_count[lane] = lane_count.get(lane, 0) + 1
            cat_count[cat] = cat_count.get(cat, 0) + 1
            exchanges = sum(1 for m in msgs if m.get('role') == 'user' and 'NOTE: LOOKUP RESULT' not in m.get('content', ''))
            if exchanges >= 3:
                multi += 1
            if c.get('multi_turn') and not 3 <= exchanges <= 6:
                err(where, f'multi-turn conversation has {exchanges} exchanges (want 3-6)')
            routed, noted, filed = False, False, False
            facts_cards = set()
            for i, m in enumerate(msgs):
                role, content = m.get('role'), m.get('content', '')
                w = f'{where} msg {i}'
                if role not in ('user', 'assistant'):
                    err(w, f'role {role!r} (user/assistant only)')
                    continue
                if role != ('user' if i % 2 == 0 else 'assistant'):
                    err(w, 'roles must alternate user/assistant, starting with user')
                if role == 'user':
                    check_context(w, content)
                    facts_cards |= {int(x) for x in re.findall(r'#(\d+)', content)}
                    if 'filed GitHub card #' in content:
                        filed = True
                    continue
                # assistant
                prev = msgs[i - 1]['content'] if i else ''
                if OLD_ROUTE_RE.search(content):
                    err(w, 'HOME: / CARD: routing lines are not trained (the server handles those in code)')
                if ROUTE_RE.match(content) or re.search(r'(LOOKUP|ASK_CLAUDE):', content):
                    if not ROUTE_RE.match(content) or '\n' in content.strip():
                        err(w, 'a routing line must be alone in its message, on one line')
                    routed = True
                    nxt = msgs[i + 1]['content'] if i + 1 < len(msgs) else ''
                    if 'NOTE: LOOKUP RESULT from' not in nxt:
                        err(w, 'routing line must be followed by the user turn carrying the NOTE: LOOKUP RESULT')
                    elif nxt.split('NOTE: LOOKUP RESULT', 1)[0] != prev.split('[/context]', 1)[0]:
                        err(w, 'the NOTE turn must repeat the same context block, with the NOTE added')
                    else:
                        noted = True
                    if i == len(msgs) - 1:
                        err(w, 'conversation ends on a routing line')
                    continue
                # a spoken reply
                spoken += 1
                sir += bool(SIR_RE.search(content))
                for p in spoken_problems(content):
                    err(w, p)
                if BANNED.search(content):
                    err(w, f'banned word {BANNED.search(content).group(0)!r}')
                if GAVE_UP.search(content):
                    err(w, f'gives up ({GAVE_UP.search(content).group(0)!r}); answer or route instead')
                grounded = any(k in prev for k in ('LIVE (fresh data', 'FACTS (live', 'NOTE: '))
                if grounded and lane != 'local':
                    extra = numbers(content) - context_numbers(prev)
                    if extra:
                        err(w, f'numbers not in the context/result: {sorted(extra)}')
                for num in re.findall(r'\b(?:card|cards|#)\s*#?(\d{3})\b', content, re.I):
                    if int(num) not in facts_cards:
                        err(w, f'card #{num} is not in any FACTS or filing NOTE')
                if 'filed GitHub card #' in prev:
                    num = re.search(r'filed GitHub card #(\d+)', prev).group(1)
                    if num not in content:
                        err(w, f'card confirmation must cite #{num}')
                if cat == 'small_talk' and re.search(r'\bcards?\s*#?\d|#\d', content):
                    err(w, 'no card numbers in small talk')
            if lane in ('lookup', 'claude') and not (routed and noted):
                err(where, f'{lane} lane needs a routing line followed by the NOTE turn')
            if lane == 'claude' and not any(m['content'].startswith('ASK_CLAUDE:') for m in msgs):
                err(where, 'claude lane needs an ASK_CLAUDE line')
            if lane == 'lookup' and not any(m['content'].startswith('LOOKUP:') for m in msgs):
                err(where, 'lookup lane needs a LOOKUP line')
            if lane == 'card' and not any('filed GitHub card' in m['content'] or 'Filing the card FAILED' in m['content']
                                          for m in msgs if m['role'] == 'user'):
                err(where, 'card lane needs the filing NOTE in the context')
            if lane == 'tasks' and not any('FACTS (live' in m['content'] for m in msgs if m['role'] == 'user'):
                err(where, 'tasks lane needs a FACTS block')
            if lane in ('live', 'home') and not any('LIVE (fresh data' in m['content'] for m in msgs if m['role'] == 'user'):
                err(where, f'{lane} lane needs a LIVE block')
    ratio = sir / max(spoken, 1)
    if not MIN_SIR <= ratio <= MAX_SIR:
        err('conversations.jsonl', f'"sir" in {ratio:.0%} of spoken replies (want {MIN_SIR:.0%}-{MAX_SIR:.0%})')
    if multi < 40:
        err('conversations.jsonl', f'only {multi} conversations with 3+ exchanges (want 40+)')
    ids = [c.get('id') for c in convs]
    if len(ids) != len(set(ids)):
        err('conversations.jsonl', 'duplicate ids')
    return convs, lane_count, cat_count, multi, ratio, spoken


# ----------------------------------------------------------------------------- prompts.jsonl

def check_prompts(path, convs):
    by_id = {c.get('id'): c for c in convs}
    out = []
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            where = f'prompts.jsonl:{n}'
            try:
                p = json.loads(line)
            except ValueError as e:
                err(where, f'bad JSON: {e}')
                continue
            out.append(p)
            for k in ('id', 'category', 'prompt', 'lane'):
                if not p.get(k):
                    err(where, f'missing {k}')
            if p.get('lane') not in LANES:
                err(where, f'lane {p.get("lane")!r}')
            if p.get('category') not in CATEGORIES:
                err(where, f'category {p.get("category")!r}')
            c = by_id.get(p.get('id'))
            if not c:
                err(where, f'no conversation with id {p.get("id")}')
            elif (c['lane'], c['category']) != (p['lane'], p['category']):
                err(where, 'lane/category differ from its conversation')
            elif not any(m['role'] == 'user' and m['content'].endswith('Jake: ' + p['prompt']) for m in c['messages']):
                err(where, 'prompt text not found in its conversation')
    if len({p.get('id') for p in out}) != len(out):
        err('prompts.jsonl', 'duplicate ids')
    if not 180 <= len(out) <= 240:
        err('prompts.jsonl', f'{len(out)} prompts (want about 200)')
    missing = CATEGORIES - {p.get('category') for p in out}
    if missing:
        err('prompts.jsonl', f'categories with no prompts: {sorted(missing)}')
    return out


# ----------------------------------------------------------------------------- modelfile_examples.txt

def check_modelfile(path):
    with open(path, encoding='utf-8') as f:
        lines = [l.rstrip('\n') for l in f if l.strip()]
    pairs, sir = 0, 0
    for i, l in enumerate(lines):
        where = f'modelfile_examples.txt:{i + 1}'
        want = 'MESSAGE user ' if i % 2 == 0 else 'MESSAGE assistant '
        if not l.startswith(want):
            err(where, f'expected a line starting {want!r}')
            continue
        body = l[len(want):]
        if '[context]' in body or 'NOTE:' in body:
            err(where, 'no context blocks or NOTEs in Modelfile examples')
        if want.endswith('assistant '):
            pairs += 1
            if OLD_ROUTE_RE.search(body):
                err(where, 'no HOME: / CARD: lines')
            if not ROUTE_RE.match(body):
                for p in spoken_problems(body):
                    err(where, p)
                sir += bool(SIR_RE.search(body))
            if BANNED.search(body) or GAVE_UP.search(body):
                err(where, 'banned word or giving up')
    if pairs != 16:
        err('modelfile_examples.txt', f'{pairs} examples (want 16)')
    return pairs


def main():
    convs, lanes, cats, multi, ratio, spoken = check_conversations(os.path.join(FOLDER, 'conversations.jsonl'))
    prompts = check_prompts(os.path.join(FOLDER, 'prompts.jsonl'), convs)
    mf = check_modelfile(os.path.join(FOLDER, 'modelfile_examples.txt'))
    pl, pc = {}, {}
    for p in prompts:
        pl[p['lane']] = pl.get(p['lane'], 0) + 1
        pc[p['category']] = pc.get(p['category'], 0) + 1
    print(f'prompts.jsonl: {len(prompts)} prompts')
    print('  by lane:     ' + ', '.join(f'{k} {v}' for k, v in sorted(pl.items(), key=lambda x: -x[1])))
    print('  by category: ' + ', '.join(f'{k} {v}' for k, v in sorted(pc.items(), key=lambda x: -x[1])))
    print(f'conversations.jsonl: {len(convs)} conversations ({multi} with 3+ exchanges), {spoken} spoken replies, '
          f'"sir" in {ratio:.0%}')
    print('  by lane:     ' + ', '.join(f'{k} {v}' for k, v in sorted(lanes.items(), key=lambda x: -x[1])))
    print(f'modelfile_examples.txt: {mf} examples')
    for w in warnings:
        print('WARNING:', w)
    if errors:
        print(f'\nFAILED: {len(errors)} problem(s)')
        for e in errors:
            print('  ' + e)
        sys.exit(1)
    print('\nOK: all checks passed')


if __name__ == '__main__':
    main()
