"""Fallback lane for the Jarvis Worker: model:local cards run on the rig's local model, never on Claude (card #608).

fallback.py (card #415) narrows the rig to machine:rig + model:local cards while Claude is out of usage, but two
things kept those cards on Claude or idle:
  1. local_lane.route() sends "Research ..." / brief cards to Claude (they look like research or judgment work);
  2. poll() returns "paused" while BLOCKED, before its card loop, so the lane cards never ran at all.

Hook at the end of agent.py, AFTER the local lane (so this wrapper is the outer one):

    try:   # card #608: model:local cards run on the local model only; lane cards still run while Claude is out
        sys.path.insert(0, HERE); import fallback_lane; fallback_lane.install(globals())
    except Exception as _e:
        log(f"fallback lane not loaded: {_e}")

install() wraps:
  run_card(c) - a card labelled model:local goes to Ollama (LOCAL_MODEL) with its STATUS.md and the cards it
                refers to as context. Never Claude. A failed or rejected draft ends in CHECKPOINT (retried next poll,
                nightly pass cap applies), never a usage block. Other cards: unchanged.
  poll()      - when poll() answers "paused" (Claude blocked) and fallback.json says local-fallback for this machine,
                runs ONE lane card (fallback.allowed + model:local) through the normal work_one() claim/finish path.
  proofread_outputs(c, since) - card #1050: for a model:local card (or a pass the local lane drafted) Gemini still
                proofreads, but NEEDS FIXES never starts the short Claude fix pass. Other cards: unchanged.
Off switch: env JARVIS_FALLBACK_LANE=off -> old behaviour (all wrappers pass straight through).

CLI (from this folder): python fallback_lane.py selftest
Stdlib only.
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

AG = {}      # agent.py's globals (looked up at call time, so later wrappers are honoured)
ORIG = {}
LOCAL_LABEL = "model:local"
LOCAL_MODEL = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3.6:35b")
AUTONOMY_DIR = r"C:\Jarvis\autonomy"
NEXT_PROJECT = re.compile(r"\bnext[- ]project\b", re.I)
REF = re.compile(r"(?<![\w/])#(\d{1,5})\b")

SYSTEM = """You are Jarvis, Jake's local model on his rig, working a card while Claude is unavailable.
You have NO web access and NO tools: work only from the card and the context given below.
- Output only the finished deliverable in clean Markdown, starting with "# <short title>".
- No preamble, no questions back, no promises to do more later.
- Never invent facts, prices, links, citations, statistics or people. Where a fact is needed and you don't know it,
  write [UNVERIFIED: what to check] instead. For research cards, separate what you know from what must be checked.
- Nothing is bought, posted, installed or filed: this is a draft for Jake.
- End with a section "## Next steps" listing at most 5 concrete follow-up cards."""


def log(msg):
    (AG.get("log") or print)(msg)


def off():
    return os.environ.get("JARVIS_FALLBACK_LANE", "").strip().lower() in ("off", "0", "false", "no")


def is_local_card(c):
    return LOCAL_LABEL in (c.get("labels") or [])


def _fallback():
    if AUTONOMY_DIR not in sys.path:
        sys.path.append(AUTONOMY_DIR)
    import fallback
    return fallback


# ------------------------------------------------------------------ context for a tool-less model

def context(c, ghq=None, now=None, limit=8000):
    """Text the model can't fetch itself: the cards this one refers to (#N), and for a next-project brief the
    cards closed in the last 7 days plus the open ideas. Best effort; '' when GitHub isn't reachable."""
    ghq = ghq if ghq is not None else AG.get("ghq")
    if ghq is None:
        return ""
    now = now or datetime.now(timezone.utc)
    parts = []
    try:
        own = c.get("number")
        refs = []
        for n in REF.findall(f"{c.get('task', '')}\n{c.get('notes', '')}"):
            if int(n) != own and int(n) not in refs:
                refs.append(int(n))
        for n in refs[:3]:
            i = ghq.api("GET", ghq.repo_path(f"/issues/{n}")) or {}
            if i.get("title"):
                parts.append(f"CARD #{n}: {i['title']}\n{(i.get('body') or '').strip()[:1500]}")
        if NEXT_PROJECT.search(c.get("task") or ""):
            since = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            closed = [i for i in ghq.paged(ghq.repo_path(f"/issues?state=closed&since={since}"))
                      if not i.get("pull_request")]
            if closed:
                parts.append("CARDS CLOSED IN THE LAST 7 DAYS:\n" +
                             "\n".join(f"- #{i['number']} {i['title']}" for i in closed[:40]))
            ideas = [i for i in ghq.paged(ghq.repo_path("/issues?state=open&labels=type:idea"))
                     if not i.get("pull_request")]
            if ideas:
                parts.append("OPEN IDEAS:\n" + "\n".join(f"- #{i['number']} {i['title']}" for i in ideas[:30]))
    except Exception as e:
        log(f"FALLBACK-LANE: context fetch failed ({e.__class__.__name__}: {e})")
    return "\n\n".join(parts)[:limit]


