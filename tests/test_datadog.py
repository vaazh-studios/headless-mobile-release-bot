"""Datadog monitors as an optional health source; Play Vitals is the template default."""

import json
from pathlib import Path

import pytest

from release_bot import cli, config, doctor, rules
from release_bot.datadog import Datadog
from release_bot.gate import Level, Verdict

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def dd_norm(extra_rules):
    return rules.normalize({"sources": {"datadog": {"query": 'tag:"team:mobile"'}}, "rules": extra_rules})


def test_datadog_rules_status_priority_and_name():
    n = dd_norm([
        {"name": "P1/P2 pager", "source": "datadog", "status": "alert", "priority": [1, 2], "action": "halt"},
        {"name": "Any warn", "source": "datadog", "status": "warn", "action": "notify"},
        {"name": "Start time", "source": "datadog", "status": ["alert", "warn"], "monitor": "app start", "action": "hold"},
    ])
    def level(mons):
        return Verdict(rules.evaluate(n, {"datadog": {"monitors": mons}})).level
    assert level([{"name": "Checkout errors", "status": "alert", "priority": 1}]) == Level.HALT
    assert level([{"name": "Checkout errors", "status": "alert", "priority": 4}]) == Level.OK      # P4 not in [1, 2]
    assert level([{"name": "Feed latency", "status": "warn", "priority": 3}]) == Level.NOTIFY
    assert level([{"name": "App start p90 > 3s", "status": "alert", "priority": 4}]) == Level.HOLD
    assert level([]) == Level.OK


def test_datadog_rule_validation():
    with pytest.raises(rules.RuleError, match="alert and/or warn"):
        rules.parse_rule({"source": "datadog", "status": "nodata"}, 1)


class Resp:
    def __init__(self, data):
        self.data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self.data


class Pages:
    def __init__(self):
        self.calls = []
    def get(self, url, headers, timeout, params):
        self.calls.append((url, headers, params))
        page = params["page"]
        mons = [{"name": f"m{page}a", "status": "Alert", "priority": 1},
                {"name": f"m{page}b", "status": "OK"}]
        return Resp({"monitors": mons, "metadata": {"page": page, "page_count": 2}})


def test_datadog_client_pages_filters_and_uses_site():
    http = Pages()
    dd = Datadog("api", "app", 'tag:"team:mobile"', "datadoghq.eu", session=http)
    firing = dd.firing_monitors()
    assert [m["name"] for m in firing] == ["m0a", "m1a"] and firing[0]["status"] == "alert"
    url, headers, params = http.calls[0]
    assert url == "https://api.datadoghq.eu/api/v1/monitor/search"
    assert headers == {"DD-API-KEY": "api", "DD-APPLICATION-KEY": "app"}
    assert params["query"] == 'tag:"team:mobile" status:(alert OR warn)'


def test_template_defaults_to_play_vitals_only():
    cfg = config.resolve(config.load(ROOT / "release-bot.yml"))
    n = rules.normalize(cfg["health"])
    assert set(n["sources"]) == {"play_vitals"}
    assert {r.source for r in n["rules"]} == {"play_vitals"}


def test_mock_demo_still_exercises_every_source(tmp_path, monkeypatch):
    monkeypatch.setenv("RELEASE_BOT_MOCK", "true")
    monkeypatch.setenv("MOCK_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("MOCK_REVIEW_MINUTES", "0")
    monkeypatch.setenv("MOCK_ON_DUTY", "hero")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("RELEASE_BOT_MODE", raising=False)
    run = lambda *a: cli.main(["--config", str(ROOT / "release-bot.yml"), *a])
    run("submit", "--aab", "x.aab", "--version", "1.1.0")
    run("mock-inject", "--incident", "datadog-alert")   # template has no Datadog rules: demo defaults apply
    run("check")
    state = json.loads((tmp_path / "s.json").read_text())
    live = next(r for r in state["releases"] if r["status"] in ("inProgress", "halted"))
    assert live["status"] == "halted"


class DD:
    def firing_monitors(self):
        return [{"name": "x", "status": "alert"}]


def test_doctor_datadog_and_missing_signals_warning():
    raw = config.load(HERE / "release-bot.teams.test.yml")
    cfg = config.resolve(raw, "checkout-team")
    cfg["health"]["sources"]["datadog"] = {"query": "tag:team:mobile"}
    cfg["health"]["rules"].append({"name": "dd", "source": "datadog", "status": "alert", "action": "halt"})
    f = doctor.Factory(google_identity=lambda: "sa@x", play=lambda c: None, vitals=lambda c: None,
                       bigquery_table=lambda *a: None, grafana=lambda *a: None, datadog=lambda *a: DD(),
                       slack_call=lambda *a, **k: {"ok": False}, env={"DD_API_KEY": "a", "DD_APP_KEY": "b"})
    checks = {c.name: c for c in doctor.run(cfg, f)}
    assert checks["Datadog"].status == doctor.OK and "1 matching monitor" in checks["Datadog"].detail
    f.env = {}
    assert {c.name: c for c in doctor.run(cfg, f)}["Datadog"].status == doctor.FAIL

    ios_only_vitals = config.resolve(raw, "checkout-team", "ios")
    ios_only_vitals["health"] = {"sources": {"play_vitals": {}}, "rules": rules.DEFAULT_RULES["play_vitals"]}
    warn = {c.name: c for c in doctor.run(ios_only_vitals, f)}["health signals"]
    assert warn.status == doctor.WARN and "iOS" in warn.detail
