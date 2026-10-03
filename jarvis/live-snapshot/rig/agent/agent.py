#!/usr/bin/env python3
"""Jarvis Agent - runs on every machine so they can hand each other work without Jake.

What it does
  * Every 15 min it asks Notion: "any APPROVED task, Owner=Claude, for this machine?"
    If yes, it claims the card (Status -> In progress, Claimed by -> this machine),
    runs Claude Code headless on it, writes the result to "Agent log", and sets
    Status -> Done (or back to Staged with a question if Jake is needed, which the
    phone app / boot screener then puts in front of him).
  * Homebase (always on) is the coordinator: 4x a day it routes approved tasks that
    have no Machine set (using the rig's local LLM when the rig is awake), and wakes
    the rig / laptop when they have approved work waiting.
  * Any device can poke any machine instantly over Tailscale:
        https://<machine>.tail3bbcb8.ts.net:8443/check        -> check Notion now
        https://<machine>.tail3bbcb8.ts.net:8443/run/<page>   -> run one approved card now
        https://<machine>.tail3bbcb8.ts.net:8443/status
    or from a terminal:  python agent.py tell homebase check

Guardrails: only cards with Status=Approved are ever executed. Claude Code is told
not to spend money, create accounts, or handle credentials, and to stop and ask
(NEEDS_JAKE) instead of guessing.

Stdlib only. Local port 127.0.0.1:8790, published tailnet-only by `tailscale serve`.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, "JarvisAgent")
WORK_DIR = os.path.join(STATE_DIR, "work")
LOG_DIR = os.path.join(STATE_DIR, "logs")
for d in (STATE_DIR, WORK_DIR, LOG_DIR):
    os.makedirs(d, exist_ok=True)
LOG = os.path.join(LOG_DIR, "agent.log")

OWNER_LOGIN = "flanneryjake240@gmail.com"
TAILNET = "tail3bbcb8.ts.net"
DB = "7c1c59e9-2764-4dfb-a461-c88a67dbd32c"
NOTION_VER = "2022-06-28"
BRAIN = "http://100.96.134.64:11434"
PORT = 8790
POLL_MIN = 15
ROUTE_TIMES = ("08:30", "12:30", "16:30", "20:30")  # homebase coordinator runs
TASK_TIMEOUT_MIN = 40          # one checkpointed pass; long cards continue on the next cycle (Worker card s.7)
MAX_PASSES_PER_NIGHT = 12
MAX_PER_POLL = int(os.environ.get("JARVIS_MAX_PER_POLL", "1"))   # protects Jake's Claude usage
MAX_PER_DAY = int(os.environ.get("JARVIS_MAX_PER_DAY", "0"))     # 0 = no daily cap (card 9/29 s.2): run while cards exist,
                                                                 # stop only on a Claude usage/session limit (_block), resume after reset
LIGHT_MODEL = os.environ.get("JARVIS_LIGHT_MODEL", "sonnet")   # 9/29 rc: P2 / no-priority cards run on this model to
                                                                # stretch Claude usage; P0/P1 keep the default. "" = off
FOLLOW_ON_MIN = 1     # when a card ran and more are waiting, poll again after 1 min instead of POLL_MIN
WAKE = threading.Event()   # set when any poll (incl. /check) ran a card and more are waiting: scheduler skips its sleep
STALE_CLAIM_H = 2     # In progress + Claimed by older than this (and not running here) -> back to Approved
HEARTBEAT = r"C:\Jarvis\worker.heartbeat"
JOBS_DIR = r"C:\Jarvis\jobs"
RIG_PROJECTS = r"C:\Users\Jake\Desktop\Claude"   # rig only (Fieldwork Clinical etc.); skipped where it doesn't exist
IDLE_DIR = r"C:\Jarvis\idle"
IDLE_AFTER_MIN = 15
IDLE_MAX_NIGHT_MIN = 120
# 9/30 overnight plan (Jake): rig drafts, Gemini researches/proofreads, Claude only checks and finishes.
GEMINI = r"C:\Jarvis\helper\gemini_helper.py"      # exit 0 ok, 2 no key, 3 failed -> fall back to Claude
PY = sys.executable.replace("pythonw.exe", "python.exe")
USAGE_CSV = os.path.join(LOG_DIR, "claude-usage.csv")   # per-card Claude minutes/runs for the 7 AM summary
NEEDS_JAKE_FILE = os.path.join(STATE_DIR, "needs-jake.json")   # card id -> Notion edit time when we parked it
RIG_DRAFT_CARDS = {"3ea11c3639af8192ba82d16a94c96b63"}   # FULL BUILD curriculum: rig drafts, Claude reviews
RIG_DRAFT_MIN = 20                                  # short Claude review/fix pass for rig-drafted cards
RIG_DRAFT_MAX_PASSES = 4                            # per night (instead of MAX_PASSES_PER_NIGHT)
LIGHT_POLL_SEC = 60                                 # new Approved card is picked up within ~1 min
OFFHOURS_EVERY_MIN = 20                             # while Claude is out of usage: one Gemini/rig backlog job per 20 min
# 9/30 FIX NOW (rig runs cards directly): while Claude is out, approved cards go to the rig worker as drafting jobs
RIG_CARD_MAX_QUEUED = 3                             # card jobs waiting in C:\Jarvis\jobs\in at once
RIG_CARD_REDRAFT_H = 20                             # a card gets a fresh rig draft at most this often
RIG_CARDS_FILE = os.path.join(STATE_DIR, "rig-card-jobs.json")   # job name -> card, queued, output, done
RIG_RULES_FILE = r"C:\Jarvis\worker\rig-job-rules.md"
COMMAND_CENTER = "3e611c36-39af-816d-b900-e056b36dce4f"   # Jarvis Command Center page (overnight reports)
# Unattended runs: Notion read/report tools allowed, anything that sends, posts, deletes or buys is denied.
# No notion-update-page: connector edits show up as Jake's Notion user, so an unattended run could "approve" its
# own card past the gate. The agent writes Agent log/Status itself with its bot token; runs report via comments.
NOTION_ALLOW = ["mcp__claude_ai_Notion__notion-fetch", "mcp__claude_ai_Notion__notion-search",
                "mcp__claude_ai_Notion__notion-query-data-sources",
                "mcp__claude_ai_Notion__notion-get-comments", "mcp__claude_ai_Notion__notion-create-comment"]
SHELL_ALLOW = ["Bash(python *)", "Bash(ollama *)", "Bash(ffmpeg *)", "Bash(tailscale status*)", "Bash(ping *)",
               "PowerShell(python *)", "PowerShell(ollama *)", "PowerShell(ffmpeg *)", "PowerShell(tailscale status*)",
               "PowerShell(Test-NetConnection *)", "PowerShell(Resolve-DnsName *)", "PowerShell(ping *)",
               # card #592 (Jake 10/02): run the card's own .py/.bat/.ps1/.cmd; worker_script_guard.py (PreToolUse,
               # WORKER_SETTINGS) refuses any script outside JARVIS_CARD_DIR, elevation, installs, download+run
               "PowerShell(py *)", "PowerShell(cmd *)", "PowerShell(powershell *)", "PowerShell(& *)",
               "PowerShell(.\\*)"]
WORKER_SETTINGS = os.path.join(os.path.expanduser("~"), "JarvisAgent", "worker-settings.json")
DENY = ["mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Gmail__reply", "mcp__claude_ai_Gmail__forward",
        "mcp__claude_ai_Gmail__trash_message", "mcp__claude_ai_Gmail__trash_thread",
        "mcp__claude_ai_Slack__slack_send_message", "mcp__claude_ai_Slack__slack_schedule_message",
        "mcp__claude_ai_Google_Drive__trash_file", "mcp__claude_ai_Google_Drive__share_file",
        "mcp__claude_ai_Google_Calendar__delete_event", "mcp__claude_ai_Notion__notion-move-pages",
        "mcp__claude_ai_Notion__notion-delete", "mcp__claude_ai_Notion__notion-update-page",
        "mcp__claude_ai_Notion__notion-create-pages", "mcp__claude_ai_Shopify__*", "mcp__claude_ai_Instacart__*",
        "Bash(rm *)", "Bash(del *)", "PowerShell(Remove-Item *)", "PowerShell(del *)", "PowerShell(rd *)"]

MACHINES = {  # name -> (Windows computer name, tailscale IP)
    "rig": ("DESKTOP-VLLDDM4", "100.96.134.64"),
    "laptop": ("LAPTOP-4150EGRS", "100.85.255.99"),
    "homebase": ("LAPTOP-4150EGRS", "100.85.255.99"),   # 10/1 cutover: the 5060 is homebase (was DESKTOP-5VE3C77)
}
WAKER = "http://100.85.255.99:8765"
HUB = "https://laptop-4150egrs.tail3bbcb8.ts.net"  # approval authority (Jake's PIN)
HUB_LOCAL = "http://127.0.0.1:8770"


def me():
    if os.environ.get("JARVIS_MACHINE"):
        return os.environ["JARVIS_MACHINE"]
    cn = (os.environ.get("COMPUTERNAME") or socket.gethostname()).upper()
    for name, (host, _) in MACHINES.items():
        if host == cn:
            return name
    return cn.lower()


ME = me()
LOCK = threading.Lock()
STATE = {"machine": ME, "started": datetime.now().isoformat(timespec="seconds"), "boot": time.time(),
         "last_poll": None, "running": None, "done_today": 0, "last_error": None}


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{ME}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ------------------------------------------------------------------ Notion

_token = None


def token():
    global _token
    if _token:
        return _token
    if os.environ.get("NOTION_TOKEN"):
        _token = os.environ["NOTION_TOKEN"]
        return _token
    f = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Jarvis", "notion.token")
    if not os.path.exists(f):
        raise RuntimeError("No Notion token on this machine")
    ps = ("$s = Get-Content '%s' | ConvertTo-SecureString; "
          "[Runtime.InteropServices.Marshal]::PtrToStringBSTR("
          "[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))") % f
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _token = out.stdout.strip()
    if not _token:
        raise RuntimeError("Could not decrypt the Notion token")
    return _token


def notion(method, path, body=None):
    req = urllib.request.Request(
        "https://api.notion.com/v1/" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token(), "Notion-Version": NOTION_VER,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def txt(p):
    if not p:
        return ""
    t = p.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in p[t])
    if t == "select":
        return (p["select"] or {}).get("name", "")
    if t == "date":
        return (p["date"] or {}).get("start", "")
    return ""


def card(p):
    pr = p["properties"]
    return {k.lower().replace(" ", "_"): txt(pr.get(k)) for k in
            ("Task", "Status", "Owner", "Priority", "Project", "Machine", "Notes", "Claimed by", "Type", "Approval code")} | \
        {"id": p["id"], "url": p["url"], "edited": p.get("last_edited_time", "")}


def query(flt):
    out, cursor = [], None
    while True:
        body = {"page_size": 100, "filter": flt}
        if cursor:
            body["start_cursor"] = cursor
        r = notion("POST", f"databases/{DB}/query", body)
        out += [card(p) for p in r["results"]]
        if not r.get("has_more"):
            return out
        cursor = r["next_cursor"]


def rich(s):
    s = (s or "")[:1990]
    return {"rich_text": [{"type": "text", "text": {"content": s}}] if s else []}


def update(page_id, **props):
    p = {}
    for k, v in props.items():
        name = {"status": "Status", "machine": "Machine", "claimed_by": "Claimed by",
                "agent_log": "Agent log", "notes": "Notes"}[k]
        p[name] = {"select": {"name": v}} if name in ("Status", "Machine") else rich(v)
    return notion("PATCH", f"pages/{page_id}", {"properties": p})


# NEEDS JAKE guard (9/30): a card the Worker parked on NEEDS JAKE is never re-dispatched until someone else edits it
# after the park (the Worker itself doesn't touch a parked card, so any later edit is Jake / his hub approval).
def _nj_load():
    try:
        with open(NEEDS_JAKE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def mark_needs_jake(cid, edited):
    d = _nj_load()
    d[cid.replace("-", "")] = edited or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with open(NEEDS_JAKE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=0)


def needs_jake_blocked(c):
    """True = still waiting on Jake. Legacy cards (no record) fall back to the Notes prefix rule."""
    if c.get("number"):
        return False   # GitHub: status:needs-jake cards never show up in ghq.ready()
    d = _nj_load()
    k = c["id"].replace("-", "")
    if k not in d:
        return (c["notes"] or "").startswith("NEEDS JAKE:")
    parked = datetime.strptime(d[k][:19], "%Y-%m-%dT%H:%M:%S")
    try:
        edited = datetime.strptime((c.get("edited") or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return True
    if (edited - parked).total_seconds() > 90:   # edited by Jake after we parked it -> runnable again
        d.pop(k)
        with open(NEEDS_JAKE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=0)
        log(f"NEEDS JAKE cleared by an edit after the park: {c['task'][:60]}")
        return False
    return True


def _norm_title(t):
    import re
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t.lower()).split())


def find_open_duplicate(title):
    """An open card (not Done) with the same or nearly the same title, on any machine."""
    import difflib
    words = [w for w in _norm_title(title).split() if len(w) > 3][:3]
    if not words:
        return None
    flt = {"and": [{"property": "Status", "select": {"does_not_equal": "Done"}}] +
           [{"property": "Task", "title": {"contains": w}} for w in words[:1]]}
    want = _norm_title(title)
    for c in query(flt):
        if difflib.SequenceMatcher(None, want, _norm_title(c["task"])).ratio() >= 0.8:
            return c
    return None


def create_card(title, notes, parent_id, machine=None, owner="Jake", status="Inbox"):
    """Follow-ups go to Jake's Inbox (Approved means a machine can run it, and an AI can't approve its own work).
    If an open card with a near-identical title exists, comment there instead of making a duplicate."""
    dup = find_open_duplicate(title)
    if dup:
        notion("POST", "comments", {"parent": {"page_id": dup["id"]}, "rich_text": [{"type": "text", "text": {
            "content": f"{ME} {datetime.now():%m/%d %H:%M}: same follow-up raised again. {notes}"[:1900]}}]})
        log(f"follow-up '{title}' already open - commented on it")
        return
    props = {"Task": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
             "Status": {"select": {"name": status}}, "Owner": {"select": {"name": owner}},
             "Type": {"select": {"name": "Idea"}}, "Notes": rich(notes)}
    if parent_id:
        props["Spawned from"] = {"relation": [{"id": parent_id}]}
    if machine:
        props["Machine"] = {"select": {"name": machine}}
    notion("POST", "pages", {"parent": {"database_id": DB}, "properties": props})


def hub_verify(c):
    """Ask the hub's approval gate whether this card may run: approved by Jake in Notion (no money/publish/
    email/delete), or signed with his PIN. Returns (ok, paused, why). Fail closed."""
    if c.get("number"):   # GitHub: status:approved is the approval (the hub asks the PIN before approving a pin card)
        return True, hub_paused(), ""
    from urllib.parse import quote
    base = HUB_LOCAL if ME == "homebase" else HUB
    try:
        with urllib.request.urlopen(f"{base}/api/verify/{c['id']}?code={quote(c.get('approval_code') or '')}", timeout=30) as r:
            v = json.loads(r.read())
        return bool(v.get("ok")), bool(v.get("paused")), v.get("why", "")
    except Exception as e:
        log(f"hub verify failed ({e}) - not running anything")
        return False, True, f"hub unreachable: {e}"   # FAIL CLOSED = paused (Jake chose "Restore pause" 10/02 02:09 UTC)


# ------------------------------------------------------------------ queue: GitHub Issues (9/30 switch, Flow PR #7)
# JARVIS_QUEUE=github (default) | notion. GitHub cards look like Notion cards to the rest of this file, with
# id "gh-<n>" and "number" set; every Notion write has a GitHub branch (claim / log_run / ask_jake / new_card).
def _user_env(name, default=""):
    """Process env first, then the user's registry value: a Worker started from a shortcut whose parent (Explorer)
    predates a setx doesn't see it (10/1 17:16 restart came up on the Notion queue that way)."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            os.environ[name] = str(winreg.QueryValueEx(k, name)[0])
            return os.environ[name]
    except Exception:
        return default


