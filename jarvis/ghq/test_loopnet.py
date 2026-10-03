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
    files = os.listdir(tmp_path / "training" / "raw")
    assert len(files) == 1 and files[0].startswith("loop-triage-9-")
    rec = json.load(open(tmp_path / "training" / "raw" / files[0]))
    assert set(("history", "why", "fix", "result")) <= set(rec)
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
