"""loopnet - the loop net (Jake 10/03): a card that comes back for Jake a second time never reaches him; it goes to
Claude flagged LOOP. Detection is plain code (free, instant, works while every model is offline); the WHY is triage.

Pieces
  ledger      every push forward (approved, claimed, needs-jake, snoozed, loop, done) as one JSON line, keyed on the
              GitHub card number + a fingerprint of the reason ("missing photos.csv"). Kept forever, never reset.
              It lives on homebase (C:\\Jarvis\\loopnet\\ledger.jsonl, served by the hub as /api/loopnet/*). Other PCs
              reach it over the tailnet; if the hub can't be reached, GitHub is the backup (ghq.bounce_history).
  gate        check(n, reason) before a card goes to Jake: first time -> "first" (normal send-back + To-Do step),
              seen before -> "loop" (ghq.send_back parks it on triage instead; the hub's triage lane picks it up).
  daily tag   NN-YYYYMMDD, a friendly per-day label for reading and training data only (never the identity).
              Rolls to 00 of the next day when the morning report is sent (rollover()), or by itself at 07:30.
  breaker     GitHub writes per card per hour. Over BREAKER_WRITES, further writes to that card are refused for the
              rest of the hour and one alert is pushed, before a runaway loop can burn the 5,000/hr limit.
  training    export_triage(): one record per loop triage (history, why, fix, result) added to
              C:\\Jarvis\\training\\raw through guardrails\\training_intake.py (secrets / patient ids refused).

Kill switch: user env var JARVIS_LOOPNET=off (gate, ledger writes and breaker all stand down). Standard library only.
"""
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

HOME = os.environ.get("JARVIS_LOOPNET_DIR", r"C:\Jarvis\loopnet")
LEDGER = os.path.join(HOME, "ledger.jsonl")
DAILY = os.path.join(HOME, "daily.json")
BREAKER = os.path.join(HOME, "breaker.json")
HUB = os.environ.get("JARVIS_HUB_URL", "https://laptop-4150egrs.tail3bbcb8.ts.net")
MACHINE_FILE = r"C:\Jarvis\watchdog\machine.txt"
TRAINING_INTAKE = r"C:\Jarvis\guardrails\training_intake.py"
BREAKER_WRITES = int(os.environ.get("JARVIS_BREAKER_WRITES", "40"))   # GitHub writes per card per hour
AUTO_ROLL = (7, 30)   # if no morning report rolled the tag by 07:30 local, the first call after that rolls it
EVENTS = ("approved", "claimed", "needs-jake", "snoozed", "loop", "done", "triaged", "looped", "read", "exile-review")
LOOP_RE = re.compile(r"<!-- jarvis:loop (\{.*?\}) -->", re.S)
LOOPED = "looped"   # label: this card went through a loop; Jake must open the summary before acting on it
TRAIN_DIR_NAME = "loop-triage"   # C:\Jarvis\training\raw\loop-triage\


def enabled():
    return os.environ.get("JARVIS_LOOPNET", "on").lower() not in ("off", "0", "false", "no")


def is_home():
    """True on the PC that keeps the ledger (homebase). Everyone else talks to the hub."""
    if os.environ.get("JARVIS_LOOPNET_LOCAL") in ("1", "0"):
        return os.environ["JARVIS_LOOPNET_LOCAL"] == "1"
    try:
        with open(MACHINE_FILE, encoding="utf-8") as f:
            return f.read().strip().lower() == "homebase"
    except OSError:
        return False


# ---------------------------------------------------------------- small file helpers (several processes share HOME)

