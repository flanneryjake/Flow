"""Laptop power listener: lets the hub app shut down or restart the 5060 laptop over the tailnet (Jake approved 10/01).

POST /power  header X-Jarvis-Key: <key>
             body {"action": "shutdown"|"restart"|"cancel"|"status", "force": false, "delay": 60, "dry_run": false}
GET  /health (no key) -> {"ok": true}

- Key: C:\\Jarvis\\power\\power.key (made on first run). 5 wrong keys in 10 minutes locks it for 10 minutes.
- shutdown/restart are refused while the laptop Worker is mid-card (its /health state starts with "Working")
  unless "force": true. They go through shutdown.exe with a delay (default 60 s, 0-600) so "cancel" can stop them.
- Every request is logged to power.log.
Listens on 127.0.0.1:8792; `tailscale serve --bg --tcp 8792 tcp://127.0.0.1:8792` publishes it on the tailnet only.
"""
import datetime as dt
import hmac
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, 'power.key')
LOG = os.path.join(HERE, 'power.log')
PORT = int(os.environ.get('JARVIS_POWER_PORT', '8792'))
WORKER = 'http://127.0.0.1:8791/health'
NO_WINDOW = 0x08000000

bad = []
lock = threading.Lock()
pending = {'action': None, 'at': None}


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
            os.replace(LOG, LOG + '.old')
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n')
    except OSError:
        pass


def key():
    if not os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'w', encoding='utf-8') as f:
            f.write(secrets.token_urlsafe(24))
    with open(KEY_FILE, encoding='utf-8') as f:
        return f.read().strip()


def worker_state():
    try:
        with urllib.request.urlopen(WORKER, timeout=3) as r:
            return json.loads(r.read()).get('state', '')
    except Exception:  # noqa: BLE001
        return 'not answering'


def run(args):
    r = subprocess.run(['shutdown.exe'] + args, capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def handle(body):
    action = str(body.get('action', '')).lower()
    force = bool(body.get('force'))
    dry = bool(body.get('dry_run'))
    delay = max(0, min(int(body.get('delay', 60)), 600))
    state = worker_state()
    if action == 'status':
        return 200, {'ok': True, 'worker': state, 'pending': pending}
    if action == 'cancel':
        code, out = (0, 'dry run') if dry else run(['/a'])
        pending.update(action=None, at=None)
        return 200, {'ok': True, 'result': out or 'cancelled'}
    if action not in ('shutdown', 'restart'):
        return 400, {'ok': False, 'error': 'action must be shutdown, restart, cancel or status'}
    if state.startswith('Working') and not force:
        return 409, {'ok': False, 'refused': True,
                     'error': f'the laptop Worker is mid-card ({state[:120]}); send "force": true to override'}
    args = ['/s' if action == 'shutdown' else '/r', '/t', str(delay),
            '/c', f'Jarvis: {action} requested from the hub app' + (' (forced)' if force else '')]
    if dry:
        return 200, {'ok': True, 'dry_run': True, 'would_run': 'shutdown.exe ' + ' '.join(args), 'worker': state}
    code, out = run(args)
    if code == 0:
        pending.update(action=action, at=(dt.datetime.now() + dt.timedelta(seconds=delay)).isoformat(timespec='seconds'))
    return (200 if code == 0 else 500), {'ok': code == 0, 'action': action, 'in_seconds': delay,
                                         'result': out or 'scheduled', 'cancel_with': {'action': 'cancel'}}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip('/') in ('', '/health'):
            return self._send(200, {'ok': True, 'service': 'jarvis-power', 'machine': 'laptop'})
        self._send(404, {'error': 'POST /power'})

    def do_OPTIONS(self):
        self._send(204, None)

    def do_POST(self):
        if self.path.rstrip('/') != '/power':
            return self._send(404, {'error': 'POST /power'})
        now = time.time()
        with lock:
            bad[:] = [t for t in bad if now - t < 600]
            if len(bad) >= 5:
                log('locked: too many wrong keys')
                return self._send(429, {'ok': False, 'error': 'locked for 10 minutes after wrong keys'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(min(n, 10_000)) or b'{}')
        except ValueError:
            return self._send(400, {'ok': False, 'error': 'body must be JSON'})
        given = self.headers.get('X-Jarvis-Key') or str(body.get('key', ''))
        if not hmac.compare_digest(given.encode(), key().encode()):
            with lock:
                bad.append(now)
            log(f'REJECTED wrong key, action={body.get("action")!r}')
            return self._send(401, {'ok': False, 'error': 'wrong key'})
        try:
            code, resp = handle(body)
        except Exception as e:  # noqa: BLE001
            code, resp = 500, {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        log(f'{body.get("action")!r} force={bool(body.get("force"))} dry={bool(body.get("dry_run"))} '
            f'-> {code} {json.dumps(resp)[:300]}')
        self._send(code, resp)

    def _send(self, code, obj):
        b = json.dumps(obj).encode() if obj is not None else b''
        self.send_response(code)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Jarvis-Key')
        if obj is not None:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        if b:
            self.wfile.write(b)

    def log_message(self, *a):
        pass


def main():
    key()
    try:
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    except OSError:
        return 0   # already running
    log(f'power listener on 127.0.0.1:{PORT}')
    srv.serve_forever()


if __name__ == '__main__':
    sys.exit(main())
