"""Jarvis node agent: the same small helper on every machine, so the machines can check and fix each other.

Cross-connect proposal (Jake 2026-10-03): https://claude.ai/code/artifact/b19b451c-e442-408e-b6d4-77a8cbb1ea20

  GET  /health   -> {"ok": true, "node": "<name>"}             no auth, no details (peer up-checks)
  POST /cmd      -> run ONE command from the fixed list below  signed, allowed by approvals.json

Commands (anything else is refused):
  status                       services up/down, RAM, disk, uptime
  restart_service {"name"}     restart a service named in this machine's config (scheduled task or Windows service)
  restart_pc                   graceful restart: shutdown /r /t 60, never /f
  wake {"peer"}                wake a peer through its configured wake URL or Wake-on-LAN
  run_fix {"name"}             run a fix script listed in config, only when its sha256 still matches
  logs {"name"}                last 200 lines of a log file listed in config

A request is JSON {"cmd", "args", "caller", "ts", "nonce"} with header X-Jarvis-Sig = base64 Ed25519 signature of the
raw body by the CALLER's private key. Each machine makes its own key pair once (--make-key); the private half stays in
C:\\Jarvis\\secrets\\crossconnect-self.json and never leaves that machine. Only public keys are shared, in
pubkeys\\<node>.pub in Flow. Requests older than 60 s, or a nonce seen before, are refused.

approvals.json (in Flow, Jake approves it once) says which caller may run which command on which target, how often.
Default is deny. Every call, allowed or refused, goes to the audit log. Needs the Python `cryptography` package.
"""
import argparse
import base64
import collections
import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8799
MAX_BODY = 4096
SKEW_S = 60
COMMANDS = ('status', 'restart_service', 'restart_pc', 'wake', 'run_fix', 'logs')
SAFE_SHUTDOWN = ['shutdown', '/r', '/t', '60', '/c', 'Jarvis node agent: graceful restart requested by a peer']
WINDOWS = os.name == 'nt'


class Refused(Exception):
    """A command that was understood and turned down. The message is safe to send back."""


def now():
    return dt.datetime.now().astimezone()


