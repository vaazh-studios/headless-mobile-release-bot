"""Watchdog for runs stuck in GitHub Actions (a stuck job blocks its app's queue)."""

from datetime import datetime, timezone
from pathlib import Path

import yaml

from release_bot import cli, schema, stuck_runs
from test_play_review import MemorySlack

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
    def json(self):
        return self.data
    def raise_for_status(self):
        assert self.status_code == 200


class GitHub:
    def __init__(self, runs, status=200):
        self.runs, self.status, self.calls = runs, status, []
    def get(self, url, params, headers, timeout):
        self.calls.append(params["status"])
        return Resp({"workflow_runs": [r for r in self.runs if r["status"] == params["status"]]}, self.status)


def run(id_, status, started, path=".github/workflows/android-health.yml"):
    return {"id": id_, "status": status, "name": "Android · Health check", "path": path,
            "created_at": started, "run_started_at": started, "html_url": f"https://github.com/o/r/actions/runs/{id_}"}


RUNS = [
    run(1, "waiting", "2026-10-06T09:16:56Z"),                                   # the real case: 8 days
    run(2, "in_progress", "2026-10-14T09:00:00Z"),                               # 1 h: fine
    run(3, "queued", "2026-10-14T01:00:00Z", ".github/workflows/ci.yml"),        # not a release bot workflow
    run(4, "waiting", "2026-10-13T20:00:00Z", ".github/workflows/android-submit.yml@refs/heads/main"),
]
RAW = {"package_name": "com.x", "slack": {"channel_id": "C1"}}


def test_finds_only_old_release_bot_runs():
    found = stuck_runs.find("o/r", "t", 6, stuck_runs.DEFAULT_WORKFLOWS, NOW, session=GitHub(RUNS))
    assert [(r["id"], r["hours"]) for r in found] == [(1, 192), (4, 14)]
    assert "pending" not in GitHub(RUNS).calls


def test_reports_each_stuck_run_once_with_cancel_command(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    slack = MemorySlack()
    for _ in range(2):
        assert stuck_runs.report(RAW, slack, "o/r", "t", NOW, session=GitHub(RUNS)) == 0
    assert len(slack.posts) == 2
    assert "gh run cancel 1 -R o/r" in slack.posts[0] and "192 h" in slack.posts[0]


def test_settings_threshold_off_and_no_access(capsys):
    slack = MemorySlack()
    raw = {**RAW, "stuck_runs": {"after_hours": 0}}
    assert stuck_runs.report(raw, slack, "o/r", "t", NOW, session=GitHub(RUNS)) == 0 and not slack.posts
    raw = {**RAW, "stuck_runs": {"after_hours": 200}}
    stuck_runs.report(raw, slack, "o/r", "t", NOW, session=GitHub(RUNS))
    assert not slack.posts
    assert stuck_runs.report(RAW, slack, "o/r", None, NOW, session=GitHub(RUNS, status=403)) == 0
    assert "actions: read" in capsys.readouterr().out


def test_cli_command_uses_alerts_channel(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    slack = MemorySlack()
    assert cli.cmd_stuck_runs(RAW, slack=slack, session=GitHub(RUNS)) == 0
    assert slack.posts                                     # clock is real: both runs are long past 6 h


def test_schema_accepts_stuck_runs():
    multi = yaml.safe_load((ROOT / "examples" / "multi-account.release-bot.yml").read_text())
    multi.setdefault("defaults", {})["stuck_runs"] = {"after_hours": 4, "workflows": ["release-health.yml"]}
    assert not schema.errors(multi)
    multi["defaults"]["stuck_runs"]["after_hours"] = -1
    assert schema.errors(multi)


def test_health_workflow_runs_watchdog_and_callers_allow_it():
    rw = yaml.safe_load((ROOT / ".github/workflows/rw-health.yml").read_text())
    steps = rw["jobs"]["plan"]["steps"]
    assert any("stuck-runs" in s.get("run", "") and s.get("continue-on-error") for s in steps)
    assert "environment" not in rw["jobs"]["plan"]
    caller = yaml.safe_load((ROOT / "examples/caller-workflows/android-health.yml").read_text())
    assert caller["permissions"]["actions"] == "read"
