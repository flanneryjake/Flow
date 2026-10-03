"""Budget guard for the Jarvis Worker (card #437, 10/02): stop stalling on Claude usage limits.

Drop this file next to agent.py. One hook at the end of agent.py (apply_budget_guard.py adds it):

    try:   # card #437 budget guard
        sys.path.insert(0, HERE); import budget_guard; budget_guard.install(globals())
    except Exception as _e:
        log(f"budget guard not loaded: {_e}")

install() wraps a few agent.py functions at runtime; nothing else in agent.py changes:
  hub_verify          + 5-hour budget gate and the no-repeat-pass rule, per card
  card_extra          + rig draft before Claude, routing / local-step rules in the prompt, light model when tight
  gh_finish           + "do the local step" (one retry instead of needs-jake) and SNOOZE_UNTIL lines
  _block              + learns how much a 5-hour window holds each time the usage limit is hit
  idle_reason         + "budget-hold" is a normal wait, not a stuck-Worker alert
  idle_tick           + idle-mode Claude jobs only with budget to spare
  wake_snoozed_cards  + rewrites the one pinned "To-do for Jake" issue

Rules (from the card):
 1. No repeat passes: a GitHub card whose last finished run (done / needs-jake) has no newer Jake comment, body
    edit, or new file in its work folder is not run again; it is snoozed with label repeat-hold and listed in the
    To-do issue. A comment from Jake ("ran it", "try X") makes it runnable again.
 2. Route before spending Claude: drafting cards get a rig draft first; the prompt points Claude at the rig and
    Gemini for prose and keeps Claude on tools, code and checking.
 3. Budget per 5-hour window: >= 80% used -> no new Claude jobs; >= 60% -> P0/P1 only; >= 50% -> no idle jobs,
    and P2/none cards use the light model. Usage = weighted tokens in ~/.claude/projects transcripts over the
    last 5 h (Jake's own sessions on this PC included), against a capacity learned at each limit hit.
 4. Do the local step: a NEEDS_JAKE that is only local and reversible (password for a local service, config/env
    file, restarting a service) goes back to approved once, with "do it yourself" in the next prompt.
 5. One bundled ask: every open needs-jake / repeat-hold card in one pinned issue (label jake-todo).

Settings (user env vars, all optional): JARVIS_BUDGET_STOP (0.80), JARVIS_BUDGET_P01 (0.60), JARVIS_BUDGET_IDLE
(0.50), JARVIS_BUDGET_UNITS (fixed capacity, skips learning), JARVIS_WINDOW_CAP_MIN (150 Worker minutes per 5 h
until the first limit hit calibrates it), JARVIS_BUDGET_OFF=1 (gate off, other rules stay).
Stdlib only.
"""
import glob
import json
import os
import re
import time
from datetime import datetime, timezone

AG = {}      # agent.py globals (live dict, so ghq etc. are read at call time)
ORIG = {}
HOME = os.path.expanduser("~")
STATE_FILE = os.path.join(HOME, "JarvisAgent", "budget-guard.json")
TRANSCRIPTS = os.path.join(HOME, ".claude", "projects")
HERE = os.path.dirname(os.path.abspath(__file__))
WINDOW_H = 5


def _f(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


STOP_AT = _f("JARVIS_BUDGET_STOP", 0.80)
P01_AT = _f("JARVIS_BUDGET_P01", 0.60)
IDLE_BELOW = _f("JARVIS_BUDGET_IDLE", 0.50)
FIXED_UNITS = _f("JARVIS_BUDGET_UNITS", 0)
DEFAULT_CAP_MIN = _f("JARVIS_WINDOW_CAP_MIN", 150)
TODO_EVERY_S = 900
TODO_LABEL = "jake-todo"
TODO_TITLE = "To-do for Jake (all Workers)"
TODO_ISSUE = 650   # the one pinned summary issue; To-Do tab cards carry jake-todo / todo-tab too, so never search by label
TODO_TAB_URL = "https://laptop-4150egrs.tail3bbcb8.ts.net/#todo"
REPEAT_LABEL = "repeat-hold"


def log(msg):
    (AG.get("log") or print)(msg)


def _load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)


def _epoch(iso):
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ 3. budget per 5-hour window

def _model_w(model):
    m = (model or "").lower()
    return 0.2 if "haiku" in m else 0.6 if "sonnet" in m else 1.0


