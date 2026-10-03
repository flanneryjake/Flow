"""Tests for loopnet.py (the loop net) and the ghq hooks: all/any gate, send_back's loop path, the write breaker.
Run: python -m pytest -q test_loopnet.py   (no network: GitHub and the hub are faked)"""
import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def ln(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOOPNET_LOCAL", "1")
    monkeypatch.setenv("JARVIS_LOOPNET", "on")
    import loopnet
    monkeypatch.setattr(loopnet, "HOME", str(tmp_path))
    monkeypatch.setattr(loopnet, "LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(loopnet, "DAILY", str(tmp_path / "daily.json"))
    monkeypatch.setattr(loopnet, "BREAKER", str(tmp_path / "breaker.json"))
    return loopnet


def test_fingerprint_ignores_noise_keeps_the_blocker(ln):
    a = ln.fingerprint("Missing photos.csv (run 3, took 41 s at 2026-10-03T01:02:03Z)")
    assert a == ln.fingerprint("still missing photos.csv after 12 s")
    assert a != ln.fingerprint("Missing receipts.csv")
    assert ln.fingerprint("needs JELLYFIN_API_KEY set") == ln.fingerprint("JELLYFIN_API_KEY is not set yet")
    assert ln.fingerprint("waiting on #12") != ln.fingerprint("waiting on #13")


def test_gate_first_then_loop(ln):
    v1 = ln.gate(42, "rig", "missing photos.csv")
    assert v1["verdict"] == "first"
    v2 = ln.gate(42, "homebase", "missing photos.csv again")
    assert v2["verdict"] == "loop" and v2["bounces"] == 1 and v2["same_reason"] == 1
    events = [r["event"] for r in ln.history(42)]
    assert events == ["needs-jake", "loop"]
    assert ln.check(43, "x", github_bounces=0)["verdict"] == "first"


def test_github_backup_when_ledger_is_empty(ln):
    v = ln.check(77, "missing x.csv", github_bounces=3)
    assert v["verdict"] == "loop" and v["source"] == "github"


def test_daily_tag_and_rollover(ln, monkeypatch):
    t1, t2 = ln.daily_tag(10), ln.daily_tag(11)
    day = dt.datetime.now().astimezone().strftime("%Y%m%d")
    assert t1 == f"00-{day}" and t2 == f"01-{day}" and ln.daily_tag(10) == t1   # same card keeps its tag all day
    r = ln.rollover("test")
    assert r["ok"]
    st = json.load(open(ln.DAILY))
    assert st["next"] == 0 and st["tags"] == {} and st["previous"]["count"] == 2


def test_auto_roll_after_0730(ln, monkeypatch):
    json.dump({"day": "20000101", "next": 5, "tags": {"9": "04-20000101"}}, open(ln.DAILY, "w"))
    fake_now = dt.datetime(2026, 10, 3, 8, 0).astimezone()
    monkeypatch.setattr(ln, "_now", lambda: fake_now)
    assert ln.daily_tag(9) == "00-20261003"


def test_breaker_trips_once_and_refuses(ln, monkeypatch):
    monkeypatch.setattr(ln, "BREAKER_WRITES", 3)
    alerts = []
    for _ in range(3):
        ln.note_write(5, alert=lambda n, c: alerts.append((n, c)))
    with pytest.raises(ln.BreakerOpen):
        ln.note_write(5, alert=lambda n, c: alerts.append((n, c)))
    with pytest.raises(ln.BreakerOpen):
        ln.note_write(5, alert=lambda n, c: alerts.append((n, c)))
    assert alerts == [(5, 4)]                       # one alert per card per hour
    ln.note_write(6)                                # other cards unaffected
    assert any(r["event"] == "loop" and r.get("alert") == "breaker" for r in ln.history(5))


def test_kill_switch(ln, monkeypatch):
    monkeypatch.setenv("JARVIS_LOOPNET", "off")
    assert ln.gate(1, "rig", "x")["verdict"] == "first" and ln.history(1) == []


def test_export_triage_goes_through_intake(ln, tmp_path, monkeypatch):
    if not os.path.exists(ln.TRAINING_INTAKE):
        pytest.skip("training_intake.py not installed on this PC")
    monkeypatch.setenv("JARVIS_TRAINING_DIR", str(tmp_path / "training"))
    rc = ln.export_triage(9, "Card", [{"event": "needs-jake"}], "Why it looped: x", {"action": "close"}, {"outcome": "closed"})
    assert rc == 0
    files = os.listdir(tmp_path / "training" / "raw" / "loop-triage")
    assert len(files) == 1 and files[0].startswith("loop-triage-9-")
    pair = json.loads(open(tmp_path / "training" / "raw" / "loop-triage" / files[0], encoding="utf-8").readline())
    assert pair["meta"]["result"] == {"outcome": "closed"} and pair["messages"][1]["role"] == "user"   # history in, fix out
    rc = ln.export_triage(9, "Card", [], "token ghp_" + "a" * 36, {}, {})   # guardrails refuse secrets
    assert rc == 1


# ---------------------------------------------------------------- ghq hooks

@pytest.fixture
def ghq_fake(ln, monkeypatch):
    import ghq
    calls = {"api": [], "comments": [], "status": []}
    issues = {42: {"number": 42, "title": "Card 42", "body": "", "state": "open",
                   "labels": [{"name": "status:working"}, {"name": "claimed:rig"}, {"name": "p1"}]},
              12: {"number": 12, "title": "Card 12", "body": "", "state": "open", "labels": []}}
    def api(method, path, body=None):
        calls["api"].append((method, path, body))
        n = int(path.split("/issues/")[1].split("/")[0])
        if method == "PUT":
            issues[n]["labels"] = [{"name": x} for x in body["labels"]]
        return issues[n]
    monkeypatch.setattr(ghq, "api", api)
    monkeypatch.setattr(ghq, "comment", lambda n, t, **k: calls["comments"].append((n, t)) or {"id": 1})
    monkeypatch.setattr(ghq, "set_status", lambda n, s, **k: calls["status"].append((n, s)))
    monkeypatch.setattr(ghq, "bounce_history", lambda n: [])
    return ghq, calls, issues


def test_send_back_second_time_parks_for_triage(ghq_fake, ln, monkeypatch):
    ghq, calls, issues = ghq_fake
    asked = []
    monkeypatch.setattr(ghq, "ask_jake", lambda n, q: asked.append(n))
    ghq.send_back(42, "rig", "missing photos.csv", models=[])          # first: goes to Jake as before
    assert asked == [42]
    r = ghq.send_back(42, "rig", "missing photos.csv", models=[])      # second: LOOP, never reaches Jake
    assert r == ("loop",) and asked == [42]
    names = [l["name"] for l in issues[42]["labels"]]
    assert "triage" in names and "needs-claude-review" in names and "status:snoozed" in names
    assert not any(x.startswith("claimed:") for x in names)
    assert calls["comments"][-1][1].startswith(ghq.TRIAGE_MARK)


def test_gate_conditions_and_text(ghq_fake, tmp_path):
    ghq, calls, issues = ghq_fake
    f = tmp_path / "photos.csv"
    gate = [{"kind": "file", "value": str(f)},
            {"kind": "any", "value": [{"kind": "card", "value": 12}, {"kind": "time", "value": "2099-01-01T00:00:00Z"}]}]
    g = ghq.snooze_until(42, "all", gate, "rig", "test gate")
    assert g["kind"] == "all" and "AND" in ghq._gate_text(g) and "OR" in ghq._gate_text(g)
    assert calls["comments"][-1][1].startswith("<!-- jarvis:snooze ")
    s = {"kind": "all", "value": g["value"]}
    assert not ghq.condition_met(s, closed=set())                     # nothing met yet
    f.write_text("x")
    assert not ghq.condition_met(s, closed=set())                     # file yes, but neither #12 nor the time
    assert ghq.condition_met(s, closed={12})                          # file AND (#12 OR time)
    assert ghq.condition_met({"kind": "any", "value": g["value"]}, closed=set())   # any: the file alone is enough
    with pytest.raises(ValueError):
        ghq.snooze_until(42, "any", [{"kind": "file", "value": str(f)}], "rig")    # already open: nothing to wait for
    with pytest.raises(ValueError):
        ghq.snooze_until(42, "all", [{"kind": "file", "value": "photos.csv"}], "rig")   # gate files need full paths


def test_breaker_blocks_writes_in_request(ghq_fake, ln, monkeypatch):
    import ghq
    monkeypatch.setattr(ln, "BREAKER_WRITES", 2)
    monkeypatch.setattr(ghq, "_breaker_alert", lambda n, c: None)
    ghq._breaker("/repos/x/y/issues/88/comments")
    ghq._breaker("/repos/x/y/issues/88/labels")
    with pytest.raises(ghq.GitHubError):
        ghq._breaker("/repos/x/y/issues/88")
    ghq._breaker("/repos/x/y/labels")   # not a card path: never counted


# ---------------------------------------------------------------- loop marks, read gate, report, SFT pairs, dates

def test_loop_mark_round_trip_and_read(ln):
    text, mark = ln.loop_mark_text("It kept asking for photos.csv.", "Made #501 to export it; #500 waits on it.", [501], "Tars (5060) + Claude")
    cs = [{"id": 7, "body": "older"}, {"id": 8, "body": text}]
    m = ln.loop_mark_of(cs)
    assert m["id"] == 8 and m["related"] == [501] and m["why"].startswith("It kept")
    assert not ln.is_read(500, 8)
    ln.mark_read(500, 8)
    assert ln.is_read(500, 8) and not ln.is_read(500, 9)       # a newer loop mark needs a new read


def test_report_shape(ln):
    ln.record(500, "loop", "missing photos.csv", "rig")
    ln.record(500, "triaged", "the export never ran", "homebase", first_pass_by="Tars (5060)", outcome="snoozed")
    ln.record(501, "looped", "made for #500", "homebase")
    ln.record(502, "snoozed", "waits on #12", "rig")
    tri = {"500": {"verdict": "The export step never ran, so the file never existed", "done": ["created #501"], "title": "Card 500",
                   "first_pass_by": "Tars (5060)"}}
    r = ln.report(since="2000-01-01T00:00:00", triage_states=tri)
    assert set(r) >= {"since", "generated", "day", "counts", "cards", "exile_review"}
    c = {x["card"]: x for x in r["cards"]}
    assert c[500]["looped"] and c[500]["reviewed_by"] == ["Tars (5060)", "Claude"]
    assert c[500]["explanation"].startswith("It looped because the export step never ran")
    assert len([s for s in c[500]["explanation"].split(". ") if s]) <= 2
    assert c[502]["held"] and not c[502]["looped"]
    assert r["counts"]["looped"] == 2


def test_sft_pairs_for_tars_and_jarvis(ln):
    pairs = ln.sft_pairs(500, "Card", [{"at": "2026-10-03", "event": "needs-jake", "text": "missing photos.csv"}],
                         {"by": "Tars (5060)", "diagnosis": {"why": "x"}}, "Why it looped: the export never ran\nAction: depend",
                         {"action": "depend", "cards": [{"title": "Export photos.csv"}]}, {"outcome": "snoozed"})
    assert [p["target"] for p in pairs] == ["tars", "jarvis"]
    msg = pairs[0]["messages"]
    assert [m["role"] for m in msg] == ["system", "user", "assistant"]
    assert json.loads(msg[2]["content"])["why"] == "the export never ran"
    assert pairs[0]["meta"]["first_pass"]["by"] == "Tars (5060)"


def test_export_goes_to_raw_loop_triage(ln, tmp_path, monkeypatch):
    if not os.path.exists(ln.TRAINING_INTAKE):
        pytest.skip("training_intake.py not installed on this PC")
    monkeypatch.setenv("JARVIS_TRAINING_DIR", str(tmp_path / "training"))
    assert ln.export_triage(9, "Card", [], "Why it looped: x", {"action": "close"}, {"outcome": "closed"}) == 0
    files = os.listdir(tmp_path / "training" / "raw" / "loop-triage")
    assert len(files) == 1 and files[0].endswith(".jsonl")
    lines = open(tmp_path / "training" / "raw" / "loop-triage" / files[0], encoding="utf-8").read().splitlines()
    assert len(lines) == 2


def test_midnight_never_gates_running(ghq_fake, ln, monkeypatch):
    """A card tagged yesterday still claims and runs today: neither the tag nor the approval date gates anything."""
    import ghq
    day1 = dt.datetime(2026, 10, 2, 23, 59).astimezone()
    monkeypatch.setattr(ln, "_now", lambda: day1)
    tag1 = ln.daily_tag(42)
    v1 = ln.check(42, "x", github_bounces=0)
    day2 = dt.datetime(2026, 10, 3, 0, 1).astimezone()          # past midnight, before the 07:30 roll
    monkeypatch.setattr(ln, "_now", lambda: day2)
    assert ln.tag_of(42) == tag1                                  # same report day until the morning report rolls it
    assert ln.check(42, "x", github_bounces=0) == v1              # the gate doesn't look at dates
    day2b = dt.datetime(2026, 10, 3, 8, 0).astimezone()           # after the roll
    monkeypatch.setattr(ln, "_now", lambda: day2b)
    assert ln.tag_of(42) == "" and ln.daily_tag(43).startswith("00-20261003")
    # ready() lists the approved card regardless of when (or which day) it was approved
    monkeypatch.setattr(ghq, "fleet_allows", lambda m: True)
    monkeypatch.setattr(ghq, "fleet_defers", lambda m: False)
    card = {"number": 42, "title": "t", "labels": [{"name": "status:approved"}, {"name": "machine:any"}],
            "created_at": "2026-10-02T23:59:00Z", "pull_request": None}
    monkeypatch.setattr(ghq, "request", lambda *a, **k: (200, [card], {}))
    assert [c["number"] for c in ghq.ready("homebase")] == [42]
