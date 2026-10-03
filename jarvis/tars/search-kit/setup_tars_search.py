"""Card #924, run ON TARS (the 5060 laptop): local SearXNG + wire TARS to it, then prove it. No admin needed.

  1. SearXNG container "searxng" on 127.0.0.1:8888 (formats html+json, limiter off, like homebase's), restart policy
     unless-stopped, settings in %USERPROFILE%\\searxng\\settings.yml (secret_key generated there, never printed).
  2. Startup-folder entry "Jarvis SearXNG.cmd": at logon starts Docker Desktop if needed and `docker start searxng`.
  3. apply_tars_tools.py (local-first search, sunrise/sunset + weather, gemini_helper), TARS restarted.
  4. Checks: curl local search, a voice-style chat question; everything goes to tars-tools-result.txt next to this file.
Standard library only. Re-runnable: an existing container/settings file is kept.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, 'tars-tools-result.txt')
CONF_DIR = os.path.join(os.path.expanduser('~'), 'searxng')
IMAGE = 'searxng/searxng:latest'
DOCKER_DESKTOP = r'C:\Program Files\Docker\Docker\Docker Desktop.exe'
STARTUP = os.path.join(os.environ.get('APPDATA', ''), r'Microsoft\Windows\Start Menu\Programs\Startup')
TARS_TASK = 'Jarvis TARS'
QUESTION = 'Hey Jarvis, who won the most recent Super Bowl, and what was the score?'
SUN_Q = 'What time is sunset tonight?'

out = []


def say(msg):
    print(msg)
    out.append(msg)


def run(cmd, timeout=300, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace',
                           cwd=cwd,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 99, str(e)


def docker_up(wait=180):
    if not shutil.which('docker'):
        return False
    if run(['docker', 'info'], 30)[0] == 0:
        return True
    if os.path.exists(DOCKER_DESKTOP):
        subprocess.Popen([DOCKER_DESKTOP], creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
    end = time.time() + wait
    while time.time() < end:
        if run(['docker', 'info'], 30)[0] == 0:
            return True
        time.sleep(5)
    return False


SETTINGS = """# Jarvis local SearXNG (card #924) - same as homebase: json on, limiter off. Tailnet/localhost only.
use_default_settings: true
general:
  instance_name: "jarvis-tars"
server:
  secret_key: "{secret}"
  limiter: false
  image_proxy: false
  public_instance: false
search:
  safe_search: 1
  formats:
    - html
    - json
