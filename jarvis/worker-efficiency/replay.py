"""Replay real card histories: for every claim a Worker made, would the repeat guard have refused it?
    python3 replay.py fixtures/card-690.json ..."""
import json, sys
from repeatguard import unchanged_since_ask


def replay(comments):
    out = []
    for i, c in enumerate(comments):
        if (c.get('body') or '').startswith('<!-- jarvis:claim'):
            why = unchanged_since_ask({'labels': []}, comments[:i])
            out.append((c['created_at'], why))
    return out


if __name__ == '__main__':
    for f in sys.argv[1:]:
        r = replay(json.load(open(f)))
        blocked = [t for t, w in r if w]
        print(f'{f}: {len(r)} claims, {len(blocked)} would have been refused')
