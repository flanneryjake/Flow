r"""Test followup-park on a PC's autotask.py (no network: ghq is faked).
    python test_followup_park.py [autonomy dir, default C:\Jarvis\autonomy]
Exit 0 = all checks pass."""
import importlib
import os
import sys
import tempfile
import types

d = sys.argv[1] if len(sys.argv) > 1 else r'C:\Jarvis\autonomy'
sys.path.insert(0, d)
os.environ['JARVIS_FOLLOWUP_LEDGER'] = os.path.join(tempfile.mkdtemp(), 'followups-parked.jsonl')
autotask = importlib.import_module('autotask')

cards, comments, snoozes, n = {}, [], [], [3000]
parents = {10: ['status:working', 'project:homebase'], 11: ['status:working', 'pin', 'project:homebase']}


def new_card(title, body, machine='any', priority=None, status='staged', card_type='task', pin=False, spawned_from=None,
             extra_labels=()):
    n[0] += 1
    cards[n[0]] = (status, list(extra_labels))
    return n[0]


g = types.SimpleNamespace(
    paged=lambda p: [], repo_path=lambda s='': '/repos/x' + s, now_iso=lambda: '2026-10-03T19:55:00+00:00',
    api=lambda m, p, b=None: {'labels': [{'name': x} for x in parents.get(int(p.rsplit('/', 1)[1]), [])],
                              'created_at': '2026-10-03T19:55:00Z'},
    label_names=lambda i: [l['name'] for l in i['labels']], new_card=new_card,
    comment=lambda num, t, **k: comments.append((num, t)),
    snooze_until=lambda num, k, v, m, r='', p=None: snoozes.append((num, k, v)) or v, wake=lambda *a: None,
    park_for_triage=lambda *a, **k: None, LAST_NEW_CARD_EXILED=False, GitHubError=Exception)
autotask._ghq = lambda: g
autotask.auto_depth = lambda *a: 0
autotask.ensure_label = lambda *a: None


def run(cap_today, parent, title):
    autotask.auto_count_today = lambda ghq: cap_today
    num, st, why = autotask.propose(title, '', machine='rig', spawned_from=parent, source='Worker on test')
    return st, cards[num][1], comments[-1][1]


fails = 0
for label, args, want_status, want_label in [
        ('follow-up past the cap -> parked', (999, 10, 'Add a test for the new_card None path'), 'snoozed', 'preapproved'),
        ('follow-up of a PIN parent -> Jake', (999, 11, 'Check the order page layout'), 'staged', None),
        ('follow-up under the cap -> approved', (0, 10, 'Close or update card #1082'), 'approved', 'auto'),
        ('not a follow-up past the cap -> Jake', (999, None, 'Research Gumroad fees'), 'staged', None)]:
    st, labels, text = run(*args)
    ok = st == want_status and (want_label is None or want_label in labels)
    fails += not ok
    print(('PASS' if ok else 'FAIL'), label, '|', st, labels, '|', text[:60])
ok = bool(snoozes) and snoozes[0][1] == 'time' and 'T06:00' in snoozes[0][2]
fails += not ok
print(('PASS' if ok else 'FAIL'), 'parked follow-up snoozes until 06:00 |', snoozes[:1])
ok = os.path.exists(os.environ['JARVIS_FOLLOWUP_LEDGER'])
fails += not ok
print(('PASS' if ok else 'FAIL'), 'ledger line written')
sys.exit(1 if fails else 0)
