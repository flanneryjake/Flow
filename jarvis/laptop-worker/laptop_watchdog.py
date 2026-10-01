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
import re
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


CODE_FILES = (WORKER, r'C:\Jarvis\ghq\ghq.py')


def code_changed(health):
    """True when laptop_worker.py or ghq.py was replaced after the running Worker started. On 9/30 the GitHub-queue
    version was copied in two minutes after a Notion-only copy started, and that copy ran for 7 hours without
    seeing a single GitHub card."""
    started = (health or {}).get('started_epoch')
    if not started:
        return bool(health) and 'started_epoch' not in health   # a pre-fix Worker: restart once onto new code
    try:
        return max(os.path.getmtime(f) for f in CODE_FILES if os.path.exists(f)) > started + 2
    except ValueError:
        return False


def stop_worker():
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                    "Where-Object { $_.CommandLine -match 'laptop_worker\\.py' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   creationflags=0x08000000, capture_output=True)
    time.sleep(3)


def ensure_worker():
    health = get(WORKER_URL)
    stale = True
    try:
        stale = (time.time() - os.path.getmtime(HEARTBEAT)) > 600
    except OSError:
        pass
    working = str((health or {}).get('state', '')).startswith('Working')
    if health is not None and not working and code_changed(health):
        stop_worker()
        log('Worker code changed since it started; restarted it onto the new code')
    elif health is not None and not (stale and not working):
        return 'up'
    elif health is not None:
        # Answering but its loop has stopped writing the heartbeat: end it so a fresh copy can take the port.
        stop_worker()
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


REVIEW_REPO = r'C:\Jarvis\laptop-review-repo'   # clone of jarvis-outputs on its orphan branch laptop-review
JOBS = os.path.join(HERE, 'logs', 'laptop-jobs.jsonl')
REVIEW_STATE = os.path.join(HERE, 'logs', 'review-push.json')
SECRET = re.compile(r'(ntn_|secret_|sk-ant-|sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{16,}')


def git(*args):
    return subprocess.run(['git', '-C', REVIEW_REPO, *args], capture_output=True, text=True, timeout=120,
                          creationflags=0x08000000)


def push_review(force=False):
    """Hourly: copy the laptop Worker's job log (secrets redacted) to jarvis-outputs branch laptop-review, where
    homebase picks it up as a review queue for Baby Jarvis training. Never touches the repo's main branch."""
    if not os.path.exists(JOBS) or not os.path.isdir(os.path.join(REVIEW_REPO, '.git')):
        return 'skipped'
    try:
        with open(REVIEW_STATE, encoding='utf-8') as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    mtime = os.path.getmtime(JOBS)
    if not force and (mtime <= st.get('mtime', 0) or time.time() - st.get('at', 0) < 3300):
        return 'unchanged'
    dest = os.path.join(REVIEW_REPO, 'review', 'laptop-jobs.jsonl')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(JOBS, encoding='utf-8') as f, open(dest, 'w', encoding='utf-8', newline='\n') as o:
        o.write(SECRET.sub('<redacted>', f.read()))
    git('pull', '-q', '--rebase', 'origin', 'laptop-review')
    git('add', 'review/laptop-jobs.jsonl')
    if git('diff', '--cached', '--quiet').returncode == 0:
        st.update(mtime=mtime, at=time.time())
    else:
        git('-c', 'user.name=flanneryjake', '-c', 'user.email=119984498+flanneryjake@users.noreply.github.com',
            'commit', '-q', '-m', f'Laptop jobs {dt.datetime.now():%Y-%m-%d %H:%M}')
        r = git('push', '-q', 'origin', 'laptop-review')
        if r.returncode != 0:
            log(f'review push failed: {r.stderr.strip()[:200]}')
            return 'failed'
        st.update(mtime=mtime, at=time.time())
        log('pushed laptop jobs to jarvis-outputs laptop-review')
    with open(REVIEW_STATE, 'w', encoding='utf-8') as f:
        json.dump(st, f)
    return 'pushed'


TARS = r'C:\Jarvis\tars\tars_server.py'
POWER = r'C:\Jarvis\power\power_listener.py'


def ensure_tars():
    """Jarvis TARS chat service (:8790). Started at logon by its own task; this only covers a crash."""
    if not os.path.exists(TARS) or get('http://127.0.0.1:8790/health') is not None:
        return 'up'
    subprocess.Popen([pythonw(), TARS], cwd=os.path.dirname(TARS), creationflags=HIDDEN, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    log('TARS chat service was not answering; started it')
    return 'restarted'


if __name__ == '__main__':
    o = ensure_ollama()
    w = ensure_worker()
    try:
        ensure_tars()
        if os.path.exists(POWER) and get('http://127.0.0.1:8792/health') is None:
            subprocess.Popen([pythonw(), POWER], cwd=os.path.dirname(POWER), creationflags=HIDDEN, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            log('Power listener was not answering; started it')
    except Exception as e:
        log(f'TARS/power check error: {e}')
    try:
        rv = push_review('--push-now' in sys.argv)
    except Exception as e:
        rv = f'error {e}'
        log(f'review push error: {e}')
    if '--verbose' in sys.argv:
        print(f'ollama: {o}, worker: {w}, review: {rv}')
