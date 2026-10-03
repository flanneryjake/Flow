"""Tars tools that run in code, no Claude needed: tailnet checks, filing movies and TV shows, ecosystem pings.

Recognised from Jake's words with regexes (like home.py), never chosen by the model:
  - "is the rig online", "which machines are up", "ping hal"    -> tailscale status / ping (read-only)
  - "wake the rig"                                             -> the backup laptop's wake relay
  - "file the new movies", "sort my downloads"                 -> move video files from the inbox folders into
                                                                  Movies\\Title (Year)\\ or TV\\Show\\Season NN\\
  - "send a ping to my phone saying X"                         -> hub /api/notify
  - "wake the worker", "check the board now"                   -> the Worker's POST /wake

Guardrails (Jake 2026-10-03): only folders and endpoints in actions.json; moves only, never delete, never overwrite
(a name clash is skipped and reported); nothing leaves the tailnet; every action goes to actions.log. Standard
library only.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, 'actions.json')
LOG = os.path.join(HERE, 'actions.log')
VIDEO = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv', '.ts'}
EXTRAS = {'.srt', '.sub', '.idx', '.ass', '.vtt'}       # subtitles travel with their video

DEFAULTS = {
    'machines': {   # spoken name -> tailnet host
        'rig': 'desktop-vllddm4', 'jarvis': 'desktop-vllddm4', 'big pc': 'desktop-vllddm4',
        'tars': 'laptop-4150egrs', 'homebase': 'laptop-4150egrs', '5060': 'laptop-4150egrs', 'laptop': 'laptop-4150egrs',
        'junky pos': 'desktop-5ve3c77', 'junk laptop': 'desktop-5ve3c77', 'backup': 'desktop-5ve3c77',
        'hal': 'hal9000', 'hal9000': 'hal9000', 'pi': 'hal9000',
    },
    'wake_url': {'rig': 'http://desktop-5ve3c77.tail3bbcb8.ts.net:8767/wake/rig'},
    'notify_url': 'http://127.0.0.1:8770/api/notify',
    'notify_headers': {},          # {"Header-Name": "@C:\\path\\to\\file"} reads the value from that file
    'worker_wake_url': 'http://127.0.0.1:8780/wake',
    'worker_headers': {},
    'media': {'inbox': [], 'movies': '', 'tv': '', 'min_mb': 50},
}

STATUS_RE = re.compile(r"\b(?:is|are)\s+(?:the\s+)?(.+?)\s+(?:on|online|up|awake|off|offline|down|asleep)\b|"
                       r"\b(which|what)\s+(?:machines|pcs|computers)\b.*\b(up|online|on)\b|\btailnet status\b", re.I)
PING_RE = re.compile(r"^\s*(?:jarvis[,.]?\s+)?ping\s+(?:the\s+)?(.+?)\s*[.?!]*$", re.I)
WAKE_RE = re.compile(r"\bwake\s+(?:up\s+)?(?:the\s+)?(rig|big pc|jarvis)\b", re.I)
MEDIA_RE = re.compile(r"\b(?:file|sort|organi[sz]e|move|put away|tidy)\b.*\b(movies?|tv|shows?|episodes?|downloads?|"
                      r"videos?|media)\b", re.I)
NOTIFY_RE = re.compile(r"\b(?:send|push)\s+(?:a\s+)?(?:ping|notification|alert|message)\s+to\s+my\s+phone\s*"
                       r"(?:saying|that|:)?\s*(.*)$", re.I)
WORKER_RE = re.compile(r"\b(wake|poke|kick)\s+(?:up\s+)?the\s+worker\b|\bcheck the board now\b", re.I)
EPISODE_RE = re.compile(r"^(?P<show>.+?)[ ._-]+[Ss](?P<s>\d{1,2})[ ._-]?[Ee](?P<e>\d{1,3})|"
                        r"^(?P<show2>.+?)[ ._-]+(?P<s2>\d{1,2})x(?P<e2>\d{2})\b")
YEAR_RE = re.compile(r"^(?P<title>.+?)[ ._(\[-]+(?P<year>19\d\d|20\d\d)\b")
JUNK_RE = re.compile(r"\b(1080p|2160p|720p|480p|4k|uhd|web[- .]?dl|webrip|bluray|brrip|hdrip|x26[45]|h\.?26[45]|hevc|"
                     r"aac|ddp?5\.1|atmos|proper|repack|extended|remux)\b.*$", re.I)


def config():
    try:
        with open(CONFIG, encoding='utf-8') as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def audit(msg):
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n')
    except OSError:
        pass


def _headers(spec):
    out = {'Content-Type': 'application/json'}
    for k, v in (spec or {}).items():
        if isinstance(v, str) and v.startswith('@'):
            try:
                with open(v[1:], encoding='utf-8') as f:
                    v = f.read().strip()
            except OSError:
                continue
        out[k] = v
    return out


def _post(url, body, headers=None, timeout=10):
    host = urllib.parse.urlparse(url).hostname or ''
    if not (host in ('127.0.0.1', 'localhost') or host.endswith('.ts.net') or host.startswith('100.')):
        raise PermissionError(f'{host} is outside the tailnet')
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST', headers=_headers(headers))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


# ----------------------------------------------------------------------------- tailnet

def tailscale(*args, timeout=20):
    exe = shutil.which('tailscale') or r'C:\Program Files\Tailscale\tailscale.exe'
    p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def peers(run=tailscale):
    rc, out = run('status', '--json')
    data = json.loads(out)
    nodes = [data.get('Self', {})] + list((data.get('Peer') or {}).values())
    return {(n.get('HostName') or '').lower(): bool(n.get('Online')) or n is data.get('Self') for n in nodes}


def _host(name, cfg):
    name = re.sub(r'^(?:the|my)\s+', '', (name or '').strip().lower())
    return cfg['machines'].get(name) or (name if re.fullmatch(r'[a-z0-9-]+', name) else None)


def nice(host, cfg):
    return {'desktop-vllddm4': 'The rig', 'laptop-4150egrs': 'Tars', 'desktop-5ve3c77': 'The backup laptop',
            'hal9000': 'Hal'}.get(host, host)


def tailnet_status(text, cfg, run=tailscale):
    state = peers(run)
    m = STATUS_RE.search(text)
    host = _host(m.group(1), cfg) if m and m.group(1) else None
    if host:
        if host not in state:
            return f"{nice(host, cfg)} isn't on the tailnet at all, sir."
        return f"{nice(host, cfg)} is {'online' if state[host] else 'offline'}, sir."
    known = {h: nice(h, cfg) for h in set(cfg['machines'].values())}
    up = [known[h] for h in known if state.get(h)]
    down = [known[h] for h in known if h in state and not state[h]]
    return ('Online: ' + (', '.join(up) or 'nothing') + '.' + (' Offline: ' + ', '.join(down) + '.' if down else ''))


def ping(text, cfg, run=tailscale):
    host = _host(PING_RE.match(text).group(1), cfg)
    if not host:
        return None
    rc, out = run('ping', '--c', '1', '--timeout', '5s', host)
    m = re.search(r'in (\d+(?:\.\d+)?)\s*ms', out)
    audit(f'ping {host} rc={rc}')
    if rc == 0 and m:
        return f"{nice(host, cfg)} answered in {round(float(m.group(1)))} milliseconds, sir."
    return f"{nice(host, cfg)} didn't answer, sir."


def wake(text, cfg, post=_post):
    url = cfg['wake_url'].get('rig')
    try:
        post(url, {'by': 'tars', 'reason': text[:120]})
    except Exception as e:  # noqa: BLE001
        audit(f'wake rig FAILED {type(e).__name__}')
        return "The wake relay on the backup laptop didn't answer, sir."
    audit('wake rig sent')
    return 'Waking the rig, sir. Give it a minute or two.'


# ----------------------------------------------------------------------------- movies and TV

def _clean(name):
    name = JUNK_RE.sub('', name)
    name = re.sub(r'[._]+', ' ', name)
    name = re.sub(r'[\[\](){}]', ' ', name)
    return re.sub(r'\s{2,}', ' ', name).strip(' -').title()


def destination(filename, cfg):
    """Where a video belongs: (folder, new_name) or (None, reason)."""
    stem, ext = os.path.splitext(os.path.basename(filename))
    m = EPISODE_RE.search(stem)
    if m:
        show = _clean(m.group('show') or m.group('show2'))
        season = int(m.group('s') or m.group('s2'))
        ep = int(m.group('e') or m.group('e2'))
        if not cfg['media'].get('tv'):
            return None, 'no TV folder set'
        return (os.path.join(cfg['media']['tv'], show, f'Season {season:02d}'),
                f'{show} - S{season:02d}E{ep:02d}{ext.lower()}')
    m = YEAR_RE.search(stem)
    if m:
        title = f"{_clean(m.group('title'))} ({m.group('year')})"
        if not cfg['media'].get('movies'):
            return None, 'no Movies folder set'
        return os.path.join(cfg['media']['movies'], title), f'{title}{ext.lower()}'
    return None, "can't tell if it's a movie or a show"


def _inside(path, roots):
    path = os.path.realpath(path)
    return any(r and os.path.commonpath([path, os.path.realpath(r)]) == os.path.realpath(r) for r in roots)


def file_media(cfg, dry_run=False):
    """Move videos (and same-named subtitles) from the inbox folders. Returns (moved, skipped) lists."""
    media = cfg['media']
    roots = [media.get('movies'), media.get('tv')]
    moved, skipped = [], []
    for inbox in media.get('inbox') or []:
        for dirpath, _dirs, files in os.walk(inbox):
            for fn in files:
                src = os.path.join(dirpath, fn)
                stem, ext = os.path.splitext(fn)
                if ext.lower() not in VIDEO or re.search(r'\bsample\b', stem, re.I):
                    continue
                if os.path.getsize(src) < media.get('min_mb', 50) * 1024 * 1024:
                    continue            # partial downloads and samples
                if fn.endswith(('.part', '.!qb', '.crdownload')):
                    continue
                folder, name = destination(fn, cfg)
                if not folder:
                    skipped.append(f'{fn}: {name}')
                    continue
                dst = os.path.join(folder, name)
                if not _inside(dst, roots):
                    skipped.append(f'{fn}: outside the allowed folders')
                    continue
                if os.path.exists(dst):
                    skipped.append(f'{fn}: {name} already there')
                    continue
                if not dry_run:
                    os.makedirs(folder, exist_ok=True)
                    shutil.move(src, dst)       # same drive: a rename; never deletes anything else
                    for sub in os.listdir(dirpath):
                        s_stem, s_ext = os.path.splitext(sub)
                        if s_ext.lower() in EXTRAS and s_stem.startswith(stem):
                            s_dst = os.path.join(folder, os.path.splitext(name)[0] + s_stem[len(stem):] + s_ext.lower())
                            if not os.path.exists(s_dst):
                                shutil.move(os.path.join(dirpath, sub), s_dst)
                    audit(f'moved {src} -> {dst}')
                moved.append(name)
    return moved, skipped


def media_reply(cfg):
    if not (cfg['media'].get('inbox') and (cfg['media'].get('movies') or cfg['media'].get('tv'))):
        return "I haven't been told where your movie and TV folders are yet, sir."
    moved, skipped = file_media(cfg)
    if not moved and not skipped:
        return 'Nothing new to file, sir.'
    out = f"Filed {len(moved)} {'video' if len(moved) == 1 else 'videos'}" + (f": {', '.join(moved[:3])}" if moved else '')
    if len(moved) > 3:
        out += f' and {len(moved) - 3} more'
    out += '.'
    if skipped:
        out += f" I left {len(skipped)} alone: {skipped[0]}" + ('…' if len(skipped) > 1 else '.')
    return out


# ----------------------------------------------------------------------------- ecosystem pings

def notify(message, cfg, post=_post):
    message = message.strip().strip('"').strip()
    if not message:
        return 'What should the notification say, sir?'
    try:
        post(cfg['notify_url'], {'title': 'Jarvis', 'body': message[:200], 'source': 'tars'},
             cfg.get('notify_headers'))
    except Exception as e:  # noqa: BLE001
        audit(f'notify FAILED {type(e).__name__}')
        return "The hub didn't take the notification, sir."
    audit(f'notify: {message[:80]}')
    return 'Sent to your phone, sir.'


def worker_wake(cfg, post=_post):
    try:
        post(cfg['worker_wake_url'], {'by': 'tars'}, cfg.get('worker_headers'))
    except Exception as e:  # noqa: BLE001
        audit(f'worker wake FAILED {type(e).__name__}')
        return "The Worker didn't answer, sir."
    audit('worker wake sent')
    return "The Worker's checking the board now, sir."


# ----------------------------------------------------------------------------- dispatch

def act(text, log=print):
    """Run a tool if Jake's words ask for one. Returns the spoken reply, or None."""
    t = (text or '').strip()
    cfg = config()
    try:
        m = NOTIFY_RE.search(t)
        if m:
            return notify(m.group(1), cfg)
        if WORKER_RE.search(t):
            return worker_wake(cfg)
        if WAKE_RE.search(t):
            return wake(t, cfg)
        if PING_RE.match(t):
            return ping(t, cfg)
        if MEDIA_RE.search(t):
            return media_reply(cfg)
        if STATUS_RE.search(t) and (not STATUS_RE.search(t).group(1) or _host(STATUS_RE.search(t).group(1), cfg)
                                    in set(cfg['machines'].values())):
            return tailnet_status(t, cfg)
    except Exception as e:  # noqa: BLE001
        log(f'action failed: {type(e).__name__}: {str(e)[:160]}')
        audit(f'FAILED {type(e).__name__} for: {t[:80]}')
        return "That didn't work, sir. It's in the actions log."
    return None


def main(argv):
    """`python actions.py file-media [--dry-run]`: run the filer from a scheduled task (the rig's media library)."""
    if argv[:1] != ['file-media']:
        print('usage: python actions.py file-media [--dry-run]')
        return 2
    cfg = config()
    if not (cfg['media'].get('inbox') and (cfg['media'].get('movies') or cfg['media'].get('tv'))):
        print('actions.json has no media folders')
        return 1
    moved, skipped = file_media(cfg, dry_run='--dry-run' in argv)
    for m in moved:
        print(('would move: ' if '--dry-run' in argv else 'moved: ') + m)
    for s in skipped:
        print('skipped: ' + s)
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv[1:]))