# ------------------------------------------------------------------ the local run

def generate(prompt, timeout=900):
    brain = AG.get("BRAIN") or "http://127.0.0.1:11434"
    body = json.dumps({"model": LOCAL_MODEL, "system": SYSTEM, "prompt": prompt, "stream": False, "think": False,
                       "options": {"num_ctx": 16384, "num_predict": 4096, "temperature": 0.4}}).encode()
    req = urllib.request.Request(brain + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return d.get("response", ""), d.get("done_reason", "stop")


def check(c, text, done_reason="stop"):
    """None if usable, else why not. Reuses local_lane.check() (length, cut-off, on-topic, refusal)."""
    try:
        import local_lane
        return local_lane.check(c, text, done_reason)
    except ImportError:
        t = (text or "").strip()
        if len(t) < 300:
            return "empty or too short"
        return None if done_reason in ("", None, "stop") else f"cut off ({done_reason})"


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run_lane(c, gen=None):
    """run_card() for a model:local card. Always returns (output, out_file); never calls Claude."""
    gen = gen or generate
    d = AG["card_dir"](c)
    STATE = AG["STATE"]
    status_md = os.path.join(d, "STATUS.md")
    prev = ""
    if os.path.exists(status_md):
        with open(status_md, encoding="utf-8", errors="replace") as f:
            prev = f.read()[:4000]
    ctx = context(c)
    prompt = (f"CARD: {c['task']}\nPRIORITY: {c.get('priority') or '-'}   PROJECT: {c.get('project') or '-'}\n"
              f"NOTES FROM THE CARD:\n{(c.get('notes') or '(none)')[:6000]}\n"
              + (f"\nCONTEXT (fetched for you):\n{ctx}\n" if ctx else "")
              + (f"\nEARLIER PROGRESS (STATUS.md):\n{prev}\n" if prev else ""))
    log(f"FALLBACK-LANE: {LOCAL_MODEL} on model:local card: {c['task'][:80]}")
    t0 = time.time()
    STATE["run_t0"] = t0
    STATE.update(last_rc=0, last_stderr="", run_model=f"local:{LOCAL_MODEL}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = os.path.join(AG["LOG_DIR"], f"task-{stamp}-fallback.txt")
    try:
        text, done_reason = gen(prompt)
        why = check(c, text, done_reason)
    except Exception as e:
        text, why = "", f"model call failed: {e.__class__.__name__}: {str(e)[:150]}"
    mins = (time.time() - t0) / 60
    if why:
        try:
            _write(os.path.join(d, "local-draft.rejected.md"), f"<!-- rejected: {why} -->\n{text}")
        except OSError:
            pass
        output = (f"Local model {LOCAL_MODEL} could not finish this model:local card ({why}); "
                  f"no Claude was used.\nCHECKPOINT: local draft rejected ({why[:80]}) - retried on the next poll")
        result = "checkpoint"
        log(f"FALLBACK-LANE: rejected ({why}) - card stays queued for the local model")
    else:
        label = f"Drafted by local model {LOCAL_MODEL} (rig, fallback lane), {datetime.now():%m/%d %H:%M}. No Claude used."
        out_md = os.path.join(d, "local-draft.md")
        _write(out_md, f"_{label}_\n\n{text.strip()}\n")
        output = f"{label}\nDeliverable: {out_md} ({len(text)} chars, {mins:.1f} min).\n\n{text.strip()[:6000]}"
        result = "done"
        STATE["fallback_lane_runs"] = STATE.get("fallback_lane_runs", 0) + 1
        log(f"FALLBACK-LANE: done in {mins:.1f} min -> {out_md}")
    _write(out_file, output + ("\n\n" + text if text and why else ""))
    try:
        AG["usage_row"](c, f"local:{LOCAL_MODEL}", t0, result)
    except Exception:
        pass
    return output, out_file


def _run_card(c):
    if not off() and is_local_card(c):
        return run_lane(c)
    return ORIG["run_card"](c)


# ------------------------------------------------------------------ poll while Claude is blocked

def lane_cards(cards, st, allowed=None):
    """The approved cards the lane may run now, P0 first. Pure apart from fallback.allowed()."""
    allowed = allowed or _fallback().allowed
    keep = [c for c in cards if is_local_card(c) and allowed({"title": c.get("task") or "",
                                                              "labels": c.get("labels") or []}, st)]
    return sorted(keep, key=lambda c: c.get("priority") or "P9")


def lane_poll():
    """One lane card through work_one(). Returns a short status string, or None if nothing ran."""
    g = AG
    me = g.get("ME", "rig")
    st = _fallback().load_state()
    if st.get("mode") != "local-fallback" or st.get("machine", "rig") != me:
        return None
    lock = g["LOCK"]
    if not lock.acquire(blocking=False):
        return None
    try:
        cap = g.get("MAX_PASSES_PER_NIGHT", 3)
        for c in lane_cards(g["approved_for"](me), st):
            if g["needs_jake_blocked"](c) or g["STATE"].get("passes", {}).get(c["id"], 0) >= cap:
                continue
            ok, is_paused, why = g["hub_verify"](c)
            if is_paused:
                return None
            if not ok:
                continue
            g["work_one"](c)
            g["STATE"]["last_run"] = time.time()
            return f"fallback lane ran {c.get('number') or c['id']}"
        return None
    except Exception as e:
        log(f"FALLBACK-LANE: poll error ({e.__class__.__name__}: {e})")
        return None
    finally:
        lock.release()


def _poll():
    r = ORIG["poll"]()
    if r == "paused" and not off():
        ran = lane_poll()
        if ran:
            log(f"FALLBACK-LANE: {ran} while Claude is out")
            return ran
    return r


# ------------------------------------------------------------------ proofread without Claude (card #1050)

def local_pass(c):
    """True when this pass must stay off Claude: a model:local card, or the local lane wrote the draft."""
    return is_local_card(c) or str(AG["STATE"].get("run_model") or "").startswith("local:")


def proofread_local(c, since):
    """proofread_outputs() for a local pass: Gemini still reviews (non-private only), but a NEEDS FIXES verdict
    never starts the Claude fix pass. The .review.md stays next to the draft for the next local pass or Jake."""
    if AG["private"](c):
        return
    d = AG["card_dir"](c)
    docs = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".md") and n not in ("STATUS.md",)
            and not n.endswith((".review.md",)) and not n.startswith("gemini-")
            and os.path.getmtime(os.path.join(d, n)) >= since][:2]
    for doc in docs:
        review = AG["gemini"]("proofread", doc, doc[:-3] + ".review.md")
        if not review:
            continue
        verdict = "NEEDS FIXES" if "NEEDS FIXES" in review[-300:].upper() else "READY"
        log(f"gemini proofread {os.path.basename(doc)}: {verdict}"
            + (" - Claude fix pass skipped (local card), review kept" if verdict == "NEEDS FIXES" else ""))


