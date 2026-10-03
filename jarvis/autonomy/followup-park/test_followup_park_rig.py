r"""Test followup-park on the RIG's autotask.py (no network: ghq, classify and dedupe are faked).
    python test_followup_park_rig.py [autonomy dir, default C:\Jarvis\autonomy]
Exit 0 = all checks pass."""
import importlib
import os
import sys
import tempfile
import types

d = sys.argv[1] if len(sys.argv) > 1 else r'C:\Jarvis\autonomy'
sys.path.insert(0, d)
os.environ['JARVIS_FOLLOWUP_LEDGER'] = os.path.join(tempfile.mkdtemp(), 'followups-parked.jsonl')
os.environ.pop('JARVIS_AUTO_OFF', None)
if 'classify' not in sys.modules:   # the rig's classify.py may be absent in a test copy; tier by title here
    sys.modules['classify'] = types.SimpleNamespace(
        classify=lambda t, b='', l=(): ('pin', ['spend']) if 'buy' in t.lower() else ('free', []))
autotask = importlib.import_module('autotask')
autotask.classify = lambda t, b='', l=(): ('pin', ['spend']) if 'buy' in t.lower() else ('free', [])
autotask.dedupe = types.SimpleNamespace(gather=lambda g, t: ([], {}, []), check=lambda *a, **k: None,
                                        remember=lambda *a: None, log_reject=lambda *a, **k: None)

cards, comments, snoozes, posts, n = {}, [], [], [], [3000]
parents = {10: ['status:working', 'project:homebase'], 11: ['status:working', 'pin', 'project:homebase']}
fail_snooze = [False]


def new_card(title, body, machine='any', priority=None, status='staged', card_type='task', pin=False, spawned_from=None,
             extra_labels=()):
    n[0] += 1
    cards[n[0]] = (status, list(extra_labels))
    return n[0]


def api(m, p, b=None):
    if m == 'POST':
        posts.append((p, b))
        return {}
    return {'labels': [{'name': x} for x in parents.get(int(p.rsplit('/', 1)[1]), [])]}


def snooze_until(num, k, v, m, r='', p=None):
    if fail_snooze[0]:
        raise ValueError('boom')
    snoozes.append((num, k, v))
    return v


g = types.SimpleNamespace(
    paged=lambda p: [], repo_path=lambda s='': '/repos/x' + s, now_iso=lambda: '2026-10-03T19:55:00+00:00', api=api,
    label_names=lambda i: [l['name'] for l in i['labels']], new_card=new_card,
    comment=lambda num, t, **k: comments.append((num, t)), snooze_until=snooze_until, wake=lambda *a: None,
    park_for_triage=lambda *a, **k: None, FOCUS_EXILED=(), GitHubError=Exception)
autotask._ghq = lambda: g
autotask.auto_depth = lambda *a: 0
autotask.ensure_label = lambda *a: None


def run(cap_today, parent, title):
    autotask.auto_count_today = lambda ghq: cap_today
    num, st, why = autotask.propose(title, '', machine='rig', spawned_from=parent, source='the rig Worker')
    return num, st, cards[num][1], why


fails = 0


def check(ok, label, detail=''):
    global fails
    fails += not ok
    print(('PASS' if ok else 'FAIL'), label, '|', detail)


num, st, labels, why = run(999, 10, 'Add a test for the new_card None path')
check(st == 'snoozed' and 'preapproved' in labels and 'sched:deferred' not in labels,
      'follow-up past the cap -> parked, preapproved, not deferred', (st, labels))
check(snoozes and snoozes[-1][0] == num and snoozes[-1][1] == 'time' and 'T06:00' in snoozes[-1][2],
      'parked follow-up snoozes until 06:00', snoozes[-1:])
check(not any(c[0] == num for c in comments), 'no "Filed for Jake" comment on a parked card')
num, st, labels, why = run(999, 11, 'Check the order page layout')
check(st == 'staged' and 'sched:deferred' in labels, 'follow-up of a PIN parent -> Jake, deferred as before', (st, labels))
num, st, labels, why = run(0, 10, 'Close or update card #1082')
check(st == 'approved' and 'auto' in labels, 'follow-up under the cap -> approved', (st, labels))
num, st, labels, why = run(999, None, 'Research Gumroad fees')
check(st == 'staged' and 'sched:deferred' in labels, 'not a follow-up past the cap -> Jake, deferred as before', (st, labels))
num, st, labels, why = run(999, 10, 'Buy a spare SSD')
check(st == 'staged' and 'preapproved' not in labels, 'PIN-tier follow-up -> Jake', (st, labels))
fail_snooze[0] = True
num, st, labels, why = run(999, 10, 'Tidy the rig scratch folder')
check(st == 'staged' and any(p[0].endswith(f'/issues/{num}/labels') and p[1] == {'labels': ['sched:deferred']} for p in posts)
      and 'park failed' in why, 'snooze failure falls back to staged + sched:deferred', why[-60:])
fail_snooze[0] = False
os.environ['JARVIS_FOLLOWUP_PARK'] = 'off'
num, st, labels, why = run(999, 10, 'Tidy the 5060 scratch folder')
check(st == 'staged' and 'sched:deferred' in labels, 'off switch -> old behaviour', (st, labels))
os.environ.pop('JARVIS_FOLLOWUP_PARK')
check(os.path.exists(os.environ['JARVIS_FOLLOWUP_LEDGER']), 'ledger line written')
sys.exit(1 if fails else 0)