def load_json(path, default=None):
    try:
        with open(path, encoding='utf-8-sig') as f:    # -sig: PowerShell 5.1 writes a BOM
            return json.load(f)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def make_key():
    """(private PEM text, public key base64). The PEM is a secret; the public key is safe to publish."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return pem, public_of(k)


def public_of(private_key):
    from cryptography.hazmat.primitives import serialization
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def load_private(pem):
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(pem.encode(), password=None)


def sign(private_key, body):
    return base64.b64encode(private_key.sign(body)).decode()


def signature_ok(public_b64, body, sig):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64, validate=True)).verify(
            base64.b64decode(sig, validate=True), body)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def load_public_keys(folder):
    """{node: public key base64} from pubkeys\\<node>.pub."""
    keys = {}
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.endswith('.pub'):
            with open(os.path.join(folder, name), encoding='utf-8') as f:
                keys[name[:-4]] = f.read().strip()
    return keys


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- approvals and limits

def _match(rule_value, value):
    return rule_value == '*' or rule_value == value or (isinstance(rule_value, list) and value in rule_value)


def find_rule(approvals, caller, target, cmd):
    """The first approvals rule that lets `caller` run `cmd` on `target`, or None (default deny)."""
    for rule in approvals.get('rules', []):
        if _match(rule.get('caller'), caller) and _match(rule.get('target'), target) and _match(rule.get('cmd'), cmd):
            return rule
    return None


class Limits:
    """Per-command rate limits from approvals.json, kept in a small state file so they survive a restart.
    Only runs that went through count; a refused or failed command never uses up the limit."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()

    def check(self, key, limit, at=None):
        """limit = {"min_gap_min": 30, "per_day": 4}. Raises Refused when a limit is hit."""
        at = at or time.time()
        runs = [t for t in load_json(self.path, {}).get(key, []) if at - t < 86400]
        gap = (limit or {}).get('min_gap_min')
        if gap and runs and at - runs[-1] < gap * 60:
            wait = int((gap * 60 - (at - runs[-1])) // 60) + 1
            raise Refused(f'{key} ran {int((at - runs[-1]) // 60)} min ago; the limit is once per {gap} min '
                          f'(try again in {wait} min)')
        per_day = (limit or {}).get('per_day')
        if per_day and len(runs) >= per_day:
            raise Refused(f'{key} already ran {len(runs)} times in 24 h; the limit is {per_day}, so Jake decides')

    def record(self, key, at=None):
        at = at or time.time()
        state = load_json(self.path, {})
        state[key] = [t for t in state.get(key, []) if at - t < 86400] + [at]
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(state, f)
        os.replace(tmp, self.path)

    def check_and_record(self, key, limit, at=None):
        with self.lock:
            self.check(key, limit, at)
            self.record(key, at)


# ----------------------------------------------------------------------------- the commands

def _run(argv, timeout=60):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()[-400:]


def _port_open(host, port, timeout=2):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _memory():
    if WINDOWS:
        import ctypes

        class MEMSTAT(ctypes.Structure):
            _fields_ = [('len', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong),
                        ('avail', ctypes.c_ulonglong), ('pf_t', ctypes.c_ulonglong), ('pf_a', ctypes.c_ulonglong),
                        ('v_t', ctypes.c_ulonglong), ('v_a', ctypes.c_ulonglong), ('x', ctypes.c_ulonglong)]
        m = MEMSTAT()
        m.len = ctypes.sizeof(MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {'total_gb': round(m.total / 2**30, 1), 'free_gb': round(m.avail / 2**30, 1)}
    try:
        info = dict(line.split(':', 1) for line in open('/proc/meminfo'))
        kb = lambda k: int(info[k].split()[0])  # noqa: E731
        return {'total_gb': round(kb('MemTotal') / 2**20, 1), 'free_gb': round(kb('MemAvailable') / 2**20, 1)}
    except (OSError, KeyError, ValueError):
        return {}


def _uptime_h():
    if WINDOWS:
        import ctypes
        return round(ctypes.windll.kernel32.GetTickCount64() / 3.6e6, 1)
    try:
        return round(float(open('/proc/uptime').read().split()[0]) / 3600, 1)
    except (OSError, ValueError):
        return None


class Node:
    def __init__(self, config, approvals, peer_keys, state_dir, runner=None, wol=None, opener=None):
        self.cfg = config
        self.name = config['node']
        self.approvals = approvals
        self.peer_keys = peer_keys       # {caller: public key base64}
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.limits = Limits(os.path.join(state_dir, 'limits.json'))
        self.audit_path = os.path.join(state_dir, 'audit.log')
        self.nonces = collections.OrderedDict()
        self.nonce_lock = threading.Lock()
        self.run = runner or _run        # injectable so tests never restart anything
        self.wol = wol or send_wol
        self.open = opener or urllib.request.urlopen

    # -- request checks
    def verify(self, body, sig):
        """(request, rule). Raises Refused with a reason that never echoes a key."""
        try:
            req = json.loads(body)
        except ValueError:
            raise Refused('body is not JSON')
        caller, cmd = req.get('caller'), req.get('cmd')
        key = self.peer_keys.get(caller)
        if not key or not sig or not signature_ok(key, body, sig):
            raise Refused('bad signature or unknown caller')
        if abs(time.time() - float(req.get('ts') or 0)) > SKEW_S:
            raise Refused('request expired (older than 60 s or clock off)')
        nonce = str(req.get('nonce') or '')
        with self.nonce_lock:
            if not nonce or nonce in self.nonces:
                raise Refused('repeated request')
            self.nonces[nonce] = time.time()
            while len(self.nonces) > 2000 or (self.nonces and time.time() - next(iter(self.nonces.values())) > 300):
                self.nonces.popitem(last=False)
        if cmd not in COMMANDS:
            raise Refused(f'unknown command {cmd!r}')
        if cmd in (self.cfg.get('disabled') or []):
            raise Refused(f'{cmd} is not switched on for {self.name} yet')
        rule = find_rule(self.approvals, caller, self.name, cmd)
        if not rule:
            raise Refused(f'approvals.json does not let {caller} run {cmd} on {self.name}')
        if self._paused() and cmd != 'status':
            raise Refused(f'{self.name} is paused in fleet control; only status is answered')
        return req, rule

    def _paused(self):
        """Paused by fleet control (its C:\\Jarvis\\fleet\\paused.flag) or by an off.txt beside this agent."""
        flags = (os.path.join(HERE, 'off.txt'), self.cfg.get('pause_flag', 'C:\\Jarvis\\fleet\\paused.flag'))
        return any(p and os.path.exists(p) for p in flags)

    def audit(self, caller, cmd, args, outcome, detail=''):
        line = json.dumps({'at': now().isoformat(timespec='seconds'), 'node': self.name, 'caller': caller,
                           'cmd': cmd, 'args': args, 'outcome': outcome, 'detail': str(detail)[:300]})
        with open(self.audit_path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    # -- dispatch
    def handle(self, body, sig):
        caller, cmd, args = None, None, {}
        try:
            req, rule = self.verify(body, sig)
            caller, cmd, args = req['caller'], req['cmd'], req.get('args') or {}
            if not isinstance(args, dict):
                raise Refused('args must be an object')
            if cmd == 'status':
                result = self.do_status(args)
            else:   # one limit per command and thing (restart_service:searxng); held while it runs, counted if it ran
                key = cmd + (f":{args.get('name') or args.get('peer')}" if args.get('name') or args.get('peer') else '')
                with self.limits.lock:
                    self.limits.check(key, rule.get('limit'))
                    result = getattr(self, 'do_' + cmd)(args)
                    self.limits.record(key)
            self.audit(caller, cmd, args, 'ok', result.get('summary', ''))
            return 200, dict(result, ok=True, node=self.name, cmd=cmd)
        except Refused as e:
            self.audit(caller, cmd, args, 'refused', e)
            return 403, {'ok': False, 'node': self.name, 'cmd': cmd, 'error': str(e)}
        except Exception as e:  # noqa: BLE001
            self.audit(caller, cmd, args, 'error', f'{type(e).__name__}: {e}')
            return 500, {'ok': False, 'node': self.name, 'cmd': cmd, 'error': type(e).__name__}

    def do_status(self, args):
        services = {}
        for name, svc in (self.cfg.get('services') or {}).items():
            port = svc.get('port')
            services[name] = 'up' if port and _port_open(svc.get('host', '127.0.0.1'), port) else (
                'down' if port else 'unknown')
        disk = shutil.disk_usage(self.cfg.get('disk', 'C:\\' if WINDOWS else '/'))
        out = {'services': services, 'memory': _memory(), 'uptime_h': _uptime_h(),
               'disk_free_gb': round(disk.free / 2**30, 1), 'paused': self._paused()}
        out['summary'] = ', '.join(f'{k} {v}' for k, v in services.items()) or 'no services listed'
        return out

    def do_restart_service(self, args):
        name = args.get('name')
        svc = (self.cfg.get('services') or {}).get(name)
        if not svc or not (svc.get('task') or svc.get('service')):
            raise Refused(f'{name!r} is not a restartable service on {self.name}')
        if svc.get('task'):   # scheduled tasks: end, then run again (the task's own settings decide the account)
            self.run(['schtasks', '/End', '/TN', svc['task']])
            code, out = self.run(['schtasks', '/Run', '/TN', svc['task']])
        else:
            code, out = self.run(['powershell', '-NoProfile', '-Command', f"Restart-Service -Name '{svc['service']}'"])
        if code:
            raise Refused(f'restart of {name} failed: {out}')
        return {'summary': f'restarted {name}'}

    def do_restart_pc(self, args):
        busy = self.cfg.get('busy_flag')
        if busy and os.path.exists(busy):
            raise Refused(f'{self.name} is busy (a card is running); try after it finishes')
        code, out = self.run(SAFE_SHUTDOWN)
        if code:
            raise Refused(f'shutdown refused: {out}')
        return {'summary': 'graceful restart in 60 s'}

    def do_wake(self, args):
        peer = args.get('peer')
        how = (self.cfg.get('wake') or {}).get(peer)
        if not how:
            raise Refused(f'{self.name} has no way to wake {peer!r}')
        if how.get('url'):
            if not how['url'].startswith(('http://100.', 'http://desktop-', 'http://laptop-', 'http://hal9000')):
                raise Refused('wake URL must be on the tailnet')
            r = self.open(urllib.request.Request(how['url'], data=b'{}', method='POST',
                                                 headers={'Content-Type': 'application/json'}), timeout=10)
            return {'summary': f'wake sent to {peer} (HTTP {getattr(r, "status", "?")})'}
        self.wol(how['mac'], how.get('broadcast', '255.255.255.255'))
        return {'summary': f'Wake-on-LAN sent to {peer}'}

    def do_run_fix(self, args):
        name = args.get('name')
        fix = (self.cfg.get('fixes') or {}).get(name)
        if not fix:
            raise Refused(f'{name!r} is not a listed fix on {self.name}')
        path = fix['path']
        if not os.path.isfile(path) or sha256_file(path) != fix.get('sha256'):
            raise Refused(f'fix {name} is missing or changed since it was approved')
        argv = (['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path]
                if path.lower().endswith('.ps1') else [sys.executable, path])
        code, out = self.run(argv, timeout=int(fix.get('timeout_s', 300)))
        return {'summary': f'fix {name} exit {code}', 'exit': code, 'output': out}

    def do_logs(self, args):
        name = args.get('name')
        path = (self.cfg.get('logs') or {}).get(name)
        if not path:
            raise Refused(f'{name!r} is not a listed log on {self.name}')
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = collections.deque(f, maxlen=200)
        return {'summary': f'{len(lines)} lines of {name}', 'lines': [ln.rstrip('\n') for ln in lines]}


def send_wol(mac, broadcast='255.255.255.255'):
    raw = bytes.fromhex(mac.replace(':', '').replace('-', ''))
    if len(raw) != 6:
        raise Refused('bad MAC address')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(b'\xff' * 6 + raw * 16, (broadcast, 9))


# ----------------------------------------------------------------------------- HTTP

def make_handler(node):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'JarvisNode/1'

        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path == '/health':
                return self._send(200, {'ok': True, 'node': node.name})
            self._send(404, {'ok': False})

        def do_POST(self):  # noqa: N802
            if self.path != '/cmd':
                return self._send(404, {'ok': False})
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(413, {'ok': False, 'error': 'body too large or empty'})
            code, obj = node.handle(self.rfile.read(n), self.headers.get('X-Jarvis-Sig', ''))
            self._send(code, obj)

        def log_message(self, *a):   # the audit log is the record; keep stderr quiet
            pass

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--config', default=os.path.join(HERE, 'node.json'))
    ap.add_argument('--approvals', default=os.path.join(HERE, 'approvals.json'))
    ap.add_argument('--pubkeys', default=os.path.join(HERE, 'pubkeys'))
    ap.add_argument('--self-key', default='C:\\Jarvis\\secrets\\crossconnect-self.json' if WINDOWS else
                    os.path.join(HERE, 'self.json'))
    ap.add_argument('--make-key', metavar='NODE',
                    help="make this machine's key pair once (refuses to overwrite), print the PUBLIC key, then exit")
    ap.add_argument('--state', default=os.path.join(HERE, 'state'))
    a = ap.parse_args(argv)
    if a.make_key:
        if os.path.exists(a.self_key):
            sys.exit(f'{a.self_key} already exists; not overwriting it')
        pem, pub = make_key()
        fd = os.open(a.self_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'node': a.make_key, 'pem': pem}, f)
        print(pub)
        return
    cfg = load_json(a.config)
    node = Node(cfg, load_json(a.approvals), load_public_keys(a.pubkeys), a.state)
    bind = cfg.get('bind')
    if not bind or not bind.startswith('100.'):
        sys.exit('node.json "bind" must be this machine\'s tailnet address (100.x.y.z); refusing to listen elsewhere')
    srv = ThreadingHTTPServer((bind, int(cfg.get('port', PORT))), make_handler(node))
    print(f'Jarvis node agent {node.name} on {bind}:{srv.server_address[1]}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