"""

STARTUP_CMD = r"""@echo off
rem Jarvis SearXNG (card #924): start Docker Desktop if needed, then the searxng container.
docker info >nul 2>&1 || start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
for /l %%i in (1,1,40) do (
  docker info >nul 2>&1 && goto up
  timeout /t 5 /nobreak >nul
)
:up
docker start searxng >nul 2>&1
"""


def search_ok(base='http://127.0.0.1:8888', q='sunset Plymouth MA'):
    url = f'{base}/search?' + urllib.parse.urlencode({'q': q, 'format': 'json'})
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read().decode('utf-8', 'replace'))
        res = d.get('results') or []
        return len(res), (res[0].get('title', '') if res else '')
    except Exception as e:  # noqa: BLE001
        return 0, f'{type(e).__name__}: {e}'


def chat(text, timeout=240):
    req = urllib.request.Request('http://127.0.0.1:8790/chat', data=json.dumps({'text': text}).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8')).get('reply', '')


def find_live(port=8790):
    """(pid, cmdline, script) of the python process serving :8790 (card #952 moved/renamed it), else Nones."""
    for line in run(['netstat', '-ano', '-p', 'TCP'], 30)[1].splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == 'LISTENING' and parts[1].endswith(f':{port}'):
            pid = int(parts[4])
            cl = run(['powershell', '-NoProfile', '-Command',
                      f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'], 30)[1]
            m = re.search(r'([A-Za-z]:\\[^"]+?\.pyw?)\b', cl)
            if 'python' in cl.lower() and m and os.path.exists(m.group(1)):
                return pid, cl, m.group(1)
    return None, None, None


def restart_tars(pid, cl, folder):
    """Scheduled task 'Jarvis TARS' if it exists, else kill + relaunch the same command line (no admin)."""
    if run(['schtasks', '/query', '/tn', TARS_TASK], 30)[0] == 0:
        rc1 = run(['schtasks', '/end', '/tn', TARS_TASK], 30)[0]
        time.sleep(3)
        return f'schtasks end rc={rc1}, run rc={run(["schtasks", "/run", "/tn", TARS_TASK], 30)[0]}'
    if not (pid and cl):
        return 'no scheduled task and no live process found; restart TARS by hand'
    run(['taskkill', '/PID', str(pid), '/F'], 30)
    time.sleep(3)
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    subprocess.Popen(cl, cwd=folder, creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f'killed pid {pid} and relaunched: {cl[:160]}'


def main():
    say(f'card #924 setup on {os.environ.get("COMPUTERNAME")} at {time.strftime("%Y-%m-%d %H:%M:%S")}')
    # 1. SearXNG
    if not docker_up():
        say('SEARXNG: FAILED - Docker is not installed or will not start. Installing Docker Desktop needs admin; '
            'TARS still falls back to homebase search.')
    else:
        os.makedirs(CONF_DIR, exist_ok=True)
        conf = os.path.join(CONF_DIR, 'settings.yml')
        if not os.path.exists(conf):
            with open(conf, 'w', encoding='utf-8', newline='\n') as f:
                f.write(SETTINGS.format(secret=secrets.token_hex(32)))
            say(f'settings.yml written to {conf} (secret_key stored there)')
        rc, txt = run(['docker', 'inspect', '-f', '{{.State.Running}}', 'searxng'], 30)
        if rc != 0:
            rc, txt = run(['docker', 'run', '-d', '--name', 'searxng', '--restart', 'unless-stopped',
                           '-p', '127.0.0.1:8888:8080', '-v', f'{CONF_DIR}:/etc/searxng',
                           '-e', 'SEARXNG_BASE_URL=http://127.0.0.1:8888/', IMAGE], 600)
            say(f'docker run searxng: rc={rc} {txt[-200:]}')
        elif txt.strip() != 'true':
            say('docker start searxng: rc=%s' % run(['docker', 'start', 'searxng'], 60)[0])
        else:
            say('searxng container already running')
        for _ in range(24):
            n, first = search_ok()
            if n:
                break
            time.sleep(5)
        say(f'LOCAL SEARCH http://127.0.0.1:8888 sunset Plymouth MA: {n} results; first: {first[:100]}')
    # 2. boot
    try:
        with open(os.path.join(STARTUP, 'Jarvis SearXNG.cmd'), 'w', encoding='ascii', newline='\r\n') as f:
            f.write(STARTUP_CMD)
        say('boot: Startup-folder entry "Jarvis SearXNG.cmd" written (plus docker restart policy unless-stopped)')
    except OSError as e:
        say(f'boot: could not write Startup entry: {e}')
    if '--searxng-only' in sys.argv:   # Flow's jarvis/tars already searches local-first (JARVIS_SEARX_URL list)
        with open(RESULT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out) + '\n')
        print(f'\nwrote {RESULT}')
        return 0
    # 3. TARS code
    pid, cl, script = find_live()
    tars_dir = os.environ.get('TARS_DIR') or (os.path.dirname(script) if script else r'C:\Jarvis\tars')
    args = [sys.executable, os.path.join(HERE, 'apply_tars_tools.py'), '--tars-dir', tars_dir]
    if script:
        args += ['--server', script]
    say(f'live TARS: pid {pid}, script {script}; folder {tars_dir}')
    rc, txt = run(args, 60)
    say(f'apply_tars_tools rc={rc}:\n{txt}')
    rc, txt = run([sys.executable, '-m', 'unittest', 'test_lookup'], 120, cwd=tars_dir)
    say(f'test_lookup rc={rc}: {txt.splitlines()[-1] if txt else ""}')
    say('TARS restart: ' + restart_tars(pid, cl, tars_dir))
    for _ in range(20):
        try:
            urllib.request.urlopen('http://127.0.0.1:8790/health', timeout=5).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(3)
    # 4. proof
    for q in (QUESTION, SUN_Q):
        try:
            say(f'Q: {q}\nA: {chat(q)}')
        except Exception as e:  # noqa: BLE001
            say(f'Q: {q}\nA: (chat failed: {type(e).__name__}: {e})')
    with open(RESULT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    print(f'\nwrote {RESULT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
