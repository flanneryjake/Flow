"""Local-model lane for the Jarvis Worker (Jake 10/02: routine cards go to the local model FIRST, Claude is saved
for what needs it).

Hook at the end of agent.py, after the budget guard:

    try:   # 10/2 local-model lane
        sys.path.insert(0, HERE); import local_lane; local_lane.install(globals())
    except Exception as _e:
        log(f"local lane not loaded: {_e}")

install() wraps agent.py's run_card(). For each card:
  route(c) -> "local" only when ALL hold:
    - guardrail tier (C:\\Jarvis\\autonomy\\classify.py) is free or free_logged
    - the card is plain text work: drafting, summarizing, tagging, reformatting, templates, lists, outlines
    - it needs no code, tools, files, web/research, accounts or judgment calls (TOOLING / NEEDS_CLAUDE below)
    - it is not clinical / safety / private (those stay on Claude, with their existing review + 988 rules)
  Local run: the rig's Ollama (LOCAL_MODEL) writes the deliverable to <card dir>\\local-draft.md, labelled
  "Drafted by local model <name>". A basic check (not empty, not cut off, on topic) must pass, else the card falls
  back to Claude in the same pass. Off switch: user env JARVIS_LOCAL_LANE=off.

CLI (from this folder): python local_lane.py selftest | survey
Stdlib only.
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

AG = {}
ORIG = {}
LOCAL_MODEL = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3.6:35b")
AUTONOMY_DIR = r"C:\Jarvis\autonomy"

TEXT_WORK = re.compile(
    r"\b(draft|write|rewrite|summar\w*|outline|template\w*|tag(?:s|ging)?|reformat\w*|format as|list of|"
    r"checklist|question bank|questions?|prompts?|faq|glossary|copy|caption\w*|title ideas|names? for|"
    r"description\w*|blurb|bullet\w*|letter|email text|script for a video|lesson|worksheet|guide text)\b", re.I)
TOOLING = re.compile(
    r"\b(install\w*|uninstall|script(?!\s+for a video)|code|coding|fix|bug|debug|config\w*|server|deploy\w*|"
    r"wire|patch|service|api|endpoint|run(?:s|ning)?\b|test\w*|selftest|benchmark\w*|build\w*|compile|"
    r"python|powershell|\.py|\.bat|\.ps1|\.json|\.ya?ml|task scheduler|scheduled task|restart\w*|reboot|"
    r"printer|gcode|ollama|docker|tailscale|hub|agent\.py|worker|github|notion|repo|commit|merge|pr\b|"
    r"database|sheet|spreadsheet|csv|xlsx|folder|file\b|files\b|path|scan\w*|"
    r"connector|oauth|login|account|sign[- ]?up|password|token|key\b)", re.I)
NEEDS_CLAUDE = re.compile(
    r"\b(research\w*|look up|lookup|search|browse|web|website|url|https?://|verify|fact[- ]?check|check\w*|"
    r"compare|investigate|find out|confirm|decide|decision|review|audit|diagnos\w*|why\b|figure out|"
    r"price\w*|cost\w*|legal|attorney|tax\w*|repl(?:y|ies)|respond|send|message|landlord|customer)", re.I)
CLINICAL_SAFETY = re.compile(
    r"clinical|fieldwork|curriculum|patient|client|counsel\w*|therap\w*|diagnos|treatment|symptom|medication|"
    r"suicid\w*|self[- ]?harm|crisis|988|overdose|naloxone|abuse|safety plan|\bDSM\b|ICD-?10|private|personal|"
    r"financ\w*|bank (?:account|statement|csv)|banking|budget|\bbills?\b", re.I)
PATHLIKE = re.compile(r"[A-Za-z]:\\|\\\\|~/|/[a-z]+/[a-z]")

SYSTEM = """You are Jarvis, Jake's local writing model on his rig. Do the card's text task completely and directly.
- Output only the finished deliverable in clean Markdown, starting with "# <short title>".
- No preamble ("Sure", "Here is"), no questions back, no promises to do more later.
- Never invent facts, prices, links, citations, statistics or people. Where a fact is needed and you don't know it,
  write [UNVERIFIED: what to check] in place of the fact.
