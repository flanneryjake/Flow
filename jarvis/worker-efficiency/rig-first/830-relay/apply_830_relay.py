r"""Card #830, rig half (10/03): the rig's Jarvis Relay names itself when it polls the 5060's /status.

The 5060 Worker records who last polled /status (X-Jarvis-From header or ?from=; live there since 10/03). The rig's
poller is the "Jarvis Relay" scheduled task (every minute): C:\Users\Jake\JarvisAgent\work\relay\relay.ps1, which
fetches $HB/status through Get-Text with no caller name, so the 5060 can't tell it was the rig. This adds the header
`X-Jarvis-From: rig` to Get-Text (also harmless on its other calls).

  python apply_830_relay.py --check     anchor found once; writes nothing
  python apply_830_relay.py --apply     relay.ps1.bak-<stamp>-830 backup, byte-level edit (the file mixes CRLF/LF)
  python apply_830_relay.py --revert    newest -830 backup back
  python apply_830_relay.py --file X    another copy of relay.ps1
Takes effect on the relay's next minute; no restart needed.
"""
import argparse
import datetime as dt
import glob
import os
import shutil
import sys

DEFAULT = r'C:\Users\Jake\JarvisAgent\work\relay\relay.ps1'
TAG = '830'
OLD = b"(Invoke-WebRequest -Uri $u -Method $m -UseBasicParsing -TimeoutSec 30).Content"
NEW = b"(Invoke-WebRequest -Uri $u -Method $m -UseBasicParsing -TimeoutSec 30 -Headers @{ 'X-Jarvis-From' = 'rig' }).Content"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=DEFAULT)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    if a.revert:
        baks = sorted(glob.glob(f'{a.file}.bak-*-{TAG}'))
        if baks:
            shutil.copy2(baks[-1], a.file)
            print(f'relay.ps1: restored {os.path.basename(baks[-1])}')
        else:
            print(f'relay.ps1: no -{TAG} backup, left as is')
        return 0
    raw = open(a.file, 'rb').read()
    if b'X-Jarvis-From' in raw:
        print('relay.ps1: already sends X-Jarvis-From')
        return 0
    if raw.count(OLD) != 1:
        print(f'relay.ps1: Get-Text anchor found {raw.count(OLD)}x (need exactly 1). Nothing written.')
        return 1
    if not a.apply:
        print('relay.ps1: ready (anchor found once).' + ('' if a.check else ' Dry run; add --apply to write.'))
        return 0
    bak = f'{a.file}.bak-{dt.datetime.now():%Y%m%d-%H%M}-{TAG}'
    shutil.copy2(a.file, bak)
    tmp = a.file + '.tmp-' + TAG
    with open(tmp, 'wb') as f:
        f.write(raw.replace(OLD, NEW, 1))
    os.replace(tmp, a.file)
    print(f'relay.ps1: applied (backup {os.path.basename(bak)})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