QUEUE = _user_env("JARVIS_QUEUE", "notion").strip().lower()   # rig switched 10/1 (Jake): user env JARVIS_QUEUE=github
for _v in ("JARVIS_AUTO_PER_DAY", "JARVIS_AUTO_DEPTH", "JARVIS_AUTO_OFF",   # autotask reads these from os.environ
           "JARVIS_BUDGET_OFF", "JARVIS_BUDGET_UNITS"):   # budget_guard: 10/2 estimate-hold off (it learned from false hits)
    _user_env(_v)
GHQ_DIR = r"C:\Jarvis\ghq"
AUTONOMY_DIR = r"C:\Jarvis\autonomy"   # classify.py + autotask.py (Flow 84c09a0)
ghq = None


def gh_token_env():
    """The token is a user env var; a Worker started before it was set doesn't have it, so read the registry too."""
    if not os.environ.get("GITHUB_TASKS_TOKEN"):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                os.environ["GITHUB_TASKS_TOKEN"] = winreg.QueryValueEx(k, "GITHUB_TASKS_TOKEN")[0]
        except Exception:
            pass


def load_ghq():
    """Import C:\\Jarvis\\ghq\\ghq.py when JARVIS_QUEUE=github. Returns True when the GitHub queue is on."""
    global ghq
    if QUEUE != "github":
        return False
    gh_token_env()
    try:
        if GHQ_DIR not in sys.path:
            sys.path.insert(0, GHQ_DIR)
        import ghq as g
        g._token()
        ghq = g
        return True
    except Exception as e:
        log(f"GitHub queue unavailable ({e}) - falling back to Notion")
        notify(f"Worker on {ME}: GitHub queue unavailable", f"{e} - using Notion until fixed")
        return False


def gh():
    return ghq is not None


def file_followup(title, why, c):
    """FOLLOWUP lines (10/1 autonomy lane): autotask.propose() files a GitHub card for this machine - routine ->
    status:approved + auto label + comment, pin -> staged + pin label, ask_once -> staged. Any error -> the Notion
    Inbox card as before, never auto. Duplicates / loop meta-cards are skipped by autotask + ghq (Flow 4e6085d)."""
    try:
        gh_token_env()
        if AUTONOMY_DIR not in sys.path:
            sys.path.insert(0, AUTONOMY_DIR)
        # Jake 10/02 01:11 UTC chose "auto on, cap 100" (supersedes 10/01 21:12 "stage follow-ups"); cap = JARVIS_AUTO_PER_DAY
        import autotask
        n, status, reason = autotask.propose(title[:200], f"{why}\n\nFollow-up from {c['url']}", machine=ME,
                                             spawned_from=c.get("number"), source=f"the {ME} Worker")
        if status == "dropped":
            log(f"FOLLOWUP-DEDUPE: dropped '{title[:80]}' ({reason})")
            return
        if status == "duplicate":
            log(f"FOLLOWUP-DEDUPE: skipped '{title[:80]}' (dup of #{n})")
            if c.get("number"):
                try:
                    ghq.comment(n, f"Also requested by #{c['number']}")
                except Exception as e:
                    log(f"follow-up dedupe comment on #{n} failed: {e}")
            return
        log(f"follow-up #{n} {status}: {title[:60]} ({reason[:120]})")
    except Exception as e:
        # 10/2: Notion is retired - no Inbox fallback; just say it failed
        log(f"FOLLOWUP-FAILED: {title[:120]} ({e})")


def gh_card(i):
    """GitHub issue (full, or slim from ghq.ready) -> the card dict the rest of the agent uses."""
    names = ghq.label_names(i) if isinstance((i.get("labels") or [None])[0], dict) else list(i.get("labels") or [])
    pick = lambda pre: next((n.split(":", 1)[1] for n in names if n.startswith(pre)), "")
    return {"id": f"gh-{i['number']}", "number": i["number"], "task": i["title"],
            "priority": next((n.upper() for n in names if n in ("p0", "p1", "p2")), ""),
            "project": pick("project:"), "machine": pick("machine:") or "any", "type": pick("type:"),
            "notes": i.get("body") or "", "url": f"https://github.com/{ghq.REPO}/issues/{i['number']}",
            "status": "Approved", "owner": "Jake" if "owner:jake" in names else "Claude", "claimed_by": "",
            "approval_code": "", "edited": i.get("updated_at", ""), "pin": "pin" in names, "labels": names}


def gh_full(c):
    """Fill in the issue body (ghq.ready's slim list has none)."""
    if c.get("number") and not c.get("notes"):
        c.update(gh_card(ghq.api("GET", ghq.repo_path(f"/issues/{c['number']}"))))
    return c


def gh_log(c, outcome, summary="", tail=""):
    try:
        return ghq.log_run(c["number"], ME, outcome, started=STATE.get("gh_started"), summary=summary[:3000],
                           log_tail=tail, model=STATE.get("run_model") or "")
    except Exception as e:
        log(f"github log_run #{c['number']} {outcome}: {e}")


PAUSE_FLAG = r"C:\Jarvis\paused.flag"
_HUB_DOWN = {"logged": False}


def hub_paused():
    """Paused if C:\\Jarvis\\paused.flag exists, the hub answers paused, OR the hub can't be reached.
    DO NOT CHANGE TO FAIL-OPEN: Jake chose "Restore pause" on 10/02 02:09 UTC after a 22:02 edit made an unreachable
    hub count as not paused. An unreachable hub = paused for ALL cards, GitHub included. paused.flag is an extra
    local kill switch on top of that."""
    if os.path.exists(PAUSE_FLAG):
        return True
    try:
        with urllib.request.urlopen((HUB_LOCAL if ME == "homebase" else HUB) + "/api/me", timeout=15) as r:
            paused = bool(json.loads(r.read()).get("paused"))
        if _HUB_DOWN["logged"]:
            log("hub reachable again")
            _HUB_DOWN["logged"] = False
        return paused
    except Exception as e:
        if not _HUB_DOWN["logged"]:   # one line per change, not per poll
            log(f"HUB-UNREACHABLE, treating as PAUSED - not running anything ({e.__class__.__name__})")
            _HUB_DOWN["logged"] = True
        return True


def gh_unstick():
    """A card still labelled claimed:<me> that isn't running here lost its result (agent restarted mid-run)."""
    for i in ghq.paged(ghq.repo_path(f"/issues?state=open&labels=claimed:{ME}")):
        if STATE.get("running_number") == i["number"]:
            continue
        STATE["gh_started"] = None
        gh_log({"number": i["number"]}, "released", summary=f"{ME}: stale claim cleared - the run's result was lost "
                                                           f"(agent restarted mid-run); it will run again.")
        log(f"cleared stale GitHub claim on #{i['number']} {i['title'][:60]}")


def gh_find_dup(title):
    import difflib
    want = _norm_title(title)
    for i in ghq.paged(ghq.repo_path("/issues?state=open")):
        if not i.get("pull_request") and difflib.SequenceMatcher(None, want, _norm_title(i["title"])).ratio() >= 0.8:
            return i["number"]
    return None


# ------------------------------------------------------------------ autonomy lane, next jobs (Flow PR 30, card #415)
# scheduler.py labels cards sched:today / sched:deferred / sched:batched; fallback.py narrows the rig to the local lane
# while Claude is out of usage. Neither touches hub_paused()/hub_verify(): every card still goes through them in poll().
SCHED_SKIP = {"sched:deferred", "sched:batched"}   # waiting for the 8 PM ET cap reset / folded into a batch card
_AUTONOMY = {"warned": set(), "skipped": None, "held": None}


def _autonomy(name):
    """Import a module from C:\\Jarvis\\autonomy, or None (logged once) so the Worker runs exactly as before."""
    try:
        if AUTONOMY_DIR not in sys.path:
            sys.path.insert(0, AUTONOMY_DIR)
        return __import__(name)
    except Exception as e:
        if name not in _AUTONOMY["warned"]:
            log(f"autonomy module {name} unavailable ({e}) - that part of the autonomy lane is off")
            _AUTONOMY["warned"].add(name)
        return None


def _label_names(i):
    return [l["name"] if isinstance(l, dict) else l for l in (i.get("labels") or [])]


def sched_filter(issues):
    """Skip cards scheduler.py parked (sched:deferred / sched:batched); sched:today cards go first."""
    keep = [i for i in issues if not SCHED_SKIP & set(_label_names(i))]
    skipped = len(issues) - len(keep)
    if skipped != _AUTONOMY["skipped"]:   # one line per change, not per poll
        log(f"scheduler: {skipped} deferred/batched card(s) skipped")
        _AUTONOMY["skipped"] = skipped
    return sorted(keep, key=lambda i: "sched:today" not in _label_names(i))


def _log_tail(n=300):
    """Last lines of agent.log plus one line for the Worker's own usage state, so fallback.detect() sees the truth:
    BLOCKED -> Claude's limit text (with its reset time); not blocked -> a 'done' line (usage window open)."""
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        lines = []
    if BLOCKED["until"] > time.time():
        lines.append(f"BLOCKED: {BLOCKED.get('why') or ''}")
    else:
        lines.append(f"Run on {ME}: done - Worker not blocked (usage window open)")
    return "\n".join(lines)


def fallback_tick(file_cards=True):
    """Rig, every poll: fallback.check(log_tail, 'rig'). While Claude is out of usage it writes fallback.json
    (mode local-fallback) and may file a next-project / local research card (deduped, model:local); a later normal
    run switches back. It never runs a card itself."""
    if ME != "rig" or not gh():
        return None
    fb = _autonomy("fallback")
    if fb is None:
        return None
    try:
        r = fb.check(_log_tail(), "rig", file_cards=file_cards)
    except Exception as e:
        log(f"fallback check failed ({e.__class__.__name__}: {e}) - normal mode")
        return None
    if (STATE.get("fallback") or {}).get("mode") != r.get("mode"):
        log(f"fallback: {r.get('mode')} - {r.get('say', '')}")
    STATE["fallback"] = {k: r.get(k) for k in ("mode", "resets", "queued", "can_sleep", "say")}
    for f in r.get("filed") or []:
        log(f"fallback filed #{f.get('number')} {f.get('status')}: {str(f.get('title'))[:60]}")
    return r


def fallback_filter(cards):
    """While fallback.json says local-fallback, only cards fallback.allowed() lets through stay queued (machine:rig +
    model:local research / next-project). Normal mode, or an error here: the list is unchanged."""
    if ME != "rig":
        return cards
    fb = _autonomy("fallback")
    if fb is None:
        return cards
    try:
        st = fb.load_state()
        if st.get("mode") != "local-fallback":
            _AUTONOMY["held"] = None
            return cards
        keep = [c for c in cards if fb.allowed({"title": c.get("task", ""), "body": c.get("notes", ""),
                                                "labels": c.get("labels") or []}, st)]
    except Exception as e:
        log(f"fallback filter failed ({e.__class__.__name__}: {e}) - queue unchanged")
        return cards
    held = len(cards) - len(keep)
    if held != _AUTONOMY["held"]:
        log(f"fallback: local lane only, {held} card(s) held until Claude usage resets")
        _AUTONOMY["held"] = held
    return keep


def approved_for(machine):
    if gh():
        cache = os.path.join(GHQ_DIR, f"ready-cache-{machine}.json")   # ETag: an unchanged queue costs no quota
        ready = [i for i in ghq.ready(machine, use_etag_file=cache) if "owner:jake" not in i["labels"]]
        return [gh_card(i) for i in sched_filter(ready)]   # card #415: skip sched:deferred/batched
    mine = [{"property": "Machine", "select": {"equals": machine}}]
    if machine == "homebase":  # homebase also takes "Any"; empty ones get routed first
        mine += [{"property": "Machine", "select": {"equals": "Any"}}]
    return query({"and": [
        {"property": "Status", "select": {"equals": "Approved"}},
        {"property": "Owner", "select": {"equals": "Claude"}},
        {"or": mine},
    ]})


# ------------------------------------------------------------------ Claude Code

