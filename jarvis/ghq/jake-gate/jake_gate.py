"""Jake gate (10/03, Jake: "more tasks are looping ... we need a better filter"): ONE deterministic check that every path
onto Jake's lists (needs-jake, a To-Do step, Approvals) goes through. Standalone: the rig uses the same file. No AI call.

decide(...) -> ('jake', why) or ('claude', why). Rules, in order:
  0. Jake's own items (owner:jake, from:jake, life, health, homework) always reach him.
  1. Otherwise only these reach Jake:
     (c) PIN / spend / post or send / deleting his data
     (d) a real decision: a proposal, an explicit choice or yes/no, answers he has to give
     (a) a physical action (hardware, photos, the printer bed, his phone)
     (b) a sign-in or credentials (accounts, logins, API keys, the Tailscale admin console, secret links)
     (e) a UAC Yes tap (Claude pops the prompt first, so it is one tap)
  2. Everything else (apply / patch / copy / edit a file, run a script, restart something, check files or logs, "the
     sandbox couldn't write", "the Worker isn't allowed", fix a path, rerun a test) goes to Claude.
  3. A card that was on Jake's list before never goes back to him by itself: Claude, except PIN items and decisions he
     hasn't answered yet.
  4. Fails closed toward Claude: any error -> Claude.
Ledger: C:\\Jarvis\\loopnet\\jake-gate.jsonl, one line per decision. Off switch: JARVIS_JAKE_GATE=off.
"""
import datetime as dt
import json
import os
import re

LEDGER = os.environ.get("JARVIS_JAKE_GATE_LEDGER", os.path.join(r"C:\Jarvis\loopnet", "jake-gate.jsonl"))
OWN = {"owner:jake", "from:jake", "life", "health", "homework"}
PROPOSAL = {"proposal", "phase-plan", "type:proposal"}
EPISODE_GAP_S = 15 * 60   # markers / asks this close together are one ask (the old double-marker bug)

RULES = [
    ("pin", re.compile(r"\b(pin|buy|purchase|pay|payment|place (an |the )?order|spend|checkout|subscribe|refund|"
                       r"publish|post (it|to|on|the)|send (the |an |a )?(email|message|text|dm)|email (the|a|your) |"
                       r"delete (your|my|his|jake'?s)|wipe (your|the drive))\b", re.I)),
    ("decision", re.compile(r"\b(yes or no|yes/no|pick (one|between|warm|hot|a |an |which)|choose|decide|decision|"
                            r"which (one|option|of)|approve or|not now|go ahead\?|ok to|okay to|do you want|"
                            r"should (i|we|jarvis|tars)|say yes|answer (the|these|your|a)|quiz answers|your answers?)\b", re.I)),
    ("uac", re.compile(r"\b(admin powershell|as admin|run as administrator|administrator|uac|elevated|admin rights|"
                       r"admin prompt|admin script|driver|bios)\b", re.I)),
    ("signin", re.compile(r"\b(sign in|sign-in|signin|log in|log-in|login|password|passcode|credentials?|accounts?|"
                          r"api key|tailscale (admin|dns|acl|console|settings)|admin console|oauth|authori[sz]e|2fa|"
                          r"two-factor|ical|secret link|private link|google drive|google account|app password|"
                          r"claude app|claude\.ai|remote control environment|(api|secret|license|access|private) keys?|"
                          r"provide\b[^.\n]{0,40}\bkeys?)\b", re.I)),
    ("physical", re.compile(r"\b(plug|unplug|cable|photos?|pictures?|camera|printer bed|clear the bed|filament|"
                            r"power button|press the|physically|by hand|hardware|liquid metal|coolant|thermal paste|"
                            r"iphone|home screen|on your phone|receipts?|walk to|in person|call (the|a) )\b", re.I)),
]
ANSWERED = re.compile(r"^(Jake on his To-Do page|Jake answered|Jake's answer|Jake tapped)", re.I)
ASK_START = ("**Needs Jake", )


def _text(reason, step, comments):
    parts = [str(reason or "")]
    s = step if isinstance(step, dict) else {}
    parts += [str(s.get(k) or "") for k in ("title", "why", "ask")] + [str(x) for x in (s.get("steps") or [])]
    t = "\n".join(p for p in parts if p.strip())
    if not t.strip():   # no reason given (a bare set_status): the card's latest notes
        t = "\n".join(re.sub(r"<!--.*?-->", "", c.get("body") or "", flags=re.S)[:600] for c in list(comments)[-3:])
    return t


def category(text, step=None, labels=()):
    labels = set(labels)
    s = step if isinstance(step, dict) else {}
    if "pin" in labels or s.get("pin"):
        return "pin"
    if labels & PROPOSAL or s.get("proposal"):
        return "decision"
    for name, rx in RULES:
        if rx.search(text or ""):
            return name
    return None


def _ts(c):
    try:
        return dt.datetime.fromisoformat(str(c.get("created_at") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def ask_episodes(comments):
    """(number of separate asks on this card, whether Jake answered/ticked after the first one)."""
    times, answered, first = [], False, None
    for c in comments or []:
        b = c.get("body") or ""
        is_ask = ("<!-- jarvis:jake " in b or b.startswith(ASK_START)
                  or (b.startswith("<!-- jarvis:run") and '"outcome": "needs-jake"' in b))
        if is_ask:
            times.append(_ts(c))
            first = first if first is not None else _ts(c)
        elif first is not None and ANSWERED.match(b.lstrip()):
            answered = True
    eps, last = 0, None
    for t in sorted(times):
        if last is None or t - last > EPISODE_GAP_S:
            eps += 1
        last = t
    return eps, answered


def _log(rec):
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def decide(number, issue=None, labels=(), comments=(), reason="", step=None, pending=True, source=""):
    """('jake'|'claude', why). pending=True: the ask being decided is not written on the card yet."""
    if os.environ.get("JARVIS_JAKE_GATE", "on") == "off":
        return "jake", "gate off"
    try:
        labels = set(labels or [])
        s = step if isinstance(step, dict) else {}
        if labels & OWN or s.get("place") == "homework":
            verdict, why, cat = "jake", "Jake's own item", "own"
        else:
            text = _text(reason, s, comments or [])
            cat = category(text, s, labels)
            eps, answered = ask_episodes(comments or [])
            prior = eps if pending else max(0, eps - 1)
            if cat == "pin":
                verdict, why = "jake", "PIN / spend / post / delete"
            elif cat == "decision" and not (prior and answered):
                verdict, why = "jake", "a decision only Jake can make"
            elif prior >= 1:
                verdict, why = "claude", f"was on Jake's list before ({prior}x): it doesn't go back to him by itself"
            elif cat in ("physical", "signin", "uac"):
                verdict, why = "jake", {"physical": "a physical action", "signin": "a sign-in / credentials",
                                        "uac": "a UAC Yes tap (Claude pops the prompt first)"}[cat]
            else:
                verdict, why = "claude", "not a hands-only step (apply / run / restart / check / fix): Claude does it"
    except Exception as e:  # noqa: BLE001 - fail closed toward Claude
        verdict, why, cat = "claude", f"gate error ({type(e).__name__}): parked for Claude", "error"
    _log({"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "card": number, "verdict": verdict,
          "why": why, "category": cat, "source": source, "pending": pending})
    return verdict, why