- Keep it practical and specific to the card. Finish every section you start."""


def log(msg):
    (AG.get("log") or print)(msg)


def _env_off():
    v = os.environ.get("JARVIS_LOCAL_LANE", "")
    if not v:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = str(winreg.QueryValueEx(k, "JARVIS_LOCAL_LANE")[0])
        except Exception:
            v = ""
    return v.strip().lower() in ("off", "0", "false", "no")


def _tier(title, notes):
    try:
        if AUTONOMY_DIR not in sys.path:
            sys.path.insert(0, AUTONOMY_DIR)
        from classify import classify
        return classify(title, notes)[0]
    except Exception:
        return "unknown"


def route(c):
    """('local' | 'claude', reason). Pure apart from classify()."""
    title = c.get("task") or ""
    notes = c.get("notes") or ""
    text = f"{title}\n{notes}"
    if CLINICAL_SAFETY.search(text):
        return "claude", "clinical/safety/private"
    tier = _tier(title, notes)
    if tier not in ("free", "free_logged"):
        return "claude", f"tier {tier}"
    if not TEXT_WORK.search(title):
        return "claude", "title isn't a text task"
    m = TOOLING.search(text)
    if m:
        return "claude", f"needs tools ({m.group(0)})"
    m = NEEDS_CLAUDE.search(text)
    if m:
        return "claude", f"needs judgment/web ({m.group(0)})"
    if PATHLIKE.search(notes):
        return "claude", "refers to files"
    if len(notes) > 6000:
        return "claude", "long brief"
    return "local", "text-only, free tier"


def _keywords(s):
    stop = {"the", "and", "for", "with", "from", "into", "that", "this", "your", "jake", "draft", "write", "make",
            "list", "about", "card", "more", "each", "into", "some", "over", "them", "they", "what", "when"}
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in stop}


def check(c, text, done_reason="stop"):
    """None if the local output is usable, else why not."""
    t = (text or "").strip()
    if len(t) < 300:
        return "empty or too short"
    if done_reason and done_reason != "stop":
        return f"cut off ({done_reason})"
    if re.search(r"\b(the|a|an|and|or|of|to|with|for|in|on|is|are|but|that)\s*$", t, re.I):
        return "ends mid-sentence"
    want = _keywords(c.get("task") or "")
    if want and len(want & _keywords(t)) < max(1, min(2, len(want) // 2)):
        return "off topic"
    if re.match(r"(?i)\s*(i can't|i cannot|i'm unable|as an ai)", t):
        return "refused"
    return None


def generate(prompt, timeout=900):
    brain = AG.get("BRAIN") or "http://127.0.0.1:11434"
    body = json.dumps({"model": LOCAL_MODEL, "system": SYSTEM, "prompt": prompt, "stream": False, "think": False,
                       "options": {"num_ctx": 16384, "num_predict": 4096, "temperature": 0.4}}).encode()
    req = urllib.request.Request(brain + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return d.get("response", ""), d.get("done_reason", "stop")


def run_local(c):
    """Returns (output, out_file) like run_card, or None to fall back to Claude."""
    card_dir, LOG_DIR, STATE = AG["card_dir"], AG["LOG_DIR"], AG["STATE"]
    d = card_dir(c)
    status_md = os.path.join(d, "STATUS.md")
    prev = ""
    if os.path.exists(status_md):
        with open(status_md, encoding="utf-8", errors="replace") as f:
            prev = f.read()[:4000]
    prompt = (f"CARD: {c['task']}\nPRIORITY: {c.get('priority') or '-'}   PROJECT: {c.get('project') or '-'}\n"
              f"NOTES FROM THE CARD:\n{(c.get('notes') or '(none)')[:6000]}\n"
              + (f"\nEARLIER PROGRESS (STATUS.md):\n{prev}\n" if prev else ""))
    log(f"LOCAL-LANE: {LOCAL_MODEL} on: {c['task'][:80]}")
    t0 = time.time()
    STATE["run_t0"] = t0
    try:
        text, done_reason = generate(prompt)
    except Exception as e:
        log(f"LOCAL-LANE: model call failed ({e}) - Claude takes it")
        return None
    why = check(c, text, done_reason)
    if why:
        log(f"LOCAL-LANE: check failed ({why}) - Claude takes it")
        try:
            with open(os.path.join(d, "local-draft.rejected.md"), "w", encoding="utf-8") as f:
                f.write(f"<!-- rejected: {why} -->\n{text}")
        except OSError:
            pass
        return None
    label = f"Drafted by local model {LOCAL_MODEL} (rig), {datetime.now():%m/%d %H:%M}. Not reviewed by Claude."
    out_md = os.path.join(d, "local-draft.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(f"_{label}_\n\n{text.strip()}\n")
    mins = (time.time() - t0) / 60
    output = (f"{label}\nDeliverable: {out_md} ({len(text)} chars, {mins:.1f} min, no Claude usage).\n\n"
              + "\n".join(text.strip().splitlines()[:8]))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = os.path.join(LOG_DIR, f"task-{stamp}-local.txt")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(output + "\n\n" + text)
    STATE.update(last_rc=0, last_stderr="", run_model=f"local:{LOCAL_MODEL}")
    try:
        AG["usage_row"](c, f"local:{LOCAL_MODEL}", t0, "done")
    except Exception:
        pass
    STATE["local_lane_runs"] = STATE.get("local_lane_runs", 0) + 1
    log(f"LOCAL-LANE: done in {mins:.1f} min -> {out_md}")
    return output, out_file


def _run_card(c):
    if not _env_off():
        lane, why = route(c)
        if lane == "local":
            r = run_local(c)
            if r:
                return r
        else:
            log(f"LOCAL-LANE: Claude for '{c['task'][:60]}' ({why})")
    AG["STATE"].pop("run_model", None)
    return ORIG["run_card"](c)


def install(g):
    AG.clear()
    AG.update({k: g[k] for k in ("log", "STATE", "card_dir", "LOG_DIR", "BRAIN", "usage_row") if k in g})
    ORIG["run_card"] = g["run_card"]
    g["run_card"] = _run_card
    log(f"local lane on: text-only free-tier cards -> {LOCAL_MODEL} first, Claude fallback"
        + (" (OFF by JARVIS_LOCAL_LANE)" if _env_off() else ""))


# ------------------------------------------------------------------ self-test / survey

def selftest():
    cases = [
        ({"task": "Draft 10 Etsy listing descriptions for printable meal planners", "notes": ""}, "local"),
        ({"task": "Write a question bank of 50 meal-preference questions", "notes": "Group by section."}, "local"),
        ({"task": "Summarize the Jarvis autonomy goals into a one-page outline", "notes": ""}, "local"),
        ({"task": "Fix the hub's System checks after the homebase move", "notes": ""}, "claude"),
        ({"task": "Draft 10 counseling templates with the local model", "notes": ""}, "claude"),
        ({"task": "Write the install script for Docker", "notes": ""}, "claude"),
        ({"task": "Research passive income options", "notes": ""}, "claude"),
        ({"task": "Draft a reply to the landlord", "notes": "Send Dana the proposal"}, "claude"),
        ({"task": "Write a summary of C:\\Jarvis\\idle\\out\\notes.md", "notes": "read C:\\Jarvis\\x.md"}, "claude"),
        ({"task": "Draft a crisis resource card", "notes": "include 988"}, "claude"),
    ]
    bad = 0
    for c, want in cases:
        got, why = route(c)
        bad += got != want
        print("PASS" if got == want else "FAIL", f"{got:6}", c["task"][:60], f"({why})")
    good = "# Meal planner listings\n\n" + "Printable meal planner listing with weekly grid and shopping list. " * 8 + "\n"
    for text, reason, want_ok in [(good, "stop", True), ("short", "stop", False), (good + "and then the", "stop", False),
                                  (good, "length", False), ("# Cats\n\n" + "Cats are nice animals indeed. " * 20, "stop", False)]:
        ok = check({"task": "Draft 10 Etsy listing descriptions for printable meal planners"}, text, reason) is None
        bad += ok != want_ok
        print("PASS" if ok == want_ok else "FAIL", "check", repr(text[-30:]), reason, "->", ok)
    print("ALL PASS" if not bad else f"{bad} FAILED")
    return bad


def survey(machine="rig"):
    sys.path.insert(0, r"C:\Jarvis\ghq")
    import ghq
    from collections import Counter
    issues = [i for i in ghq.paged(ghq.repo_path("/issues?state=open&labels=status:approved&per_page=100"))
              if not i.get("pull_request")]
    mine = [i for i in issues if ({"machine:any", f"machine:{machine}"} & set(ghq.label_names(i)))
            and not any(n.startswith("claimed:") for n in ghq.label_names(i))]
    split, reasons, sample = Counter(), Counter(), []
    for i in mine:
        lane, why = route({"task": i["title"], "notes": i.get("body") or ""})
        split[lane] += 1
        reasons[why.split(" (")[0]] += 1
        if lane == "local":
            sample.append(f"#{i['number']} {i['title'][:80]}")
    print(f"{len(mine)} approved cards for {machine}: local {split['local']}, Claude {split['claude']}")
    for k, v in reasons.most_common():
        print(f"  {v:4}  {k}")
    print("local examples:")
    for s in sample[:15]:
        print("  " + s)


if __name__ == "__main__":
    if sys.argv[1:2] == ["selftest"]:
        sys.exit(selftest())
    if sys.argv[1:2] == ["survey"]:
        survey(*(sys.argv[2:3] or ["rig"]))
