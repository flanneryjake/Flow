"""Tests for the Jake gate (10/03). No network. Run next to ghq.py: python -m pytest -q test_jake_gate.py"""
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import jake_gate as g  # noqa: E402


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "LEDGER", str(tmp_path / "gate.jsonl"))
    monkeypatch.delenv("JARVIS_JAKE_GATE", raising=False)


def step(title, **kw):
    return dict({"place": "phone", "title": title}, **kw)


def ask(at="2026-10-03T10:00:00Z"):
    return {"body": '<!-- jarvis:jake {"title": "x"} -->\n**On Jake\'s To-Do', "created_at": at}


@pytest.mark.parametrize("title,labels,want", [
    ("Add 3-5 real receipt photos for the scanner test", [], "jake"),            # (a) physical
    ("Check Tailscale ACLs in admin console", [], "jake"),                       # (b) sign-in / credentials
    ("Provide Lemon Squeezy keys to restart the task", [], "jake"),              # (b) secrets
    ("Buy the smart plug for the printer", [], "jake"),                          # (c) spend
    ("Anything at all", ["pin"], "jake"),                                        # (c) PIN label
    ("Pick warm or hot standby for the hub", [], "jake"),                        # (d) decision
    ("Rig-first offload plan", ["proposal"], "jake"),                            # (d) proposal
    ("Run admin script to label HB tasks", [], "jake"),                          # (e) UAC
    ("MUST DO: Take the PSY 620 week 5 quiz", ["owner:jake"], "jake"),           # Jake's own item
    ("Apply Gmail patch and restart Worker", [], "claude"),                      # rule 2
    ("Run adguardhome-sync on the rig", [], "claude"),
    ("Verify new kit files replaced old ones on drive", [], "claude"),
    ("The sandbox couldn't write to the hub source, copy the file over", [], "claude"),
    ("Fix the task path and rerun the test", [], "claude"),
])
def test_rules(title, labels, want):
    assert g.decide(1, None, labels, [], "", step(title))[0] == want


def test_bounce_once_goes_to_claude():
    cs = [ask("2026-10-03T10:00:00Z")]                    # it was on Jake's list once already
    assert g.decide(2, None, [], cs, "", step("Add receipt photos"), pending=True)[0] == "claude"


def test_double_marker_is_one_ask():
    cs = [ask("2026-10-03T10:00:00Z"), ask("2026-10-03T10:00:12Z")]   # the old double-step bug: one ask
    assert g.decide(3, None, [], cs, "", step("Add receipt photos"), pending=False)[0] == "jake"


def test_pin_still_reaches_jake_after_a_bounce():
    cs = [ask("2026-10-03T10:00:00Z")]
    assert g.decide(4, None, ["pin"], cs, "", step("Approve the payout"), pending=True)[0] == "jake"


def test_unanswered_decision_still_reaches_jake_but_answered_one_does_not():
    cs = [ask("2026-10-03T10:00:00Z")]
    assert g.decide(5, None, [], cs, "", step("Pick warm or hot standby"), pending=True)[0] == "jake"
    cs.append({"body": "Jake on his To-Do page: warm", "created_at": "2026-10-03T11:00:00Z"})
    assert g.decide(5, None, [], cs, "", step("Pick warm or hot standby"), pending=True)[0] == "claude"


def test_fail_closed_toward_claude(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(g, "category", boom)
    v, why = g.decide(6, None, [], [], "", step("Add receipt photos"))
    assert v == "claude" and "gate error" in why


def test_off_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_JAKE_GATE", "off")
    assert g.decide(7, None, [], [], "", step("Apply the patch"))[0] == "jake"


def test_ledger_line():
    g.decide(8, None, [], [], "", step("Apply the patch"))
    rows = [json.loads(x) for x in open(g.LEDGER, encoding="utf-8")]
    assert rows[-1]["card"] == 8 and rows[-1]["verdict"] == "claude"


def test_ghq_gate_parks_for_claude(monkeypatch):
    import ghq
    labels = {"v": ["status:working", "claimed:homebase", "todo-tab", "project:homebase"]}
    posted = []
    monkeypatch.setattr(ghq, "api", lambda m, p, b=None: (labels.__setitem__("v", b["labels"]) if m == "PUT" else None)
                        or {"number": 9, "labels": [{"name": x} for x in labels["v"]], "title": "t", "body": ""})
    monkeypatch.setattr(ghq, "paged", lambda p: [])
    monkeypatch.setattr(ghq, "comment", lambda n, t, **k: posted.append(t))
    monkeypatch.setattr(ghq, "_GATE_OK", {})
    assert ghq.gate_to_jake(9, "Apply worker.merged.ps1 and restart the Worker", source="test") is False
    assert "status:snoozed" in labels["v"] and "for:claude" in labels["v"] and "needs-claude-review" in labels["v"]
    assert "todo-tab" not in labels["v"] and not any(x.startswith("claimed:") for x in labels["v"])
    assert posted and "Parked for Claude, not Jake" in posted[-1]
