"""Jake's house rules for the local models (Jarvis on the rig, Tars on the 5060), plus the Claude skills installer.

The same know-how lives in two shapes:
- jarvis/skills/<name>/SKILL.md   Claude Code skills. PC sessions (Remote Control, Worker `claude -p`) load them from
                                  %USERPROFILE%\\.claude\\skills once `install-skills` copies them there.
- jarvis/knowledge/cards/*.md     short cards for the small local models. Each has `triggers` (a regex) and a one-line
                                  `summary`. Tars pulls the matching cards into its context per question; the rig's
                                  `jarvis` model gets every summary baked into its system prompt.

Cards hold no secrets, no dollar amounts and no account numbers (jake-facts.md rules).

CLI:
  python knowledge.py match "what's on my to-do"      cards Tars would add for that question
  python knowledge.py summary                          the always-on block (one line per card)
  python knowledge.py modelfile --base jarvis --out Modelfile.jarvis-skills
                                                       FROM <base> + its current system prompt + the summary block
                                                       (an older block is replaced, so re-running is safe)
  python knowledge.py install-skills [--dest DIR]      copy jarvis/skills/* into ~/.claude/skills (backs up changed ones)
"""
import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.join(HERE, 'cards')
SKILLS = os.path.join(os.path.dirname(HERE), 'skills')
START, END = '### JARVIS HOUSE RULES START', '### JARVIS HOUSE RULES END'


def parse(text):
    """Front matter (name, triggers, summary) + body of one card."""
    meta, body = {}, text
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)
    meta['body'] = body.strip()
    meta['re'] = re.compile(meta['triggers'], re.I) if meta.get('triggers') else None
    return meta


def load(folder=CARDS):
    cards = []
    for fn in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if fn.endswith('.md'):
            with open(os.path.join(folder, fn), encoding='utf-8') as f:
                c = parse(f.read())
            c.setdefault('name', fn[:-3])
            cards.append(c)
    return cards


def select(text, cards=None, max_cards=2, max_chars=1600):
    """Cards whose triggers match the question, most hits first, capped so a small model's context stays small."""
    cards = load() if cards is None else cards
    hits = []
    for i, c in enumerate(cards):
        n = len(c['re'].findall(text or '')) if c['re'] else 0
        if n:
            hits.append((-n, i, c))
    out, size = [], 0
    for _, _, c in sorted(hits)[:max_cards]:
        if size + len(c['body']) > max_chars and out:
            break
        out.append(c)
        size += len(c['body'])
    return out


def context_for(text, cards=None):
    """The block Tars adds to its [context]; '' when no card applies."""
    picked = select(text, cards)
    if not picked:
        return ''
    return ("HOUSE RULES (Jake's standing rules for this topic; follow them, don't recite them):\n" +
            '\n'.join(c['body'] for c in picked))


def summary(cards=None):
    cards = load() if cards is None else cards
    lines = [f"- {c['summary']}" for c in cards if c.get('summary')]
    return '\n'.join([START, "Jake's house rules (short form; Claude has the details):"] + lines + [END])


def merge_system(current, block):
    """Put the house-rules block into a system prompt, replacing an older copy."""
    current = (current or '').rstrip()
    pat = re.compile(re.escape(START) + r'.*?' + re.escape(END), re.S)
    if pat.search(current):
        return pat.sub(lambda _: block, current)
    return (current + '\n\n' + block) if current else block


def modelfile(base, current_system, block):
    system = merge_system(current_system, block).replace('"""', '"')
    return f'FROM {base}\nSYSTEM """{system}"""\n'


def ollama_system(base):
    r = subprocess.run(['ollama', 'show', base, '--system'], capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        sys.exit(f'ollama show {base} --system failed: {r.stderr.strip()}')
    return r.stdout


def install_skills(dest, src=SKILLS):
    """Copy every jarvis/skills/<name>/SKILL.md to dest/<name>/SKILL.md. A changed existing file is kept as .bak-<date>."""
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M')
    done = []
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name, 'SKILL.md')
        if not os.path.isfile(s):
            continue
        d = os.path.join(dest, name, 'SKILL.md')
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.isfile(d):
            with open(s, 'rb') as a, open(d, 'rb') as b:
                if a.read() == b.read():
                    done.append((name, 'same'))
                    continue
            shutil.copy2(d, d + '.bak-' + stamp)
            done.append((name, 'updated'))
        else:
            done.append((name, 'added'))
        shutil.copy2(s, d)
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('match')
    m.add_argument('text')
    sub.add_parser('summary')
    mf = sub.add_parser('modelfile')
    mf.add_argument('--base', required=True)
    mf.add_argument('--out', required=True)
    ins = sub.add_parser('install-skills')
    ins.add_argument('--dest', default=os.path.join(os.path.expanduser('~'), '.claude', 'skills'))
    a = ap.parse_args(argv)
    if a.cmd == 'match':
        print(context_for(a.text) or '(no card matches)')
    elif a.cmd == 'summary':
        print(summary())
    elif a.cmd == 'modelfile':
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(modelfile(a.base, ollama_system(a.base), summary()))
        print(f'wrote {a.out}; build with: ollama create {a.base} -f {a.out}  (back up first: ollama cp {a.base} {a.base}-bak-<date>)')
    elif a.cmd == 'install-skills':
        for name, what in install_skills(a.dest):
            print(f'{what:8} {name}')
        print(f'skills folder: {a.dest}')


if __name__ == '__main__':
    main()
