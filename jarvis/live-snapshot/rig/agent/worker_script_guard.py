"""PreToolUse hook for the rig Worker's unattended `claude -p` card runs (card #592, Jake approved 10/02:
"let workers run their own scripts").

Allows running .py / .bat / .ps1 / .cmd files ONLY when they live inside the current card's work folder
(JARVIS_CARD_DIR, set by agent.py for each run). Refuses anywhere: elevation (RunAs, schtasks /RL HIGHEST),
service/HKLM changes (sc config/create, reg add HKLM), package installs (pip/npm/winget/choco install), and
download-then-execute (iwr/curl/wget/irm/Start-BitsTransfer feeding iex/a shell/a script).
Exit 0 = allow (normal permission rules still apply), exit 2 = block, reason on stderr (shown to Claude).
"""
import json
import os
import re
import sys

SCRIPT_EXT = (".py", ".pyw", ".bat", ".cmd", ".ps1")
# Scripts outside the card folder a card may still run (exact paths, compared case-insensitively)
ALLOWED_SCRIPTS = {os.path.normcase(os.path.realpath(p)) for p in (
    r"C:\Jarvis\guardrails\training_intake.py",   # TRAINING_ADD intake (guardrails, Jake 10/02)
    r"C:\Jarvis\sandbox\sandbox.ps1",              # Docker sandbox test of card code (Jake 10/02)
)}

DENY = [
    (r"(?i)\brunas\b|-verb\s+['\"]?runas", "elevation (RunAs) is not allowed in unattended runs"),
    (r"(?i)\bschtasks(\.exe)?\b[^\n]*?/rl\s+highest", "schtasks /RL HIGHEST (elevated task) is not allowed"),
    (r"(?i)\bsc(\.exe)?\s+(config|create)\b", "changing Windows services (sc config/create) is not allowed"),
    (r"(?i)\breg(\.exe)?\s+add\s+['\"]?(hklm|hkey_local_machine)", "reg add HKLM is not allowed"),
    (r"(?i)\b(pip3?|npm|pnpm|yarn|winget|choco|scoop)(\.exe)?\s+(install|i|add)\b", "package installs are not allowed"),
    (r"(?i)\b-m\s+pip\s+install\b", "package installs are not allowed"),
    (r"(?i)\b(iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget|start-bitstransfer)\b[^\n]*"
     r"(\|\s*(iex|invoke-expression|powershell|pwsh|python|py|cmd|bash|sh)\b"
     r"|[;&|]+[^\n]*\b(iex|invoke-expression|start-process|&\s*['\"]?[^\s]*\.(ps1|bat|cmd|py|exe))\b"
     r"|[;&|]+\s*(powershell|pwsh|python|py|cmd|bash|sh)\b[^\n]*\.(ps1|bat|cmd|py))",
     "download-then-execute is not allowed"),
    (r"(?i)\b(iex|invoke-expression)\s*\(?\s*\(?\s*(iwr|irm|invoke-webrequest|invoke-restmethod|new-object\s+net\.webclient)",
     "download-then-execute is not allowed"),
    # 10/02 Docker sandbox: test card code with C:\Jarvis\sandbox\sandbox.ps1 (copy in, no mounts, no network)
    (r"(?i)\bdocker(\.exe)?\s+(run|create|container\s+(run|create))\b[^\n]*?(\s-v\s|\s--volume\b|\s--mount\b|"
     r"--privileged|--pid[= ]host|--network[= ]host|--net[= ]host|--cap-add)",
     "docker with host mounts/privileges is not allowed - use C:/Jarvis/sandbox/sandbox.ps1"),
]

# a script file being run: python/py file, cmd /c file, powershell -File file, & file, .\file, bash file, or a bare path
RUN_RE = re.compile(
    r"""(?ix)
    (?:^|[\s;&|(])
    (?:python(?:3|w)?(?:\.exe)?|py(?:\.exe)?|cmd(?:\.exe)?\s+/[ck]|(?:powershell|pwsh)(?:\.exe)?[^\n]*?-file|bash|sh|call|start(?:-process)?|&)?
    \s*
    (?P<path>"[^"\n]+?\.(?:py|pyw|bat|cmd|ps1)"|'[^'\n]+?\.(?:py|pyw|bat|cmd|ps1)'|[^\s"';&|()]+?\.(?:py|pyw|bat|cmd|ps1))
    (?=$|[\s;&|)])
    """)

READ_ONLY_HEAD = re.compile(r"(?i)^\s*(cat|type|get-content|gc|less|more|head|tail|ls|dir|get-childitem|copy|cp|"
                            r"move|mv|copy-item|move-item|echo|write-output|notepad|code|git\s+(add|diff|status|log|show))\b")


def run_targets(cmd):
    """Script files the command would execute (not ones it only reads, copies or edits)."""
    out = []
    for seg in re.split(r"&&|\|\||;|\n|\|", cmd):
        if not seg.strip() or READ_ONLY_HEAD.match(seg):
            continue
        for m in RUN_RE.finditer(seg):
            p = m.group("path").strip("\"'")
            # python -c "...x.py..." / -m module: not a file run
            if re.search(r"(?i)\bpython\w*(\.exe)?\s+-(c|m)\b", seg):
                continue
            out.append(p)
    return out


def inside(path, folder):
    try:
        p, f = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
        return os.path.commonpath([p, f]) == f
    except ValueError:   # different drives
        return False


def check(cmd, cwd, card_dir):
    """None = allow; else the reason to block."""
    for pat, why in DENY:
        if re.search(pat, cmd):
            return why
    targets = run_targets(cmd)
    if not targets:
        return None
    if not card_dir:
        return "running script files is only allowed inside the card's work folder (JARVIS_CARD_DIR not set)"
    # `cd <dir> && python x.py`: resolve relative scripts against the last cd in the command
    for m in re.finditer(r"""(?i)(?:^|[\s;&|(])(?:cd|chdir|pushd|set-location|sl)\s+(?:/d\s+)?(?:-path\s+)?(["']?)([^"'&;|\n]+?)\1\s*(?=$|&&|;|\||\n)""", cmd):
        d = m.group(2).strip()
        cwd = d if os.path.isabs(d) else os.path.join(cwd or card_dir, d)
    for t in targets:
        full = t if os.path.isabs(t) else os.path.join(cwd or card_dir, t)
        if os.path.normcase(os.path.realpath(full)) in ALLOWED_SCRIPTS:
            continue
        if not inside(full, card_dir):
            return (f"{t} is outside this card's work folder ({card_dir}); copy the script there and run it from "
                    f"that folder, or leave it for Jake")
    return None


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    if ev.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    cmd = (ev.get("tool_input") or {}).get("command") or ""
    why = check(cmd, ev.get("cwd") or os.getcwd(), os.environ.get("JARVIS_CARD_DIR", ""))
    if why:
        print(f"Blocked by the Worker script guard (card #592): {why}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
