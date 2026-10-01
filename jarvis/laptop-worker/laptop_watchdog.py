"""Keeps the Baby Jarvis laptop Worker and Ollama running. Started every 5 min by the scheduled task
"Jarvis Baby Watchdog" through pythonw.exe, so no console window ever appears.

Each run:
  1. Ollama: if http://127.0.0.1:11434 doesn't answer, start "ollama app.exe" (or "ollama serve" if the app is
     missing), hidden, and wait up to 60 s.
  2. Worker: if nothing answers on 127.0.0.1:8791, or the heartbeat file is over 10 min old while the Worker
     says it is idle, (re)start laptop_worker.py with pythonw.exe, detached.
It touches nothing else; the Notion Machine Health row stays the job of the existing Jarvis Watchdog.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, 'laptop_worker.py')
LOG = os.path.join(HERE, 'logs', 'laptop-watchdog.log')
HEARTBEAT = r'C:\Jarvis\worker.heartbeat'
OLLAMA = 'http://127.0.0.1:11434'
WORKER_URL = 'http://127.0.0.1:8791/health'
OLLAMA_DIR = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Ollama')
HIDDEN = 0x08000000 | 0x00000008 | 0x00000200   # CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
        os.replace(LOG, LOG + '.old')
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f'{dt.datetime.now():%m/%d %H:%M:%S} {msg}\n')


def get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return None


def pythonw():
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return w if os.path.exists(w) else exe


def ensure_ollama():
    if get(f'{OLLAMA}/api/tags') is not None:
        return 'up'
    app = os.path.join(OLLAMA_DIR, 'ollama app.exe')
    cli = os.path.join(OLLAMA_DIR, 'ollama.exe')
    cmd = [app] if os.path.exists(app) else [cli if os.path.exists(cli) else 'ollama', 'serve']
    try:
        subprocess.Popen(cmd, creationflags=HIDDEN, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except OSError as e:
        log(f'Ollama down and could not start it: {e}')
        return 'down'
    for _ in range(12):
        time.sleep(5)
        if get(f'{OLLAMA}/api/tags') is not None:
            log(f'Ollama was down; started {os.path.basename(cmd[0])}')
            return 'restarted'
    log('Ollama was down; started it but it is still not answering')
    return 'down'


def ensure_worker():
    health = get(WORKER_URL)
    stale = True
    try:
        stale = (time.time() - os.path.getmtime(HEARTBEAT)) > 600
    except OSError:
        pass
    if health is not None and not (stale and not str(health.get('state', '')).startswith('Working')):
        return 'up'
    if health is not None:
        # Answering but its loop has stopped writing the heartbeat: end it so a fresh copy can take the port.
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                        "Where-Object { $_.CommandLine -match 'laptop_worker\\.py' } | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                       creationflags=0x08000000, capture_output=True)
        time.sleep(3)
        log('Worker answered but its heartbeat was over 10 min old; stopped it')
    subprocess.Popen([pythonw(), WORKER], cwd=HERE, creationflags=HIDDEN, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    for _ in range(6):
        time.sleep(2)
        if get(WORKER_URL) is not None:
            log('Worker was not running; started it')
            return 'restarted'
    log('Worker was not running; started it but it is not answering on :8791 yet')
    return 'starting'


if __name__ == '__main__':
    o = ensure_ollama()
    w = ensure_worker()
    if '--verbose' in sys.argv:
        print(f'ollama: {o}, worker: {w}')
