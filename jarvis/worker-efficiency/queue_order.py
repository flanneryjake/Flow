"""E10 (Jake's 10/03 email): work the queue by priority, not by order received.

On 10/03 the board had 408 approved cards: 2 p1, 229 p2, 4 p3, and 173 with no priority label. Both Workers sorted
by p0 > p1 > p2 > everything else, so a focus-project card with no label waited behind every p2 idea, and p3 counted
the same as unlabelled. The rig also ignored `now` and `resume`.

key(c, parked) orders a Worker's approved cards like this (oldest first inside each step):
  1. `now` (Jake typed it just now)
  2. the card parked by a usage reset, then `resume` cards (picking up where they left off)
  3. p0, then p1, then p2, then cards with no priority, then p3
  4. inside a step: a focus project (project:homebase/handsfree/brains/income) before project:admin before no
     project, then the lower phase:N first (phase:later last)

Cheap text cards already go to the local model first on the rig (local_lane.py), so no separate batching is needed.
Pure function, stdlib only. Any error falls back to the old p0 > p1 > p2 order in agent.py.
"""
import re

FOCUS = ('homebase', 'handsfree', 'brains', 'income')
PRIO = {'P0': 0, 'P1': 1, 'P2': 2, '': 3, 'P3': 4}
PHASE_RE = re.compile(r'^phase:(\d+)$')


def labels(c):
    return set(c.get('labels') or [])


def priority(c):
    names = labels(c)
    for p in ('p0', 'p1', 'p2', 'p3'):
        if p in names or (c.get('priority') or '').upper() == p.upper():
            return p.upper()
    return ''


def project_rank(c):
    proj = c.get('project') or next((n.split(':', 1)[1] for n in labels(c) if n.startswith('project:')), '')
    return 0 if proj in FOCUS else 1 if proj else 2


def phase_rank(c):
    for n in labels(c):
        m = PHASE_RE.match(n)
        if m:
            return int(m.group(1))
    return 99 if 'phase:later' in labels(c) else 50


def key(c, parked=None):
    names = labels(c)
    return (not (c.get('now') or 'now' in names),
            c.get('id') != parked,
            not (c.get('resume') or 'resume' in names),
            PRIO[priority(c)],
            project_rank(c),
            phase_rank(c))
