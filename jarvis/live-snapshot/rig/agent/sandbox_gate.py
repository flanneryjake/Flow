"""Docker sandbox gate for the rig Worker (Jake 2026-10-02).

Hook at the end of agent.py, after the local lane:

    try:   # 10/2 Docker sandbox gate
        sys.path.insert(0, HERE); import sandbox_gate; sandbox_gate.install(globals())
    except Exception as _e:
        log(f"sandbox gate not loaded: {_e}")

install() does two things:
  1. Adds the DOCKER SANDBOX rule to the card prompt: code / installs / medium- or high-risk work is tested with
     C:\\Jarvis\\sandbox\\sandbox.ps1 before it goes to Jake (fix and rerun, max 3 runs), and the run ends with a
     SANDBOX: line (verdict, risk, the script to apply, plain-English intent and result).
  2. Wraps finish(). With a fresh sandbox-result.md in the card folder:
     - medium risk + clean PASS (see may_autopass) -> the Worker applies it on Windows itself, no PIN, renames the
       card "[Docker-tested] <intent> - <result>", attaches sandbox-result.md, logs it to the decisions log
     - anything else -> goes to Jake as before, with "Tested in Docker sandbox: PASS" (or the failure) in front
       of the Needs Jake text and sandbox-result.md attached
CLI: python sandbox_gate.py selftest
Stdlib only.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

AG = {}
ORIG = {}
SANDBOX = r"C:\Jarvis\sandbox\sandbox.ps1"
DECISIONS = r"C:\Jarvis\guardrails\decisions.jsonl"
AUTONOMY_DIR = r"C:\Jarvis\autonomy"
NATIVE_TIMEOUT_MIN = 20

PROMPT_RULE = """- DOCKER SANDBOX (Jake 10/2): if this card produces code to run or install, a package install, a scheduled
  task / startup entry / service, or anything you would rate MEDIUM or HIGH risk, test it BEFORE it goes to Jake.
  Write the bash test command(s) to sandbox-cmd.sh in this card's work folder (the folder that holds STATUS.md),
  then run (from Bash):
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:/Jarvis/sandbox/sandbox.ps1 -Folder "<card work folder>" -CmdFile sandbox-cmd.sh
  Add -Net only if the test must download packages. It is a throwaway Linux container (python3, pip, pwsh, node,
  git, gcc) holding a copy of the folder; installs inside it are fine. It writes sandbox-result.md in the folder.
  On FAIL, read it, fix the code, rerun - at most 3 runs. Windows-only parts (.bat, registry, scheduled tasks,
  services) only get a static check there; that is expected - say so, don't fake a Linux equivalent.
  Risk: LOW = only writes files in work folders (skip the sandbox); MEDIUM = installs packages, adds a scheduled
  task or startup entry, edits files outside the work folders, an easy-to-undo setting; HIGH = system-wide or hard
  to undo (services, firewall, HKLM registry, drivers, admin rights).
  After testing add ONE line (plain English, no jargon):
  SANDBOX: <PASS|FAIL> | risk=<LOW|MEDIUM|HIGH> | run=<script in the card folder that applies it on this PC, or none> | intent=<what it does, max 12 words> | result=<how the test went, max 15 words>
  Set run= only when the one thing left for Jake is running that tested .py/.ps1 on this machine; keep your
  NEEDS_JAKE line as usual - the Worker decides whether it may go ahead without him.
