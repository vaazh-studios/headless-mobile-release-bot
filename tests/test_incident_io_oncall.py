"""incident.io: release hero from an on-call schedule, and declaring incidents on halt."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from release_bot import cli, config, doctor, notify
from release_bot.incident_io import IncidentIOAdmin

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self):
        return self.data


class Http:
    def __init__(self):
        self.posts = []
    def get(self, url, params=None, headers=None, timeout=None):
        if url.endswith("/v2/schedule_entries"):
            return Resp({"schedule_entries": {"final": [
                {"start_at": "2026-10-12T00:00:00Z", "end_at": "2026-10-19T00:00:00Z",
                 "user": {"name": "Alice", "email": "alice@vaazh.com", "slack_user_id": "UALICE"}},
                {"start_at": "2026-10-05T00:00:00Z", "end_at": "2026-10-12T00:00:00Z",
                 "user": {"name": "Bob", "email": "bob@vaazh.com", "slack_user_id": "UBOB"}}]}})
        if url.endswith("/v1/severities"):
            return Resp({"severities": [{"id": "SEV_MINOR", "name": "Minor"}, {"id": "SEV_MAJOR", "name": "Major"}]})
        raise AssertionError(url)
    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json))
        return Resp({"incident": {"id": "01INC", "reference": "INC-42", "permalink": "https://app.incident.io/vaazh/incidents/42"}})


def test_on_call_returns_only_the_current_shift():
    users = IncidentIOAdmin("key", session=Http()).on_call("01SCHED", now=NOW)
    assert [u["name"] for u in users] == ["Alice"]          # Bob's shift ended at the boundary


def test_declare_uses_idempotency_key_and_severity_by_name():
    http = Http()
    out = IncidentIOAdmin("key", session=http).declare("Shop Android 2.0.0 halted", "details", "release-bot-shop-android-2.0.0",
                                                        severity="Major", mode="test")
    url, body = http.posts[0]
    assert url == "https://api.incident.io/v2/incidents"
    assert body["idempotency_key"] == "release-bot-shop-android-2.0.0" and body["severity_id"] == "SEV_MAJOR"
    assert body["mode"] == "test" and body["visibility"] == "public"
    assert out["reference"] == "INC-42"


def hero_deps(on_call):
    raw = config.load(HERE / "release-bot.teams.test.yml")
    cfg = config.resolve(raw, "checkout-team")
    cfg["access"] = {"on_duty": {"incident_io_schedule_id": "01SCHED"},
                     "release_heroes": {"alice-gh": "alice@vaazh.com", "bob-gh": "UBOB"}}
    return cli.Deps(cfg=cfg, play=None, slack=None), on_call


@pytest.mark.parametrize("actor, ok", [("alice-gh", True), ("bob-gh", False), ("stranger", False)])
def test_authorize_uses_incident_io_schedule(actor, ok, monkeypatch, capsys):
    deps, _ = hero_deps(None)
    monkeypatch.setattr(cli, "_incident_io_on_call",
                        lambda s: [{"name": "Alice", "email": "Alice@Vaazh.com", "slack_user_id": "UALICE"}])
    class A:
        pass
    a = A(); a.actor = actor
    assert (cli.cmd_authorize(deps, a) == 0) == ok
    if actor == "bob-gh":
        assert "on call now: Alice" in capsys.readouterr().out


def test_mention_pings_the_on_call_person(monkeypatch):
    monkeypatch.setenv("INCIDENT_IO_API_KEY", "k")
    monkeypatch.setattr(cli, "_incident_io_on_call", lambda s: [{"name": "Alice", "email": "a@x", "slack_user_id": "UALICE"}])
    deps, _ = hero_deps(None)
    assert cli._mention(deps.cfg) == "<@UALICE> "


def test_declare_incident_only_on_automatic_halts(monkeypatch):
    calls = []
    monkeypatch.setattr("release_bot.incident_io.IncidentIOAdmin.declare",
                        lambda self, *a, **k: calls.append(k) or {"reference": "INC-7", "permalink": "https://x/7"})
    cfg = {"notify": {"incident_io": {"declare_incident": {"severity": "Minor", "mode": "test"}}}}
    env = {"INCIDENT_IO_API_KEY": "k"}
    lines = notify.on_halt(cfg, "Shop 2.0.0 halted", "dk", dry_run=False, automatic=True, env=env)
    assert calls[0]["severity"] == "Minor" and calls[0]["mode"] == "test"
    assert lines == ["🚨 Declared INC-7 in incident.io: https://x/7"]
    calls.clear()
    assert notify.on_halt(cfg, "manual", "dk2", dry_run=False, automatic=False, env=env) == [] and calls == []
    assert "dry run" in notify.on_halt(cfg, "x", "dk3", dry_run=True, automatic=True, env=env)[0]


def test_doctor_shows_who_is_on_call():
    deps, _ = hero_deps(None)
    class Admin:
        def on_call(self, s):
            return [{"name": "Alice", "email": "a@x", "slack_user_id": "UALICE"}]
    f = doctor.Factory(slack_call=lambda m, **p: {"ok": True, "user": "bot", "team": "t"} if m == "auth.test"
                       else {"ok": True, "channel": {"name": "c", "is_member": True}},
                       incident_io_admin=lambda k: Admin(),
                       env={"SLACK_BOT_TOKEN": "x", "INCIDENT_IO_API_KEY": "k"})
    checks = {c.name: c for c in doctor._slack_checks(deps.cfg, f)}
    assert checks["Release hero (incident.io)"].status == doctor.OK
    assert "on call now: Alice" in checks["Release hero (incident.io)"].detail


def test_declare_incident_with_default_settings(monkeypatch):
    calls = []
    monkeypatch.setattr("release_bot.incident_io.IncidentIOAdmin.declare",
                        lambda self, *a, **k: calls.append(k) or {"reference": "INC-9"})
    notify.on_halt({"notify": {"incident_io": {"declare_incident": {}}}}, "s", "k", dry_run=False, automatic=True,
                   env={"INCIDENT_IO_API_KEY": "key"})
    assert calls and calls[0]["mode"] == "standard"