def claude_exe():
    for c in (shutil.which("claude"), os.path.join(HOME, ".local", "bin", "claude.exe"),
              os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")):
        if c and os.path.exists(c):
            return c
    return None


PROMPT = """You are Claude Code running unattended on Jake's machine "{machine}" as the Jarvis agent.
Jake APPROVED this Notion task, so do it now, end to end, without asking him anything mid-way.

TASK: {task}
PROJECT: {project}   PRIORITY: {priority}
NOTION CARD: {url}
NOTES FROM THE CARD:
{notes}

Rules:
- Work inside {work} unless the task clearly names another folder on this machine. Jake's project
  folders on the rig are under C:\\Users\\Jake\\Desktop\\Claude.
- Never spend money, sign up for accounts, enter passwords/keys, or change security settings.
  Never delete Jake's files. Never read or modify anything under jarvis-hub\\data or the Jarvis
  token files, and never change a Notion card's Status or Approval code. If a Windows admin (UAC) prompt would be needed, write the script
  and stop; say so with NEEDS_JAKE.
- Use Bash, not PowerShell, for shell commands (card 199).
- Do not edit the running Worker code (agent.py, ghq.py, autonomy/*.py); write a patch in your work folder instead.
- If something only Jake can do blocks you, finish everything else, then end with ONE line:
  NEEDS_JAKE: <exactly what he must do, in one sentence>
- You may read the Notion Tasks board and this card with the Notion tools. Rig drafting jobs go in
  C:\\Jarvis\\jobs\\in. Sending, posting, deleting and purchasing tools are blocked: if a step needs one, don't
  work around it; end with NEEDS_JAKE: NEEDS PIN: <action> - <why>.
- CHECKPOINTS: this pass has {minutes} minutes. Keep your progress in {status_md} (what's done, what's next).
  Read it first if it exists and continue from there. If the task isn't finished when time is nearly up, save
  STATUS.md and end with the line: CHECKPOINT: <one-line progress>. The next cycle continues the card.
- For each useful follow-up you notice, add a line: FOLLOWUP: <short title> | <one-sentence why>
- TRAINING DATA needs no approval (Jake 9/30): save the material in your work folder, then add a line
  TRAINING_ADD: <full path> | <topic, usually raw> | <where it came from>. The Worker files it through
  C:\\Jarvis\\guardrails\\training_intake.py (logged, undoable, refuses secrets/patient identifiers). Never write
  under C:\\Jarvis\\training yourself. Clinical or client content never goes to Gemini or any outside service.
- End with a 3-6 sentence plain-English summary of what you did and where the results are.
{extra}"""


def card_dir(c):
    import re
    d = os.path.join(WORK_DIR, (re.sub(r"[^a-z0-9]+", "-", c["task"].lower()).strip("-")[:50] or c["id"][:8]))
    os.makedirs(d, exist_ok=True)
    return d


def claude_cmd(exe, extra_dirs=(), allow=None):
    cmd = [exe, "-p", "--output-format", "text", "--permission-mode", "acceptEdits",
           "--allowedTools", ",".join(allow or (["Read", "Write", "Edit", "Glob", "Grep", "Bash", "WebSearch",
                                                 "WebFetch", "TodoWrite"] + NOTION_ALLOW + SHELL_ALLOW)),
           "--disallowedTools", ",".join(DENY)]
    if os.path.exists(WORKER_SETTINGS):
        cmd += ["--settings", WORKER_SETTINGS]   # card #592 script guard hook
    for d in extra_dirs:
        cmd += ["--add-dir", d]
    return cmd


# ------------------------------------------------------------------ helpers: Gemini (free tier) + rig drafting

def private(c):
    """Never send to Gemini (free tier may train on inputs): Fieldwork Clinical, client/private/personal/financial."""
    import re
    text = f"{c.get('task', '')} {c.get('project', '')} {c.get('notes', '')}"
    return bool(re.search(r"fieldwork|clinical|curriculum|client|patient|private|personal|financ|budget|bill|"
                          r"payday|bank|password|secret|token|fc-curriculum", text, re.I)) or c.get("id", "").replace(
        "-", "") in RIG_DRAFT_CARDS


def research_card(c):
    import re
    text = f"{c.get('task', '')} {c.get('type', '')} {c.get('notes', '')[:300]}"
    return bool(re.search(r"research|compare|find the best|options|proposal|idea|\[income\]|\[qol\]|survey|"
                          r"which .* should|look into|investigate", text, re.I))


CLINICAL = r"clinical|fieldwork|curriculum|patient|client'?s? (name|record|file|note|session)|diagnos|therap|treatment plan|symptom|medication|" \
           r"\bDSM\b|ICD-?10|intake note|session note|caseload|fc-curriculum"


def clinical(text):
    import re
    return bool(re.search(CLINICAL, text or "", re.I))


def gemini(mode, arg, out_path, timeout=300):
    """Run the Gemini helper. Returns the output text, or None (no key / failed / not installed): caller falls back."""
    if not os.path.exists(GEMINI):
        return None
    # guardrails hard stop: clinical content never goes to Gemini, whatever the caller checked (arg may be a file)
    body = arg
    if os.path.isfile(arg):
        with open(arg, encoding="utf-8", errors="replace") as f:
            body = f.read()
    if clinical(body) or clinical(out_path):
        log(f"gemini {mode}: refused - clinical content stays on homebase/the rig ({os.path.basename(out_path)})")
        return None
    try:
        r = subprocess.run([PY, GEMINI, mode, arg, "-o", out_path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        log(f"gemini {mode}: {e}")
        return None
    if r.returncode != 0 or not os.path.exists(out_path):
        log(f"gemini {mode}: exit {r.returncode} - Claude does it as usual")
        return None
    with open(out_path, encoding="utf-8", errors="replace") as f:
        return f.read()


def rig_generate(prompt, model="qwen3.6:35b", timeout=900):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False}).encode()
    req = urllib.request.Request(BRAIN + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"]


# ------------------------------------------------------------------ guardrails: training data free lane
INTAKE = r"C:\Jarvis\guardrails\training_intake.py"   # switches on by itself once the guardrails are installed


def training_add(path, topic="raw", source="", by="worker"):
    """File one path through training_intake.py. 0 = added, 1 = refused (hard stop: logged, never retried),
    2 = bad input, None = intake not installed."""
    if not os.path.exists(INTAKE):
        log(f"training intake not installed - {path} left where it is")
        return None
    key = f"{path}|{os.path.getmtime(path) if os.path.exists(path) else 0}"
    refused = STATE.setdefault("intake_refused", [])
    if key in refused:
        return 1
    try:
        r = subprocess.run([PY, INTAKE, "add", path, "--topic", topic or "raw", "--source", source or "Worker",
                            "--by", by], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        log(f"training intake: {e}")
        return None
    out = " ".join((r.stdout or r.stderr or "").split())[:300]
    if r.returncode == 1:
        refused.append(key)
        del refused[:-200]
        log(f"training intake REFUSED (not retried): {path}: {out}")
    elif r.returncode == 0:
        log(f"training data added: {out}")
    else:
        log(f"training intake exit {r.returncode}: {path}: {out}")
    return r.returncode


def training_lines(lines, by="worker"):
    """TRAINING_ADD: <path> | <topic> | <source>   (topic may be left out -> raw)."""
    for l in lines:
        if l.startswith("TRAINING_ADD:"):
            parts = [p.strip() for p in l.split(":", 1)[1].split("|")]
            path, topic, source = (parts + ["", ""])[:3] if len(parts) >= 3 else (parts[0], "raw", parts[-1])
            training_add(path.strip('"'), topic or "raw", source, by)


def training_summary(hours=24):
    """7 AM report page 1: what went into the training set, from training_intake.py list --json."""
    if not os.path.exists(INTAKE):
        return ["Guardrails not installed (no training_intake.py)."]
    try:
        r = subprocess.run([PY, INTAKE, "list", "--hours", str(hours), "--json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        recs = json.loads(r.stdout or "[]")
    except Exception as e:
        return [f"Couldn't read the intake log: {e}"]
    reverted = {r.get("batch") for r in recs if r.get("event") == "revert"}
    out = []
    for x in recs:
        if x.get("event") == "add":
            out.append(f"{x['at'][11:16]} {x['batch']}{' (reverted)' if x['batch'] in reverted else ''}: "
                       f"{len(x.get('files', []))} file(s) -> training\\{x['topic']}, {x.get('source', '')} "
                       f"[by {x.get('by', '?')}]  undo: training_intake.py revert {x['batch']}")
        elif x.get("event") == "refused":
            out.append(f"{x['at'][11:16]} REFUSED {x.get('topic')}: {'; '.join(x.get('problems', []))[:160]}")
    return out or ["Nothing added."]


# ------------------------------------------------------------------ Jake's app notes (9/30, app-notes-fix)
# The hub saves app notes with for_claude / status "open". Every run gets the open ones; the launched Claude says it
# saw them and emits NOTE_ACK lines; agent.py (never the launched Claude - it may not read jarvis-hub\data) acks them
# through the hub's /api/notes/ack. Crosstalk health alerts are not notes from Jake and are skipped.
NOTES_FILE = os.path.join(HOME, "ClaudeCode", "JarvisKit", "jarvis-hub", "data", "notes.json")


def open_notes():
    try:
        if os.path.exists(NOTES_FILE):
            with open(NOTES_FILE, encoding="utf-8") as f:
                n = json.load(f)
        else:
            with urllib.request.urlopen(HUB + "/api/notes", timeout=15) as r:
                n = json.loads(r.read())
    except Exception as e:
        log(f"app notes: {e}")
        return []
    return [x for x in n if x.get("status") == "open" and x.get("for_claude", True) is not False
            and not str(x.get("text", "")).startswith("Crosstalk check:")]


def notes_block():
    """Prompt text listing Jake's unacknowledged notes, N1..Nn (several notes can share a minute)."""
    notes = open_notes()[-15:]
    STATE["note_map"] = {f"N{i + 1}": {"at": x.get("at"), "text": x.get("text")} for i, x in enumerate(notes)}
    if not notes:
        return ""
    rows = "\n".join(f"- N{i + 1} ({x.get('at')}): {str(x.get('text', ''))[:1500]}" for i, x in enumerate(notes))
    return ("\nUNACKNOWLEDGED NOTES FROM JAKE (from the phone app):\n" + rows + "\n"
            "Begin your final summary with \"I see the note(s) you left in the app\" and one line per note on what you did "
            "or will do about it (the card still comes first). For each note you handled or answered, add a line\n"
            "NOTE_ACK: <N number> | <one-sentence reply to Jake>\n"
            "Leave a note un-acked if nothing was done about it yet; it will be shown again next run.\n")


def ack_notes(lines):
    m = STATE.get("note_map") or {}
    for l in lines:
        if not l.startswith("NOTE_ACK:"):
            continue
        key, _, reply = l.split(":", 1)[1].strip().partition("|")
        note = m.get(key.strip().upper())
        if not note:
            log(f"NOTE_ACK for unknown note {key.strip()!r}")
            continue
        try:
            req = urllib.request.Request((HUB_LOCAL if ME == "homebase" else HUB) + "/api/notes/ack",
                                         data=json.dumps(dict(note, reply=reply.strip())).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=15).read()
            log(f"app note acked {note['at']}: {reply.strip()[:80]}")
        except Exception as e:
            log(f"app note ack {note['at']}: {e}")


def usage_row(c, model, t0, result):
    import csv
    new = not os.path.exists(USAGE_CSV)
    try:
        with open(USAGE_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["start", "minutes", "card_id", "task", "model", "result"])
            w.writerow([datetime.fromtimestamp(t0).isoformat(timespec="seconds"), round((time.time() - t0) / 60, 1),
                        c.get("id", ""), c.get("task", "")[:90], model, result])
    except Exception as e:
        log(f"usage csv: {e}")


RIG_DRAFT_RULES = """
RIG DRAFTS, YOU CHECK (Jake 9/30): this card's drafting is done by the rig's local model, not by you.
- The rig is {rig}. Do NOT write long prose yourself. For anything that needs drafting or redrafting, write a job
  for the rig (python calling {brain}/api/generate with model qwen3.6:35b, or a job file in C:\\Jarvis\\jobs\\in the
  way the earlier passes did) and let it produce the text. Your part: plan, check against sources, fix small errors.
- Resume, don't rebuild: never overwrite C:\\Jarvis\\jobs\\out\\fc-curriculum files; back a file up as
  <name>.bak-<date> before changing it. Clinical content never goes to Gemini or any web service.
- This is a short review pass ({minutes} min). Save STATUS.md and end with CHECKPOINT if there's more.
"""


def card_extra(c):
    """Extra prompt text + (timeout min, model or None) for this card."""
    cid = c["id"].replace("-", "")
    if cid in RIG_DRAFT_CARDS:
        up = wake_rig_and_wait()
        if not up:
            update(c["id"], agent_log=f"{datetime.now():%m/%d %H:%M} {ME}: rig unreachable after wake - Claude "
                                      f"review pass only, no rig drafting this pass")
        return RIG_DRAFT_RULES.format(rig="UP" if up else "DOWN (only review/verify this pass; draft nothing)",
                                      brain=BRAIN, minutes=RIG_DRAFT_MIN - 3), RIG_DRAFT_MIN, LIGHT_MODEL or None
    extra = ""
    if research_card(c) and not private(c):
        notes = os.path.join(card_dir(c), "gemini-research.md")
        fresh = os.path.exists(notes) and time.time() - os.path.getmtime(notes) < 86400
        if fresh or gemini("research", f"{c['task']}\n\n{(c['notes'] or '')[:1500]}", notes):
            log(f"gemini research ready for {c['task'][:60]}")
            extra = (f"\nSTARTING NOTES: Gemini already researched this; its notes are in {notes}. Read them first, "
                     f"verify anything you rely on (Gemini marks unsure items unverified), and finish the task - "
                     f"don't redo the research from scratch.\n")
    draft = os.path.join(card_dir(c), "rig-draft.md")
    if os.path.exists(draft) and time.time() - os.path.getmtime(draft) < 3 * 86400:
        extra += (f"\nRIG DRAFT: while Claude was out of usage the rig drafted this card into {draft} (its UNVERIFIED / "
                  f"NEED / UNFILLED lines are the gaps). Read it first, keep what's right, verify flagged claims and "
                  f"do the parts it couldn't (commands, files, checks) - don't redo it from scratch.\n")
    light = LIGHT_MODEL and (c["priority"] or "P9")[:2] not in ("P0", "P1")
    return extra, TASK_TIMEOUT_MIN, (LIGHT_MODEL if light else None)


def _guarded_files():
    """The live Worker code a card must not change: this agent.py, ghq.py and the autonomy modules."""
    import glob
    files = sorted(glob.glob(os.path.join(HERE, "*.py"))) + [os.path.join(GHQ_DIR, "ghq.py")]   # agent.py, budget_guard.py, worker_script_guard.py ...
    files.append(WORKER_SETTINGS)   # card #592: a card must not loosen its own script-guard hook settings
    files.append(os.path.join(GHQ_DIR, "loopnet.py"))   # loop net (10/03): the loop gate and write breaker are rules too
    files.append(os.path.join(GHQ_DIR, "needsjake.py"))   # needs-Jake catcher (10/03): decides what reaches Jake's To-Do
    # guardrails (Jake 10/02): the training intake and its policy are rules too
    files += sorted(glob.glob(r"C:\Jarvis\guardrails\*.py")) + [r"C:\Jarvis\guardrails\policy.json"]
    return files + sorted(glob.glob(os.path.join(AUTONOMY_DIR, "*.py")))


def code_snapshot():
    """10/2: copies of the live Worker code taken right before a card runs (see code_restore)."""
    snap = {}
    for p in _guarded_files():
        try:
            with open(p, "rb") as f:
                snap[p] = f.read()
        except OSError:
            pass
    return snap


def code_restore(c, snap):
    """After a card: any guarded file it changed, added or deleted is staged in the card's work folder as
    <name>.proposed + <name>.diff and the live copy is put back. Returns one line for the result comment, or ''."""
    import difflib
    changed = []
    for p in sorted(set(snap) | set(_guarded_files())):
        try:
            with open(p, "rb") as f:
                now = f.read()
        except OSError:
            now = None
        before = snap.get(p)
        if now == before:
            continue
        name = os.path.basename(p)
        try:
            d = card_dir(c)
            prop = os.path.join(d, name + ".proposed")
            if now is not None:
                with open(prop, "wb") as f:
                    f.write(now)
            diff = difflib.unified_diff((before or b"").decode("utf-8", "replace").splitlines(True),
                                        (now or b"").decode("utf-8", "replace").splitlines(True),
                                        fromfile=f"live/{name}", tofile=f"proposed/{name}")
            with open(os.path.join(d, name + ".diff"), "w", encoding="utf-8") as f:
                f.writelines(diff)
            if before is None:
                os.remove(p)              # a new file the card added
            else:
                with open(p, "wb") as f:  # put the live copy back
                    f.write(before)
            label = f"#{c['number']}" if c.get("number") else c.get("task", "?")[:60]
            log(f"SELF-EDIT-REVERTED: card {label} changed {p}; patch staged at {prop}")
            changed.append(name)
        except Exception as e:
            log(f"SELF-EDIT guard could not restore {p}: {e}")
    if not changed:
        return ""
    return (f"Code patch staged for review: this card changed the live Worker code ({', '.join(changed)}); the change "
            f"was reverted and saved as .proposed + .diff in {card_dir(c)}.")


def run_card(c):
    exe = claude_exe()
    if not exe:
        raise RuntimeError("Claude Code is not installed on this machine")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = os.path.join(LOG_DIR, f"task-{stamp}.txt")
    status_md = os.path.join(card_dir(c), "STATUS.md")
    extra, minutes, model = card_extra(c)
    extra += notes_block()
    prompt = PROMPT.format(machine=ME, task=c["task"], project=c["project"], priority=c["priority"],
                           url=c["url"], notes=c["notes"] or "(none)", work=WORK_DIR,
                           minutes=minutes - 5, status_md=status_md, extra=extra)
    # prompt goes in on stdin: a .cmd wrapper (npm install) cuts multi-line arguments at the first newline
    cmd = claude_cmd(exe, [d for d in (JOBS_DIR, RIG_PROJECTS) if os.path.isdir(d)])
    if model:
        cmd += ["--model", model]
    log(f"running Claude Code{' (' + model + ')' if model else ''} on: {c['task']}")
    t0 = time.time()
    STATE["run_t0"] = t0
    snap = code_snapshot()
    env = dict(os.environ, JARVIS_CARD_DIR=card_dir(c))   # card #592: scripts may run only from this folder
    try:
        r = subprocess.run(cmd, cwd=WORK_DIR, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=minutes * 60, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = (r.stdout or "") + ("\n[stderr]\n" + r.stderr if r.returncode and r.stderr else "")
        STATE["last_rc"] = r.returncode
        STATE["last_stderr"] = r.stderr or ""
    except subprocess.TimeoutExpired:
        output = (f"Pass stopped at the {minutes}-min checkpoint.\n"
                  f"CHECKPOINT: timed out mid-pass; progress (if any) is in {status_md}")
        STATE["last_rc"] = 0
        STATE["last_stderr"] = ""
    staged = code_restore(c, snap)
    if staged:
        output = output.rstrip() + "\n" + staged   # last line, so it lands in the card's result comment
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(output)
    fail = claude_failed(output)
    usage_row(c, model or "default", t0, "limit/fail" if fail else
              "needs-jake" if "NEEDS_JAKE:" in output else "checkpoint" if "CHECKPOINT:" in output else "done")
    return output, out_file


def proofread_outputs(c, since):
    """After a successful pass: Gemini proofreads the Markdown the card wrote (non-private only). A short Claude
    fix pass runs only when a review ends in NEEDS FIXES."""
    if private(c):
        return
    d = card_dir(c)
    docs = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".md") and n not in ("STATUS.md",)
            and not n.endswith((".review.md",)) and not n.startswith("gemini-")
            and os.path.getmtime(os.path.join(d, n)) >= since][:2]
    for doc in docs:
        review = gemini("proofread", doc, doc[:-3] + ".review.md")
        if not review:
            continue
        verdict = "NEEDS FIXES" if "NEEDS FIXES" in review[-300:].upper() else "READY"
        log(f"gemini proofread {os.path.basename(doc)}: {verdict}")
        if verdict == "NEEDS FIXES" and BLOCKED["until"] < time.time():
            exe = claude_exe()
            p = (f"Apply the concrete fixes listed in {doc[:-3]}.review.md to {doc}. Only fix what the review lists "
                 f"and you agree is wrong; don't rewrite the document. Back it up as {os.path.basename(doc)}.bak first. "
                 f"End with one line saying what you fixed.")
            allow = ["Read", f"Edit(//{d.replace(chr(92), '/')}/**)", f"Write(//{d.replace(chr(92), '/')}/**)"]
            t0 = time.time()
            try:
                r = subprocess.run(claude_cmd(exe, [d], allow=allow) + (["--model", LIGHT_MODEL] if LIGHT_MODEL else []),
                                   cwd=d, input=p, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                fail = claude_failed(r.stdout or "", r.stderr or "", r.returncode)
                usage_row(c, (LIGHT_MODEL or "default") + " fix", t0, "limit/fail" if fail else "fixed")
                if fail:
                    _block(fail)
                    return
            except Exception as e:
                log(f"proofread fix: {e}")


FAIL_MARKERS = ("Not logged in", "Please run /login", "hit your session limit", "hit your limit", "usage limit", "rate limit",
                "Invalid API key", "credit balance", "overloaded_error")
# The whole stdout is one short line that IS the CLI's own message (not a card summary that mentions it)
CLI_ONLY_LINE = ("claude ai usage limit reached", "you've hit your", "you have hit your", "not logged in",
                 "please run /login", "invalid api key", "credit balance is too low", "api error: 529", "overloaded_error")
NO_TASK_MARKERS = ("haven't given me a task", "havent given me a task", "no task was provided", "what would you like me to")
BLOCKED = {"until": 0, "why": ""}


def claude_failed(output, stderr=None, rc=None):
    """Claude Code didn't actually do the task (login / usage limit / crash). Never mark those Done.
    10/2 (2nd false 5-h BLOCK): a limit counts only when the CLI says so - nonzero exit, its stderr, or a stdout that
    is nothing but one short CLI message line. Words like "limit" inside a card's own summary never count."""
    rc = STATE.get("last_rc") if rc is None else rc
    err = (STATE.get("last_stderr", "") if stderr is None else stderr or "").strip()
    out = (output or "").strip()
    if rc:
        return (err or out).splitlines()[0][:300] if (err or out) else f"exit code {rc}"
    if err and any(m.lower() in err.lower() for m in FAIL_MARKERS):
        return err.splitlines()[0][:300]
    if not out:
        return "empty output"
    lines = out.splitlines()
    if len(lines) == 1 and len(out) <= 300:
        low = out.lower()
        if low.startswith(CLI_ONLY_LINE) or any(m in low for m in NO_TASK_MARKERS):
            return out
    return None


def _selftest_claude_failed():
    """python agent.py selftest-failed: prints PASS/FAIL per case."""
    cases = [("The rig Worker no longer stalls on the usage limit. It held P2 cards past 60%.", "", 0, False),
             ("I checked the rate limit handling; nothing to change.", "", 0, False),
             ("Done.\nThe 5-hour usage limit logic was reviewed; hit your limit lines are gone.", "", 0, False),
             ("Claude AI usage limit reached|1790990000", "", 0, True),
             ("You've hit your limit · resets 3:10pm", "", 0, True),
             ("", "", 0, True),
             ("partial", "Error: Not logged in", 1, True),
             ("summary text", "warning: usage limit reached", 0, True),
             ("It looks like you haven't given me a task.", "", 0, True)]
    bad = 0
    for out, err, rc, want in cases:
        got = bool(claude_failed(out, err, rc))
        bad += got != want
        print(("PASS" if got == want else "FAIL"), repr(out[:60]), "->", got)
    print("ALL PASS" if not bad else f"{bad} FAILED")
    return bad


def reset_hours(why, now=None):
    """Hours to wait for "resets 3:10pm" (local clock, 12-hour). A reset that is already up to 2 h past
    (Claude still reporting the old window) retries in 10 min instead of rolling to tomorrow (9/30 fix)."""
    import re
    now = now or datetime.now()
    m = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)", why, re.I)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        t = now.replace(hour=h, minute=int(m.group(2) or 0), second=0, microsecond=0)
        secs = (t - now).total_seconds()
        if -7200 < secs <= 0:
            return 10 / 60
        return (secs if secs > 0 else secs + 86400) / 3600 + 0.1
    return 1 if "login" in why.lower() else 5


def _block(why):
    hours = reset_hours(why)
    first = BLOCKED["until"] < time.time()
    BLOCKED.update(until=time.time() + hours * 3600, why=why)
    log(f"BLOCKED {hours:.1f} h: {why}")
    if first:
        notify(f"Jarvis agent waiting on {ME}", f"{why[:120]} - resumes by itself")


NOTIFY_URL = os.environ.get("JARVIS_NOTIFY_URL", "https://laptop-4150egrs.tail3bbcb8.ts.net/api/notify")


def _post_notify(payload):
    """10/2: phone pushes go to the 5060 hub's tailnet URL with X-Jarvis-Notify (it refused raw :8770 as 'not local')."""
    req = urllib.request.Request(NOTIFY_URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Jarvis-Notify": "1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            reply = r.read().decode("utf-8", "replace")
        if "refused" in reply.lower():
            log(f"notify refused by hub: {reply[:160]}")
        else:
            log(f"notify accepted: {payload.get('title', '')[:60]}")
    except Exception as e:
        log(f"notify failed: {e}")


def notify(title, body):
    _post_notify({"title": title, "body": body[:150]})


def finish(c, output, out_file):
    fail = claude_failed(output)
    if fail:
        _block(fail)
        until = datetime.fromtimestamp(BLOCKED["until"]).strftime("%m/%d %H:%M")
        # park it, don't bother Jake: the card resumes by itself once the usage window resets
        if c.get("number"):
            gh_log(c, "waiting-usage", summary=f"WAITING: usage resets {until} ({fail}). Resumes automatically.")
        else:
            update(c["id"], status="Approved", claimed_by="",
                   agent_log=f"{datetime.now():%m/%d %H:%M} {ME}: WAITING: usage resets {until} ({fail}). "
                             f"Resumes automatically.\nLog: {out_file}")
        STATE["parked"] = c["id"]   # after the reset this card goes first
        return
    if STATE.get("parked") == c["id"]:
        STATE["parked"] = None
    try:
        proofread_outputs(c, STATE.get("run_t0", time.time()) - 5)
    except Exception as e:
        log(f"proofread: {e}")
    lines = output.strip().splitlines()
    needs = [l.split(":", 1)[1].strip() for l in lines if l.startswith("NEEDS_JAKE:")]
    follow = [l.split(":", 1)[1].strip() for l in lines if l.startswith("FOLLOWUP:")]
    ckpt = [l.split(":", 1)[1].strip() for l in lines if l.startswith("CHECKPOINT:")]
    training_lines(lines)
    ack_notes(lines)
    body = [l for l in lines if not l.startswith(("NEEDS_JAKE:", "FOLLOWUP:", "CHECKPOINT:", "TRAINING_ADD:",
                                                  "NOTE_ACK:"))]
    summary = "\n".join(body[-12:]).strip()
    log_text = f"{datetime.now():%m/%d %H:%M} {ME}: {summary}\nFull log: {out_file}"
    passes = STATE.setdefault("passes", {})
    if c.get("number"):
        return gh_finish(c, output, summary, needs, ckpt, follow, passes)
    if needs:
        prev = (c["notes"] or "")
        if prev.startswith("NEEDS JAKE:") and needs[0][:60] in prev:
            update(c["id"], status="Snoozed", agent_log=log_text)   # same blocker twice: stop, one hub line
            notify(f"Snoozed: {c['task'][:60]}", f"Same blocker twice: {needs[0][:120]}")
        else:
            # de-dup: drop earlier copies of NEEDS JAKE lines so the note never repeats itself
            import re
            rest = "\n".join(l for l in prev.splitlines() if not l.startswith("NEEDS JAKE:")).strip()
            rest = re.sub(r"\n{3,}", "\n\n", rest)
            res = update(c["id"], status="Staged", agent_log=log_text, notes=f"NEEDS JAKE: {needs[0]}\n\n{rest}")
            mark_needs_jake(c["id"], (res or {}).get("last_edited_time", ""))
        log(f"needs Jake: {needs[0]}")
    elif ckpt:
        n = passes[c["id"]] = passes.get(c["id"], 0) + 1
        if n >= (RIG_DRAFT_MAX_PASSES if c["id"].replace("-", "") in RIG_DRAFT_CARDS else MAX_PASSES_PER_NIGHT):
            update(c["id"], status="Approved", claimed_by="",
                   agent_log=f"{log_text}\nCHECKPOINT {n}: {ckpt[0]} - nightly cap of {MAX_PASSES_PER_NIGHT} passes "
                             f"reached, continues tomorrow.")
        else:
            update(c["id"], status="Approved", claimed_by="", agent_log=f"{log_text}\nCHECKPOINT {n}: {ckpt[0]}")
        log(f"checkpoint {n} on {c['task']}: {ckpt[0][:100]}")
    else:
        update(c["id"], status="Done", agent_log=log_text)
        STATE["done_today"] += 1
    for f in follow[:5]:
        title, _, why = f.partition("|")
        try:
            file_followup(title.strip(), why.strip(), c)
        except Exception as e:
            log(f"follow-up card failed: {e}")


def gh_finish(c, output, summary, needs, ckpt, follow, passes):
    """GitHub side of finish(): one run comment per pass (log_run), questions via ask_jake."""
    if needs:
        gh_log(c, "needs-jake", summary=summary, tail=output)
        try:   # 10/2: triage first (local model, then Claude); only a real Jake blocker goes to him, else snoozed
            if hasattr(ghq, "send_back"):
                os.environ.setdefault("JARVIS_TRIAGE_MODEL", "jarvis")   # rig has no "baby-jarvis" model
                os.environ.setdefault("CLAUDE_EXE", claude_exe())
                res = ghq.send_back(c["number"], ME, needs[0])
                if res[0] == "snoozed":
                    log(f"triage snoozed #{c['number']} on {res[1]}: {str(res[2])[:120]}")
            else:
                ghq.ask_jake(c["number"], needs[0])
        except Exception as e:
            log(f"github send_back/ask_jake #{c['number']}: {e}")
        log(f"needs Jake: {needs[0]}")
    elif ckpt:
        n = passes[c["id"]] = passes.get(c["id"], 0) + 1
        cap = RIG_DRAFT_MAX_PASSES if c["id"].replace("-", "") in RIG_DRAFT_CARDS else MAX_PASSES_PER_NIGHT
        status_md = os.path.join(card_dir(c), "STATUS.md")
        # a pass that hit the time limit without saving progress is a real timeout (two in a row -> needs-jake)
        stuck = STATE.get("timed_out") and not (os.path.exists(status_md) and
                                                 os.path.getmtime(status_md) > STATE.get("run_t0", 0))
        gh_log(c, "timeout" if stuck else "released", tail=output,
               summary=f"CHECKPOINT {n}: {ckpt[0]}" + (f" - nightly cap of {cap} passes reached, continues tomorrow."
                                                       if n >= cap else "") + f"\n\n{summary}")
        log(f"checkpoint {n} on {c['task']}: {ckpt[0][:100]}")
    else:
        gh_log(c, "done", summary=summary, tail=output)
        STATE["done_today"] = STATE.get("done_today", 0) + 1
    for f in follow[:5]:
        title, _, why = f.partition("|")
        try:
            file_followup(title.strip(), why.strip(), c)
        except Exception as e:
            log(f"follow-up card failed: {e}")


def work_one(c):
    if STATE.get("power_pending"):
        log(f"not claiming {c['task'][:60]} - {STATE['power_pending']} pending")
        return
    if c.get("number"):
        return gh_work_one(c)
    # claim, then re-read to make sure no other machine grabbed it first
    claim = f"{ME} {datetime.now():%m/%d %H:%M}"
    update(c["id"], status="In progress", claimed_by=claim)
    time.sleep(3)
    fresh = card(notion("GET", f"pages/{c['id']}"))
    ok, _, why = hub_verify(fresh)
    if not ok:
        update(c["id"], status="Approved", claimed_by="", agent_log=f"{ME}: not run - {why}. Waiting for Jake in Hub v3.")
        return
    if not fresh["claimed_by"].startswith(ME):
        log(f"lost claim race on {c['task']}")
        STATE["claim_lost"] = True
        return
    STATE["running"] = c["task"]
    try:
        out, f = run_card(fresh)
        finish(fresh, out, f)
    except Exception as e:
        log(f"task failed: {e}")
        update(c["id"], status="Approved", claimed_by="", agent_log=f"{ME} failed: {e}")
    finally:
        STATE["running"] = None


def gh_work_one(c):
    n = c["number"]
    if not ghq.claim(n, ME):   # the other PC posted its claim comment first
        log(f"lost claim race on #{n} {c['task'][:60]}")
        return
    STATE.update(running=c["task"], running_number=n, gh_started=ghq.now_iso())
    try:
        fresh = gh_full(dict(c, notes=""))
        out, f = run_card(fresh)
        finish(fresh, out, f)
    except Exception as e:
        log(f"task failed: {e}")
        gh_log(c, "failed", summary=f"{ME} failed: {e}")
    finally:
        STATE.update(running=None, running_number=None)


def unstick_claims():
    """Card 9/29 s.4: In progress cards claimed by this machine more than STALE_CLAIM_H ago, and not running
    here now, go back to Approved (e.g. the agent restarted mid-run and the result was lost)."""
    import re
    if gh():
        return gh_unstick()
    now = datetime.now()
    for c in query({"and": [{"property": "Status", "select": {"equals": "In progress"}},
                            {"property": "Owner", "select": {"equals": "Claude"}}]}):
        m = re.match(r"(\S+) (\d\d)/(\d\d) (\d\d):(\d\d)", c["claimed_by"] or "")
        if not m or m.group(1) != ME or STATE.get("running") == c["task"]:
            continue
        try:
            t = now.replace(month=int(m.group(2)), day=int(m.group(3)), hour=int(m.group(4)),
                            minute=int(m.group(5)), second=0, microsecond=0)
        except ValueError:
            continue
        if t > now:
            t = t.replace(year=t.year - 1)
        if (now - t).total_seconds() >= STALE_CLAIM_H * 3600:
            update(c["id"], status="Approved", claimed_by="",
                   agent_log=f"{now:%m/%d %H:%M} {ME}: stale claim ({c['claimed_by']}) cleared - result was lost, "
                             f"will run again")
            log(f"cleared stale claim on {c['task'][:60]}")


def needs_jake_digest():
    """Card 9/29 s.4: ONE combined hub line per day listing Approved cards parked on NEEDS JAKE (not one per card)."""
    today = datetime.now().strftime("%Y-%m-%d")
    if STATE.get("needs_digest_day") == today or datetime.now().hour < 9:
        return
    STATE["needs_digest_day"] = today
    parked = [c["task"][:40] for c in approved_for(ME) if (c["notes"] or "").startswith("NEEDS JAKE:")]
    if parked:
        notify(f"{len(parked)} card(s) on {ME} need you", "; ".join(parked))


IDLE_ALERT_MIN = 30   # same reason with work waiting this long (not usage-limit / all-needs-jake) -> one push to Jake


def idle_reason(code, detail=""):
    """Jake 9/30 19:41 UTC: with work waiting the Worker is either running or says what it's waiting for.
    One IDLE-REASON line per poll that found approved cards but started none; code None = a card ran."""
    if code is None:
        STATE.update(idle_reason=None, idle_alerted=None)
        return
    prev = STATE.get("idle_reason") or {}
    since = prev.get("since") if prev.get("code") == code else datetime.now().isoformat(timespec="seconds")
    STATE["idle_reason"] = {"code": code, "detail": detail, "since": since}
    log(f"IDLE-REASON: {code} {detail}")
    mins = (datetime.now() - datetime.fromisoformat(since)).total_seconds() / 60
    if code not in ("usage-limit", "all-needs-jake") and mins >= IDLE_ALERT_MIN and STATE.get("idle_alerted") != code:
        STATE["idle_alerted"] = code   # once per reason, not every poll
        notify(f"Worker on {ME} stuck {int(mins)} min", f"{code}: {detail}"[:150])


def worker_line():
    if STATE.get("running"):
        return f"Working: {STATE['running'][:80]} (started {datetime.fromtimestamp(STATE.get('run_t0') or time.time()):%H:%M})"
    r = STATE.get("idle_reason")
    return f"Waiting: {r['code']} {r['detail']}"[:240] if r else "Idle: no approved cards waiting"


def wake_snoozed_cards():
    """10/2 (Flow 571878e): snoozed cards this PC watches (a file, a card, a PC, a time) go back to approved when the
    condition is met. Throttled to every SNOOZE_CHECK_S: each check reads the comments of every snoozed card
    (60+ of them), which every 60 s would spend most of the token's hourly GitHub quota. A failure only logs."""
    if not gh() or not hasattr(ghq, "wake_snoozed") or time.time() - STATE.get("snooze_check_t", 0) < SNOOZE_CHECK_S:
        return
    STATE["snooze_check_t"] = time.time()
    try:
        woken = ghq.wake_snoozed(ME)
        log(f"snooze check: {len(woken)} woken" + (f" ({', '.join('#%d' % n for n in woken)})" if woken else ""))
    except Exception as e:
        log(f"snooze check failed: {e}")


SNOOZE_CHECK_S = int(os.environ.get("JARVIS_SNOOZE_CHECK_S", "300"))


DOWNTIME_FILE = r"C:\ProgramData\JarvisIdle\downtime.json"   # {"start","end" (local ISO),"allow":["P0","P1"]}
_DOWNTIME = {"logged": None}


def downtime_window():
    """The active downtime window from downtime.json, or None (missing file, bad JSON, or outside start-end)."""
    try:
        with open(DOWNTIME_FILE, encoding="utf-8") as f:
            d = json.load(f)
        now = datetime.now()
        if datetime.fromisoformat(d["start"]) <= now < datetime.fromisoformat(d["end"]):
            return d
    except Exception:
        pass
    return None


def downtime_filter(cards):
    """10/2 (Jake: downtime 07:15-12:00 today): during the window only P0/P1 cards are claimed; a running card
    finishes. The file is ignored once its end has passed, so nothing needs undoing."""
    d = downtime_window()
    if not d:
        _DOWNTIME["logged"] = None
        return cards
    allow = {p.upper() for p in d.get("allow", ["P0", "P1"])}
    keep = [c for c in cards if (c.get("priority") or "").upper() in allow]
    if _DOWNTIME["logged"] != d["end"]:
        log(f"DOWNTIME: holding P2 until {datetime.fromisoformat(d['end']):%H:%M} "
            f"({len(cards) - len(keep)} held, {len(keep)} P0/P1 allowed)")
        _DOWNTIME["logged"] = d["end"]
    return keep


def poll():
    if STATE.get("power_pending"):   # 10/2: phone-app restart waiting for the running card - no new claims
        return "restart pending"
    if not LOCK.acquire(blocking=False):
        return "busy"
    try:
        STATE["last_poll"] = datetime.now().isoformat(timespec="seconds")
        if ME == "homebase":
            route_unassigned()
        heartbeat()
        try:
            unstick_claims()
            needs_jake_digest()
        except Exception as e:
            log(f"unstick/digest error: {e}")
        wake_snoozed_cards()
        cards = approved_for(ME)
        fallback_tick()                  # rig: Claude out of usage -> local lane (card #415)
        cards = fallback_filter(cards)   # local-fallback: only fallback.allowed() cards stay
        cards = downtime_filter(cards)   # 10/2 downtime window: only P0/P1 until it ends
        # after a usage reset the parked card goes first, then P0 > P1 > P2 > none
        cards.sort(key=lambda c: (c["id"] != STATE.get("parked"), c["priority"] or "P9"))
        STATE["seen_ids"] = [c["id"] for c in cards]   # list: STATE is served as JSON on /status
        CARDS_CACHE.update(t=time.time(), cards=cards)   # the rig lane drafts from this list
        ran = 0
        if BLOCKED["until"] > time.time():
            # "usage resets MM/dd HH:MM" = the real resume time; the watchdog reads it before the am/pm text
            # (the rig lane keeps drafting approved cards on its own thread meanwhile)
            until = datetime.fromtimestamp(BLOCKED["until"])
            if cards:
                idle_reason("usage-limit", f"resets {until:%H:%M} ({len(cards)} approved; usage resets "
                                           f"{until:%m/%d %H:%M}; {BLOCKED['why'][:80]})")
            try:
                offhours_tick()   # Claude is out: keep Gemini + the rig busy on the backlog
            except Exception as e:
                log(f"off-hours: {e}")
            STATE["work_waiting"] = 0   # Claude is out: nothing the agent itself can run until the reset
            return "paused"
        today = datetime.now().strftime("%Y-%m-%d")
        if STATE.get("day") != today:
            STATE.update(day=today, done_today=0, runs_today=0)
        if datetime.now().hour == 18 and STATE.get("passes_day") != today:   # nightly pass counts reset at 18:00
            STATE.update(passes={}, passes_day=today)
        skipped, nj, capped, stop = [], [], [], None
        for c in cards:
            if ran >= MAX_PER_POLL or BLOCKED["until"] > time.time():
                break
            if MAX_PER_DAY and STATE.get("runs_today", 0) >= MAX_PER_DAY:
                log(f"daily cap of {MAX_PER_DAY} runs reached - resumes after midnight")
                stop = ("cap-reached", f"daily cap of {MAX_PER_DAY} runs, resumes after midnight")
                break
            if needs_jake_blocked(c):
                nj.append(c["task"])
                continue   # don't re-dispatch until Jake edits the card
            if STATE.get("passes", {}).get(c["id"], 0) >= (RIG_DRAFT_MAX_PASSES if c["id"].replace("-", "")
                                                            in RIG_DRAFT_CARDS else MAX_PASSES_PER_NIGHT):
                capped.append(c["task"])
                continue
            ok, is_paused, why = hub_verify(c)
            if is_paused:
                log("hub says PAUSED - not starting anything")
                stop = ("blocked-other", "the hub is PAUSED (resume it in the app)")
                break
            if not ok:
                skipped.append(f"{c['task'][:50]} ({why})")
                continue
            STATE.pop("claim_lost", None)
            work_one(c)
            if STATE.pop("claim_lost", None):
                stop = ("claim-lost", f"'{c['task'][:60]}' was claimed by another machine first")
                continue
            ran += 1
            STATE["runs_today"] = STATE.get("runs_today", 0) + 1
        if skipped:
            log(f"skipped {len(skipped)}: " + "; ".join(skipped)[:600])
        # runnable cards still waiting: the rig's autosleep / idle shutdown must not power off while this is > 0
        STATE["work_waiting"] = 0 if stop and stop[0] in ("blocked-other", "cap-reached") else \
            max(0, len(cards) - ran - len(nj) - len(capped) - len(skipped))
        log(f"poll: {len(cards)} approved card(s) for {ME}, ran {ran}")
        if ran:
            STATE["last_run"] = time.time()
            idle_reason(None)
        elif cards:
            first3 = lambda xs: "; ".join(x[:50] for x in xs[:3])
            if BLOCKED["until"] > time.time():
                stop = ("usage-limit", f"resets {datetime.fromtimestamp(BLOCKED['until']):%H:%M} (hit during this poll)")
            elif stop is None and len(nj) == len(cards):
                stop = ("all-needs-jake", f"{len(nj)} card(s): {first3(nj)}")
            elif stop is None and capped and len(nj) + len(capped) == len(cards):
                stop = ("cap-reached", f"{len(capped)} card(s) at the nightly pass cap (resets 18:00): {first3(capped)}"
                                       + (f"; {len(nj)} need Jake" if nj else ""))
            elif stop is None:
                stop = ("blocked-other", f"{len(skipped)} not verified by the hub ({first3(skipped)}), {len(nj)} need "
                                         f"Jake, {len(capped)} at the pass cap")
            idle_reason(*stop)
        STATE["more_waiting"] = bool(ran and len(cards) > ran)
        if STATE["more_waiting"]:
            WAKE.set()
        if not ran and ME == "homebase" and not (MAX_PER_DAY and STATE.get("runs_today", 0) >= MAX_PER_DAY):
            try:
                offhours_tick()   # free prep first (Gemini / rig), then at most 1 Claude idle job per hour
            except Exception as e:
                log(f"off-hours: {e}")
            idle_tick()   # nothing runnable this cycle
        if ME == "homebase":
            wake_machines_with_work()
        STATE["last_error"] = None
        return f"{len(cards)} task(s)"
    except Exception as e:
        STATE["last_error"] = str(e)
        log(f"poll error: {e}")
        idle_reason("error", str(e)[:200])
        return f"error: {e}"
    finally:
        LOCK.release()


# ------------------------------------------------------------------ coordinator (homebase)

def is_up(name):
    try:  # the other machine's agent answers only when that machine is awake
        tell(name, "status")
        return True
    except Exception:
        return False


def wake(name):
    try:
        req = urllib.request.Request(f"{WAKER}/wake/{name}", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=120).read()
        return True
    except Exception as e:
        log(f"wake {name} via waker failed: {e}")
        return False


def tell(name, what="check"):
    host = MACHINES[name][0].lower()
    url = f"https://{host}.{TAILNET}:8443/{what}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read().decode()


def rig_ready():
    """Rig counts as up only when its Tailscale address answers AND Ollama responds."""
    try:
        with urllib.request.urlopen(BRAIN + "/api/tags", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def wake_rig_and_wait(max_min=5):
    """WoL through the Waker, then poll every 10 s for up to max_min until Tailscale + Ollama answer."""
    if rig_ready():
        return True
    STATE["waking_rig"] = True
    try:
        log("rig asleep - sending wake (WoL) and waiting for Tailscale + Ollama")
        wake("rig")
        end = time.time() + max_min * 60
        while time.time() < end:
            if rig_ready():
                log("rig is up")
                return True
            time.sleep(10)
        log(f"rig didn't wake in {max_min} min")
        return False
    finally:
        STATE["waking_rig"] = False


def wake_machines_with_work():
    for name in ("rig", "laptop"):
        try:
            cards = [c for c in approved_for(name) if not (c["notes"] or "").startswith("NEEDS JAKE:")]
            if not cards:
                continue
            if name == "rig":
                if rig_ready() and is_up("rig"):
                    continue
                if wake_rig_and_wait():
                    time.sleep(20)   # give the rig's agent a moment after Ollama is up
                    tell("rig", "check")
                else:
                    tries = STATE.setdefault("rig_wake_fails", 0) + 1
                    STATE["rig_wake_fails"] = tries
                    for c in ([] if gh() else cards[:3]):
                        update(c["id"], agent_log=f"{datetime.now():%m/%d %H:%M} {ME}: rig didn't wake in 5 min "
                                                  f"(try {tries}/3) - left Approved, retrying next cycle")
                    if tries >= 3:
                        notify("Rig won't wake", "3 wake tries failed with rig work waiting - NEEDS JAKE")
                        STATE["rig_wake_fails"] = 0
                continue
            if not is_up(name):
                log(f"{name} has approved work and is asleep - waking it")
                if wake(name):
                    time.sleep(60)
                    tell(name, "check")
        except Exception as e:
            log(f"wake-for-work {name}: {e}")
    if rig_ready():
        STATE["rig_wake_fails"] = 0


def route_unassigned():
    """4x/day: give every approved Claude card with no Machine a home."""
    if gh():
        return   # GitHub cards always carry a machine:* label (machine:any = either PC)
    cards = query({"and": [{"property": "Status", "select": {"equals": "Approved"}},
                           {"property": "Owner", "select": {"equals": "Claude"}},
                           {"property": "Machine", "select": {"is_empty": True}}]})
    for c in cards:
        ok, is_paused, _ = hub_verify(c)
        if not ok or is_paused:
            continue
        m = pick_machine(c)
        update(c["id"], machine=m)
        log(f"routed '{c['task']}' -> {m}")
        if m != ME:
            try:
                if not is_up(m):
                    wake(m)
                    time.sleep(60)
                tell(m, "check")
            except Exception as e:
                log(f"could not poke {m}: {e}")


def pick_machine(c):
    text = f"{c['task']} {c['project']} {c['notes']}".lower()
    if any(k in text for k in ("gpu", "ollama", "local model", "jellyfin", "media server", "adguard", "blender", "print")):
        return "rig"
    try:  # let the rig's local LLM decide when it's awake
        prompt = ("Pick the best machine for this task. rig = gaming PC with GPU, local AI, media server. "
                  "laptop = school laptop, often off. homebase = always-on small laptop, default for "
                  "research, writing, Notion/Drive work. Answer with one word: rig, laptop, or homebase.\n"
                  f"Task: {c['task']}\nNotes: {c['notes'][:500]}")
        body = json.dumps({"model": "jarvis", "prompt": prompt, "stream": False, "think": False}).encode()
        req = urllib.request.Request(BRAIN + "/api/generate", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ans = json.loads(r.read())["response"].strip().lower()
        for m in ("rig", "laptop", "homebase"):
            if m in ans:
                return m
    except Exception:
        pass
    return "homebase"


# ------------------------------------------------------------------ heartbeat / idle mode / overnight report (homebase)

def heartbeat():
    if ME != "homebase":
        return
    try:
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "machine": ME,
                                "running": STATE["running"], "runs_today": STATE.get("runs_today", 0),
                                "worker": worker_line(), "idle_reason": STATE.get("idle_reason"),
                                "rig": STATE.get("rig_lane", "Rig: lane not started")}))
    except Exception as e:
        log(f"heartbeat: {e}")


IDLE_PROMPT = """You are the Jarvis agent on homebase in IDLE MODE: no approved card is waiting, so do one piece
of READ-ONLY research or drafting from Jake's backlog. Topic:

{topic}

Rules:
- Rig first: if the rig's Ollama answers at {brain} (model qwen3.6:35b), use it for bulk drafting
  (python + its /api/generate); use your own effort mainly to find and check sources.
- Only read and research. Write your results as Markdown in {out} (one file for this topic). You may read
  Notion for context. Never change a system, a setting, a Notion card, or anything outside {out}. Never spend,
  post, email or delete.
- If you find something worth DOING (changes a system, spends, posts, emails, deletes), don't do it. Add a line:
  PROPOSAL: <short title> | <one sentence on what and why>
- Research worth keeping as Jarvis training data needs no approval: add a line
  TRAINING_ADD: <full path of your file in {out}> | raw | <sources>. The Worker files it (logged, undoable).
- Stop within {minutes} minutes. End with a 2-4 sentence summary.
"""


def _night():
    return datetime.now().hour >= 23 or datetime.now().hour < 6


def next_backlog_item():
    path = os.path.join(IDLE_DIR, "backlog.md")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return None, None, None
    for i, l in enumerate(lines):
        if l.lstrip().startswith(("- [ ]", "- [~]")):   # [~] = Gemini/rig already prepped notes for Claude
            return path, lines, i
    return path, lines, None


def _slug(t):
    import re
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:50]


def offhours_tick():
    """No Claude needed: every OFFHOURS_EVERY_MIN, prep the next unchecked backlog topic with Gemini research
    (non-private topics), or a rig draft when Gemini is unavailable. The line becomes "- [~]" and the Claude idle
    job later only verifies and finishes it."""
    if time.time() - STATE.get("offhours_last", 0) < OFFHOURS_EVERY_MIN * 60:
        return
    STATE["offhours_last"] = time.time()
    path = os.path.join(IDLE_DIR, "backlog.md")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return
    out = os.path.join(IDLE_DIR, "out")
    os.makedirs(out, exist_ok=True)
    for i, l in enumerate(lines):
        if not l.lstrip().startswith("- [ ]"):
            continue
        topic = l.split("]", 1)[1].strip()
        if private({"task": topic}):
            continue   # client / clinical / personal topics stay with Claude + the rig on its own jobs
        notes = os.path.join(out, _slug(topic) + "-prep.md")
        who = "gemini" if gemini("research", topic, notes) else None
        if not who and (rig_ready() or (_night() and wake_rig_and_wait())):
            try:
                txt_ = rig_generate("Draft research notes for this topic for a small home-automation / side-business "
                                    "project. Short answer first, then specific resources with why they fit and cost. "
                                    "Mark anything you are not sure of as [UNVERIFIED].\n\nTopic: " + topic)
                with open(notes, "w", encoding="utf-8") as f:
                    f.write(txt_ + "\n\n_drafted by Jarvis (rig), not verified_\n")
                who = "rig"
            except Exception as e:
                log(f"off-hours rig draft: {e}")
        if not who:
            log("off-hours: Gemini and the rig both unavailable - nothing prepped")
            return
        lines[i] = l.replace("- [ ]", f"- [~] ({who} {datetime.now():%m/%d %H%M}: {notes})", 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        log(f"off-hours prep ({who}): {topic[:70]}")
        return


RIG_CARD_SYSTEM = """You are Jake's local drafting model on his rig. Claude, the agent that normally works his approved
Notion task cards, is out of usage right now, so you draft this card instead. You cannot run commands, open files,
browse or change anything: you only write. Produce the most useful draft you can, in clean Markdown:
- # <card title>, then "Status: rig draft for review, not done" on its own line.
- The deliverable itself when the card asks for writing, research, a plan, a list or a template.
- When the card needs actions on a machine or an account: a step-by-step plan, with any script or command as a
  draft in a code block (marked [UNVERIFIED HIGH]), plus what must be checked on the machine before it is run.
- Use the card notes and STATUS.md (earlier progress) when given: continue from them, don't restart.
- End with "## Next for Claude": 3-6 bullets on what still needs doing, checking or running.
"""


def _rig_cards_load():
    try:
        with open(RIG_CARDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _rig_cards_save(d):
    with open(RIG_CARDS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)


def rig_card_tick(cards, limit=None):
    """9/30 FIX NOW: while Claude is out of usage, approved cards keep moving. Each poll, top up C:\\Jarvis\\jobs\\in
    with rig drafting jobs (worker.ps1 runs them on the rig's Ollama, no Claude) until RIG_CARD_MAX_QUEUED card jobs
    are waiting. The draft lands in the card's work folder as rig-draft.md; Claude starts from it after the reset."""
    import re
    if ME != "homebase" or not os.path.isdir(JOBS_DIR):
        return
    inq = os.path.join(JOBS_DIR, "in")
    waiting = [n for n in os.listdir(inq) if n.startswith("card-") and n.endswith(".json")] if os.path.isdir(inq) else []
    room = RIG_CARD_MAX_QUEUED - len(waiting)
    if limit is not None:
        room = min(room, limit)
    if room <= 0:
        return 0
    jobs = _rig_cards_load()
    last = {}   # card id -> newest queue time
    for j in jobs.values():
        last[j["card"]] = max(last.get(j["card"], 0), j["queued"])
    draftable = re.compile(r"draft|write|research|plan|outline|template|guide|list|summar|compare|proposal|email|"
                           r"letter|resume|checklist|idea|script|copy|brief|report|design|spec", re.I)
    todo = [c for c in cards
            if time.time() - last.get(c["id"], 0) > RIG_CARD_REDRAFT_H * 3600
            and not needs_jake_blocked(c)]
    todo.sort(key=lambda c: (not draftable.search(f"{c['task']} {c.get('type', '')}"), c["priority"] or "P9"))
    try:
        with open(RIG_RULES_FILE, encoding="utf-8") as f:
            rules = f.read()
    except Exception:
        rules = ""
    for c in todo[:room]:
        if c.get("number"):
            try:
                gh_full(c)
            except Exception as e:
                log(f"rig card: couldn't read #{c['number']}: {e}")
                continue
        d = card_dir(c)
        name = f"card-{_slug(c['task'])[:40]}-{datetime.now():%m%d%H%M%S}"
        status_md = os.path.join(d, "STATUS.md")
        job = {"task": f"card:{c['task'][:80]}", "model": "qwen3.6:35b", "num_ctx": 32768, "max_chars_per_file": 20000,
               "system": (rules + "\n\n" if rules else "") + RIG_CARD_SYSTEM,
               "prompt": f"CARD: {c['task']}\nPROJECT: {c['project'] or '-'}   PRIORITY: {c['priority'] or '-'}   "
                         f"TYPE: {c.get('type') or '-'}\nNOTES FROM THE CARD:\n{(c['notes'] or '(none)')[:4000]}",
               "files": [status_md] if os.path.exists(status_md) else [],
               "output_md": os.path.join(d, "rig-draft.md")}
        with open(os.path.join(inq, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(job, f, indent=1)
        jobs[name] = {"card": c["id"], "task": c["task"], "dir": d, "queued": time.time(), "done": None}
        log(f"rig card job queued (Claude out): {name} <- {c['task'][:70]}")
    _rig_cards_save(jobs)
    return min(room, len(todo))


def rig_card_collect():
    """Log each finished rig card job and note it on the card (Agent log only; Status is never touched)."""
    if ME != "homebase" or not os.path.isdir(JOBS_DIR):
        return
    jobs, changed = _rig_cards_load(), False
    for name, j in jobs.items():
        if j.get("done"):
            continue
        out = os.path.join(JOBS_DIR, "out", name + ".md")
        if os.path.exists(os.path.join(JOBS_DIR, "in", name + ".json")) or not os.path.exists(out):
            if time.time() - j["queued"] > 12 * 3600:
                j["done"], changed = "expired", True
                log(f"rig card job expired (never ran): {name}")
            continue
        with open(out, encoding="utf-8", errors="replace") as f:
            text = f.read()
        ok = not text.startswith("ERROR")
        src = os.path.join(JOBS_DIR, "done", name + ".json")
        if not ok and "connect" in text[:300].lower() and not j.get("retry_of") and os.path.exists(src):
            # rig dropped off (sleep / Ollama restart) after the worker's wake check: queue it once more
            new = f"{name}-r"
            shutil.copy2(src, os.path.join(JOBS_DIR, "in", new + ".json"))
            jobs[new] = dict(j, queued=time.time(), done=None, retry_of=name)
            j["done"], changed = "retried", True
            log(f"rig card job {name}: rig unreachable ({text[7:90].strip()}) - re-queued as {new}")
            break   # dict changed size; the rest are picked up next poll
        draft = os.path.join(j["dir"], "rig-draft.md")
        if ok and not os.path.exists(draft):
            shutil.copy2(out, draft)
        flags = sum(text.count(k) for k in ("UNVERIFIED HIGH", "NEED:", "UNFILLED:"))
        j["done"], changed = ("ok" if ok else "error"), True
        log(f"rig card job {'done' if ok else 'FAILED'} (no Claude): {j['task'][:60]} -> "
            f"{draft if ok else out} ({len(text)} chars, {flags} high/need flags)")
        if ok:
            try:
                note = (f"{datetime.now():%m/%d %H:%M} {ME}: Claude was out of usage, so the rig drafted this card: "
                        f"{draft} ({flags} high/need flags). Claude reviews and finishes it after the reset.")
                if str(j["card"]).startswith("gh-"):
                    if gh():
                        ghq.comment(int(j["card"][3:]), note)
                else:
                    update(j["card"], agent_log=note)
            except Exception as e:
                log(f"rig card note: {e}")
    if changed:
        _rig_cards_save(jobs)


# ------------------------------------------------------------------ rig lane (Jake 9/30 20:32 UTC: "the rig is idle without tasks")
# Homebase keeps the rig's Ollama busy on its own, with or without Claude, through the existing jobs\in queue
# (worker.ps1 wakes the rig, adds the Rig Job Rules, logs to training\raw). Whenever no rig job has been queued
# or running for RIG_LANE_IDLE_SEC, one job is dispatched: (a) a draft of an approved card, (b) the next backlog
# topic (clinical topics too: they stay on the rig, never Gemini), (c) new backlog topics when fewer than
# BACKLOG_MIN_OPEN are left. The rig only writes; anything that would spend/post/install/delete comes back as a
# PROPOSAL line -> an Owner=Jake Inbox card, never executed. Off switch: %USERPROFILE%\JarvisAgent\rig-lane.off
RIG_LANE_IDLE_SEC = 120
RIG_LANE_FILE = os.path.join(STATE_DIR, "rig-lane.json")
RIG_LANE_OFF = os.path.join(STATE_DIR, "rig-lane.off")
BACKLOG_MIN_OPEN = 10
CARDS_CACHE = {"t": 0, "cards": []}
PASSIVE_NOTES = os.path.join(IDLE_DIR, "out", "passive-income-research-from-the-overnight-job-ide-prep.md")

RIG_LANE_SYSTEM = """You are Jake's local research and drafting model on his rig, working through his backlog while
nobody is watching. You cannot run commands, browse or change anything: you only write. Write clean Markdown:
- # <topic>, then "Status: rig draft, not verified" on its own line.
- Short answer first, then specifics: options, resources (name them, say why they fit and what they cost),
  steps. Mark anything you are not sure of as [UNVERIFIED]. Never invent citations, links or prices.
- If the topic suggests something to DO that would spend money, post, sign up, install or delete, don't do it or
  describe it as done: add a line  PROPOSAL: <short title> | <one sentence on what and why>  (at most 3).
- End with "## Next for Claude": 3-5 bullets on what to verify or finish."""

RIG_TOPICS_PROMPT = """Jake's open projects: Phase 2 passive income (low-risk income that fits a GPU rig with a local LLM,
3D printer, digital products), Fieldwork Clinical (occupational-therapy fieldwork curriculum and storefront),
MTG bulk scanner (Magic: The Gathering card scanning / sorting / collection sheet), and Jarvis itself (home
automation, Home Assistant, tailnet, phone app, local LLM on the rig).
Topics already on the backlog (done or open) - do NOT repeat or rephrase any of them:
{existing}

Write 12 NEW read-only research topics that would move these projects forward, spread across the projects,
each answerable by reading and writing only (no purchases, sign-ups, installs or posting). Output ONLY the
12 lines, each exactly in this form:
- [ ] <Project>: <specific research question or drafting task, one line>"""


def _lane_load():
    try:
        with open(RIG_LANE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _lane_save(d):
    with open(RIG_LANE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)


def _backlog():
    path = os.path.join(IDLE_DIR, "backlog.md")
    try:
        with open(path, encoding="utf-8") as f:
            return path, f.read().splitlines()
    except FileNotFoundError:
        return path, []


def _backlog_write(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _lane_queue(jobs, kind, title, prompt, dest, files=(), system=RIG_LANE_SYSTEM, **extra):
    name = f"lane-{kind}-{_slug(title)[:36]}-{datetime.now():%m%d%H%M%S}"
    job = {"task": f"lane:{title[:80]}", "model": "qwen3.6:35b", "num_ctx": 32768, "max_chars_per_file": 20000,
           "system": system, "prompt": prompt, "files": [f for f in files if os.path.exists(f)]}
    with open(os.path.join(JOBS_DIR, "in", name + ".json"), "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1)
    jobs[name] = dict({"kind": kind, "title": title, "dest": dest, "queued": time.time(), "done": None}, **extra)
    _lane_save(jobs)
    log(f"RIG-LANE: {name} started ({kind}: {title[:70]})")
    return name


def _lane_proposals(text, title):
    for l in [l for l in text.splitlines() if l.strip().startswith("PROPOSAL:")][:3]:
        t, _, why = l.split(":", 1)[1].partition("|")
        try:
            create_card(t.strip()[:150], f"Rig-lane proposal from '{title[:80]}': {why.strip()} "
                                         f"(drafted by Jarvis (rig), nothing was executed)", None, owner="Jake")
        except Exception as e:
            log(f"RIG-LANE proposal card: {e}")


def _lane_add_topics(text):
    import difflib, re
    path, lines = _backlog()
    bare = lambda l: re.sub(r"^\s*\([^)]*\)\s*|\s*<!--.*?-->", "", l.split("]", 1)[-1])   # drop "(rig ..: path)" / markers
    have = [_norm_title(bare(l)) for l in lines if re.match(r"\s*- \[.\]", l)]
    added = 0
    for l in text.splitlines():
        m = re.match(r"\s*-\s*\[ \]\s*(.{12,300})$", l)
        if not m or added >= 12:
            continue
        t = m.group(1).strip()
        n = _norm_title(t)
        if any(difflib.SequenceMatcher(None, n, h).ratio() >= 0.75 for h in have):
            continue
        lines.append(f"- [ ] {t}  <!-- rig-lane topic {datetime.now():%m/%d} -->")
        have.append(n)
        added += 1
    if added:
        _backlog_write(path, lines)
    log(f"RIG-LANE: {added} new backlog topic(s) added")


def rig_lane_collect(jobs):
    changed = False
    for name, j in jobs.items():
        if j.get("done"):
            continue
        out = os.path.join(JOBS_DIR, "out", name + ".md")
        if os.path.exists(os.path.join(JOBS_DIR, "in", name + ".json")) or not os.path.exists(out):
            if time.time() - j["queued"] > 6 * 3600:
                j["done"], changed = "expired", True
                log(f"RIG-LANE: {name} expired (never ran in 6 h)")
            continue
        with open(out, encoding="utf-8", errors="replace") as f:
            text = f.read()
        ok = not text.startswith("ERROR")
        j["done"], changed = ("ok" if ok else "error"), True
        j["finished"] = os.path.getmtime(out)
        log(f"RIG-LANE: {name} finished ({(j['finished'] - j['queued']) / 60:.0f} min)"
            + ("" if ok else f" - ERROR {text[6:120].strip()}"))
        path, lines = _backlog()
        if j["kind"] == "topic" and j.get("line"):   # un-queue the line: [~] when drafted, back to [ ] on error
            for i, l in enumerate(lines):
                if l.startswith(j["line"]):
                    lines[i] = (l.replace("- [>]", f"- [~] (rig {datetime.now():%m/%d %H%M}: {j['dest']})", 1) if ok
                                else l.replace("- [>]", "- [ ]", 1))
                    _backlog_write(path, lines)
                    break
        if not ok:
            continue
        if j["kind"] == "topics":
            _lane_add_topics(text)
            continue
        os.makedirs(os.path.dirname(j["dest"]), exist_ok=True)
        shutil.copy2(out, j["dest"])
        _lane_proposals(text, j["title"])
    if changed:
        _lane_save(jobs)


def rig_lane_tick():
    import re
    if ME != "homebase" or not os.path.isdir(JOBS_DIR):
        return
    jobs = _lane_load()
    rig_lane_collect(jobs)
    try:
        rig_card_collect()   # card drafts: collected here now (was in poll), one thread owns the rig-card file
    except Exception as e:
        log(f"rig card collect: {e}")
    inq = os.path.join(JOBS_DIR, "in")
    waiting = sorted((n for n in os.listdir(inq) if n.endswith(".json")),
                     key=lambda n: os.path.getmtime(os.path.join(inq, n)))
    if waiting:
        STATE["rig_lane"] = f"Rig: busy with {waiting[0][:-5]}" + (f" (+{len(waiting) - 1} queued)" if len(waiting) > 1 else "")
        STATE["rig_lane_last_busy"] = time.time()
        return
    if os.path.exists(RIG_LANE_OFF):
        STATE["rig_lane"] = f"Rig: idle because the rig lane is switched off ({RIG_LANE_OFF})"
        return
    outd = os.path.join(JOBS_DIR, "out")
    newest = max((os.path.getmtime(os.path.join(outd, n)) for n in os.listdir(outd)), default=0) \
        if os.path.isdir(outd) else 0
    last = max(STATE.get("rig_lane_last_busy", 0), newest)
    if time.time() - last < RIG_LANE_IDLE_SEC:
        STATE["rig_lane"] = "Rig: finishing up (last job just ended)"
        return
    # (a) an approved card the rig hasn't drafted in RIG_CARD_REDRAFT_H
    if time.time() - CARDS_CACHE["t"] > 1800:
        try:
            CARDS_CACHE.update(t=time.time(), cards=approved_for(ME))
        except Exception as e:
            log(f"RIG-LANE: couldn't read the approved cards: {e}")
    if CARDS_CACHE["cards"] and rig_card_tick(CARDS_CACHE["cards"], limit=1):
        log("RIG-LANE: card draft started (see 'rig card job queued' above)")
        STATE["rig_lane_last_busy"] = time.time()
        return
    # (b) the next open backlog topic
    path, lines = _backlog()
    opened = [i for i, l in enumerate(lines) if l.lstrip().startswith("- [ ]")]
    if opened:
        i = opened[0]
        topic = re.sub(r"\s*<!--.*?-->", "", lines[i].split("]", 1)[1]).strip()
        dest = os.path.join(IDLE_DIR, "out", _slug(topic) + "-rig.md")
        key = lines[i][:60].replace("- [ ]", "- [>]", 1)
        lines[i] = lines[i].replace("- [ ]", "- [>]", 1)   # queued: idle/off-hours skip it until the rig is done
        _backlog_write(path, lines)
        _lane_queue(jobs, "topic", topic, f"TOPIC: {topic}", dest, line=key)
        STATE["rig_lane_last_busy"] = time.time()
        return
    # (c) backlog running low: the rig proposes new topics
    if len(opened) < BACKLOG_MIN_OPEN and not any(j["kind"] == "topics" and not j.get("done") for j in jobs.values()):
        existing = "\n".join("- " + re.sub(r"^\s*\([^)]*\)\s*|\s*<!--.*?-->", "", l.split("]", 1)[1]).strip()[:140]
                             for l in lines if re.match(r"\s*- \[.\]", l))
        _lane_queue(jobs, "topics", "new backlog topics", RIG_TOPICS_PROMPT.format(existing=existing or "(none)"),
                    "", files=[PASSIVE_NOTES], system="You write concise research-topic lists. Output only the list.")
        STATE["rig_lane_last_busy"] = time.time()
        return
    STATE["rig_lane"] = "Rig: idle because nothing is left to draft (cards drafted <20 h ago, backlog empty)"


def rig_lane_loop():
    while True:
        try:
            rig_lane_tick()
        except Exception as e:
            STATE["rig_lane"] = f"Rig: idle because the rig lane hit an error: {e}"
            log(f"RIG-LANE error: {e}")
        time.sleep(60)


def idle_tick():
    now = time.time()
    if STATE.get("power_pending"):
        return
    if STATE["running"] or now - STATE.get("last_run", STATE.get("boot", now)) < IDLE_AFTER_MIN * 60:
        return
    if now - STATE.get("idle_last", 0) < 3600:
        return                                                    # at most 1 idle Claude job per hour
    night_key = (datetime.now() - __import__("datetime").timedelta(hours=12)).strftime("%Y-%m-%d")
    if STATE.get("idle_night") != night_key:
        STATE.update(idle_night=night_key, idle_night_min=0)
    if _night() and STATE["idle_night_min"] >= IDLE_MAX_NIGHT_MIN:
        return                                                    # 2 h of Claude per night
    path, lines, i = next_backlog_item()
    if i is None:
        return
    exe = claude_exe()
    if not exe:
        return
    topic = lines[i].split("]", 1)[1].strip()
    if lines[i].lstrip().startswith("- [~]"):   # prepped by Gemini/the rig: Claude verifies and finishes
        import re
        m = re.match(r"\((\w+) [^:]*: ([^)]+)\)\s*(.*)", topic)
        if m:
            topic = (f"{m.group(3)}\n\n{m.group(1).title()} already drafted notes in {m.group(2)}. Read them first, "
                     f"verify the claims you keep (items marked unverified especially), fix errors and finish. "
                     f"Don't redo the research from scratch.")
    out = os.path.join(IDLE_DIR, "out")
    os.makedirs(out, exist_ok=True)
    if _night():
        wake_rig_and_wait()                                       # overnight window: rig may be woken for drafting
    minutes = 40
    prompt = IDLE_PROMPT.format(topic=topic, brain=BRAIN, out=out, minutes=minutes - 5) + notes_block()
    allow = ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite",
             f"Write(//{out.replace(chr(92), '/')}/**)", f"Edit(//{out.replace(chr(92), '/')}/**)",
             "mcp__claude_ai_Notion__notion-fetch", "mcp__claude_ai_Notion__notion-search",
             "Bash(python *)", "PowerShell(python *)", "Bash(curl *)"]
    cmd = claude_cmd(exe, [out], allow=allow)
    log(f"idle job: {topic}")
    STATE.update(running=f"idle: {topic}", idle_last=now)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=out, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=minutes * 60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = r.stdout or ""
        idle_err, idle_rc = r.stderr or "", r.returncode
    except subprocess.TimeoutExpired:
        output = "idle job hit its time limit"
        idle_err, idle_rc = "", 0
    finally:
        STATE["running"] = None
        STATE["idle_night_min"] = STATE.get("idle_night_min", 0) + (time.time() - t0) / 60
    fail = claude_failed(output, idle_err, idle_rc) if output != "idle job hit its time limit" else None
    if fail:
        _block(fail)
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    with open(os.path.join(LOG_DIR, f"idle-{stamp}.txt"), "w", encoding="utf-8") as f:
        f.write(output)
    lines[i] = lines[i].replace("- [ ]", f"- [x] ({stamp})", 1).replace("- [~]", f"- [x] ({stamp})", 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    training_lines(output.splitlines())
    ack_notes(output.splitlines())
    for l in output.splitlines():
        if l.startswith("PROPOSAL:"):
            title, _, why = l.split(":", 1)[1].partition("|")
            try:
                create_card(title.strip(), f"Idle-mode proposal ({topic}): {why.strip()}", None)
            except Exception as e:
                log(f"proposal card failed: {e}")
    log(f"idle job done: {topic}")


def overnight_report():
    """07:00: one Notion page 'Overnight report - <date>' under Jarvis Command Center."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if now.hour != 7 or STATE.get("report_day") == today:
        return
    STATE["report_day"] = today
    write_overnight_report(now)


def report_sections(now, hours=13):
    """Jake 9/30: page 1 = one-minute summary of work done (+ per-card Claude usage), page 2 = still to do,
    page 3 = ideas to keep honing. Returns {page title: [(heading, [lines])]}."""
    import csv
    from collections import defaultdict
    since = (now - __import__("datetime").timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    done, needs, failed, pauses, ckpt, prep, idle = [], [], [], [], [], [], []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for l in f:
            if l[:16] < since:
                continue
            msg = l.split("] ", 1)[-1].strip()
            if msg.startswith("needs Jake:"):
                needs.append(msg.split(":", 1)[1].strip())
            elif msg.startswith(("task failed", "follow-up card failed", "overnight report failed")):
                failed.append(f"{l[11:16]} {msg}")
            elif msg.startswith("BLOCKED"):
                pauses.append(f"{l[11:16]} {msg}")
            elif msg.startswith("checkpoint "):
                ckpt.append(msg)
            elif msg.startswith("off-hours prep"):
                prep.append(msg.split(":", 1)[1].strip())
            elif msg.startswith("idle job done:"):
                idle.append(msg.split(":", 1)[1].strip())
    use = defaultdict(lambda: [0.0, 0])
    try:
        with open(USAGE_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["start"].replace("T", " ")[:16] >= since:
                    u = use[row["task"]]
                    u[0] += float(row["minutes"] or 0)
                    u[1] += 1
    except FileNotFoundError:
        pass
    iso = (now - __import__("datetime").timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:00")
    done_cards = query({"and": [{"property": "Status", "select": {"equals": "Done"}},
                                {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": iso}}]})
    staged = query({"property": "Status", "select": {"equals": "Staged"}})
    waiting = [c for c in approved_for(ME) if not needs_jake_blocked(c)]
    props = query({"and": [{"property": "Status", "select": {"equals": "Inbox"}},
                           {"property": "Owner", "select": {"equals": "Jake"}}]})
    top = sorted(use.items(), key=lambda kv: -kv[1][0])
    total = sum(v[0] for v in use.values())
    p1 = [("At a glance", [f"{len(done_cards)} card(s) done, {len(ckpt)} checkpoint pass(es), {len(staged)} waiting "
                           f"on you, {len(failed)} failure(s), {len(pauses)} Claude usage pause(s), "
                           f"{round(total)} Claude min over {sum(v[1] for v in use.values())} run(s)."]),
          ("Done", [f"{c['task']} - {c['url']}" for c in done_cards] or ["Nothing finished."]),
          ("Training data added (last 24 h, no approval needed)", training_summary(24)),
          ("Waiting on you", [f"{c['task']}: {(c['notes'] or '')[12:160]} - {c['url']}" for c in staged]
           or ["Nothing."]),
          ("Failed", failed or ["Nothing."]),
          ("Claude usage pauses", pauses or ["None."]),
          ("Claude usage per card (min / runs)", [f"{round(v[0])} min / {v[1]} - {k}" for k, v in top[:15]]
           or ["No Claude runs."])]
    p2 = [("Approved and queued", [f"{c['priority'] or '--'} {c['task']} - {c['url']}" for c in waiting[:40]]
           or ["Queue empty."]),
          ("Mid-way (checkpointed)", ckpt or ["None."])]
    p3 = [("Proposals and follow-ups in your Inbox", [f"{c['task']} - {c['url']}" for c in props[:30]] or ["Empty."]),
          ("Prepped by Gemini / the rig overnight", prep or ["None."]),
          ("Idle research finished (C:\\Jarvis\\idle\\out)", idle or ["None."])]
    return {"1 - Summary of work done": p1, "2 - Still to do": p2, "3 - Ideas to keep honing": p3}


def write_overnight_report(now):
    try:
        pages = report_sections(now)
    except Exception as e:
        log(f"overnight report: {e}")
        return
    md = f"# Overnight report - {now:%m/%d}\n"
    for title, secs in pages.items():
        md += f"\n# Page {title}\n" + "".join(f"\n## {h}\n" + "".join(f"- {x}\n" for x in xs) for h, xs in secs)
    rep_dir = r"C:\Jarvis\reports"
    os.makedirs(rep_dir, exist_ok=True)
    local = os.path.join(rep_dir, f"overnight-{now:%Y%m%d}.md")
    with open(local, "w", encoding="utf-8") as f:
        f.write(md)

    def blocks(secs):
        out = []
        for h, xs in secs:
            out.append({"object": "block", "type": "heading_2",
                        "heading_2": {"rich_text": [{"type": "text", "text": {"content": h}}]}})
            out += [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": x[:1900]}}]}} for x in xs[:40]]
        return out[:100]

    def page(parent, title, secs):
        return notion("POST", "pages", {"parent": {"page_id": parent}, "properties": {"title": {"title": [
            {"type": "text", "text": {"content": title}}]}}, "children": blocks(secs)})

    try:
        (t1, s1), (t2, s2), (t3, s3) = pages.items()
        root = page(COMMAND_CENTER, f"Overnight report - {now:%m/%d}", s1)   # opens on the 1-minute summary
        page(root["id"], "Page " + t2, s2)
        page(root["id"], "Page " + t3, s3)
        log("overnight report posted")
        notify("Overnight report ready", f"{s1[0][1][0][:140]}")
    except Exception as e:
        log(f"overnight report failed: {e} - saved to {local}")
        notify("Overnight report (local file)", f"Notion refused ({e}); report is in {local}")


def scheduler():
    routed = set()
    while True:
        poll()
        if ME == "homebase":
            try:
                overnight_report()
            except Exception as e:
                log(f"overnight report: {e}")
        if ME == "homebase":
            today, now = datetime.now().date(), datetime.now().strftime("%H:%M")
            due = [t for t in ROUTE_TIMES if now >= t and (today, t) not in routed]
            if due:
                try:
                    route_unassigned()
                except Exception as e:
                    log(f"route error: {e}")
                routed.update((today, t) for t in due)
        more = STATE.pop("more_waiting", False)
        WAKE.clear()
        # a /check-triggered poll that ran a card sets WAKE, so the next card starts ~1 min later, not after POLL_MIN
        end = time.time() + (FOLLOW_ON_MIN if more else POLL_MIN) * 60
        if BLOCKED["until"] > time.time():   # 9/30 fix: poll right when the usage window resets, not up to 15 min later
            end = min(end, BLOCKED["until"] + 5)
        while time.time() < end:
            if WAKE.wait(min(LIGHT_POLL_SEC, max(1, end - time.time()))):
                WAKE.clear()
                time.sleep(FOLLOW_ON_MIN * 60)
                break
            if new_card_waiting():
                log("light poll: new approved card - checking now")
                break


def new_card_waiting():
    """60 s light check (one Notion query, no routing/unstick/wake): is there an Approved card we haven't seen?"""
    if BLOCKED["until"] > time.time() or LOCK.locked():
        return False
    try:
        ids = {c["id"] for c in approved_for(ME)}
    except Exception:
        return False
    return bool(ids - set(STATE.get("seen_ids", ids)))


def run_locked(c):
    with LOCK:
        work_one(c)


def _restart_when_idle(cmd, max_min=TASK_TIMEOUT_MIN + 20):
    """Phone-app restart queued behind a running card: poll/work_one/idle_tick claim nothing while power_pending
    is set; once the card is done and no poll holds LOCK, run shutdown /r. Gives up (and says so) after max_min."""
    t0 = time.time()
    while STATE.get("power_pending") == "restart":
        if not STATE.get("running") and not LOCK.locked():
            STATE.pop("power_pending", None)
            STATE["power_fired"] = time.time()
            subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log(f"POWER restart: card finished - {' '.join(cmd[:4])} (phone app, queued)")
            try:
                _post_notify({"title": "Rig restarting", "body": "Running card finished - restarting in 1 min."})
            except Exception:
                pass
            return
        if time.time() - t0 > max_min * 60:
            STATE.pop("power_pending", None)
            log(f"POWER restart dropped: card still running after {max_min} min ({str(STATE.get('running'))[:60]})")
            try:
                _post_notify({"title": "Rig restart not done", "body": f"Card still running after {max_min} min - "
                                                                       "claims resumed. Ask again to retry."})
            except Exception:
                pass
            return
        time.sleep(10)
    log("POWER restart wait ended (cancelled)")


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    def _ok(self):
        who = self.headers.get("Tailscale-User-Login")
        return who is None or who.lower() == OWNER_LOGIN  # None = local call on this machine

    def _send(self, code, obj):
        data = json.dumps(obj, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        if not self._ok():
            return self._send(403, {"error": "not Jake's tailnet login"})
        p = self.path.split("?")[0].rstrip("/")
        if p in ("", "/status"):
            return self._send(200, STATE)
        if p == "/check":
            # 9/30: every app approval pings /check; while Claude is out that was a full poll every 2-3 s (-> 429s)
            gap = time.time() - STATE.get("last_check_t", 0)
            if gap < (60 if BLOCKED["until"] > time.time() else 10):
                return self._send(200, {"ok": True, "machine": ME, "msg": "checked %d s ago" % gap})
            STATE["last_check_t"] = time.time()
            threading.Thread(target=poll, daemon=True).start()
            return self._send(200, {"ok": True, "machine": ME, "msg": "checking Notion now"})
        if p.startswith("/run/"):
            pid = p.split("/run/", 1)[1]
            c = card(notion("GET", f"pages/{pid}"))
            if c["status"] != "Approved" or not hub_verify(c)[0]:
                return self._send(409, {"error": "only cards Jake approved with his PIN can run"})
            threading.Thread(target=run_locked, args=(c,), daemon=True).start()
            return self._send(200, {"ok": True, "running": c["task"]})
        if p == "/route" and ME == "homebase":
            threading.Thread(target=route_unassigned, daemon=True).start()
            return self._send(200, {"ok": True})
        if p in ("/power/shutdown", "/power/restart", "/power/cancel"):
            return self._power(p.rsplit("/", 1)[1])
        if p == "/log":
            with open(LOG, encoding="utf-8") as f:
                return self._send(200, {"log": f.readlines()[-80:]})
        return self._send(404, {"error": "try /status, /check, /run/<page-id>, /log"})

    def _power(self, action):
        """10/2 (Jake approved, app's Rig Power buttons): POST /power/shutdown -> shutdown /s /t 60,
        POST /power/restart -> shutdown /r /t 60. Never /f. Refused (409 busy) while a card runs or an off-switch
        file exists. ?dry=1 returns what it would do without doing it.
        10/2 later (Jake): restart is his own request, so off.txt / no-autoshutdown don't block it, and a running
        card doesn't refuse it: no new claims, wait for that card to finish, then restart (202 queued).
        POST /power/cancel drops a queued restart (and runs shutdown /a if it already fired)."""
        from urllib.parse import urlparse, parse_qs
        dry = parse_qs(urlparse(self.path).query).get("dry", ["0"])[0] in ("1", "true", "yes")
        if self.command != "POST":
            return self._send(405, {"error": "POST only", "dry_run_hint": f"POST /power/{action}?dry=1"})
        if action == "cancel":
            was = STATE.pop("power_pending", None)
            fired = STATE.pop("power_fired", None)
            if fired and not dry:
                subprocess.run(["shutdown.exe", "/a"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log(f"POWER cancel (was: {was or 'nothing queued'}{', shutdown /a' if fired else ''})")
            return self._send(200, {"ok": True, "cancelled": was, "aborted_countdown": bool(fired), "dry": dry})
        cmd = ["shutdown.exe", "/s" if action == "shutdown" else "/r", "/t", "60", "/c",
               f"Jarvis: {action} requested from the phone app - in 1 min. Run 'shutdown /a' to cancel."]
        if action == "restart":
            if STATE.get("running") or LOCK.locked():
                if dry:
                    return self._send(202, {"ok": True, "dry": True, "would": "queue restart after the running card",
                                            "running": STATE.get("running")})
                if not STATE.get("power_pending"):
                    STATE["power_pending"] = "restart"
                    threading.Thread(target=_restart_when_idle, args=(cmd,), daemon=True).start()
                log(f"POWER restart queued: no new claims, restarting after '{(STATE.get('running') or 'poll')[:60]}' finishes")
                return self._send(202, {"ok": True, "queued": True, "waiting_for": STATE.get("running"),
                                        "cancel": "POST /power/cancel"})
            if dry:
                return self._send(200, {"ok": True, "dry": True, "would_run": " ".join(cmd[:4]), "action": action})
            STATE["power_fired"] = time.time()
            subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log(f"POWER restart: {' '.join(cmd[:4])} (phone app)")
            return self._send(200, {"ok": True, "action": action, "ran": " ".join(cmd[:4])})
        offs = [f for f in (r"C:\ProgramData\JarvisIdle\off.txt", r"C:\ProgramData\JarvisIdle\no-autoshutdown")
                if os.path.exists(f)]
        if STATE.get("running") or offs:
            why = f"card running: {STATE['running']}" if STATE.get("running") else f"off switch present: {offs[0]}"
            log(f"POWER {action} refused ({why}){' [dry run]' if dry else ''}")
            return self._send(409, {"error": "busy", "why": why, "dry": dry})
        if dry:
            return self._send(200, {"ok": True, "dry": True, "would_run": " ".join(cmd[:4]), "action": action})
        if action == "shutdown":
            try:   # same record the idle watcher writes (homebase's 60-min no-auto-wake cooldown reads it)
                with open(r"C:\ProgramData\JarvisIdle\last-shutdown.txt", "w", encoding="ascii") as f:
                    f.write(datetime.now().astimezone().isoformat() + "\n")
                with open(r"C:\ProgramData\JarvisIdle\last-shutdown-reason.txt", "w", encoding="ascii") as f:
                    f.write("phone app Rig Power: shutdown\n")
            except Exception as e:
                log(f"POWER: could not write last-shutdown.txt: {e}")
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log(f"POWER {action}: {' '.join(cmd[:4])} (phone app)")
        return self._send(200, {"ok": True, "action": action, "ran": " ".join(cmd[:4])})

    def _safe(self):
        try:
            self._route()
        except Exception as e:
            log(f"http error {self.path}: {e}")
            self._send(500, {"error": str(e)})

    do_GET = _safe
    do_POST = _safe

    def log_message(self, *a):
        pass


# ------------------------------------------------------------------ rig auto-sleep (Jake 9/28)
# The rig only needs to be up when there's work. It goes to Sleep (Wake-on-LAN still works) once, for
# AUTOSLEEP_MIN straight minutes: nobody has touched keyboard/mouse, no agent task is running, and the GPU is
# basically idle (<15%, so no local-LLM job, game or render). Jake gets a push 60 s before.
AUTOSLEEP_MIN = int(os.environ.get("JARVIS_AUTOSLEEP_MIN", "10"))


def _idle_sec():
    try:
        import ctypes

        class LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        lii = LII(); lii.cbSize = ctypes.sizeof(LII)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0
    except Exception:
        return 0


def _gpu_busy():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return max(int(x) for x in out.split()) >= 15
    except Exception:
        return True  # can't tell -> assume busy, never sleep blindly


def autosleep():
    quiet = 0
    while True:
        time.sleep(60)
        if os.path.exists(os.path.join(STATE_DIR, "no-autosleep.flag")):
            quiet = 0; continue
        if STATE["running"] or _idle_sec() < 600 or _gpu_busy():
            quiet = 0; continue
        if STATE.get("work_waiting") and BLOCKED["until"] < time.time():
            quiet = 0; continue   # 9/30: runnable approved cards waiting -> stay up and run them
        quiet += 1
        if quiet < AUTOSLEEP_MIN:
            continue
        _post_notify({"title": "Rig going to sleep", "body": f"Idle {AUTOSLEEP_MIN} min, no work queued. Wake it from the app."})
        time.sleep(60)
        if STATE["running"] or _idle_sec() < 60:
            quiet = 0; continue
        log("auto-sleep: idle, sleeping")
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        quiet = 0


def main():
    a = sys.argv[1:]
    if a[:1] == ["tell"] and len(a) >= 2:
        print(tell(a[1], a[2] if len(a) > 2 else "check"))
        return
    load_ghq()
    if a[:1] == ["selftest-failed"]:
        sys.exit(_selftest_claude_failed())
    if a[:1] == ["once"]:
        print(poll())
        return
    log(f"agent starting as '{ME}' (Claude Code: {claude_exe()}, queue: {'github ' + ghq.REPO if gh() else 'notion'})")
    threading.Thread(target=scheduler, daemon=True).start()
    if ME == "homebase":
        def beat():   # separate thread: a 40-min pass must not look like a dead Worker to the watchdog
            while True:
                heartbeat()
                time.sleep(60)
        threading.Thread(target=beat, daemon=True).start()
        threading.Thread(target=rig_lane_loop, daemon=True).start()   # keeps the rig busy, with or without Claude
    if ME == "rig":
        threading.Thread(target=autosleep, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


# card #437 budget guard (no repeat passes, route before Claude, 5-h budget, local steps, one to-do issue)
try:
    sys.path.insert(0, HERE); import budget_guard; budget_guard.install(globals())
except Exception as _e:
    log(f"budget guard not loaded: {_e}")
try:   # 10/2 local-model lane: text-only free-tier cards go to the rig's Ollama first (Jake), Claude fallback
    sys.path.insert(0, HERE); import local_lane; local_lane.install(globals())
except Exception as _e:
    log(f"local lane not loaded: {_e}")
try:   # card #608: model:local cards run on the local model only; lane cards still run while Claude is out
    sys.path.insert(0, HERE); import fallback_lane; fallback_lane.install(globals())
except Exception as _e:
    log(f"fallback lane not loaded: {_e}")
try:   # 10/2 Docker sandbox gate (Jake): code/install/medium+ cards tested in C:\Jarvis\sandbox before Jake sees them
    sys.path.insert(0, HERE); import sandbox_gate; sandbox_gate.install(globals())
except Exception as _e:
    log(f"sandbox gate not loaded: {_e}")


if __name__ == "__main__":
    main()
