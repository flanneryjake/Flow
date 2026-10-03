"""Tests for the node agent and client. Nothing is ever restarted: the command runner is a recorder.

  python -m unittest test_node_agent -v
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import client  # noqa: E402
import node_agent as na  # noqa: E402

PEMS = {n: na.make_key()[0] for n in ('5060', 'rig', 'junk', 'stranger')}
KEYS = {n: na.load_private(p) for n, p in PEMS.items()}            # private keys, by node
PUBLIC = {n: na.public_of(k) for n, k in KEYS.items() if n != 'stranger'}
WRONG = na.load_private(na.make_key()[0])


class Recorder:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, argv, timeout=60):
        self.calls.append(list(argv))
        return self.code, 'done'


def make_node(name='junk', cfg_extra=None, runner=None, **kw):
    tmp = tempfile.mkdtemp()
    cfg = {'node': name, 'pause_flag': os.path.join(tmp, 'paused.flag'), 'disk': tmp,
           'services': {'searxng': {'port': 1, 'task': 'Jarvis SearXNG'}, 'mosquitto': {'port': 1}},
           'wake': {'rig': {'url': 'http://desktop-5ve3c77.tail3bbcb8.ts.net:8767/wake/rig'},
                    'evil': {'url': 'http://example.com/wake'}},
           'fixes': {}, 'logs': {}}
    cfg.update(cfg_extra or {})
    with open(os.path.join(HERE, 'approvals.json'), encoding='utf-8') as f:
        approvals = json.load(f)
    node = na.Node(cfg, approvals, dict(PUBLIC), os.path.join(tmp, 'state'), runner=runner or Recorder(), **kw)
    return node, tmp


def send(node, caller, cmd, args=None, key=None, body=None):
    if body is None:
        body, sig = client.build(caller, key or KEYS[caller], cmd, args)
    else:
        sig = na.sign(key or KEYS[caller], body)
    return node.handle(body, sig)


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.node, self.tmp = make_node()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_from_any_peer(self):
        code, out = send(self.node, 'rig', 'status')
        self.assertEqual(code, 200)
        self.assertEqual(out['node'], 'junk')
        self.assertIn('searxng', out['services'])

    def test_bad_signature_refused(self):
        code, out = send(self.node, '5060', 'status', key=WRONG)
        self.assertEqual((code, out['error']), (403, 'bad signature or unknown caller'))

    def test_non_ascii_signature_refused(self):
        body, _ = client.build('5060', KEYS['5060'], 'status')
        self.assertEqual(self.node.handle(body, 'caf\u00e9')[0], 403)

    def test_unknown_caller_refused(self):
        code, _ = send(self.node, 'stranger', 'status')
        self.assertEqual(code, 403)

    def test_replay_refused(self):
        body, sig = client.build('5060', KEYS['5060'], 'status')
        self.assertEqual(self.node.handle(body, sig)[0], 200)
        code, out = self.node.handle(body, sig)
        self.assertEqual((code, out['error']), (403, 'repeated request'))

    def test_expired_refused(self):
        body = json.dumps({'cmd': 'status', 'caller': '5060', 'ts': time.time() - 120, 'nonce': 'n1'}).encode()
        code, out = send(self.node, '5060', 'status', body=body)
        self.assertEqual(code, 403)
        self.assertIn('expired', out['error'])

    def test_unknown_command_refused(self):
        code, out = send(self.node, '5060', 'format_disk')
        self.assertEqual(code, 403)
        self.assertIn('unknown command', out['error'])

    def test_not_in_approvals_refused(self):
        code, out = send(self.node, 'junk', 'restart_pc')     # junk may not restart anything
        self.assertEqual(code, 403)
        self.assertIn('approvals.json', out['error'])

    def test_audit_never_holds_keys(self):
        send(self.node, '5060', 'status')
        send(self.node, '5060', 'status', key=WRONG)
        with open(self.node.audit_path, encoding='utf-8') as f:
            log = f.read()
        self.assertEqual(log.count('\n'), 2)
        for pem in PEMS.values():
            self.assertNotIn(pem.splitlines()[1], log)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.run = Recorder()
        self.node, self.tmp = make_node(runner=self.run)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restart_pc_is_graceful_and_rate_limited(self):
        code, out = send(self.node, '5060', 'restart_pc')
        self.assertEqual(code, 200, out)
        self.assertEqual(self.run.calls, [na.SAFE_SHUTDOWN])
        self.assertNotIn('/f', self.run.calls[0])
        self.assertIn('/r', self.run.calls[0])
        code, out = send(self.node, 'rig', 'restart_pc')       # a second peer inside 30 min is refused too
        self.assertEqual(code, 403)
        self.assertIn('once per 30 min', out['error'])
        self.assertEqual(len(self.run.calls), 1)

    def test_per_day_limit(self):
        lim = na.Limits(os.path.join(self.tmp, 'l.json'))
        t0 = time.time() - 80000
        for i in range(4):
            lim.check_and_record('restart_pc', {'min_gap_min': 30, 'per_day': 4}, at=t0 + i * 3600)
        with self.assertRaises(na.Refused) as e:
            lim.check_and_record('restart_pc', {'min_gap_min': 30, 'per_day': 4}, at=t0 + 5 * 3600)
        self.assertIn('Jake decides', str(e.exception))
        lim.check_and_record('restart_pc', {'min_gap_min': 30, 'per_day': 4}, at=t0 + 86400 + 60)  # oldest aged out

    def test_paused_answers_status_only(self):
        open(self.node.cfg['pause_flag'], 'w').close()
        self.assertEqual(send(self.node, '5060', 'status')[0], 200)
        code, out = send(self.node, '5060', 'restart_pc')
        self.assertEqual(code, 403)
        self.assertIn('paused', out['error'])
        self.assertEqual(self.run.calls, [])

    def test_disabled_command_refused(self):
        self.node.cfg['disabled'] = ['restart_pc']
        code, out = send(self.node, '5060', 'restart_pc')
        self.assertEqual(code, 403)
        self.assertIn('not switched on', out['error'])
        self.assertEqual(self.run.calls, [])

    def test_busy_flag_blocks_restart(self):
        busy = os.path.join(self.tmp, 'busy')
        open(busy, 'w').close()
        self.node.cfg['busy_flag'] = busy
        code, out = send(self.node, '5060', 'restart_pc')
        self.assertEqual(code, 403)
        self.assertIn('busy', out['error'])

    def test_restart_service_only_listed_tasks(self):
        code, out = send(self.node, '5060', 'restart_service', {'name': 'mosquitto'})   # listed, but no task
        self.assertEqual(code, 403)
        code, out = send(self.node, '5060', 'restart_service', {'name': 'searxng'})
        self.assertEqual(code, 200, out)
        self.assertEqual(self.run.calls, [['schtasks', '/End', '/TN', 'Jarvis SearXNG'],
                                          ['schtasks', '/Run', '/TN', 'Jarvis SearXNG']])

    def test_run_fix_needs_matching_hash(self):
        path = os.path.join(self.tmp, 'fix.py')
        with open(path, 'w') as f:
            f.write('print("fixed")\n')
        with open(path, 'rb') as f:
            good = hashlib.sha256(f.read()).hexdigest()
        self.node.cfg['fixes'] = {'dns': {'path': path, 'sha256': good}, 'dns2': {'path': path, 'sha256': good}}
        code, out = send(self.node, '5060', 'run_fix', {'name': 'dns'})
        self.assertEqual(code, 200, out)
        with open(path, 'a') as f:
            f.write('import os\n')
        code, out = send(self.node, 'rig', 'run_fix', {'name': 'dns2'})
        self.assertEqual(code, 403)
        self.assertIn('changed since it was approved', out['error'])
        code, out = send(self.node, 'rig', 'run_fix', {'name': 'dns'})     # and the 10-minute limit holds
        self.assertIn('once per 10 min', out['error'])

    def test_wake_tailnet_only(self):
        seen = []

        class R:
            status = 200
        node, tmp = make_node(opener=lambda req, timeout=0: seen.append(req.full_url) or R())
        try:
            self.assertEqual(send(node, '5060', 'wake', {'peer': 'rig'})[0], 200)
            self.assertEqual(seen, ['http://desktop-5ve3c77.tail3bbcb8.ts.net:8767/wake/rig'])
            code, out = send(node, 'rig', 'wake', {'peer': 'evil'})
            self.assertEqual(code, 403)
            self.assertIn('tailnet', out['error'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_logs_tail(self):
        path = os.path.join(self.tmp, 'x.log')
        with open(path, 'w') as f:
            f.writelines(f'line {i}\n' for i in range(500))
        self.node.cfg['logs'] = {'x': path}
        code, out = send(self.node, '5060', 'logs', {'name': 'x'})
        self.assertEqual(code, 200)
        self.assertEqual(len(out['lines']), 200)
        self.assertEqual(out['lines'][-1], 'line 499')
        self.assertEqual(send(self.node, '5060', 'logs', {'name': '../../secrets'})[0], 403)


class HttpTests(unittest.TestCase):
    """End to end over a real socket on 127.0.0.1 (the real agent refuses to bind anywhere but the tailnet)."""

    def setUp(self):
        self.node, self.tmp = make_node()
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), na.make_handler(self.node))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.host = f'127.0.0.1:{self.srv.server_address[1]}'

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_port(self):
        import urllib.request
        real = urllib.request.urlopen
        host = self.host
        return lambda req, timeout=0: real(
            req if isinstance(req, str) and req.startswith('http://127') else
            (req.replace(f'x:{na.PORT}', host) if isinstance(req, str) else
             urllib.request.Request(req.full_url.replace(f'x:{na.PORT}', host), data=req.data,
                                    headers=dict(req.header_items()), method=req.get_method())), timeout=timeout)

    def test_health_and_signed_call(self):
        opener = self._patch_port()
        self.assertTrue(client.health('x', opener=opener))
        out = client.call('x', 'status', me=('5060', KEYS['5060']), opener=opener)
        self.assertTrue(out['ok'], out)
        out = client.call('x', 'restart_pc', me=('junk', KEYS['junk']), opener=opener)
        self.assertFalse(out['ok'])
        self.assertIn('approvals.json', out['error'])

    def test_unreachable_is_reported_not_raised(self):
        out = client.call('127.0.0.1:1', 'status', me=('5060', KEYS['5060']), timeout=1)
        self.assertFalse(out['ok'])

    def test_bind_must_be_tailnet(self):
        cfg = os.path.join(self.tmp, 'node.json')
        with open(cfg, 'w') as f:
            json.dump({'node': 'junk', 'bind': '0.0.0.0'}, f)
        with self.assertRaises(SystemExit):
            na.main(['--config', cfg, '--pubkeys', self.tmp, '--state', os.path.join(self.tmp, 's')])

    def test_make_key_never_overwrites_and_prints_only_public(self):
        import contextlib
        import io
        path = os.path.join(self.tmp, 'self.json')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            na.main(['--make-key', 'rig', '--self-key', path])
        pub = out.getvalue().strip()
        with open(path, encoding='utf-8') as f:
            me = json.load(f)
        self.assertEqual(me['node'], 'rig')
        self.assertNotIn('PRIVATE', out.getvalue())
        self.assertEqual(na.public_of(na.load_private(me['pem'])), pub)
        with self.assertRaises(SystemExit):
            na.main(['--make-key', 'rig', '--self-key', path])

    def test_new_peer_key_picked_up_without_restart(self):
        folder = os.path.join(self.tmp, 'pk')
        os.makedirs(folder)
        node, tmp = make_node(pubkeys_dir=folder)
        try:
            node.peer_keys = {}
            self.assertEqual(send(node, 'rig', 'status')[0], 403)
            with open(os.path.join(folder, 'rig.pub'), 'w') as f:
                f.write(PUBLIC['rig'])
            self.assertEqual(send(node, 'rig', 'status')[0], 200)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_json_with_bom_loads(self):
        path = os.path.join(self.tmp, 'bom.json')
        with open(path, 'wb') as f:
            f.write(b'\xef\xbb\xbf{"node": "5060"}')
        self.assertEqual(na.load_json(path), {'node': '5060'})

    def test_public_keys_folder(self):
        folder = os.path.join(self.tmp, 'pubkeys')
        os.makedirs(folder)
        with open(os.path.join(folder, 'rig.pub'), 'w') as f:
            f.write(PUBLIC['rig'] + '\n')
        self.assertEqual(na.load_public_keys(folder), {'rig': PUBLIC['rig']})


if __name__ == '__main__':
    unittest.main()
