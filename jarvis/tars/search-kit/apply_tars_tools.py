"""Card #924: wire local web search + local lookups into the TARS chat service on the 5060 (Tars).

Run ON TARS:  python apply_tars_tools.py [--tars-dir C:\\Jarvis\\tars] [--dry-run] [--rollback]

1. lookup.py: search http://127.0.0.1:8888 (local SearXNG) first, then http://100.90.201.22:8888 (homebase), 5 s each.
   JARVIS_SEARX_URLS (comma list) overrides both; an old JARVIS_SEARX_URL is kept as the fallback.
2. local_tools.py copied next to tars_server.py; tars_server.py gets sunrise/sunset + weather FACTS in its context.
3. gemini_helper.py copied to C:\\Jarvis\\helper if it isn't there (never overwrites; never reads or prints the key).
Every changed file is backed up as <file>.bak-924 first; --rollback puts them back. Anchors that don't match are
reported and skipped (nothing half-applied per file). Standard library only. Persona/Modelfile are not touched.
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BAK = '.bak-924'

LOOKUP_OLD_URL = "SEARX_URL = os.environ.get('JARVIS_SEARX_URL', 'http://100.90.201.22:8888').rstrip('/')"
LOOKUP_NEW_URL = """SEARX_URL = os.environ.get('JARVIS_SEARX_URL', 'http://100.90.201.22:8888').rstrip('/')
# Card #924: local SearXNG on this laptop first, then homebase; 5 s each.
SEARX_URLS = [u.strip().rstrip('/') for u in os.environ.get('JARVIS_SEARX_URLS', '').split(',') if u.strip()] or \\
    list(dict.fromkeys(['http://127.0.0.1:8888', SEARX_URL, 'http://100.90.201.22:8888']))
