"""Self-heal (cross-connect phase 5a): the controller checks its peers and restarts a stuck service, gracefully.

check_once() is called every few minutes by the 5060 hub's background loop (and later by the rig as backup):
  - asks each peer for `status` (a peer that doesn't answer is skipped: a sleeping rig is normal, not "down"),
  - a service counts as stuck only after it was down on DOWN_ROUNDS checks in a row,
  - at most ONE restart per round across all peers, and only of a service the peer lists as restartable,
  - the peer's own approvals.json and rate limits still decide (30 min apart, a few a day); a refusal is reported,
    not retried,
  - every restart or refusal goes to Jake's phone through notify(text).
It never restarts a whole PC; that stays with the board wipe and the app's PIN button.
"""
import json
import os

DOWN_ROUNDS = 2
PEERS = ('rig', 'junk')   # from the 5060; the rig as backup would use ('5060', 'junk')


def load_state(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f)
    os.replace(tmp, path)


def check_once(call, state, notify, peers=PEERS, log=print):
    """One round. state: {"<node>:<service>": consecutive down count}, updated in place. Returns what was done."""
    stuck = []
    for node in peers:
        out = call(node, 'status', {})
        if not out.get('ok'):
            continue   # asleep, off the tailnet, or paused-and-refusing: leave its counts alone
        restartable = set(out.get('restartable') or [])
        for svc, st in (out.get('services') or {}).items():
            key = f'{node}:{svc}'
            if st == 'down':
                state[key] = state.get(key, 0) + 1
                if state[key] >= DOWN_ROUNDS and svc in restartable and not out.get('paused'):
                    stuck.append((node, svc))
            else:
                state.pop(key, None)
    if not stuck:
        return None
    node, svc = stuck[0]   # one restart per round
    res = call(node, 'restart_service', {'name': svc})
    state[f'{node}:{svc}'] = 0
    if res.get('ok'):
        text = f'Self-heal: {svc} on the {node} was down for {DOWN_ROUNDS} checks, so I restarted it.'
    else:
        text = f'Self-heal: {svc} on the {node} is down, but the restart was refused: {res.get("error", "no reason")}.'
    log(text)
    notify(text)
    return text
