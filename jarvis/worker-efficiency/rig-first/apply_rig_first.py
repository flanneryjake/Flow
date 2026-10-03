r"""Rig-first one-time fixes (proposal #1588 / chain #39, Jake approved 10/03) for the rig Worker.

  agent.py               #856 + #858  rig_anthropic_ok(): Ollama's Anthropic-style POST /v1/messages check, and
                                      `python agent.py selftest-rig [model]` (PASS/FAIL for /api/tags and /v1/messages)
                         #862         claude_cmd() logs "claude cmd: <exact argv>" (prompt is on stdin, no card text)
                         #851 + #854  SHELL_ALLOW gets claude launches (Bash/PowerShell)
  worker_script_guard.py #851 + #854  a nested `claude` launch passes only as `claude --version` or `claude -p ...
                                      --tools ""` (no tools) with no permission-widening flag

  python apply_rig_first.py             dry run on the live rig folder (says what it would change)
  python apply_rig_first.py --check     exit 0 only if every anchor is found once and both files compile
  python apply_rig_first.py --apply     backups <file>.bak-<stamp>-rigfirst, then writes both files
  python apply_rig_first.py --revert    puts the newest -rigfirst backups back
  python apply_rig_first.py --dir X     another folder (holding agent.py and worker_script_guard.py)
Writes nothing unless every anchor in BOTH files is found. Keeps each file's line endings. Restart the Worker after.
Does not touch request(), the App token provider, the model/runner switch or the notes share (#864/#865, PIN).
"""
import argparse
import datetime as dt
import glob
import os
import py_compile
import shutil
import sys

DEFAULT_DIR = r'C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent'
TAG = 'rigfirst'
MARK = 'rig-first #851/#854'

ALLOW_OLD = ('               "PowerShell(py *)", "PowerShell(cmd *)", "PowerShell(powershell *)", "PowerShell(& *)",\n'
             '               "PowerShell(.\\\\*)"]\n')
ALLOW_NEW = ('               "PowerShell(py *)", "PowerShell(cmd *)", "PowerShell(powershell *)", "PowerShell(& *)",\n'
             '               "PowerShell(.\\\\*)",\n'
             '               # rig-first #851/#854 (Jake approved #1588, 10/03): unattended claude CLI launches (timing,\n'
             '               # startup checks). worker_script_guard.py lets them through only as `claude --version` or\n'
             '               # `claude -p ... --tools ""` (no tools) with no permission-widening flag.\n'
             '               "Bash(claude *)", "PowerShell(claude *)", "PowerShell(claude.exe *)"]\n')

CMD_OLD = ('    for d in extra_dirs:\n'
           '        cmd += ["--add-dir", d]\n'
           '    return cmd\n')
CMD_NEW = ('    for d in extra_dirs:\n'
           '        cmd += ["--add-dir", d]\n'
           '    log("claude cmd: " + subprocess.list2cmdline(cmd))   # 10/03 card #862: exact command, prompt is on stdin\n'
           '    return cmd\n')

RIG_ANCHOR = '\n\ndef wake_rig_and_wait(max_min=5):\n'
RIG_FUNCS = '''

def rig_anthropic_ok(model=None):
    """True if Ollama answers the Anthropic-style POST /v1/messages (cards #856/#858; the kit assumes it does).
    jarvis/qwen models send a thinking block first, so with max_tokens 8 there may be no text: check type only."""
    try:
        if not model:
            with urllib.request.urlopen(BRAIN + "/api/tags", timeout=5) as r:
                ms = json.loads(r.read().decode()).get("models", [])
            if not ms:
                return False
            model = ms[0]["name"]
        body = json.dumps({"model": model, "max_tokens": 8,
                           "messages": [{"role": "user", "content": "OK"}]}).encode()
        req = urllib.request.Request(BRAIN + "/v1/messages", data=body,
                                     headers={"content-type": "application/json",
                                              "x-api-key": "ollama",
                                              "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status == 200 and json.loads(r.read().decode()).get("type") == "message"
    except Exception:
        return False


def _selftest_rig(model=None):
    """python agent.py selftest-rig [model]: PASS/FAIL for the rig's /api/tags and /v1/messages (#856/#858).
    With no model it uses one Ollama already has loaded (/api/ps), so the check never swaps a model onto the GPU."""
    tags = rig_ready()
    print(("PASS" if tags else "FAIL") + f"  /api/tags at {BRAIN}")
    if tags and not model:
        try:
            with urllib.request.urlopen(BRAIN + "/api/ps", timeout=5) as r:
                loaded = json.loads(r.read().decode()).get("models", [])
            model = loaded[0]["name"] if loaded else None
        except Exception:
            model = None
    msgs = bool(tags and rig_anthropic_ok(model))
    print(("PASS" if msgs else "FAIL") + f"  /v1/messages (Anthropic-compatible), model {model or 'first in /api/tags'}")
    return 0 if tags and msgs else 1
'''

