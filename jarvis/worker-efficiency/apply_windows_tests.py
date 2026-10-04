"""E6 (Jake's 10/03 email, rig only): after a clean Docker pass, the rig runs the Windows-only part itself.

Before: sandbox_gate.may_autopass() sent every card whose Docker test skipped a Windows-only part (.bat, scheduled
task, registry) to Jake as "PASS, but the Windows-only part was not executed". 75 rig runs ended that way overnight.

After: a MEDIUM-risk card with a clean Docker PASS whose only gap is the Windows-only part is run on the rig the same
way a clean medium card already is (the tested run= script, inside the card's work folder, 20 min limit, output in
native-run.log). Jake approved scripts running in their own work folder, never as admin (#592), so it still goes to
Jake when:
  * the Worker itself is running as admin (the script would inherit it)
  * the script asks for admin or touches system-wide things (RunAs, HKLM, services, firewall, Defender, drivers,
    boot settings, ownership/ACL changes, highest-privilege scheduled tasks)
  * every existing rule: not MEDIUM, FAIL, timed out, labelled pin, always-PIN kinds, script outside the folder,
    script changed after the test
A failed Windows run goes to Jake with the exit code and output, as before. Off switch: JARVIS_E6=off.

    python apply_windows_tests.py --agent <rig folder with sandbox_gate.py> [--check | --revert]

Every anchor must match exactly once, or nothing is written. Backup: sandbox_gate.py.bak-<stamp>-e6. Keeps the
file's line endings. Restart the Worker afterwards.
"""
import argparse
import glob
import os
import shutil
import sys
import time

TAG = 'windows-tests 10/03'

HELPERS = '''# ---- %s (E6): the rig runs the Windows-only part of a clean medium card itself (#592: never as admin) ----
E6_ADMIN = re.compile(r"runas|-verb\\s+runas|\\bhklm\\b|hkey_local_machine|new-service|set-service|\\bsc(?:\\.exe)?\\s+"
                      r"(?:create|config|delete|start|stop)\\b|netsh|new-netfirewallrule|set-netfirewall|set-mppreference|"
                      r"add-mppreference|bcdedit|takeown|icacls|-runlevel\\s+highest|/rl\\s+highest|/ru\\s+system|"
                      r"install-windowsfeature|enable-windowsoptionalfeature|dism(?:\\.exe)?\\s|pnputil|"
                      r"set-executionpolicy\\b.*localmachine|requires\\s+-runasadministrator", re.I)


def _e6_is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return True   # can't tell: treat as admin, so nothing runs elevated by accident


def windows_part_refusal(body):
    """None when the rig may run the untested Windows-only part itself; else why it still goes to Jake."""
    if os.environ.get("JARVIS_E6", "on") == "off":
        return "JARVIS_E6=off"
    m = E6_ADMIN.search(body or "")
    if m:
        return f"the script needs admin or changes system settings ({m.group(0).strip()})"
    if _e6_is_admin():
        return "the Worker is running as admin, and card scripts never run as admin (#592)"
    return None


def may_autopass(''' % TAG

EDITS = [
    ('\ndef may_autopass(', '\n' + HELPERS),
    ('    if head.get("windows_only") != "no":\n        return False, "Windows-only part not executed in the sandbox"\n',
     '    win_only = head.get("windows_only") != "no"\n'
     '    if win_only and head.get("windows_only") != "yes":\n'
     '        return False, "Windows-only part not executed in the sandbox"\n'),
    ('    if hits:\n        return False, "always-PIN kind: " + "; ".join(hits[:3])\n    return True, script\n',
     '    if hits:\n        return False, "always-PIN kind: " + "; ".join(hits[:3])\n'
     '    if win_only:   # E6: the Windows run below is the Windows-only test\n'
     '        refusal = windows_part_refusal(body)\n'
     '        if refusal:\n'
     '            return False, f"Windows-only part not executed in the sandbox: {refusal}"\n'
     '    return True, script\n'),
]


def plan(path):
    raw = open(path, 'rb').read()
    s = raw.decode('utf-8').replace('\r\n', '\n')
    if TAG in s:
        return raw, None, 'already installed'
    out = s
    for anchor, repl in EDITS:
        n = out.count(anchor)
        if n != 1:
            return raw, None, f'anchor found {n} times, expected 1:\n    {anchor.strip()[:100]}'
        out = out.replace(anchor, repl, 1)
    if b'\r\n' in raw:
        out = out.replace('\n', '\r\n')
    return raw, out.encode('utf-8'), None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent', required=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    path = os.path.join(a.agent, 'sandbox_gate.py')
    if not os.path.exists(path):
        print('sandbox_gate.py not found: E6 is for the rig Worker only (the 5060 has no Docker sandbox).')
        return 1
    if a.revert:
        baks = sorted(glob.glob(path + '.bak-*-e6'))
        if baks:
            shutil.copy2(baks[-1], path)
            print(f'sandbox_gate.py: restored {os.path.basename(baks[-1])}')
        return 0
    raw, new, why = plan(path)
    print('sandbox_gate.py: ' + (why or 'ready'))
    if why:
        return 0 if why == 'already installed' else 1
    if a.check:
        return 0
    stamp = time.strftime('%Y%m%d-%H%M')
    shutil.copy2(path, f'{path}.bak-{stamp}-e6')
    with open(path, 'wb') as f:
        f.write(new)
    print(f'Installed (backup sandbox_gate.py.bak-{stamp}-e6). Restart the Jarvis Worker on the rig.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
