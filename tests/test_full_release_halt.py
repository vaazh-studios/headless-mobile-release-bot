"""Halting a release that's already at 100% (Play supports it; the previous completed release serves again)."""

import dataclasses
import json

import pytest

from release_bot import cli, policy
from release_bot.play import Play, TrackState
from test_mock_mode import sandbox  # noqa: F401 — fixture


def releases(run):
    return {r["name"]: r for r in json.loads(run.state.read_text())["releases"]}


def roll_to_full(run, version="4.12.0"):
    run("submit", "--aab", "x.aab", "--version", version)
    run.advance_time(minutes=10)
    for _ in range(3):
        run("advance")
    assert releases(run)[version]["status"] == "completed"


def test_health_check_halts_full_release_within_watch(sandbox):
    roll_to_full(sandbox)
    assert releases(sandbox)["0.1.0"]["status"] == "completed"      # kept: Play's fallback
    sandbox("mock-inject", "--incident", "new-crash")
    assert sandbox("check") == 0
    rel = releases(sandbox)
    assert rel["4.12.0"]["status"] == "halted" and "userFraction" not in rel["4.12.0"]
    assert rel["0.1.0"]["status"] == "completed"
    assert TrackState(list(rel.values())).fully_halted

    sandbox("check")                                   # halted: nothing more to do
    sandbox("advance")
    assert releases(sandbox)["4.12.0"]["status"] == "halted"

    sandbox("mock-inject", "--incident", "none")
    assert sandbox("resume", "--reason", "false alarm") == 0
    assert releases(sandbox)["4.12.0"]["status"] == "completed"       # back to everyone


def test_no_watch_after_window(sandbox):
    roll_to_full(sandbox)
    sandbox.advance_time(days=8)
    sandbox("mock-inject", "--incident", "new-crash")
    sandbox("check")
    assert releases(sandbox)["4.12.0"]["status"] == "completed"


def test_notify_only_does_not_halt(sandbox, monkeypatch, capsys):
    orig = cli.rollout_policy
    monkeypatch.setattr(cli, "rollout_policy", lambda cfg: dataclasses.replace(orig(cfg), after_full_action="notify"))
    roll_to_full(sandbox)
    sandbox("mock-inject", "--incident", "new-crash")
    sandbox("check")
    assert releases(sandbox)["4.12.0"]["status"] == "completed"
    assert "is at 100% and health needs a look" in capsys.readouterr().out


def test_manual_halt_of_full_release(sandbox):
    roll_to_full(sandbox)
    assert sandbox("halt", "--reason", "bad login flow") == 0
    assert releases(sandbox)["4.12.0"]["status"] == "halted"


def test_policy_parses_after_full_release():
    base = {"rollout": {"schedule": [{"on": "monday", "percent": 1}, {"on": "tuesday", "percent": 100}]}}
    p = policy.from_config(base)
    assert (p.after_full_action, p.after_full_watch_days) == ("halt", 7)
    p = policy.from_config({"rollout": {**base["rollout"], "after_full_release": {"action": "off", "watch_days": 3}}})
    assert (p.after_full_action, p.after_full_watch_days) == ("off", 3)
    with pytest.raises(policy.PolicyError):
        policy.from_config({"rollout": {**base["rollout"], "after_full_release": {"action": "pause"}}})


class Edits:
    def __init__(self, releases):
        self.releases, self.body = releases, None
    def insert(self, **kw): return self
    def tracks(self): return self
    def get(self, **kw):
        return Exec({"releases": self.releases})
    def update(self, **kw):
        self.body = kw["body"]
        return self
    def commit(self, **kw): return self
    def delete(self, **kw): return self
    def execute(self): return {"id": "e1"}


class Exec:
    def __init__(self, v): self.v = v
    def execute(self): return self.v


class Svc:
    def __init__(self, releases): self.e = Edits(releases)
    def edits(self): return self.e


def test_play_halts_and_resumes_completed_release():
    svc = Svc([{"name": "2.0.0", "versionCodes": ["200"], "status": "completed"}])
    Play("com.x", "production", service=svc).halt(include_completed=True)
    assert svc.e.body["releases"] == [{"name": "2.0.0", "versionCodes": ["200"], "status": "halted"}]

    svc = Svc([{"name": "2.0.0", "versionCodes": ["200"], "status": "halted"},
               {"name": "1.9.0", "versionCodes": ["190"], "status": "completed"}])
    Play("com.x", "production", service=svc).resume()
    assert svc.e.body["releases"][0]["status"] == "completed"


def test_play_never_halts_completed_without_opt_in_or_on_internal():
    done = [{"name": "2.0.0", "versionCodes": ["200"], "status": "completed"}]
    with pytest.raises(RuntimeError):
        Play("com.x", "production", service=Svc(done)).halt()
    with pytest.raises(RuntimeError):
        Play("com.x", "internal", service=Svc(done)).halt(include_completed=True)