MAIN_OLD = ('        print(tell(a[1], a[2] if len(a) > 2 else "check"))\n'
            '        return\n'
            '    load_ghq()\n')
MAIN_NEW = ('        print(tell(a[1], a[2] if len(a) > 2 else "check"))\n'
            '        return\n'
            '    if a[:1] == ["selftest-rig"]:   # #856/#858: no GitHub calls, just the rig endpoints\n'
            '        sys.exit(_selftest_rig(a[1] if len(a) > 1 else None))\n'
            '    load_ghq()\n')

GUARD_CHECK_OLD = ('def check(cmd, cwd, card_dir):\n'
                   '    """None = allow; else the reason to block."""\n'
                   '    for pat, why in DENY:\n'
                   '        if re.search(pat, cmd):\n'
                   '            return why\n')
GUARD_CHECK_NEW = ('def check(cmd, cwd, card_dir):\n'
                   '    """None = allow; else the reason to block."""\n'
                   '    for pat, why in DENY:\n'
                   '        if re.search(pat, cmd):\n'
                   '            return why\n'
                   '    why = claude_launch(cmd)\n'
                   '    if why:\n'
                   '        return why\n')
GUARD_FUNC_ANCHOR = '\n\ndef check(cmd, cwd, card_dir):\n'
GUARD_FUNC = r'''

# rig-first #851/#854 (Jake approved #1588, 10/03): unattended runs may launch the claude CLI for timing and startup
# checks, but only `claude --version` or `claude -p ... --tools ""` (no tools, so no file edits, shell or permission
# prompts). Anything that widens permissions or loads extra config is refused.
CLAUDE_BAD = re.compile(r"""(?ix)(--dangerously-skip-permissions|--allow-dangerously-skip-permissions|bypasspermissions
                        |--permission-mode|--allowed-?tools|--settings\b|--setting-sources|--mcp-config|--add-dir
                        |--plugin-dir|--plugin-url|--agents\b)""")
CLAUDE_HEAD = re.compile(r"""(?ix)^\s*(?:&\s*|call\s+|start(?:-process)?\s+(?:[/-]\w+\s+)*?|cmd(?:\.exe)?\s+/[ck]\s+|
                         (?:powershell|pwsh)(?:\.exe)?\s+(?:-\w+\s+)*?-c(?:ommand)?\s+["']?)?
                         ["']?(?:[^\s"';&|()]*[\\/])?claude(?:\.exe|\.cmd)?["']?(?=\s|$)(?P<rest>.*)$""")
CLAUDE_NO_TOOLS = re.compile(r"""(?i)--tools(?:\s+|=)(?:""|''|'""'|`"`")(?=\s|$)""")


def claude_launch(cmd):
    """None unless cmd launches the claude CLI in a way unattended runs may not; then the reason."""
    if re.search(r"(?i)--dangerously-skip-permissions|bypasspermissions", cmd):
        return "claude with permission bypass is not allowed in unattended runs"
    for seg in re.split(r"&&|\|\||;|\n|\|", cmd):
        m = CLAUDE_HEAD.match(seg)
        if not m:
            continue
        rest = m.group("rest").strip()
        if re.fullmatch(r"(?i)(--version|-v)", rest):
            continue
        if not re.search(r"(?:^|\s)(-p|--print)(?=\s|$)", rest):
            return 'only `claude --version` or `claude -p ... --tools ""` may run unattended (no subcommands, no interactive runs)'
        if CLAUDE_BAD.search(rest):
            return "claude -p may not widen permissions or load extra config (settings, MCP, plugins, agents, dirs)"
        if not CLAUDE_NO_TOOLS.search(rest):
            return 'claude -p needs --tools "" (no tools) in unattended runs'
    return None
'''


