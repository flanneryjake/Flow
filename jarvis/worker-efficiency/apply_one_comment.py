"""E8 (Jake's 10/03 email): one comment per run, edited in place.

Before: every run wrote a claim comment, (5060) a separate progress comment, then a separate run-record comment,
so a card collected 2-3 Worker comments per run before any needs-Jake or triage notes.

After: the claim comment the Worker won is reused. On the 5060 the live progress is written into it, and at the end
of the run it becomes the run record (same markers and text as before: <!-- jarvis:run -->, runmeta, summary, log
tail). One new comment per run instead of two or three; editing a comment doesn't count toward GitHub's
content-creation limit that tripped on 10/02. If the edit fails (comment deleted by hand, Worker restarted mid-run,
rate limit), the run record is posted as a new comment exactly as before.

The run record keeps the claim's position and time on the card; its runmeta still carries the real start and end
times (repeatguard.py reads `ended`). A lost claim race still deletes the loser's claim, unchanged.

    python apply_one_comment.py --ghq C:\\Jarvis\\ghq [--check | --revert]

Every anchor must match exactly once, or nothing is written. Backup: ghq.py.bak-<stamp>-e8. Keeps the file's line
endings. Restart the Worker afterwards (and the hub on the 5060, which imports ghq too). Off switch:
JARVIS_ONE_COMMENT=off.
"""
import argparse
import glob
import os
import shutil
import sys
import time

TAG = 'one-comment 10/03'

HELPERS = '''# ---- %s (E8): one comment per run ----
RUN_COMMENT = {}   # card number -> id of the claim comment this Worker won; the run record is written into it


def _post_run(number, body):
    """Write the run record into the run's claim comment (E8), or post it as a new comment if that can't be done."""
    cid = RUN_COMMENT.pop(number, None)
    if cid and os.environ.get('JARVIS_ONE_COMMENT', 'on') != 'off':
        try:
            api('PATCH', repo_path(f'/issues/comments/{cid}'), {'body': body})
            return
        except Exception as e:  # noqa: BLE001 - deleted by hand, rate limit: post it the old way
            print(f'one-comment #{number}: {e}; posting the run record as a new comment', file=sys.stderr)
    comment(number, body, dedupe=False)


def claim(number, machine):''' % TAG

COMMON = [
    ('\ndef claim(number, machine):', '\n' + HELPERS),
    ("    comment(number, '\\n'.join(text), dedupe=False)\n",
     "    _post_run(number, '\\n'.join(text))   # E8: edits the run's claim comment in place\n"),
]
RIG = [("    set_status(number, 'working', extra_add=[f'claimed:{machine}'])\n",
        "    RUN_COMMENT[number] = mine['id']   # E8\n"
        "    set_status(number, 'working', extra_add=[f'claimed:{machine}'])\n")]
H5060 = [("    set_status(number, 'working', extra_add=[f'claimed:{machine}'], extra_remove=['resume'])\n",
          "    RUN_COMMENT[number] = mine['id']   # E8\n"
          "    set_status(number, 'working', extra_add=[f'claimed:{machine}'], extra_remove=['resume'])\n"),
         ("    body = f'{PROGRESS_MARK}\\n**Progress on {machine}** (updated {now_iso()})\\n\\n{text.strip()[-3000:]}'\n",
          "    body = f'{PROGRESS_MARK}\\n**Progress on {machine}** (updated {now_iso()})\\n\\n{text.strip()[-3000:]}'\n"
          "    if not comment_id and os.environ.get('JARVIS_ONE_COMMENT', 'on') != 'off':\n"
          "        comment_id = RUN_COMMENT.get(number)   # E8: progress lives in the run's claim comment\n")]


def edits_for(s):
    return COMMON + (H5060 if "extra_remove=['resume'])\n" in s else RIG)


def plan(path):
    raw = open(path, 'rb').read()
    s = raw.decode('utf-8').replace('\r\n', '\n')
    if TAG in s:
        return raw, None, 'already installed'
    out = s
    for anchor, repl in edits_for(s):
        n = out.count(anchor)
        if n != 1:
            return raw, None, f'anchor found {n} times, expected 1:\n    {anchor.strip()[:100]}'
        out = out.replace(anchor, repl, 1)
    if b'\r\n' in raw:
        out = out.replace('\n', '\r\n')
    return raw, out.encode('utf-8'), None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--ghq', required=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    path = os.path.join(a.ghq, 'ghq.py')
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-e8'))
        if baks:
            shutil.copy2(baks[-1], path)
            print(f'ghq.py: restored {os.path.basename(baks[-1])}')
        return 0
    raw, new, why = plan(path)
    print('ghq.py: ' + (why or 'ready'))
    if why:
        return 0 if why == 'already installed' else 1
    if a.check:
        return 0
    stamp = time.strftime('%Y%m%d-%H%M')
    shutil.copy2(path, f'{path}.bak-{stamp}-e8')
    with open(path, 'wb') as f:
        f.write(new)
    print(f'Installed (backup ghq.py.bak-{stamp}-e8). Restart the Jarvis Worker on this PC, and the hub on the 5060.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
