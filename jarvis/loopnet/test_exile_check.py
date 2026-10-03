"""Tests for exile_check.py's morning fix pass (Jake 10/03: "if the task is in my to do list in the AM and you can easily
make the fix, fix it and reque it"). GitHub and Claude are faked. Run: python -m pytest -q test_exile_check.py"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exile_check as ec  # noqa: E402


class G:
    RESET_MARK = "<!-- jarvis:reset -->"

    def __init__(self):
        self.issues, self.comments, self.approved = {}, [], []

    def add(self, n, labels):
        self.issues[n] = {"number": n, "title": f"Card {n}", "body": "Do it.", "state": "open", "labels": [{"name": x} for x in labels]}

    def repo_path(self, s=""):
        return s

    def label_names(self, i):
        return [l["name"] for l in i["labels"]]

    def paged(self, path):
        if "labels=exile" in path:
            return [i for i in self.issues.values() if "exile" in self.label_names(i)]
        return []

    def api(self, method, path, body=None):
        n = int(path.split("/issues/")[1].split("/")[0])
        if method == "PUT":
            self.issues[n]["labels"] = [{"name": x} for x in body["labels"]]
        if method == "PATCH":
            self.issues[n].update(body)
        return self.issues[n]

    def comment(self, n, text, dedupe=True, queue=True):
        self.comments.append((n, text))

    def approve(self, n, by=""):
        self.approved.append(n)


@pytest.fixture
def g(monkeypatch, tmp_path):
    fake = G()
    monkeypatch.setattr(ec, "ghq", fake)
    monkeypatch.setattr(ec, "LOG", str(tmp_path / "log.txt"))
    monkeypatch.setattr(ec.loopnet, "record", lambda *a, **k: None)
    return fake


def test_fix_requeues_and_clears_todo(g, monkeypatch):
    g.add(1, ["status:snoozed", "exile", "todo-tab", "p2"])
    g.add(2, ["status:snoozed", "exile", "todo-tab"])
    g.add(3, ["status:snoozed", "exile", "todo-tab", "pin"])
    monkeypatch.setattr(ec, "claude_json", lambda prompt: [
        {"card": 1, "decision": "fix", "reason": "needs a clear finish line", "done_when": "report.pdf exists in out\\"},
        {"card": 2, "decision": "stay", "reason": "Jake wants to look at it"},
        {"card": 3, "decision": "mistake", "reason": "nothing blocks it"}])
    out = ec.review()
    by = {r["card"]: r for r in out["reviewed"]}
    assert by[1]["applied"].startswith("fixed+requeued") and g.approved == [1]
    assert "exile" not in g.label_names(g.issues[1]) and "todo-tab" not in g.label_names(g.issues[1])
    assert "Done when: report.pdf" in g.issues[1]["body"]
    assert any(n == 1 and G.RESET_MARK in t for n, t in g.comments)       # the reset clears the To-Do step
    assert by[2]["applied"] == "left" and "exile" in g.label_names(g.issues[2])
    assert by[3]["applied"].startswith("left: PIN card") and 3 not in g.approved


def test_claude_unavailable_everything_stays(g, monkeypatch):
    g.add(4, ["status:snoozed", "exile"])
    def boom(prompt):
        raise RuntimeError("out of usage")
    monkeypatch.setattr(ec, "claude_json", boom)
    out = ec.review()
    assert out["reviewed"][0]["decision"] == "stay" and "unavailable" in out["error"]


def test_dry_run_changes_nothing(g, monkeypatch):
    g.add(5, ["status:snoozed", "exile", "todo-tab"])
    monkeypatch.setattr(ec, "claude_json", lambda p: [{"card": 5, "decision": "mistake", "reason": "x"}])
    ec.review(dry=True)
    assert g.approved == [] and "exile" in g.label_names(g.issues[5])