def _read(path):
    raw = open(path, 'rb').read()
    return raw, raw.decode('utf-8').replace('\r\n', '\n')


def _once(t, old, new, what):
    n = t.count(old)
    if n != 1:
        raise SystemExit(f'{what}: anchor found {n}x (need exactly 1)')
    return t.replace(old, new, 1)


def patch_agent(t):
    if 'def rig_anthropic_ok(' in t or 'card #862: exact command' in t or MARK in t:
        raise SystemExit('agent.py: already has the rig-first fixes')
    a = t.find('\ndef claude_cmd(')
    b = t.find('\n\n\n', a) + 1   # through the function's last newline
    if a < 0 or b < 1 or t[a:b].count(CMD_OLD) != 1:
        raise SystemExit('claude_cmd(): --add-dir/return anchor not found exactly once')
    t = t[:a] + t[a:b].replace(CMD_OLD, CMD_NEW, 1) + t[b:]
    t = _once(t, ALLOW_OLD, ALLOW_NEW, 'SHELL_ALLOW end')
    if t.count('\ndef rig_ready():\n') != 1:
        raise SystemExit('rig_ready(): not found exactly once')
    t = _once(t, RIG_ANCHOR, RIG_FUNCS + RIG_ANCHOR, 'wake_rig_and_wait (insert point after rig_ready)')
    t = _once(t, MAIN_OLD, MAIN_NEW, 'main() tell/load_ghq')
    return t


def patch_guard(t):
    if 'def claude_launch(' in t:
        raise SystemExit('worker_script_guard.py: already has the rig-first fixes')
    t = _once(t, GUARD_FUNC_ANCHOR, GUARD_FUNC + GUARD_FUNC_ANCHOR, 'guard def check()')
    t = _once(t, GUARD_CHECK_OLD, GUARD_CHECK_NEW, 'guard check() DENY loop')
    return t


FILES = (('agent.py', patch_agent), ('worker_script_guard.py', patch_guard))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=DEFAULT_DIR)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    if a.revert:
        for name, _ in FILES:
            path = os.path.join(a.dir, name)
            baks = sorted(glob.glob(f'{path}.bak-*-{TAG}'))
            if baks:
                shutil.copy2(baks[-1], path)
                print(f'{name}: restored {os.path.basename(baks[-1])}')
            else:
                print(f'{name}: no -{TAG} backup, left as is')
        return 0
    out, already = [], []
    for name, fn in FILES:
        path = os.path.join(a.dir, name)
        raw, t = _read(path)
        try:
            new = fn(t)
        except SystemExit as e:
            if 'already has' in str(e):
                already.append(str(e))
                continue
            print(f'{name}: {e}. Nothing written.')
            return 1
        if b'\r\n' in raw:
            new = new.replace('\n', '\r\n')
        out.append((name, path, new.encode('utf-8')))
    if already:
        for s in already:
            print(s)
        if out:
            print('Half-applied folder (one file patched, one not): nothing written. Use --revert first.')
            return 1
        return 0
    tmps = []
    try:
        for name, path, data in out:
            tmp = f'{path}.tmp-{TAG}'
            with open(tmp, 'wb') as f:
                f.write(data)
            tmps.append(tmp)
            py_compile.compile(tmp, cfile=tmp + 'c', doraise=True)
    except py_compile.PyCompileError as e:
        print(f'patched copy does not compile; nothing written: {e}')
        return 1
    finally:
        for tmp in tmps:
            if os.path.exists(tmp + 'c'):
                os.remove(tmp + 'c')
            if not a.apply and os.path.exists(tmp):
                os.remove(tmp)
    if not a.apply:
        print('agent.py + worker_script_guard.py: ready (every anchor found, both compile).'
              + ('' if a.check else ' Dry run; add --apply to write.'))
        return 0
    stamp = f'{dt.datetime.now():%Y%m%d-%H%M}'
    for name, path, _ in out:
        shutil.copy2(path, f'{path}.bak-{stamp}-{TAG}')
    for name, path, _ in out:
        os.replace(f'{path}.tmp-{TAG}', path)
        print(f'{name}: applied (backup {name}.bak-{stamp}-{TAG})')
    print('Restart the Worker when it is idle, then: python agent.py selftest-rig')
    return 0


if __name__ == '__main__':
    sys.exit(main())
