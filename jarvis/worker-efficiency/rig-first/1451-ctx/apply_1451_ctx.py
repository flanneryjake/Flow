r"""Card #1451 step 1, GPU half only (10/03): stop the 21 GB qwen3.6:35b reloads that preceded the 4:25 AM CUDA fault.

The rig's Ollama keeps qwen3.6:35b loaded at OLLAMA_CONTEXT_LENGTH (32768, machine env). local_lane.py and
fallback_lane.py ask the same model for num_ctx 16384, and a different num_ctx makes Ollama reload the whole model
on the GPU. This points both at OLLAMA_CONTEXT_LENGTH, keeping 16384 as the fallback when the variable is missing.

Not here (on purpose): swapping jarvis -> jarvis-q36 and the ghq.py / needsjake.py num_ctx edits. Those callers use
the CPU-only jarvis today, so they never touch the GPU; they only matter with the model swap, which #1685 owns.

  python apply_1451_ctx.py --check     every anchor found once + both files compile; writes nothing
  python apply_1451_ctx.py --apply     <file>.bak-<stamp>-1451ctx backups, keeps line endings, all-or-nothing
  python apply_1451_ctx.py --revert    newest -1451ctx backups back
  python apply_1451_ctx.py --dir X     another folder holding local_lane.py + fallback_lane.py
The change takes effect at the next Worker restart.
"""
import argparse
import datetime as dt
import glob
import os
import py_compile
import shutil
import sys

DEFAULT_DIR = r'C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent'
TAG = '1451ctx'
OLD = '"options": {"num_ctx": 16384, "num_predict": 4096'
NEW = ('"options": {"num_ctx": int(os.environ.get("OLLAMA_CONTEXT_LENGTH") or 16384),   # #1451: match the loaded runner\n'
       '                                   "num_predict": 4096')
FILES = ('local_lane.py', 'fallback_lane.py')


def patch(t, name):
    if 'OLLAMA_CONTEXT_LENGTH' in t:
        raise SystemExit(f'{name}: already has the #1451 context fix')
    if t.count(OLD) != 1:
        raise SystemExit(f'{name}: num_ctx anchor found {t.count(OLD)}x (need exactly 1)')
    if '\nimport os\n' not in t and not t.startswith('import os\n'):
        raise SystemExit(f'{name}: no top-level "import os"')
    return t.replace(OLD, NEW, 1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=DEFAULT_DIR)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    if a.revert:
        for name in FILES:
            path = os.path.join(a.dir, name)
            baks = sorted(glob.glob(f'{path}.bak-*-{TAG}'))
            if baks:
                shutil.copy2(baks[-1], path)
                print(f'{name}: restored {os.path.basename(baks[-1])}')
            else:
                print(f'{name}: no -{TAG} backup, left as is')
        return 0
    out, already = [], []
    for name in FILES:
        path = os.path.join(a.dir, name)
        raw = open(path, 'rb').read()
        t = raw.decode('utf-8').replace('\r\n', '\n')
        try:
            new = patch(t, name)
        except SystemExit as e:
            if 'already has' in str(e):
                already.append(str(e))
                continue
            print(f'{e}. Nothing written.')
            return 1
        if b'\r\n' in raw:
            new = new.replace('\n', '\r\n')
        out.append((name, path, new.encode('utf-8')))
    if already:
        print('\n'.join(already))
        if out:
            print('Half-applied folder: nothing written. Use --revert first.')
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
        print('local_lane.py + fallback_lane.py: ready (every anchor found, both compile).'
              + ('' if a.check else ' Dry run; add --apply to write.'))
        return 0
    stamp = f'{dt.datetime.now():%Y%m%d-%H%M}'
    for name, path, _ in out:
        shutil.copy2(path, f'{path}.bak-{stamp}-{TAG}')
    for name, path, _ in out:
        os.replace(f'{path}.tmp-{TAG}', path)
        print(f'{name}: applied (backup {name}.bak-{stamp}-{TAG})')
    print('Restart the Worker when it is idle.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