"""

SB_LINE = re.compile(r"^SANDBOX:\s*(PASS|FAIL)\b(.*)$", re.I)
HEAD = re.compile(r"<!-- jarvis-sandbox (.*?) -->")


def log(msg):
    AG["log"](msg)


def parse_sandbox_line(lines):
    """Last SANDBOX: line -> dict(verdict, risk, run, intent, result) or None."""
    for l in reversed(lines):
        m = SB_LINE.match(l.strip())
        if not m:
            continue
        d = {"verdict": m.group(1).upper(), "risk": "", "run": "", "intent": "", "result": ""}
        for part in m.group(2).split("|"):
            k, _, v = part.partition("=")
            k = k.strip().lower()
            if k in d and v.strip():
                d[k] = v.strip()
        d["risk"] = d["risk"].upper()
        if d["run"].lower() in ("none", "-", "n/a"):
            d["run"] = ""
        return d
    return None


def read_result(folder, since):
    """sandbox-result.md written during this run -> (header dict, full text) or (None, None)."""
    p = os.path.join(folder, "sandbox-result.md")
    try:
        if os.path.getmtime(p) < since:
            return None, None
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None, None
    m = HEAD.search(text)
    head = dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv) if m else {}
    return head, text


def pin_hits(text):
    """Always-PIN kinds (money, posting/sending, deleting, accounts/credentials, exposing services, merge to main)."""
    try:
        sys.path.insert(0, AUTONOMY_DIR)
        from classify import _hits, PIN_RULES
        return [f"{k}: {w}" for k, w in _hits(text or "", PIN_RULES)]
    except Exception as e:
        return [f"classifier unavailable ({e}) - treated as PIN"]


# Jake, 2026-10-02 (in the project thread): "if a medium risk card runs cleanly and as intended, it does not need a
# pin from me. Just note in the title that it was ran in docker, what its intent is, and how it performed in
# layman's terms."
# So: MEDIUM risk + a clean Docker PASS (everything ran, nothing Windows-only skipped, did what the card intended)
# -> no PIN; the Worker applies it, renames the card "[Docker-tested] <intent> - <result>", keeps sandbox-result.md
# on the card and logs a free_logged line in C:\Jarvis\guardrails\decisions.jsonl (7 AM report).
# It still goes to Jake when: HIGH risk (tested first, result attached); FAIL after 3 runs; a Windows-only part the
# sandbox couldn't run; or any always-PIN kind - money/spending, posting or sending to others, deleting personal
# data, accounts or credentials, exposing services, merging to main. This rule never loosens those.
def may_autopass(c, sb, head, folder, since, needs):
    """(True, script path) or (False, reason it still needs Jake)."""
    if not sb:
        return False, "no SANDBOX line"
    if sb["risk"] != "MEDIUM":
        return False, f"risk {sb['risk'] or 'not stated'} (only MEDIUM may skip the PIN)"
    if sb["verdict"] != "PASS" or (head or {}).get("verdict") != "PASS":
        return False, "sandbox did not pass"
    if head.get("windows_only") != "no":
        return False, "Windows-only part not executed in the sandbox"
    if head.get("timed_out") != "no":
        return False, "sandbox run timed out"
    if "pin" in {str(l).lower() for l in (c.get("labels") or [])}:
        return False, "card is labelled pin"
    if not sb["run"] or not needs:
        return False, "nothing for the Worker to apply (no run= script)"
    script = os.path.normpath(os.path.join(folder, sb["run"]))
    try:
        inside = os.path.commonpath([os.path.normcase(script), os.path.normcase(os.path.normpath(folder))]) == \
            os.path.normcase(os.path.normpath(folder))
    except ValueError:   # different drives
        inside = False
    if not inside:
        return False, f"run= script {sb['run']} is outside the card folder"
    if not os.path.isfile(script) or not script.lower().endswith((".py", ".ps1")):
        return False, f"run= script {sb['run']} missing or not .py/.ps1"
    _, text = read_result(folder, since)
    cmd_block = (text or "").split("## Command", 1)[-1].split("## Static check", 1)[0]
    cmd_file = os.path.join(folder, "sandbox-cmd.sh")
    tested = cmd_block + (open(cmd_file, encoding="utf-8", errors="replace").read() if os.path.isfile(cmd_file) else "")
    if os.path.basename(script).lower() not in tested.lower():
        return False, f"{os.path.basename(script)} was not part of the tested command"
    if os.path.getmtime(script) > os.path.getmtime(os.path.join(folder, "sandbox-result.md")):
        return False, f"{os.path.basename(script)} changed after the sandbox run"
    with open(script, encoding="utf-8", errors="replace") as f:
        body = f.read()
    hits = pin_hits("\n".join([c.get("task", ""), sb["intent"], sb["result"], " ".join(needs), body]))
    if hits:
        return False, "always-PIN kind: " + "; ".join(hits[:3])
    return True, script


def run_native(script, folder):
    """Apply the tested script on Windows. -> (exit code, output tail)."""
    if script.lower().endswith(".ps1"):
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
    else:
        py = os.path.join(os.path.dirname(sys.executable), "python.exe")
        cmd = [py if os.path.exists(py) else "python", script]
    try:
        r = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=NATIVE_TIMEOUT_MIN * 60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or "") + (r.stderr or "")
        code = r.returncode
    except subprocess.TimeoutExpired as e:
        out, code = f"timed out after {NATIVE_TIMEOUT_MIN} min\n{e.stdout or ''}", 124
    except Exception as e:
        out, code = f"could not start: {e}", 125
    with open(os.path.join(folder, "native-run.log"), "w", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M} {' '.join(cmd)}\nexit {code}\n\n{out}")
    return code, "\n".join(out.strip().splitlines()[-40:])


def decision(entry):
    entry = dict({"at": datetime.now().astimezone().isoformat(timespec="seconds"), "tier": "free_logged",
                  "kind": "docker-tested-medium", "machine": AG.get("ME"), "rule": "Jake 2026-10-02"}, **entry)
    try:
        with open(DECISIONS, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log(f"decisions log: {e}")


def rename(c, title):
    title = title[:240]
    try:
        if c.get("number"):
            g = AG["ghq"]
            g.api("PATCH", g.repo_path(f"/issues/{c['number']}"), {"title": title})
        else:
            AG["notion"]("PATCH", f"pages/{c['id']}", {"properties": {"Task": {"title": [{"text": {"content": title}}]}}})
        return True
    except Exception as e:
        log(f"sandbox gate: rename failed: {e}")
        return False


def attach(c, text):
    """sandbox-result.md on the card (GitHub comment; Notion cards get it in Agent log via the output)."""
    if c.get("number"):
        try:
            AG["ghq"].comment(c["number"], text[:60000], dedupe=False)
        except Exception as e:
            log(f"sandbox gate: attach failed on #{c['number']}: {e}")


def _finish(c, output, out_file):
    try:
        output = gate(c, output)
    except Exception as e:
        log(f"sandbox gate error ({e.__class__.__name__}: {e}) - card finishes as usual")
    return ORIG["finish"](c, output, out_file)


def gate(c, output):
    since = AG["STATE"].get("run_t0", time.time()) - 5
    folder = AG["card_dir"](c)
    head, text = read_result(folder, since)
    lines = output.strip().splitlines()
    sb = parse_sandbox_line(lines)
    if not head:
        return output   # nothing was sandboxed this pass (low risk, or no code)
    needs = [l.split(":", 1)[1].strip() for l in lines if l.startswith("NEEDS_JAKE:")]
    label = f"#{c['number']}" if c.get("number") else c.get("task", "?")[:60]
    ok, why = may_autopass(c, sb, head, folder, since, needs)
    if ok:
        code, tail = run_native(why, folder)
        if code == 0:
            title = f"[Docker-tested] {sb['intent']} - {sb['result']}"
            renamed = rename(c, title)
            attach(c, f"**Tested in Docker sandbox: PASS** - applied on this PC by the Worker without a PIN "
                      f"(Jake's 2026-10-02 medium-risk rule).\n\nWindows run of `{os.path.basename(why)}`: exit 0\n"
                      f"```\n{tail[-3000:]}\n```\n\n{text}")
            decision({"card": label, "title": title, "intent": sb["intent"], "result": sb["result"],
                      "script": why, "sandbox": head.get("id"), "native_exit": 0, "renamed": renamed})
            log(f"DOCKER-AUTOPASS {label}: {sb['intent'][:80]} - {sb['result'][:80]} (applied, no PIN)")
            kept = [l for l in lines if not l.startswith("NEEDS_JAKE:")]
            return "\n".join(kept + [f"Tested in Docker sandbox: PASS. Applied on Windows by the Worker "
                                     f"({os.path.basename(why)}, exit 0) under Jake's medium-risk rule - no PIN needed."])
        why = f"Docker PASS but the Windows run of {os.path.basename(why)} failed (exit {code}): {tail[-300:]}"
        log(f"sandbox gate {label}: {why[:200]}")
    verdict = (head.get("verdict") or "?")
    if verdict == "PASS" and head.get("windows_only") == "yes":
        prefix = "Tested in Docker sandbox: PASS, but the Windows-only part was not executed"
    elif verdict == "PASS":
        prefix = "Tested in Docker sandbox: PASS"
    else:
        prefix = f"Docker sandbox FAIL ({(sb or {}).get('result') or 'see sandbox-result.md'})"
    attach(c, f"**{prefix}.** Still needs Jake: {why}.\n\n{text}")
    log(f"sandbox gate {label}: {prefix} - needs Jake ({why[:120]})")
    if needs:
        out, done = [], False
        for l in lines:
            if l.startswith("NEEDS_JAKE:") and not done:
                l = f"NEEDS_JAKE: {prefix} - {l.split(':', 1)[1].strip()}"
                done = True
            out.append(l)
        return "\n".join(out)
    return output + f"\n{prefix}."


def _report_sections(now, hours=13):
    pages = ORIG["report_sections"](now, hours)
    rows = []
    try:
        since = (now.astimezone() if now.tzinfo else now.astimezone()).timestamp() - 24 * 3600
        with open(DECISIONS, encoding="utf-8") as f:
            for l in f:
                try:
                    d = json.loads(l)
                    if d.get("kind") == "docker-tested-medium" and \
                            datetime.fromisoformat(d["at"]).timestamp() >= since:
                        rows.append(f"{d['at'][11:16]} {d.get('card')}: {d.get('intent')} - {d.get('result')}")
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    for key in pages:
        if key.startswith("1"):
            pages[key].insert(3, ("Docker-tested and applied without a PIN (last 24 h, medium risk)", rows or ["None."]))
            break
    return pages


def install(g):
    """Called from the end of agent.py with globals(). Safe to call twice."""
    if g.get("_SANDBOX_GATE_ON"):
        return
    AG.clear()
    AG.update(g)
    globals()["AG"] = g   # live view: ghq is set later by load_ghq()
    if "DOCKER SANDBOX" not in g["PROMPT"]:
        g["PROMPT"] = g["PROMPT"].replace("{extra}", PROMPT_RULE + "{extra}")
    ORIG["finish"] = g["finish"]
    g["finish"] = _finish
    if "report_sections" in g:
        ORIG["report_sections"] = g["report_sections"]
        g["report_sections"] = _report_sections
    g["_SANDBOX_GATE_ON"] = True
    g["log"](f"sandbox gate on: code/install/medium+ cards tested in Docker ({SANDBOX}); medium + clean PASS "
             f"applies without a PIN (Jake 10/2)")


def _selftest():
    lines = ["x", "SANDBOX: PASS | risk=medium | run=apply.py | intent=Adds a disk check | result=ran cleanly"]
    d = parse_sandbox_line(lines)
    assert d == {"verdict": "PASS", "risk": "MEDIUM", "run": "apply.py", "intent": "Adds a disk check",
                 "result": "ran cleanly"}, d
    assert parse_sandbox_line(["SANDBOX: FAIL | risk=HIGH | run=none"])["run"] == ""
    assert pin_hits("buy a subscription to the paid tier") and pin_hits("merge the branch into main")
    assert not pin_hits("adds a disk-space check to the 7 AM report")
    PROMPT_RULE.format()   # must survive PROMPT.format (no stray braces)
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:2] == ["selftest"]:
        _selftest()
