"""Jarvis <-> Alexa bridge, through Home Assistant's Alexa Media Player integration.

Runs on homebase (Windows Python, standard library only). Talks to HA's REST API with the
jarvis-bot service user that install-alexa.sh creates, so no token is ever pasted by hand.

  python jarvis_alexa.py login                 # first run: trade the bot password for a refresh token
  python jarvis_alexa.py devices               # list the Echos HA can see
  python jarvis_alexa.py say "Dinner's ready" [--echo kitchen] [--announce]
  python jarvis_alexa.py heard [--hours 24]    # latest thing said to each Echo (full history is in export)
  python jarvis_alexa.py export [--day 2026-10-01]   # one day of apartment state changes -> routine log

Files (all under C:\\Jarvis by default, override with JARVIS_ALEXA_HOME):
  secrets\\ha-bot.json     {"username", "password"}  written by install-alexa.sh
  secrets\\ha-token.json   {"refresh_token"}         written by `login`
  routine\\YYYY-MM-DD.jsonl one state change per line, input for routine learning
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HA_URL = os.environ.get("HA_URL", "http://127.0.0.1:8123").rstrip("/")
CLIENT_ID = HA_URL + "/"
HOME = os.environ.get("JARVIS_ALEXA_HOME", r"C:\Jarvis")
BOT_FILE = os.path.join(HOME, "secrets", "ha-bot.json")
TOKEN_FILE = os.path.join(HOME, "secrets", "ha-token.json")
ROUTINE_DIR = os.path.join(HOME, "routine")

# Attributes that bloat the routine log without saying anything about the routine.
NOISY_ATTRS = {"entity_picture", "entity_picture_local", "media_position", "media_position_updated_at",
               "icon", "friendly_name", "supported_features", "available_text", "media_image_url"}
# Domains that change constantly and never reflect what a person did.
SKIP_DOMAINS = {"sun", "weather", "update", "event", "conversation", "tts", "stt", "wake_word"}


def _request(method, path, data=None, token=None, form=False, timeout=30):
    headers = {}
    body = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(HA_URL + path, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def login():
    """Password login flow -> refresh token. Only needs to run once (or after the token is revoked)."""
    bot = _load(BOT_FILE)
    flow = _request("POST", "/auth/login_flow", {
        "client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": CLIENT_ID})
    step = _request("POST", "/auth/login_flow/" + flow["flow_id"], {
        "client_id": CLIENT_ID, "username": bot["username"], "password": bot["password"]})
    if step.get("type") != "create_entry":
        raise SystemExit("HA refused the jarvis-bot login: %s" % step.get("errors"))
    tok = _request("POST", "/auth/token", {
        "grant_type": "authorization_code", "code": step["result"], "client_id": CLIENT_ID}, form=True)
    _save(TOKEN_FILE, {"refresh_token": tok["refresh_token"]})
    print("Logged in as %s; refresh token saved to %s" % (bot["username"], TOKEN_FILE))


def access_token():
    if os.environ.get("HA_TOKEN"):
        return os.environ["HA_TOKEN"]
    if not os.path.exists(TOKEN_FILE):
        login()
    refresh = _load(TOKEN_FILE)["refresh_token"]
    tok = _request("POST", "/auth/token", {
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT_ID}, form=True)
    return tok["access_token"]


def echo_players(states):
    """Echo media players created by Alexa Media Player (they carry last_called attributes)."""
    return [s for s in states
            if s["entity_id"].startswith("media_player.") and "last_called" in s.get("attributes", {})]


def pick_echo(players, name):
    name = name.lower().replace(" ", "_")
    hits = [p for p in players if name in p["entity_id"].lower()
            or name in p["attributes"].get("friendly_name", "").lower().replace(" ", "_")]
    if not hits:
        raise SystemExit("No Echo matches %r. Try: %s" % (name, ", ".join(p["entity_id"] for p in players)))
    return hits[0]["entity_id"]


def heard_from(states, since):
    """Utterances the Echos reported, newest first, from current state (live) or history rows."""
    out = []
    for s in echo_players(states):
        a = s["attributes"]
        ts = a.get("last_called_timestamp")
        text = a.get("last_called_summary")
        if not ts or not text:
            continue
        when = dt.datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, dt.timezone.utc)
        if when >= since:
            out.append({"when": when.isoformat(), "echo": s["entity_id"], "said": text})
    out.sort(key=lambda r: r["when"], reverse=True)
    return out


def slim(state):
    """One routine-log row from an HA state object, or None if it's noise."""
    domain = state["entity_id"].split(".", 1)[0]
    if domain in SKIP_DOMAINS:
        return None
    attrs = {k: v for k, v in state.get("attributes", {}).items() if k not in NOISY_ATTRS}
    return {"t": state.get("last_updated") or state.get("last_changed"), "entity": state["entity_id"],
            "state": state.get("state"), "attrs": attrs}


def export_day(token, day):
    start = dt.datetime.combine(day, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    path = "/api/history/period/%s?%s" % (
        urllib.parse.quote(start.isoformat()),
        urllib.parse.urlencode({"end_time": end.isoformat(), "significant_changes_only": "0"}))
    groups = _request("GET", path, token=token, timeout=120) or []
    rows = [r for g in groups for r in (slim(s) for s in g) if r]
    rows.sort(key=lambda r: r["t"] or "")
    os.makedirs(ROUTINE_DIR, exist_ok=True)
    out = os.path.join(ROUTINE_DIR, day.isoformat() + ".jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out, len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("devices")
    p = sub.add_parser("say")
    p.add_argument("message")
    p.add_argument("--echo", help="part of the Echo's name; default is every Echo")
    p.add_argument("--announce", action="store_true", help="announcement chime instead of plain speech")
    p = sub.add_parser("heard")
    p.add_argument("--hours", type=float, default=24)
    p = sub.add_parser("export")
    p.add_argument("--day", help="YYYY-MM-DD, default yesterday")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "login":
            return login()
        token = access_token()
        if args.cmd == "export":
            day = dt.date.fromisoformat(args.day) if args.day else dt.date.today() - dt.timedelta(days=1)
            out, n = export_day(token, day)
            print("%d state changes -> %s" % (n, out))
            return
        states = _request("GET", "/api/states", token=token)
        players = echo_players(states)
        if args.cmd == "devices":
            if not players:
                print("No Echos yet. Is Alexa Media Player signed in to Amazon in HA?")
            for p in players:
                print("%-45s %-10s %s" % (p["entity_id"], p["state"], p["attributes"].get("friendly_name", "")))
        elif args.cmd == "say":
            targets = [pick_echo(players, args.echo)] if args.echo else [p["entity_id"] for p in players]
            if not targets:
                raise SystemExit("No Echos to talk to yet.")
            _request("POST", "/api/services/notify/alexa_media", {
                "message": args.message, "target": targets,
                "data": {"type": "announce" if args.announce else "tts"}}, token=token)
            print("Said it on: " + ", ".join(targets))
        elif args.cmd == "heard":
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.hours)
            for r in heard_from(states, since):
                print(json.dumps(r, ensure_ascii=False))
    except urllib.error.HTTPError as e:
        raise SystemExit("Home Assistant said %s %s on %s" % (e.code, e.reason, e.url))
    except urllib.error.URLError as e:
        raise SystemExit("Can't reach Home Assistant at %s (%s)" % (HA_URL, e.reason))


if __name__ == "__main__":
    sys.exit(main())