SEARX_TIMEOUT = 5"""

LOOKUP_OLD_SEARX = """def searx(query, n=5, timeout=12):
    url = f'{SEARX_URL}/search?' + urllib.parse.urlencode({'q': query, 'format': 'json', 'safesearch': 1})
    req = urllib.request.Request(url, headers={'User-Agent': 'jarvis-tars', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode('utf-8', 'replace'))
"""
LOOKUP_NEW_SEARX = """def searx_json(query, timeout=SEARX_TIMEOUT):
    \"\"\"Raw SearXNG JSON from the first instance that answers with results (local, then homebase).\"\"\"
    last = None
    for base in SEARX_URLS:
        url = f'{base}/search?' + urllib.parse.urlencode({'q': query, 'format': 'json', 'safesearch': 1})
        req = urllib.request.Request(url, headers={'User-Agent': 'jarvis-tars', 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode('utf-8', 'replace'))
        except Exception as e:  # noqa: BLE001
            last = e
            continue
        if data.get('results') or data.get('answers') or data.get('infoboxes'):
            return data
        last = RuntimeError(f'no results from {base}')
    raise RuntimeError(f'searx failed everywhere: {last}')


def searx(query, n=5, timeout=SEARX_TIMEOUT):
    data = searx_json(query, timeout=timeout)
"""

# Card #952's lookup.py (Iron Man) looks sunset up on the web and the model misreads it (6:38 for 6:23), so exact
# local answers go first inside find(). Optional: older lookup.py files without these lines still get the rest.
LOOKUP_OLD_FIND = "    for src in order:\n        try:\n            facts = {'searx': searx, 'gemini': gemini, 'claude': claude}[src](query)\n"
LOOKUP_NEW_FIND = """    if kind == 'lookup':   # card #924: sunrise/sunset + weather from local tools first (exact, no web)
        try:
            import local_tools
            fx = local_tools.facts_for(query, log=log)
        except Exception as e:  # noqa: BLE001
            log(f'local tools failed: {e}')
            fx = ''
        if fx:
            log(f'lookup local ok ({len(fx)} chars) for: {query[:80]}')
            return 'local', fx
""" + LOOKUP_OLD_FIND
LOOKUP_OLD_LABEL = "label = {'searx': 'a web search', 'gemini': 'Gemini', 'claude': 'Claude'}[src]"
LOOKUP_NEW_LABEL = "label = {'searx': 'a web search', 'gemini': 'Gemini', 'claude': 'Claude', 'local': 'local tools'}[src]"

TEST_OLD ="        lookup.SEARX_URL = 'http://127.0.0.1:%d' % cls.srv.server_port\n"
TEST_NEW = TEST_OLD + ("        lookup.SEARX_URLS = [lookup.SEARX_URL]   # card #924: only the fake server\n"
                       "        __import__('sys').modules['local_tools'] = None   # #924: fake-search tests skip local tools\n")

SERVER_OLD_FX ="    fx = facts(text) if (TASK_WORDS.search(text) and not fm) else ''\n"
SERVER_NEW_FX = SERVER_OLD_FX + """    try:   # card #924: sunrise/sunset + weather answered from local tools, no web round trip
        import local_tools
        tools_fx = '' if fm else local_tools.facts_for(text, log=log)
    except Exception as e:  # noqa: BLE001
        log(f'local tools failed: {e}')
        tools_fx = ''
"""
SERVER_OLD_CTX = "    if note:\n        ctx.append('NOTE: ' + note)\n"
SERVER_NEW_CTX = ("    if tools_fx:\n        ctx.append('LIVE FACTS (local tools; answer from these, give the actual numbers):\\n' + tools_fx)\n"
                  + SERVER_OLD_CTX)


def patch(path, pairs, dry, optional=()):
    """`optional` pairs go in only together and only when every one of their anchors is there."""
    with open(path, encoding='utf-8') as f:
        src = f.read()
    if '#924' in src:
        return 'already patched'
    new = src
    for old, rep in pairs:
        if new.count(old) != 1:
            return f'SKIPPED (anchor not found exactly once: {old.strip().splitlines()[0][:70]})'
        new = new.replace(old, rep)
    extra = ''
    if optional:
        if all(new.count(old) == 1 for old, _ in optional):
            for old, rep in optional:
                new = new.replace(old, rep)
            extra = ' + local tools in find()'
        else:
            extra = ' (find() hook not applicable)'
    if not dry:
        shutil.copy2(path, path + BAK)
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(new)
    return 'patched' + extra + (' (dry run)' if dry else '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tars-dir', default=r'C:\Jarvis\tars')
    ap.add_argument('--helper-dir', default=r'C:\Jarvis\helper')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--rollback', action='store_true')
    ap.add_argument('--server', help='live chat-service script (default <tars-dir>\\tars_server.py)')
    a = ap.parse_args()
    lookup_py = os.path.join(a.tars_dir, 'lookup.py')
    server_py = a.server or os.path.join(a.tars_dir, 'tars_server.py')

    if a.rollback:
        for p in (lookup_py, server_py, os.path.join(a.tars_dir, 'test_lookup.py')):
            if os.path.exists(p + BAK):
                shutil.copy2(p + BAK, p)
                print('restored', p)
        return 0

    ok = True
    if not os.path.exists(server_py):
        print(f'NO tars_server.py in {a.tars_dir}; pass --tars-dir')
        return 2
    if os.path.exists(lookup_py):
        r = patch(lookup_py, [(LOOKUP_OLD_URL, LOOKUP_NEW_URL), (LOOKUP_OLD_SEARX, LOOKUP_NEW_SEARX)], a.dry_run,
                  optional=[(LOOKUP_OLD_FIND, LOOKUP_NEW_FIND), (LOOKUP_OLD_LABEL, LOOKUP_NEW_LABEL)])
    else:
        r = 'MISSING (this TARS has no lookup lane yet; copy lookup.py from Flow claude/project-thread-16cc2j first)'
    print('lookup.py:', r)
    ok &= not r.startswith(('SKIPPED', 'MISSING'))
    test_py = os.path.join(a.tars_dir, 'test_lookup.py')
    if os.path.exists(test_py):
        print('test_lookup.py:', patch(test_py, [(TEST_OLD, TEST_NEW)], a.dry_run))
    r = patch(server_py, [(SERVER_OLD_FX, SERVER_NEW_FX), (SERVER_OLD_CTX, SERVER_NEW_CTX)], a.dry_run)
    print('tars_server.py:', r, '(fine when lookup.py got the find() hook)' if r.startswith('SKIPPED') else '')
    if not a.dry_run:
        shutil.copy2(os.path.join(HERE, 'local_tools.py'), os.path.join(a.tars_dir, 'local_tools.py'))
        print('local_tools.py: copied')
        dst = os.path.join(a.helper_dir, 'gemini_helper.py')
        if os.path.exists(dst):
            print('gemini_helper.py: already there (left alone)')
        else:
            os.makedirs(a.helper_dir, exist_ok=True)
            shutil.copy2(os.path.join(HERE, 'gemini_helper.py'), dst)
            print('gemini_helper.py: copied to', dst)
    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
                key = winreg.QueryValueEx(k, 'GEMINI_API_KEY')[0]
        except (ImportError, OSError):
            key = None
    print('GEMINI_API_KEY (user env):', 'present' if key else 'MISSING')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
