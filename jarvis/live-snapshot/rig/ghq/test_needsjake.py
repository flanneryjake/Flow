"""Tests for needsjake.py (the "Jake needs to" catcher). GitHub, Tars and Claude are faked.
Run: python -m pytest -q test_needsjake.py"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import needsjake as nj  # noqa: E402


class FakeGhq:
    def __init__(self, labels):
        self.issue = {"number": 5, "title": "Sync Drive", "state": "open", "labels": [{"name": x} for x in labels]}
        self.steps, self.comments, self.claude_calls = [], [], 0

    def repo_path(self, s=""):
        return "/repos/x/y" + s

    def label_names(self, i):
        return [l["name"] for l in i["labels"]]

    def status_of(self, i):
        return next((x[7:] for x in self.label_names(i) if x.startswith("status:")), "inbox")

    def api(self, method, path, body=None):
        if method == "PUT":
            self.issue["labels"] = [{"name": x} for x in body["labels"]]
        return self.issue

    def jake_step_of(self, n):
        return self.steps[-1] if self.steps else None

    def jake_step(self, n, step):
        self.steps.append(step)
        return step

    def make_jake_step(self, n, reason, models=None):
        out = models[0]("prompt")   # first model that answers writes it
        return {"place": "rig", "title": "Sign in to Drive", "after": "approve", "by": out}

    def ask_ollama(self, prompt, model=None):
        return "tars"

    def ask_claude(self, prompt, timeout=300):
        return "claude"


@pytest.fixture(autouse=True)
def tmp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(nj, "LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(nj, "PENDING", str(tmp_path / "pending.json"))
    monkeypatch.setattr(nj.loopnet, "TRAINING_INTAKE", str(tmp_path / "no-intake.py"))   # no training writes in tests
    monkeypatch.setenv("JARVIS_NEEDSJAKE", "on")


def local_yes(n, t, x):
    return {"who": "Tars (5060)", "needs_jake": True, "sentence": "Jake needs to sign in to Google Drive.", "secs": 1.0, "error": None}


def local_down(n, t, x):
    return {"who": "Tars (5060)", "needs_jake": None, "sentence": "", "secs": 0.1, "error": "URLError: refused"}


def claude(answer):
    def f(n, t, x):
        return {"needs_jake": answer, "reason": "says Jake must sign in" if answer else "nothing for Jake", "secs": 2.0}
    return f


def claude_out(n, t, x):
    raise nj.ClaudeUnavailable("out of usage")


@pytest.mark.parametrize("text", ["Jake needs to sign in to Drive.", "This needs Jake before it can run.", "Only Jake can approve the PIN.",
                                  "NEEDS_JAKE: buy filament", "Waiting on Jake to plug in the printer.", "It requires Jake's login.",
                                  "Jake has to decide the price.", "Blocked until Jake must confirm."])
def test_phrases_found(text):
    assert nj.candidates(text)


@pytest.mark.parametrize("text", ["Jake approved it yesterday.", "I told Jake it finished.", "Done. Jake will like this.",
                                  "Sent the summary to Jake's phone."])
def test_negatives_not_flagged(text):
    assert nj.candidates(text) == []


def test_verified_yes_parks_and_marks_once():
    g = FakeGhq(["status:approved", "claimed:rig", "p1"])
    c = nj.process(g, 5, "All set except one thing. Jake needs to sign in to Google Drive on the rig.", force_local=local_yes, claude=claude(True))
    names = g.label_names(g.issue)
    assert c["result"] == "verified: on To-Do" and c["agreement"] is True and c["step_by"] == "Tars (5060)"
    assert "status:needs-jake" in names and not any(x.startswith("claimed:") for x in names) and "p1" in names
    assert len(g.steps) == 1 and g.steps[0]["by"] == "tars"
    again = nj.process(g, 5, "Jake needs to sign in to Google Drive.", force_local=local_yes, claude=claude(True))
    assert again["result"] == "already on To-Do" and len(g.steps) == 1     # no double-mark


def test_false_positive_leaves_card_alone():
    g = FakeGhq(["status:approved", "p1"])
    c = nj.process(g, 5, "Waiting on Jake? No - the export runs by itself tonight.", force_local=local_yes, claude=claude(False))
    assert c["result"] == "false positive" and c["agreement"] is False
    assert g.label_names(g.issue) == ["status:approved", "p1"] and g.steps == []


def test_claude_out_of_usage_holds_never_sends():
    g = FakeGhq(["status:approved", "claimed:homebase"])
    c = nj.process(g, 5, "Jake needs to sign in to Google Drive.", force_local=local_yes, claude=claude_out)
    names = g.label_names(g.issue)
    assert c["result"].startswith("held") and "status:snoozed" in names and nj.HOLD_LABEL in names
    assert "status:needs-jake" not in names and not any(x.startswith("claimed:") for x in names) and g.steps == []
    assert "5" in nj.pending()
    # Claude is back: the retry verifies it and puts it on To-Do
    out = nj.retry_pending(g) if False else None   # retry uses the real models; run process again with fakes instead
    c2 = nj.process(g, 5, nj.pending()["5"]["text"], force_local=local_yes, claude=claude(True))
    assert c2["result"] == "verified: on To-Do" and nj.HOLD_LABEL not in g.label_names(g.issue) and "5" not in nj.pending()


def test_tars_down_claude_writes_backup():
    g = FakeGhq(["status:approved"])
    c = nj.process(g, 5, "Only Jake can approve the PIN for this purchase.", force_local=local_down, claude=claude(True))
    assert c["result"] == "verified: on To-Do" and c["step_by"] == "Claude (backup)" and g.steps[0]["by"] == "claude"
    assert c["agreement"] is None


def test_running_card_is_never_interrupted():
    g = FakeGhq(["status:working", "claimed:rig"])
    c = nj.process(g, 5, "Jake needs to sign in.", force_local=local_yes, claude=claude(True))
    assert c["result"].startswith("pending") and g.label_names(g.issue) == ["status:working", "claimed:rig"] and "5" in nj.pending()


def test_snoozed_and_exiled_keep_status_get_todo_tab():
    g = FakeGhq(["status:snoozed", "exile"])
    nj.process(g, 5, "Jake needs to pick a printer profile.", force_local=local_yes, claude=claude(True))
    names = g.label_names(g.issue)
    assert "status:snoozed" in names and "todo-tab" in names and "status:needs-jake" not in names


def test_score():
    g = FakeGhq(["status:approved"])
    nj.process(g, 5, "Jake needs to sign in.", force_local=local_yes, claude=claude(True))
    g2 = FakeGhq(["status:approved"])
    nj.process(g2, 5, "Waiting on Jake? no.", force_local=local_yes, claude=claude(False))
    s = nj.score()
    assert s["verified_yes"] == 1 and s["false_positives"] == 1 and s["by_model"]["Tars (5060)"]["agreement"] == 0.5
