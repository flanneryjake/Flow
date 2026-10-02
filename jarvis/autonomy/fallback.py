"""Keep the rig useful while Claude is out of usage.

When the Worker log says the account hit its limit ("You've hit your usage limit", "WAITING: usage resets ..."), Claude cards
can't run until the reset. Instead of idling, the rig's local-model lane switches to:
  * research cards, and
  * "next project" drafting cards,
that are machine:rig and labelled model:local (local model only, no Claude). If none are queued, one
"Draft the next project brief" card (and one research card for the oldest open idea) is proposed through
autotask, so they are deduped and tiered. The rig may sleep only when nothing is queued for that lane.

The lane state is a small JSON file (JARVIS_STATE_DIR/fallback.json, default C:\\Jarvis\\state) the Worker reads
on each poll; `allowed(card)` says whether a card may run while the fallback is on.

    import fallback
    r = fallback.check(log_tail, machine='rig')
    # r = {'mode': 'local-fallback'|'normal', 'resets': '8pm (America/New_York)', 'queued': [12, 15],
    #      'filed': [...], 'can_sleep': False, 'say': 'rig stays awake: 2 local-lane cards queued'}
    if r['mode'] == 'local-fallback': run only cards where fallback.allowed(card) is True

CLI: python fallback.py check --log FILE [--machine rig] [--no-file]
     python fallback.py state | clear
JARVIS_AUTO_OFF=1: the lane still switches (it only narrows what runs), but no cards are filed.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import clock  # noqa: E402
from scheduler import kind_of  # noqa: E402

LOCAL_LABEL = 'model:local'
LANE_LABEL = 'lane:fallback'
# Only the Worker's own usage-limit markers and Claude's limit error count. Bare words like "usage limit" or
# "5-hour limit" also show up in card titles and card output (e.g. "Fix usage limit stalls"), and Jake's 10/02
# rule is that only a real Claude usage-limit error pauses Claude cards.
USAGE = re.compile(r"WAITING: usage resets|you(?:'ve| have) (?:hit|reached) your (?:usage |session )?limit|"
                   r"\b(?:usage|session) limit (?:reached|hit)[^\n]{0,40}resets", re.I)
RESETS = re.compile(r'resets?(?: at)?\s+([0-9]{1,2}(?::[0-9]{2})?\s*[ap]\.?m\.?(?:\s*\([^)]*\))?|'
                    r'[0-9]{1,2}:[0-9]{2}(?:\s*\([^)]*\))?)', re.I)
NEXT_PROJECT = re.compile(r'\bnext[- ]project\b', re.I)


def state_path():
    folder = os.environ.get('JARVIS_STATE_DIR') or (r'C:\Jarvis\state' if os.path.isdir(r'C:\Jarvis') else HERE)
    return os.path.join(folder, 'fallback.json')


def load_state():
    try:
        with open(state_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'mode': 'normal'}


def save_state(st):
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    with open(state_path(), 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1)


def clear():
    save_state({'mode': 'normal', 'cleared': clock.iso(clock.now_utc())})


def detect(log_text):
    """(limited, resets_text). Only the last usage line counts: a later normal run means it already reset."""
    lines = (log_text or '').splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if USAGE.search(lines[idx]):
            m = RESETS.search(lines[idx])
            return True, (m.group(1).strip() if m else '')
        if re.search(r'\bRun on \w+: done\b|\bclaimed #?\d+|\bdone #?\d+', lines[idx], re.I):
            return False, ''
    return False, ''


def _labels(i):
    return [l['name'] if isinstance(l, dict) else l for l in i.get('labels', [])]


def allowed(card, st=None):
    """May this card run on the rig right now? Always True in normal mode."""
    st = st or load_state()
    if st.get('mode') != 'local-fallback':
        return True
    names = _labels(card)
    if LOCAL_LABEL not in names or f"machine:{st.get('machine', 'rig')}" not in names:
        return False
    return kind_of(card) == 'research' or bool(NEXT_PROJECT.search(card.get('title') or '')) or LANE_LABEL in names


def lane_queue(issues, machine='rig'):
    """Approved, unclaimed cards the local lane may run, oldest first."""
    st = {'mode': 'local-fallback', 'machine': machine}
    out = [i for i in issues if i.get('state', 'open') == 'open' and 'status:approved' in _labels(i)
           and not any(n.startswith('claimed:') for n in _labels(i)) and allowed(i, st)]
    return sorted(out, key=lambda i: i.get('created_at') or '')


def drafts(issues, now=None):
    """Cards to propose when the lane is empty. Pure."""
    now = now or clock.now_utc()
    body = ('Goal: pick the most promising next project and write a one-page brief.\n\n'
            'Local model only (Ollama on the rig); Claude is out of usage until the reset. Read the cards closed '
            'in the last 7 days and the open ideas, then write: the goal, why now, the first 5 cards, and what it '
            'needs from Jake. Save it as a comment on this card. Draft only: nothing is filed, bought or posted.')
    out = [{'title': f'Draft the next project brief ({now.strftime("%Y-%m-%d")}, local model)', 'body': body}]
    ideas = sorted([i for i in issues if i.get('state', 'open') == 'open' and 'type:idea' in _labels(i)
                    and 'pin' not in _labels(i)], key=lambda i: i.get('created_at') or '')
    if ideas:
        idea = ideas[0]
        out.append({'title': f'Research (local model): {idea["title"]}'[:200],
                    'body': (f'Goal: research idea #{idea["number"]} with the local model and write what it would '
                             'take: options, rough effort, unknowns, and the first card to file.\n\n'
                             'Local model only; reading and writing notes, no purchases, posts or installs.')})
    return out


def check(log_text, machine='rig', file_cards=True, propose=None, ghq=None, now=None):
    """What the Worker calls after each poll or failed run. Updates the lane state file and returns a dict
    (see module doc)."""
    now = now or clock.now_utc()
    limited, resets = detect(log_text)
    if ghq is None:
        import autotask
        ghq = autotask._ghq()
    issues = ghq.paged(ghq.repo_path('/issues?state=open'))
    if not limited:
        if load_state().get('mode') == 'local-fallback':
            clear()
        waiting = [i['number'] for i in issues if 'status:approved' in _labels(i)
                   and ({'machine:any', f'machine:{machine}'} & set(_labels(i)))]
        return {'mode': 'normal', 'resets': '', 'queued': waiting, 'filed': [], 'can_sleep': not waiting,
                'say': f'{machine} may sleep: nothing queued' if not waiting else
                       f'{machine} stays awake: {len(waiting)} card(s) queued'}

    save_state({'mode': 'local-fallback', 'since': clock.iso(now), 'resets': resets, 'machine': machine,
                'lanes': ['research', 'next-project'], 'require_labels': [f'machine:{machine}', LOCAL_LABEL]})
    filed = []
    if not lane_queue(issues, machine) and file_cards and os.environ.get('JARVIS_AUTO_OFF') != '1':
        if propose is None:
            import autotask
            propose = autotask.propose
        for d in drafts(issues, now):
            n, status, why = propose(d['title'], d['body'], machine=machine, source='fallback',
                                     extra_labels=[LOCAL_LABEL, LANE_LABEL])
            filed.append({'title': d['title'], 'number': n, 'status': status, 'why': why})
        issues = ghq.paged(ghq.repo_path('/issues?state=open'))
    queued = [i['number'] for i in lane_queue(issues, machine)]
    say = (f'{machine} may sleep: Claude is out of usage{" until " + resets if resets else ""} and nothing is '
           'queued for the local lane' if not queued else
           f'{machine} stays awake: {len(queued)} local-lane card(s) queued')
    return {'mode': 'local-fallback', 'resets': resets, 'queued': queued, 'filed': filed,
            'can_sleep': not queued, 'say': say}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('check')
    s.add_argument('--log', required=True, help='Worker log file (the tail is read)')
    s.add_argument('--machine', default='rig')
    s.add_argument('--no-file', action='store_true', help="don't propose cards")
    sub.add_parser('state')
    sub.add_parser('clear')
    a = ap.parse_args(argv)
    if a.cmd == 'state':
        print(json.dumps(load_state()))
    elif a.cmd == 'clear':
        clear()
        print('normal')
    else:
        with open(a.log, encoding='utf-8', errors='replace') as f:
            tail = f.read()[-20000:]
        print(json.dumps(check(tail, a.machine, file_cards=not a.no_file), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