def _proofread_outputs(c, since):
    if not off() and local_pass(c):
        return proofread_local(c, since)
    return ORIG["proofread_outputs"](c, since)


def install(g):
    global AG
    AG = g
    ORIG["run_card"] = g["run_card"]
    ORIG["poll"] = g["poll"]
    g["run_card"] = _run_card
    g["poll"] = _poll
    if "proofread_outputs" in g:   # card #1050: finish() looks it up by name, so this swap reaches it
        ORIG["proofread_outputs"] = g["proofread_outputs"]
        g["proofread_outputs"] = _proofread_outputs
    log(f"fallback lane on: model:local cards -> {LOCAL_MODEL} only (no Claude), lane cards run while Claude is out"
        + (" (OFF by JARVIS_FALLBACK_LANE)" if off() else ""))


# ------------------------------------------------------------------ self-test

def selftest():
    import tempfile
    import threading
    bad = 0

    def ok(name, cond):
        nonlocal bad
        bad += not cond
        print("PASS" if cond else "FAIL", name)

    tmp = tempfile.mkdtemp()
    calls = {"claude": 0, "work": []}
    good = ("# Research PETG vs PLA\n\n" + "PETG and PLA filament differ in strength, heat and printing. " * 10
            + "\n\n## Next steps\n- File a test-print card.\n")
    g = {"log": lambda m: None, "STATE": {}, "card_dir": lambda c: tmp, "LOG_DIR": tmp, "ME": "rig",
         "usage_row": lambda *a: None, "LOCK": threading.Lock(), "MAX_PASSES_PER_NIGHT": 3,
         "run_card": lambda c: (calls.__setitem__("claude", calls["claude"] + 1), ("claude", "f"))[1],
         "poll": lambda: "paused", "needs_jake_blocked": lambda c: False, "hub_verify": lambda c: (True, False, ""),
         "work_one": lambda c: calls["work"].append(c["id"]),
         "proofread_outputs": lambda c, since: calls.__setitem__("fix", calls.get("fix", 0) + 1),
         "private": lambda c: "private" in (c.get("notes") or ""),
         "gemini": lambda mode, doc, out: (calls.__setitem__("gem", calls.get("gem", 0) + 1),
                                           "Typos found.\nVERDICT: NEEDS FIXES")[1]}
    install(g)
    local = {"id": "gh-12", "number": 12, "task": "Research PETG vs PLA", "notes": "", "priority": "P2",
             "labels": ["status:approved", "machine:rig", "model:local"]}
    other = dict(local, id="gh-13", number=13, task="Fix the hub login", labels=["status:approved", "machine:rig"])

    out, f = run_lane(local, gen=lambda p: (good, "stop"))
    ok("good draft -> deliverable written", os.path.exists(os.path.join(tmp, "local-draft.md")) and "CHECKPOINT" not in out)
    ok("good draft -> rc 0 so finish() won't call it a usage block", g["STATE"]["last_rc"] == 0)
    out, f = run_lane(local, gen=lambda p: ("too short", "stop"))
    ok("bad draft -> CHECKPOINT, not Claude", "CHECKPOINT:" in out and calls["claude"] == 0)

    def boom(p):
        raise OSError("ollama down")
    out, f = run_lane(local, gen=boom)
    ok("model down -> CHECKPOINT, not Claude", "CHECKPOINT:" in out and calls["claude"] == 0)

    g["run_card"](other)
    ok("non-local card -> original run_card", calls["claude"] == 1)
    os.environ["JARVIS_FALLBACK_LANE"] = "off"
    g["run_card"](dict(local, notes="x"))
    ok("off switch -> original run_card", calls["claude"] == 2)
    os.environ.pop("JARVIS_FALLBACK_LANE")

    # card #1050: proofread of local drafts never reaches the Claude fix pass (the original proofread_outputs)
    out, f = run_lane(local, gen=lambda p: (good, "stop"))
    g["proofread_outputs"](local, 0)
    ok("model:local + NEEDS FIXES -> Gemini reviews, no Claude fix", calls.get("gem", 0) >= 1 and calls.get("fix", 0) == 0)
    g["STATE"]["run_model"] = "local:qwen"
    g["proofread_outputs"](other, 0)
    ok("local-lane draft on unlabelled card -> no Claude fix", calls.get("fix", 0) == 0)
    g["STATE"].pop("run_model")
    gem = calls.get("gem", 0)
    g["proofread_outputs"](dict(local, notes="private stuff"), 0)
    ok("private local card -> no Gemini, no Claude", calls.get("gem", 0) == gem and calls.get("fix", 0) == 0)
    g["proofread_outputs"](other, 0)
    ok("Claude card -> original proofread (fix pass allowed)", calls.get("fix", 0) == 1)
    os.environ["JARVIS_FALLBACK_LANE"] = "off"
    g["proofread_outputs"](local, 0)
    ok("off switch -> original proofread", calls.get("fix", 0) == 2)
    os.environ.pop("JARVIS_FALLBACK_LANE")

    st = {"mode": "local-fallback", "machine": "rig"}
    allowed = lambda card, s: "model:local" in card["labels"]
    ok("lane_cards keeps only model:local", [c["id"] for c in lane_cards([other, local], st, allowed)] == ["gh-12"])

    fb = sys.modules.setdefault("fallback", type(sys)("fallback"))
    fb.load_state = lambda: st
    fb.allowed = lambda card, s=None: "model:local" in card["labels"] and "Research" in card["title"]
    g["approved_for"] = lambda me: [other, local]
    r = g["poll"]()
    ok("paused + local-fallback -> one lane card runs", calls["work"] == ["gh-12"] and r.startswith("fallback lane"))
    fb.load_state = lambda: {"mode": "normal"}
    ok("paused + normal mode -> stays paused", g["poll"]() == "paused")
    g["poll"] = _poll
    ORIG["poll"] = lambda: "3 task(s)"
    ok("not paused -> poll result unchanged", g["poll"]() == "3 task(s)")

    class FakeGhq:
        def repo_path(self, s=""):
            return "/repos/x" + s

        def api(self, m, p):
            return {"title": "Idea: PETG drybox", "body": "Build a drybox."} if p.endswith("/issues/7") else {}

        def paged(self, p):
            return [{"number": 3, "title": "Closed thing"}] if "closed" in p else [{"number": 7, "title": "PETG drybox"}]
    ctx = context({"number": 20, "task": "Draft the next project brief", "notes": "see #7 and #20"}, ghq=FakeGhq())
    ok("context has referenced card, closed cards and ideas",
       "CARD #7" in ctx and "CARD #20" not in ctx and "Closed thing" in ctx and "OPEN IDEAS" in ctx)
    print("ALL PASS" if not bad else f"{bad} FAILED")
    return bad


if __name__ == "__main__":
    if sys.argv[1:2] == ["selftest"]:
        sys.exit(selftest())
    print(__doc__)