class _Lock:
    def __init__(self, path, wait=5.0):
        self.path, self.wait = path + ".lock", wait

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        t0 = time.time()
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:   # a lock older than 30 s was left by a crashed process
                    if time.time() - os.path.getmtime(self.path) > 30:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.time() - t0 > self.wait:
                    raise TimeoutError(f"loopnet lock busy: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *a):
        os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _now():
    return dt.datetime.now().astimezone()


# ---------------------------------------------------------------- fingerprint

FILE_RE = re.compile(r"[\w\-]+\.(?:csv|json|jsonl|md|txt|py|ps1|bat|cmd|yaml|yml|xlsx|docx|pdf|png|jpg|gcode|3mf|stl|html|js|zip)\b", re.I)
CARD_RE = re.compile(r"#(\d{1,6})\b")
ENV_RE = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b")
VOLATILE = re.compile(r"\d{4}-\d\d-\d\d[T ][\d:.+Z-]*|\b\d+(?:\.\d+)?\s*(?:s|ms|min|minutes|seconds|%)\b|\b[0-9a-f]{8,}\b|\d+", re.I)


def fingerprint(reason):
    """Stable id for WHY a card stopped: the files, cards and env vars it names, plus the gist of the words.
    "missing photos.csv (run 3, 41 s)" and "still missing photos.csv after 12 s" give the same fingerprint."""
    text = str(reason or "")
    keys = sorted({m.lower() for m in FILE_RE.findall(text)} | {f"#{m}" for m in CARD_RE.findall(text)} |
                  set(ENV_RE.findall(text)))
    if keys:
        basis = "k:" + "|".join(keys)
    else:
        words = re.findall(r"[a-z]{3,}", VOLATILE.sub(" ", text.lower()))
        stop = {"the", "and", "for", "this", "that", "with", "was", "are", "has", "have", "not", "but", "still", "again",
                "card", "jake", "needs", "need", "please", "run", "after"}
        basis = "w:" + " ".join(sorted({w for w in words if w not in stop})[:12])
    return hashlib.sha1(basis.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- ledger (local on homebase, hub API elsewhere)

def _hub(method, path, body=None, timeout=8):
    req = urllib.request.Request(HUB.rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Jarvis-Notify": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _local_rows(n=None):
    out = []
    try:
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if n is None or r.get("card") == n:
                    out.append(r)
    except OSError:
        pass
    return out


def record(n, event, reason="", machine="", **extra):
    """Append one ledger row. Never raises (the net must never break a Worker)."""
    if not enabled():
        return None
    row = {"at": _now().isoformat(timespec="seconds"), "card": int(n), "event": str(event), "fp": fingerprint(reason) if reason else "",
           "machine": machine or "", "reason": str(reason or "")[:300], "tag": tag_of(n), **extra}
    try:
        if is_home():
            os.makedirs(HOME, exist_ok=True)
            with _Lock(LEDGER), open(LEDGER, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        else:
            _hub("POST", "/api/loopnet/record", row)
    except Exception as e:  # noqa: BLE001
        print(f"loopnet.record #{n}: {e}", file=sys.stderr)
    return row


def history(n):
    """Ledger rows for one card, oldest first ([] if neither the ledger nor the hub answers)."""
    try:
        if is_home():
            return _local_rows(int(n))
        return _hub("GET", f"/api/loopnet/card/{int(n)}").get("rows", [])
    except Exception as e:  # noqa: BLE001
        print(f"loopnet.history #{n}: {e}", file=sys.stderr)
        return []


def check(n, reason="", rows=None, github_bounces=None):
    """Has this card already come back for Jake? {"verdict": "first"|"loop", "bounces", "same_reason", "fp", "source"}.
    bounces counts earlier needs-jake + loop events in the ledger. When the ledger has nothing (new install, or the hub
    is down on a remote PC) the GitHub history is the backup: github_bounces, or ghq.bounce_history() if available."""
    fp = fingerprint(reason) if reason else ""
    rows = history(n) if rows is None else rows
    back = [r for r in rows if r.get("event") in ("needs-jake", "loop")]
    source = "ledger"
    if not back:
        if github_bounces is None:
            try:
                import ghq
                github_bounces = sum(1 for h in ghq.bounce_history(int(n)) if h["event"] in ("needs-jake", "run needs-jake"))
            except Exception:  # noqa: BLE001
                github_bounces = 0
        if github_bounces:
            source = "github"
        bounces, same = github_bounces, 0
    else:
        bounces, same = len(back), sum(1 for r in back if fp and r.get("fp") == fp)
    return {"verdict": "loop" if bounces >= 1 else "first", "bounces": bounces, "same_reason": same, "fp": fp, "source": source}


def gate(n, machine, reason):
    """Call right before a card would go back to Jake. Records the bounce and returns check()'s verdict.
    'first' -> send it to Jake as usual. 'loop' -> don't; park it for triage (ghq.send_back does that)."""
    if not enabled():
        return {"verdict": "first", "bounces": 0, "same_reason": 0, "fp": "", "source": "off"}
    v = check(n, reason)
    record(n, "loop" if v["verdict"] == "loop" else "needs-jake", reason, machine, bounces=v["bounces"])
    return v


# ---------------------------------------------------------------- daily tag NN-YYYYMMDD (a label, never the identity)

def _report_day(state):
    """The day the counter belongs to. Rolls by itself at AUTO_ROLL if no morning report rolled it first."""
    today, now = _now().strftime("%Y%m%d"), _now()
    day = state.get("day")
    if not day:
        return today
    if day < today and (now.hour, now.minute) >= AUTO_ROLL:
        return today
    return day


def tag_of(n):
    """The card's tag for the current report day, or "" if it hasn't been pushed forward today."""
    st = _read_json(DAILY, {}) if is_home() else {}
    return (st.get("tags") or {}).get(str(n), "") if st.get("day") == _report_day(st) else ""


def daily_tag(n):
    """Give card n its NN-YYYYMMDD tag for today (once per day; the same card keeps its tag all day)."""
    if not enabled():
        return ""
    if not is_home():
        try:
            return _hub("POST", "/api/loopnet/tag", {"card": int(n)}).get("tag", "")
        except Exception as e:  # noqa: BLE001
            print(f"loopnet.daily_tag #{n}: {e}", file=sys.stderr)
            return ""
    with _Lock(DAILY):
        st = _read_json(DAILY, {})
        day = _report_day(st)
        if st.get("day") != day:
            st = {"day": day, "next": 0, "tags": {}, "rolled_by": st.get("rolled_by_next") or "auto 07:30"}
        tags = st.setdefault("tags", {})
        if str(n) not in tags:
            tags[str(n)] = f"{st.get('next', 0):02d}-{day}"
            st["next"] = st.get("next", 0) + 1
        _write_json(DAILY, st)
        return tags[str(n)]


def rollover(by="morning report"):
    """Call when the morning report is sent: the counter clicks forward to 00 of the next day."""
    if not is_home():
        return _hub("POST", "/api/loopnet/rollover", {"by": by})
    with _Lock(DAILY):
        old = _read_json(DAILY, {})
        nxt = (_now() + dt.timedelta(days=1)).strftime("%Y%m%d") if _now().hour >= 12 else _now().strftime("%Y%m%d")
        if old.get("day") == nxt:
            return {"ok": True, "day": nxt, "already": True}
        _write_json(DAILY, {"day": nxt, "next": 0, "tags": {}, "rolled_by": by, "rolled_at": _now().isoformat(timespec="seconds"),
                            "previous": {"day": old.get("day"), "count": old.get("next", 0)}})
    return {"ok": True, "day": nxt, "previous": old.get("day"), "previous_count": old.get("next", 0)}


# ---------------------------------------------------------------- GitHub write breaker (per card per hour)

class BreakerOpen(RuntimeError):
    """Too many GitHub writes on one card this hour: refuse until the hour turns over."""


def _hour():
    return _now().strftime("%Y%m%d%H")


def note_write(n, alert=None):
    """Count one GitHub write to card n. Raises BreakerOpen once the card is over the limit this hour.
    `alert(n, count)` is called once, the first time the breaker trips for that card in that hour."""
    if not enabled():
        return 0
    path = BREAKER if is_home() else os.path.join(tempfile.gettempdir(), "jarvis-loopnet-breaker.json")
    with _Lock(path):
        st = _read_json(path, {})
        hour = _hour()
        if st.get("hour") != hour:
            st = {"hour": hour, "writes": {}, "tripped": st.get("tripped", [])[-200:]}
        c = st["writes"].get(str(n), 0) + 1
        st["writes"][str(n)] = c
        trip = c > BREAKER_WRITES
        first_trip = trip and not any(t.get("card") == int(n) and t.get("hour") == hour for t in st["tripped"])
        if first_trip:
            st["tripped"].append({"card": int(n), "hour": hour, "writes": c, "at": _now().isoformat(timespec="seconds")})
        _write_json(path, st)
    if first_trip:
        record(n, "loop", f"GitHub breaker: {c} writes on #{n} this hour", alert="breaker")
        if alert:
            try:
                alert(n, c)
            except Exception:  # noqa: BLE001
                pass
    if trip:
        raise BreakerOpen(f"#{n}: {c} GitHub writes this hour (limit {BREAKER_WRITES}); paused until the hour turns over")
    return c


# ---------------------------------------------------------------- loop marker ("looped" label + plain summary Jake must read)

def loop_mark_text(why, changed, related=(), by=""):
    mark = {"why": str(why or "").strip()[:600], "changed": str(changed or "").strip()[:600],
            "related": sorted({int(x) for x in related or []}), "by": by, "at": _now().isoformat(timespec="seconds")}
    rel = (" Related: " + ", ".join(f"#{x}" for x in mark["related"]) + ".") if mark["related"] else ""
    return (f"<!-- jarvis:loop {json.dumps(mark, ensure_ascii=False)} -->\n**Looped.** {mark['why']} "
            f"**What changed:** {mark['changed']}{rel}"), mark


def loop_mark_of(comments):
    """The newest loop summary on a card: {why, changed, related, by, at, id} or None."""
    found = None
    for c in comments:
        m = LOOP_RE.search(c.get("body") or "")
        if m:
            try:
                found = {**json.loads(m.group(1)), "id": c.get("id")}
            except ValueError:
                pass
    return found


def mark_read(n, mark_id, by="jake"):
    """Jake opened the loop summary: recorded in the ledger (the app's approve / To-Do tick unlock after this)."""
    return record(n, "read", "", "phone", mark=int(mark_id or 0), by=by)


def is_read(n, mark_id, rows=None):
    rows = history(n) if rows is None else rows
    return any(r.get("event") == "read" and int(r.get("mark") or 0) == int(mark_id or 0) for r in rows)


# ---------------------------------------------------------------- training export (SFT pairs for Tars and Jarvis)

SFT_SYSTEM = ("You are {who}, a Jarvis Worker model. A task card was rejected or came back for Jake again (a loop). "
              "Read the card and its history, say in plain words why it looped, and pick ONE fix: fix the done-condition "
              "and re-approve, snooze it on what it really waits for, create the dependency cards it needs first, write "
              "one clear step for Jake, or close it. Answer with JSON: {{\"why\": ..., \"action\": {{...}}}}.")


def sft_pairs(n, title, history_rows, first_pass, verdict_text, fix, result):
    """Supervised pairs (chat format) teaching Tars and Jarvis to handle a rejection themselves. The answer is the
    final, Claude-reviewed fix - the local model's own first pass goes in meta so the gap can be studied."""
    hist = "\n".join(f"- {h.get('at', '')[:16]} {h.get('event')}: {str(h.get('text', ''))[:300]}" for h in history_rows[-25:])
    user = f"Card #{int(n)}: {title}\n\nHistory (oldest first):\n{hist or '- (none)'}"
    why = next((ln.split(":", 1)[1].strip() for ln in str(verdict_text).splitlines() if ln.lower().startswith("why it looped")), "")
    answer = json.dumps({"why": why or str(verdict_text)[:400], "action": fix}, ensure_ascii=False)
    meta = {"card": int(n), "tag": tag_of(n), "first_pass": first_pass, "result": result,
            "at": _now().isoformat(timespec="seconds"), "source": "loop triage (Claude-reviewed)"}
    return [{"messages": [{"role": "system", "content": SFT_SYSTEM.format(who=who)}, {"role": "user", "content": user},
                          {"role": "assistant", "content": answer}], "target": key, "meta": meta}
            for key, who in (("tars", "Tars (the 5060's model)"), ("jarvis", "Jarvis (the rig's model)"))]


def export_triage(n, title, history_rows, why, fix, result, source="loop triage", by="claude", first_pass=None):
    """Training data for each loop triage, through the guardrailed intake (refused whole if it carries secrets or
    patient ids): one .jsonl of SFT pairs in C:\\Jarvis\\training\\raw\\loop-triage\\. Jake pre-approved adding
    training data; this never starts a fine-tune. Returns the intake exit code (0 added, 1 refused, 2 bad input),
    or None when intake isn't installed here."""
    if not os.path.exists(TRAINING_INTAKE):
        return None
    d = tempfile.mkdtemp(prefix="loopnet-")
    sub = os.path.join(d, TRAIN_DIR_NAME)
    os.makedirs(sub)
    path = os.path.join(sub, f"loop-triage-{int(n)}-{_now():%Y%m%d-%H%M%S}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for p in sft_pairs(n, title, history_rows, first_pass, why, fix, result):
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    try:
        p = subprocess.run([sys.executable, TRAINING_INTAKE, "add", d, "--topic", "raw", "--source", f"{source} #{int(n)}",
                            "--by", by], capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode
    finally:
        try:
            os.remove(path)
            os.rmdir(sub)
            os.rmdir(d)
        except OSError:
            pass


# ---------------------------------------------------------------- morning-report data (/api/loopnet/report)

EXILE_REVIEW = os.path.join(HOME, "exile-review.json")


def _two_sentences(text, limit=320):
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", str(text or "")).strip())
    out = " ".join(parts[:2]).strip()
    return out if len(out) <= limit else out[:limit].rsplit(" ", 1)[0] + "..."


def report(since=None, triage_states=None, titles=None):
    """Held, looped and model-reviewed cards since the last morning report (or `since`), each with a 2-sentence plain
    explanation and who reviewed it, plus the latest exile review. JSON shape documented in RIG-SETUP-loopnet.md."""
    st = _read_json(DAILY, {})
    since = since or st.get("rolled_at") or (_now() - dt.timedelta(hours=24)).isoformat(timespec="seconds")
    rows = [r for r in _local_rows() if r.get("at", "") >= since]
    tri = triage_states or {}
    titles = titles or {}
    cards = {}
    for r in rows:
        n = r["card"]
        c = cards.setdefault(n, {"card": n, "title": titles.get(n, ""), "events": [], "reviewed_by": [], "explanation": "",
                                 "outcome": "", "looped": False, "held": False})
        c["events"].append(r["event"])
        if r["event"] in ("loop", "triaged", "looped"):
            c["looped"] = True
        if r["event"] in ("snoozed", "loop"):
            c["held"] = True
        if r["event"] == "triaged":
            c["outcome"] = r.get("outcome", "")
            for who in (r.get("first_pass_by"), "Claude"):
                if who and who not in c["reviewed_by"]:
                    c["reviewed_by"].append(who)
        if r.get("reason") and not c["explanation"]:
            c["explanation"] = _two_sentences(r["reason"])
    for n, c in cards.items():
        t = tri.get(str(n)) or {}
        if t.get("verdict"):
            v = t["verdict"].strip().rstrip(".")
            v = v[:1].lower() + v[1:] if v[:2] != v[:2].upper() else v   # "The Worker ..." -> "the Worker ...", keeps "PIN"
            c["explanation"] = _two_sentences(f"It looped because {v}. " +
                                              (f"Fix: {'; '.join(t.get('done') or [])}." if t.get("done") else ""))
            c["outcome"] = c["outcome"] or t.get("outcome", "")
            c["title"] = c["title"] or t.get("title", "")
            if t.get("first_pass_by") and t["first_pass_by"] not in c["reviewed_by"]:
                c["reviewed_by"].insert(0, t["first_pass_by"])
    items = [c for c in cards.values() if c["looped"] or c["held"] or c["reviewed_by"]]
    items.sort(key=lambda c: (not c["looped"], c["card"]))
    return {"since": since, "generated": _now().isoformat(timespec="seconds"), "day": st.get("day"),
            "counts": {"looped": sum(c["looped"] for c in items), "held": sum(c["held"] for c in items),
                       "reviewed": sum(bool(c["reviewed_by"]) for c in items)},
            "cards": items, "exile_review": _read_json(EXILE_REVIEW, {})}


# ---------------------------------------------------------------- status (hub /api/loopnet/status, morning report)

def status(day=None):
    st = _read_json(DAILY, {})
    rows = _local_rows()
    since = (_now() - dt.timedelta(hours=24)).isoformat(timespec="seconds")
    recent = [r for r in rows if r.get("at", "") >= since]
    br = _read_json(BREAKER, {})
    return {"day": st.get("day"), "tagged_today": st.get("next", 0), "rolled_by": st.get("rolled_by"),
            "last_24h": {e: sum(1 for r in recent if r.get("event") == e) for e in EVENTS},
            "loops_24h": sorted({r["card"] for r in recent if r.get("event") == "loop"}),
            "breaker": {"hour": br.get("hour"), "tripped": br.get("tripped", [])[-10:]}, "rows": len(rows)}


if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["status"])[0]
    if cmd == "rollover":
        print(json.dumps(rollover(" ".join(sys.argv[2:]) or "morning report")))
    elif cmd == "check" and len(sys.argv) > 2:
        print(json.dumps(check(int(sys.argv[2]), " ".join(sys.argv[3:]))))
    elif cmd == "tag" and len(sys.argv) > 2:
        print(daily_tag(int(sys.argv[2])))
    else:
        print(json.dumps(status(), indent=1))