def units_of(usage, model=""):
    """Rough cost weight of one API call. Only the ratio to the learned capacity matters."""
    return (usage.get("input_tokens", 0) + 1.25 * usage.get("cache_creation_input_tokens", 0)
            + 0.1 * usage.get("cache_read_input_tokens", 0) + 5 * usage.get("output_tokens", 0)) * _model_w(model)


_SCAN = {"t": 0, "units": 0.0}


def window_units(now=None, root=None, force=False):
    """Weighted tokens of every Claude Code session on this PC (Worker runs and Jake's own) in the last 5 h."""
    now = now or time.time()
    if not force and root is None and now - _SCAN["t"] < 60:
        return _SCAN["units"]
    since, total, seen = now - WINDOW_H * 3600, 0.0, set()
    for path in glob.glob(os.path.join(root or TRANSCRIPTS, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(path) < since:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if _epoch(d.get("timestamp", "")) < since:
                        continue
                    msg = d.get("message") or {}
                    key = (msg.get("id"), d.get("requestId"))
                    if key != (None, None):   # one API call is logged once per content block: count it once
                        if key in seen:
                            continue
                        seen.add(key)
                    total += units_of(msg.get("usage") or {}, msg.get("model", ""))
        except OSError:
            continue
    if root is None:
        _SCAN.update(t=now, units=total)
    return total


def worker_minutes(now=None):
    import csv
    now, mins = now or time.time(), 0.0
    try:
        with open(AG.get("USAGE_CSV", ""), encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if _epoch(row["start"]) and now - datetime.fromisoformat(row["start"]).timestamp() < WINDOW_H * 3600:
                    mins += float(row["minutes"] or 0)
    except (OSError, ValueError, KeyError):
        pass
    run_t0 = (AG.get("STATE") or {}).get("run_t0")
    if (AG.get("STATE") or {}).get("running") and run_t0:
        mins += (now - run_t0) / 60
    return mins


def capacity():
    if FIXED_UNITS:
        return FIXED_UNITS
    hits = [h["units"] for h in _load().get("hits", [])[-3:] if h.get("units", 0) > 0]
    return sorted(hits)[len(hits) // 2] if hits else 0


def used_fraction():
    """(fraction of the 5-hour window used, short text)."""
    cap = capacity()
    if cap:
        u = window_units()
        return u / cap, f"{u / 1e6:.1f}M of ~{cap / 1e6:.1f}M units"
    m = worker_minutes()
    return m / DEFAULT_CAP_MIN, f"{m:.0f} of {DEFAULT_CAP_MIN:.0f} Worker min (not calibrated yet)"


def budget_gate(c):
    """None = may start a Claude job for this card, else the reason it waits."""
    if os.environ.get("JARVIS_BUDGET_OFF"):
        return None
    frac, detail = used_fraction()
    (AG.get("STATE") or {})["budget"] = f"{frac:.0%} of the 5-h window ({detail})"
    pr = (c.get("priority") or "P9")[:2].upper()
    if frac >= STOP_AT:
        return f"budget-hold: {frac:.0%} of the 5-h window used ({detail}); new Claude jobs wait below {STOP_AT:.0%}"
    if frac >= P01_AT and pr not in ("P0", "P1"):
        return f"budget-hold: {frac:.0%} used ({detail}); only P0/P1 above {P01_AT:.0%}"
    return None


def record_limit_hit(why):
    # 10/2: only the CLI's own short limit line counts; card summaries quoting "hit your limit" made 2 false hits
    low = why.strip().lower()
    cli = low.startswith(("claude ai usage limit reached", "you've hit your", "you have hit your", "error: rate limit",
                          "api error: 429", "warning: usage limit"))
    if "login" in low or not (cli or (len(low) <= 120 and re.search(r"hit your (session )?limit|limit reached", low))):
        return
    d = _load()
    u = window_units(force=True)
    d.setdefault("hits", []).append({"at": datetime.now().isoformat(timespec="seconds"), "units": round(u),
                                     "worker_min": round(worker_minutes(), 1), "why": why[:160]})
    d["hits"] = d["hits"][-10:]
    _save(d)
    log(f"budget guard: usage limit hit at {u / 1e6:.1f}M units in the last 5 h - capacity now ~{capacity() / 1e6:.1f}M")


# ------------------------------------------------------------------ 1. no repeat passes

MACHINE_PREFIXES = ("<!-- jarvis:", "Approved via", "**Needs Jake:**", "Claimed by", "Snooze condition met",
                    "Output path set", "Also requested by", "Stopped after two failed", "Reopened only",
                    "Repeat-pass guard", "Worker rule 4")
MACHINE_NOTE_RE = re.compile(r"^\d\d/\d\d \d\d:\d\d \w+: ")   # rig-lane notes ("10/02 03:10 homebase: ...")
JAKE_HUB_MARK = "<!-- jarvis:review jake -->"   # hub-v3 review_tab.say(): Jake's own comment from the phone
RERUN_OK = ("Snooze condition met", "Reopened only")
FINISHED = ("done", "needs-jake")
IGNORED_FILES = re.compile(r"^(STATUS\.md|rig-draft\.md|gemini-.*|.*\.review\.md|.*\.bak.*)$", re.I)
_REPEAT_CACHE = {}


def _last_edited(number):
    ghq = AG.get("ghq")
    owner, name = ghq.REPO.split("/", 1)
    q = "query($o:String!,$n:String!,$i:Int!){repository(owner:$o,name:$n){issue(number:$i){lastEditedAt}}}"
    try:
        data = ghq.request("POST", "/graphql", {"query": q, "variables": {"o": owner, "n": name, "i": number}})[1]
        return _epoch(((data or {}).get("data") or {}).get("repository", {}).get("issue", {}).get("lastEditedAt") or "")
    except Exception:
        return 0.0


def _approved_label_after(n, since, comments):
    """True if status:approved was put on the card after `since` by something other than a snooze wake."""
    ghq = AG.get("ghq")
    try:
        events = ghq.paged(ghq.repo_path(f"/issues/{n}/events"))
    except Exception:
        return False
    wakes = [_epoch(cm.get("created_at", "")) for cm in comments
             if (cm.get("body") or "").startswith("Snooze condition met")]
    for ev in events:
        if ev.get("event") != "labeled" or (ev.get("label") or {}).get("name") != "status:approved":
            continue
        t = _epoch(ev.get("created_at", ""))
        if t > since + 5 and not any(abs(t - w) < 120 for w in wakes):
            return True
    return False


def repeat_reason(c, comments=None, edited=None, work_dir=None):
    """None = fine to run. Else why this would be a repeat pass. Only GitHub cards."""
    if not c.get("number"):
        return None
    n = c["number"]
    rule4 = _load().get("rule4", {}).get(str(n))
    if rule4 and not rule4.get("used"):
        return None
    if comments is None:
        hit = _REPEAT_CACHE.get(n)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        ghq = AG["ghq"]
        comments = ghq.paged(ghq.repo_path(f"/issues/{n}/comments"))
    last_fin, last_fin_out = 0.0, ""
    for cm in comments:
        m = re.search(r"<!-- jarvis:runmeta (\{.*?\}) -->", cm.get("body") or "")
        if m:
            meta = json.loads(m.group(1))
            if meta.get("outcome") in FINISHED:
                last_fin, last_fin_out = _epoch(cm.get("created_at", "")), meta["outcome"]
    reason = None
    if last_fin:
        newer = [cm for cm in comments if _epoch(cm.get("created_at", "")) > last_fin]
        # card #629: the hub's thread comments start "<!-- jarvis:review jake -->" (review_tab.JAKE_MARK); that is
        # Jake typing, not a machine line, so it must not fall under the "<!-- jarvis:" machine prefix
        human = [cm for cm in newer if (cm.get("body") or "").lstrip().startswith(JAKE_HUB_MARK)
                 or (not (cm.get("body") or "").lstrip().startswith(MACHINE_PREFIXES)
                     and not MACHINE_NOTE_RE.match(cm.get("body") or ""))]
        # a machine:<this PC> snooze is always met when this PC checks it, so that wake alone isn't news
        self_wake = f"Snooze condition met (machine: `{AG.get('ME')}`)"
        sanctioned = [cm for cm in newer if (cm.get("body") or "").startswith(RERUN_OK)
                      and not (cm.get("body") or "").startswith(self_wake)]
        if edited is None:
            edited = _last_edited(n)
        # rig 10/2 (bounce loop, 107 comments/h): Jake re-approving after the needs-jake run IS his new ruling
        # (9/29 rule: an Approved card runs). An "Approved via" comment or a status:approved label event newer
        # than the run counts, unless that label came from a snooze wake (machine, not Jake).
        approved_after = any((cm.get("body") or "").startswith("Approved via") for cm in newer) or \
            _approved_label_after(n, last_fin, comments)
        new_files = []
        d = work_dir if work_dir is not None else (AG["card_dir"](c) if AG.get("card_dir") else "")
        if d and os.path.isdir(d):
            for root, _, files in os.walk(d):
                new_files += [f for f in files if not IGNORED_FILES.match(f)
                              and os.path.getmtime(os.path.join(root, f)) > last_fin + 60]
        if not (human or sanctioned or approved_after or edited > last_fin + 60 or new_files):
            when = datetime.fromtimestamp(last_fin).strftime("%m/%d %H:%M")
            reason = (f"its last run ({when}) already finished as {last_fin_out}, and nothing new since then "
                      f"(no comment from Jake, no edit, no new file in the work folder)")
    _REPEAT_CACHE[n] = (time.time(), reason)
    return reason


def park_repeat(c, reason):
    ghq = AG["ghq"]
    n = c["number"]
    try:
        # rig 10/2: never post the same guard comment twice. If our last comment on the card already is the
        # guard's, just put it back to needs-jake silently (ghq.comment also skips repeats within 6 h).
        recent = ghq.paged(ghq.repo_path(f"/issues/{n}/comments"))[-3:]
        if any("Repeat-pass guard" in (cm.get("body") or "") for cm in recent):
            ghq.set_status(n, "needs-jake", extra_remove=[f"claimed:{m}" for m in ghq.MACHINES])
            log(f"REPEAT-SKIP #{n} (silent, already told Jake): {reason[:100]}")
            _REPEAT_CACHE.pop(n, None)
            return
        # rig 10/2: hand it to Jake once (needs-jake) instead of snoozing it: a snooze sat on top of the card's
        # older condition snooze, so every sweep woke it and this guard parked it again (#431 loop)
        ghq.ask_jake(n, f"Repeat-pass guard ({AG.get('ME')}): not run again because {reason}. To run it again, "
                        f"comment what changed (e.g. \"ran the .bat\", \"try X instead\") and approve it. It is "
                        f"listed in the pinned *{TODO_TITLE}* issue meanwhile.")
        log(f"REPEAT-SKIP #{n} {c['task'][:60]}: {reason}")
        _REPEAT_CACHE.pop(n, None)
    except Exception as e:
        log(f"repeat guard: couldn't snooze #{n}: {e}")


# ------------------------------------------------------------------ 4. do the local step

JAKE_ONLY = re.compile(
    r"physical|plug|unplug|press (the|a)|button|power[- ]?(on|cycle)|cable|usb|sd card|flash (the|an?)|in person|"
    r"log ?in|sign ?(in|up)|account|2fa|oauth|api key|token from|his password|jake'?s password|"
    r"\bpay|purchase|\bbuy|money|subscription|credit card|billing|"
    r"publish|post (it|to|on)|send|e-?mail|tweet|\bdelet|wipe|format the|"
    r"\buac\b|admin|elevat|as administrator|\bpin\b|decide|decision|choose|prefer|which (one|option)|"
    r"tailscale|\bpi\b|raspberry|"
    # another PC's hands: a rerun from here can't double-click on homebase or reach the 5060 laptop
    r"double-?click|on the 5060 laptop|laptop-4150egrs", re.I)
LOCAL = re.compile(r"password|passwd|secret|\.env\b|config|\.ya?ml|\.conf|restart|service|integration|"
                   r"generate|write the|create the (file|folder)|mqtt|mosquitto|docker|container|systemctl|"
                   r"scheduled task|env(ironment)? var", re.I)


def local_step(need):
    return bool(LOCAL.search(need or "")) and not JAKE_ONLY.search(need or "")


# ------------------------------------------------------------------ 2. route before spending Claude

DRAFTING = re.compile(r"draft|write|summar|research|proofread|outline|template|letter|e-?mail|guide|report|"
                      r"compare|proposal|brief|copy|checklist|plan\b", re.I)
TOOLING = re.compile(r"install|script|code|fix|bug|config|server|deploy|wire|patch|\.py|\.bat|\.ps1|service|"
                     r"worker|agent|watchdog|hub|docker|mqtt|network|firewall|repo|github", re.I)

ROUTE_RULES = """
ROUTE BEFORE SPENDING CLAUDE (card #437): you are the expensive model; the cheap ones write the prose.
- Drafting, summarizing, research write-ups and proofreading go to the rig LLM first (now {rig}):
    python "{rig_ask}" "<prompt>" -f <input file> -o <out.md>
  or to Gemini for non-private topics: python "{gemini}" research|proofread <text or file> -o <out.md>.
  Clinical, client and private content never goes to Gemini. Then check and fix their output.
- Spend your own effort on tools, commands, code, files and judgment. Don't re-check work STATUS.md says is done.
- If the card is finished and only waits on a file, another card, a PC or a time (not on Jake), end with
    SNOOZE_UNTIL: <full path | card:<n> | machine:<name> | time:<ISO UTC>> | <reason>
  instead of NEEDS_JAKE; the Worker wakes the card by itself when that happens.

DO THE LOCAL STEP (card #437): if a step is local and reversible and needs no account, payment, posting or
deletion (a password for a local service, a config or .env file, restarting a local service, adding a local
integration), do it yourself and write down WHERE the secret is stored (the path only, never the secret).
NEEDS_JAKE is only for physical access, logins to outside accounts, money, publishing/sending, deleting, or a
Windows admin (UAC) prompt.
"""

RULE4_NOTE = """
LAST PASS ASKED JAKE FOR A LOCAL STEP (Worker rule 4): "{need}"
That step is local and reversible, so do it yourself this pass (log where any secret is stored). Only if it truly
needs a login, money, publishing, deleting, physical access or UAC, say so again with NEEDS_JAKE and why.
"""


def predraft(c):
    """Drafting card with no fresh rig draft: let the rig write one now, so Claude only checks and finishes."""
    if not (DRAFTING.search(f"{c.get('task', '')} {c.get('type', '')}") and not TOOLING.search(c.get("task", ""))):
        return
    d = AG["card_dir"](c)
    draft = os.path.join(d, "rig-draft.md")
    if os.path.exists(draft) and time.time() - os.path.getmtime(draft) < 3 * 86400:
        return
    try:
        if not AG["wake_rig_and_wait"](3):
            return
        status_md = os.path.join(d, "STATUS.md")
        prev = open(status_md, encoding="utf-8", errors="replace").read()[:6000] if os.path.exists(status_md) else ""
        text = AG["rig_generate"](AG.get("RIG_CARD_SYSTEM", "") + f"\n\nCARD: {c['task']}\nNOTES FROM THE CARD:\n"
                                  f"{(c.get('notes') or '(none)')[:4000]}" + (f"\n\nSTATUS.md:\n{prev}" if prev else ""),
                                  timeout=600)
        with open(draft, "w", encoding="utf-8") as f:
            f.write(text + "\n\n_drafted by Jarvis (rig) before the Claude pass (card #437), not verified_\n")
        log(f"budget guard: rig drafted {c['task'][:60]} before Claude -> {draft}")
    except Exception as e:
        log(f"budget guard: rig pre-draft skipped ({e})")


# ------------------------------------------------------------------ 5. one bundled ask

_TODO = {"t": 0, "cache": {}}


def _question(number, updated):
    hit = _TODO["cache"].get(number)
    if hit and hit[0] == updated:
        return hit[1]
    ghq = AG["ghq"]
    q = ""
    for cm in ghq.paged(ghq.repo_path(f"/issues/{number}/comments")):
        b = cm.get("body") or ""
        if b.startswith("**Needs Jake:**"):
            q = b[len("**Needs Jake:**"):].strip()
        elif b.startswith("Repeat-pass guard"):
            q = "Re-approved with nothing new. " + b.split("because", 1)[-1].split("\n")[0].strip()
    q = re.sub(r"<!--.*?-->", "", q, flags=re.S).strip().split("\n\n")[0][:300]
    _TODO["cache"][number] = (updated, q)
    return q


def todo_body(needs, repeats):
    lines = [f"Rewritten by the Workers every 15 min (card #437).",
             f"Last update: {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}", "",
             f"**Your list lives in the phone app's To-Do tab now: {TODO_TAB_URL}** "
             f"({len(needs)} card(s) waiting on you, with steps, by machine).", ""]
    lines += [f"## Re-approved with nothing new ({len(repeats)})",
              "Comment what changed on the card (e.g. \"ran the .bat\"), then approve it, and it runs again.", ""]
    lines += [f"- [ ] #{n} {t}" for n, t, _ in repeats]
    return "\n".join(lines) + "\n"


def todo_tick(force=False):
    ghq = AG.get("ghq")
    if ghq is None or (not force and time.time() - _TODO["t"] < TODO_EVERY_S):
        return
    _TODO["t"] = time.time()
    try:
        needs = [(i["number"], i["title"][:90], _question(i["number"], i.get("updated_at")))
                 for i in ghq.paged(ghq.repo_path("/issues?state=open&labels=status:needs-jake"))
                 if not i.get("pull_request")]
        repeats = [(i["number"], i["title"][:90], "")
                   for i in ghq.paged(ghq.repo_path(f"/issues?state=open&labels=status:snoozed,{REPEAT_LABEL}"))
                   if not i.get("pull_request")]
        body = todo_body(needs, repeats)
        try:
            pinned = ghq.api("GET", ghq.repo_path(f"/issues/{TODO_ISSUE}"))
            found = [pinned] if pinned.get("state") == "open" else []
        except Exception:
            found = [i for i in ghq.paged(ghq.repo_path(f"/issues?state=open&labels={TODO_LABEL}"))
                     if (i.get("title") or "").startswith(TODO_TITLE)]
        if found:
            num, old = found[0]["number"], found[0].get("body") or ""
            strip = lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("Last update:"))
            if strip(old) != strip(body):
                ghq.api("PATCH", ghq.repo_path(f"/issues/{num}"),
                        {"title": f"{TODO_TITLE} - {len(needs) + len(repeats)} item(s)", "body": body})
        else:
            issue = ghq.api("POST", ghq.repo_path("/issues"), {"title": f"{TODO_TITLE} - {len(needs) + len(repeats)} "
                                                                       f"item(s)", "body": body, "labels": [TODO_LABEL]})
            num = issue["number"]
            try:
                ghq.request("POST", "/graphql", {"query": "mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{number}}}",
                                                 "variables": {"id": issue["node_id"]}})
            except Exception:
                pass
            log(f"budget guard: created the pinned to-do issue #{num}")
        d = _load()
        nums = sorted(n for n, _, _ in needs + repeats)
        new = set(nums) - set(d.get("todo_nums", []))
        if new and time.time() - d.get("todo_push", 0) > 3 * 3600:   # one push for new items, at most every 3 h
            AG["notify"](f"{len(nums)} thing(s) for you", f"New: {', '.join('#%d' % n for n in sorted(new))[:100]} - "
                                                         f"see the pinned to-do issue")
            d["todo_push"] = time.time()
        d["todo_nums"] = nums
        _save(d)
    except Exception as e:
        log(f"to-do issue: {e}")


# ------------------------------------------------------------------ wrappers

_HOLD = {"t": 0}


def _hub_verify(c):
    ok, paused, why = ORIG["hub_verify"](c)
    if not ok or paused:
        return ok, paused, why
    hold = budget_gate(c)
    if hold:
        _HOLD["t"] = time.time()
        log(f"BUDGET-HOLD {c.get('task', '')[:60]}: {hold}")
        return False, False, hold
    if c.get("number") and AG.get("ghq") is not None:
        try:
            rep = repeat_reason(c)
        except Exception as e:
            log(f"repeat guard: #{c['number']} not checked ({e})")
            rep = None
        if rep:
            park_repeat(c, rep)
            return False, False, f"repeat-pass: {rep}"
    return ok, paused, why


def _card_extra(c):
    try:
        predraft(c)
    except Exception as e:
        log(f"budget guard pre-draft: {e}")
    extra, minutes, model = ORIG["card_extra"](c)
    rig = "UP" if AG.get("rig_ready", lambda: False)() else "asleep: wake it with python agent.py tell rig check, or use Gemini"
    extra += ROUTE_RULES.format(rig=rig, rig_ask=os.path.join(HERE, "rig_ask.py"), gemini=AG.get("GEMINI", ""))
    d = _load()
    r4 = d.get("rule4", {}).get(str(c.get("number")))
    if r4 and not r4.get("used"):
        extra += RULE4_NOTE.format(need=r4["need"][:400])
        r4["used"] = True
        _save(d)
    frac = used_fraction()[0]
    pr = (c.get("priority") or "P9")[:2].upper()
    if frac >= IDLE_BELOW and not model and AG.get("LIGHT_MODEL") and pr not in ("P0", "P1"):
        model = AG["LIGHT_MODEL"]
    return extra, minutes, model


def _followups(c, follow):
    for f in follow[:5]:
        title, _, why = f.partition("|")
        try:
            AG["file_followup"](title.strip(), why.strip(), c)
        except Exception as e:
            log(f"follow-up card failed: {e}")


def _gh_finish(c, output, summary, needs, ckpt, follow, passes):
    ghq = AG["ghq"]
    n = c["number"]
    d = _load()
    if needs and local_step(needs[0]) and str(n) not in d.get("rule4", {}):
        d.setdefault("rule4", {})[str(n)] = {"need": needs[0], "at": datetime.now().isoformat(timespec="seconds")}
        _save(d)
        AG["gh_log"](c, "released", tail=output, summary=f"Worker rule 4: \"{needs[0][:300]}\" is a local, reversible "
                                                         f"step, so the next pass does it instead of asking Jake.\n\n{summary}")
        log(f"RULE4 #{n}: local step, back to approved once instead of needs-jake: {needs[0][:100]}")
        return _followups(c, follow)
    snooze = ghq.parse_snooze_line(output) if hasattr(ghq, "parse_snooze_line") else None
    if snooze and not (needs and not local_step(needs[0])):
        AG["gh_log"](c, "released", tail=output,
                     summary=f"Waiting on {snooze['kind']} `{snooze['value']}` - snoozed, no Jake needed.\n\n{summary}")
        try:
            ghq.snooze_until(n, snooze["kind"], snooze["value"], AG.get("ME"), snooze.get("reason", ""),
                             snooze.get("producer"))
            log(f"SNOOZE #{n} until {snooze['kind']} {snooze['value'][:80]}")
        except Exception as e:   # can't watch it: park by hand so it doesn't loop, and list it for Jake
            ghq.ask_jake(n, f"Repeat-pass guard: the run asked to wait on {snooze['kind']} `{snooze['value']}`, which "
                            f"couldn't be watched ({e}). Comment and approve to run it again.")
        return _followups(c, follow)
    if needs and str(n) in d.get("rule4", {}):
        d["rule4"].pop(str(n))   # rule 4 already tried once: this time it really goes to Jake
        _save(d)
    return ORIG["gh_finish"](c, output, summary, needs, ckpt, follow, passes)


def _block(why):
    try:
        record_limit_hit(why)
    except Exception as e:
        log(f"budget guard: {e}")
    return ORIG["_block"](why)


def _idle_reason(code, detail=""):
    if code == "blocked-other" and time.time() - _HOLD["t"] < 300:
        st = AG["STATE"]
        st["idle_alerted"] = "budget-hold"   # an intentional wait: no "Worker stuck" push
        return ORIG["idle_reason"]("budget-hold", f"{st.get('budget', '')}; {detail}"[:230])
    return ORIG["idle_reason"](code, detail)


def _idle_tick():
    if used_fraction()[0] >= IDLE_BELOW and not os.environ.get("JARVIS_BUDGET_OFF"):
        return
    return ORIG["idle_tick"]()


def _wake_snoozed_cards():
    r = ORIG["wake_snoozed_cards"]()
    todo_tick()
    return r


WRAPS = {"hub_verify": _hub_verify, "card_extra": _card_extra, "gh_finish": _gh_finish, "_block": _block,
         "idle_reason": _idle_reason, "idle_tick": _idle_tick, "wake_snoozed_cards": _wake_snoozed_cards}


def install(g):
    """Called from the end of agent.py with globals(). Safe to call twice."""
    if g.get("_BUDGET_GUARD_ON"):
        return
    AG.clear()
    AG.update(g)
    globals()["AG"] = g   # live view: ghq is set later by load_ghq()
    missing = [n for n in WRAPS if n not in g]
    for name, fn in WRAPS.items():
        if name in g:
            ORIG[name] = g[name]
            g[name] = fn
    g["_BUDGET_GUARD_ON"] = True
    g["log"](f"budget guard on (card #437): stop at {STOP_AT:.0%} of the 5-h window, P0/P1 only from {P01_AT:.0%}, "
             f"idle jobs below {IDLE_BELOW:.0%}" + (f"; not wrapped (missing): {', '.join(missing)}" if missing else ""))
