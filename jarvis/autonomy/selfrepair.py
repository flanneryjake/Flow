"""Turn watchdog / Worker failures into "diagnose: <machine> <symptom>" cards a Claude session picks up.

Input is text: a Machine Health alert ("Remote Control down, restart failed: ...; Worker idle with 3 approved
cards waiting, ..."), watchdog log lines, or a Worker log tail. Each recognised failure becomes one card
proposal, filed through autotask.propose, so it is deduped (an open or recent "diagnose: rig worker idle" is
not filed again) and tiered like any other card. Cards get the labels `self-repair` and `agent:claude` and p1.

Not turned into cards:
  * a lone "Remote Control restarted": the watchdog already fixed it (3+ restarts in one text is a loop and
    does get a card)
  * "Needs Jake: ...": that is waiting on Jake, not broken
  * usage-limit lines: fallback.py handles those
Secrets that slip into a log tail (tokens, Bearer headers) are redacted before anything is filed.

    import selfrepair
    results = selfrepair.run(alert_text, machine='rig')      # files the cards (unless JARVIS_AUTO_OFF=1)
    drafts = selfrepair.proposals(alert_text, machine='rig') # pure, no network

CLI: python selfrepair.py --machine rig [--file] < alert_or_log.txt     (dry run unless --file)
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

LABELS = ['self-repair', 'agent:claude']
MACHINES = ('homebase', 'rig', 'laptop', 'pi')

RESTARTED = re.compile(r'Remote Control(?::)? restarted', re.I)
IGNORE = re.compile(r'Needs Jake|usage limit|WAITING: usage resets|Push sent|restart requested from|'
                    r'Remote Control: UP\b', re.I)
# (symptom, pattern). First match per line wins; order matters (specific before generic).
RULES = [
    ('remote-control down, restart failed', re.compile(r'Remote Control(?::)? (?:was )?down|did NOT come back', re.I)),
    ('remote-control task missing', re.compile(r'Remote Control: task missing', re.I)),
    ('remote-control only running by hand', re.compile(r'Remote Control only running by hand', re.I)),
    ('worker idle with cards waiting', re.compile(r'Worker idle with \d+ approved', re.I)),
    ('cannot read the task queue', re.compile(r'Could not read the Tasks board|Tasks query failed|GITHUB_TASKS_TOKEN is not set', re.I)),
    ('health report not updating', re.compile(r'(?:GitHub|Notion) health update failed', re.I)),
    ('services down: {0}', re.compile(r'\bDown: ([\w ,.-]+)', re.I)),
    ('worker run timing out', re.compile(r'\b(?:timed out|timeout)\b', re.I)),
    ('worker error: {0}', re.compile(r'((?:\w+Error|\w+Exception)\b[^\n]{0,60}|\bERROR\b[^\n]{0,60}|\bfailed\b[^\n]{0,60})')),
]
SECRET = re.compile(r'(gh[pousr]_[A-Za-z0-9]{10,}|github_pat_\w{10,}|secret_\w{10,}|ntn_\w{10,}|sk-[\w-]{10,}|'
                    r'xox[abp]-[\w-]{10,}|AIza[\w-]{20,}|Bearer\s+\S+|(?:token|key|password|pin)\s*[=:]\s*\S+)', re.I)


def redact(text):
    return SECRET.sub('[redacted]', text or '')


def _clean(s):
    """Stable short symptom text: numbers, paths and quotes dropped, so the same failure dedupes."""
    s = re.sub(r'[A-Za-z]:\\\S+|/\S+/\S+', '<path>', s)
    s = re.sub(r'\d+', 'N', s)
    s = re.sub(r'["\'`]', '', s)
    return re.sub(r'\s+', ' ', s).strip(' .;:,')[:60].lower()


def guess_machine(text):
    m = re.search(r'Health: (\w+)|\[(\w+)\]|\bon (\w+)\b', text or '')
    for g in (m.groups() if m else ()):
        if g and g.lower() in MACHINES:
            return g.lower()
    return None


def parse(text):
    """Return [(symptom, [evidence lines])] in first-seen order. Pure."""
    found = {}
    restarts = []
    for raw in re.split(r'\n|;\s+', text or ''):
        line = raw.strip()
        if not line:
            continue
        if RESTARTED.search(line):
            restarts.append(line)
            continue
        if IGNORE.search(line):
            continue
        for symptom, rx in RULES:
            m = rx.search(line)
            if m:
                if '{0}' in symptom:
                    symptom = symptom.format(_clean(m.group(1)))
                found.setdefault(symptom, []).append(redact(line)[:300])
                break
    if len(restarts) >= 3:
        found.setdefault('remote-control restart loop', []).extend(redact(r)[:300] for r in restarts[:5])
    return list(found.items())


def proposals(text, machine=None):
    """Card drafts: [{'title', 'body', 'machine', 'priority', 'labels'}]. Pure."""
    machine = machine or guess_machine(text) or 'unknown'
    out = []
    for symptom, evidence in parse(text):
        body = '\n'.join([
            f'Goal: find why {machine} shows "{symptom}" and fix it.',
            '',
            f'Filed by selfrepair.py from the {machine} watchdog / Worker output. Evidence:',
            '',
            '```',
            *[e.replace('```', "'''") for e in evidence[:15]],
            '```',
            '',
            f'Steps: read C:\\Jarvis\\logs on {machine} (watchdog.log, worker.log, remote-control.log), find the '
            'cause, and fix it if the fix is free or free_logged under the guardrail policy. If it needs an '
            'install, a setting, a token or a hand on the machine, stop with NEEDS PIN or a Needs Jake question '
            'that says exactly what to do.',
        ])
        out.append({'title': f'diagnose: {machine} {symptom}', 'body': body, 'machine': 'any', 'priority': 'p1',
                    'labels': list(LABELS)})
    return out


def run(text, machine=None, propose=None):
    """File each proposal through autotask.propose (deduped). Returns [{'title', 'number', 'status', 'why'}].
    With JARVIS_AUTO_OFF=1 nothing is filed and each result has status 'off'."""
    drafts = proposals(text, machine)
    if os.environ.get('JARVIS_AUTO_OFF') == '1':
        return [{'title': d['title'], 'number': None, 'status': 'off', 'why': 'JARVIS_AUTO_OFF=1'} for d in drafts]
    if propose is None:
        import autotask
        propose = autotask.propose
    out = []
    for d in drafts:
        n, status, why = propose(d['title'], d['body'], machine=d['machine'], priority=d['priority'],
                                 source='selfrepair', extra_labels=d['labels'])
        out.append({'title': d['title'], 'number': n, 'status': status, 'why': why})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--machine')
    ap.add_argument('--text-file', help='read this file instead of stdin')
    ap.add_argument('--file', action='store_true', help='file the cards (default: print drafts only)')
    a = ap.parse_args(argv)
    if a.text_file:
        with open(a.text_file, encoding='utf-8', errors='replace') as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    if a.file:
        print(json.dumps(run(text, a.machine), indent=1))
    else:
        print(json.dumps([{'title': d['title'], 'labels': d['labels']} for d in proposals(text, a.machine)], indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
