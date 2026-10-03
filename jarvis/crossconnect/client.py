"""Call another machine's node agent: sign one command with this machine's key and send it over the tailnet.

  python client.py rig status
  python client.py junk restart_service --name searxng
  python client.py junk restart_pc
  python client.py 5060 wake --peer rig

The hub (app buttons), Tars (voice), the board wipe and the card lane all use call(). This machine's private key is in
C:\\Jarvis\\secrets\\crossconnect-self.json as {"node": "<this machine>", "pem": "..."} (made by
node_agent.py --make-key); it never leaves the machine and is never printed. Needs the `cryptography` package.
"""
import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from node_agent import PORT, load_private, sign  # noqa: E402

PEERS = {   # node name -> tailnet address; the same names approvals.json uses
    '5060': '100.85.255.99',
    'rig': '100.96.134.64',
    'junk': '100.90.201.22',
    'hal9000': 'hal9000',
}
SELF_KEY = 'C:\\Jarvis\\secrets\\crossconnect-self.json' if os.name == 'nt' else os.path.join(HERE, 'self.json')


def load_self(path=SELF_KEY):
    with open(path, encoding='utf-8') as f:
        me = json.load(f)
    return me['node'], load_private(me['pem'])


def build(caller, key, cmd, args=None):
    """(body, signature) for one command. ts + a fresh nonce make every request single-use."""
    body = json.dumps({'cmd': cmd, 'args': args or {}, 'caller': caller, 'ts': time.time(),
                       'nonce': secrets.token_hex(12)}, separators=(',', ':')).encode()
    return body, sign(key, body)


def call(target, cmd, args=None, me=None, timeout=15, opener=urllib.request.urlopen):
    """Send one command. Returns the agent's JSON reply; a refusal comes back as {"ok": false, "error": ...}."""
    caller, key = me or load_self()
    host = PEERS.get(target, target)
    body, sig = build(caller, key, cmd, args)
    req = urllib.request.Request(f'http://{host}:{PORT}/cmd', data=body, method='POST',
                                 headers={'Content-Type': 'application/json', 'X-Jarvis-Sig': sig})
    try:
        with opener(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except ValueError:
            return {'ok': False, 'node': target, 'cmd': cmd, 'error': f'HTTP {e.code}'}
    except (urllib.error.URLError, OSError) as e:
        return {'ok': False, 'node': target, 'cmd': cmd, 'error': f'unreachable: {type(e).__name__}'}


def health(target, timeout=3, opener=urllib.request.urlopen):
    """True when the peer's agent answers /health. No key needed."""
    try:
        with opener(f'http://{PEERS.get(target, target)}:{PORT}/health', timeout=timeout) as r:
            return bool(json.loads(r.read().decode()).get('ok'))
    except (OSError, ValueError):
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('target', help='5060, rig, junk or hal9000')
    ap.add_argument('cmd')
    ap.add_argument('--name')
    ap.add_argument('--peer')
    a = ap.parse_args(argv)
    args = {k: v for k, v in (('name', a.name), ('peer', a.peer)) if v}
    out = call(a.target, a.cmd, args)
    print(json.dumps(out, indent=2))
    return 0 if out.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
