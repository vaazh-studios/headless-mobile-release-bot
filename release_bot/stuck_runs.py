"""Watchdog for release bot runs stuck in GitHub Actions.

A job waiting on an environment (or a runner) holds its app's concurrency group,
so every later run for that app queues behind it and gets cancelled when the next
one arrives: the app silently stops rolling. `timeout-minutes` doesn't cover
that wait. The Health workflow's first job (which never waits on an environment)
runs this and reports, once per run, anything stuck longer than `after_hours`.
It never cancels: a Submit run may be waiting for a legitimate approval.
"""

import os
from datetime import datetime, timedelta, timezone

import requests

DEFAULT_WORKFLOWS = ("android-submit.yml", "android-rollout.yml", "android-health.yml",
                     "android-halt.yml", "android-resume.yml", "android-doctor.yml")
# "pending" runs are waiting for the concurrency group: the symptom, not the cause.
STUCK_STATUSES = ("waiting", "queued", "in_progress", "requested")
THREAD = "stuck-runs"


class NoAccess(Exception):
    pass


def settings(raw: dict) -> tuple[int, tuple[str, ...]]:
    from release_bot import config as config_mod
    root = (raw.get("defaults") or {}) if config_mod.is_multi_app(raw) else raw
    s = root.get("stuck_runs") or {}
    return int(s.get("after_hours", 6)), tuple(s.get("workflows") or DEFAULT_WORKFLOWS)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def find(repo: str, token: str | None, after_hours: int, workflows: tuple[str, ...], now: datetime,
         exclude_run: str | None = None, session=requests) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    found: dict[int, dict] = {}
    for status in STUCK_STATUSES:
        resp = session.get(f"https://api.github.com/repos/{repo}/actions/runs",
                           params={"status": status, "per_page": 100}, headers=headers, timeout=30)
        if resp.status_code in (401, 403, 404):
            raise NoAccess(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        for run in resp.json().get("workflow_runs", []):
            file = run.get("path", "").split("@")[0].rsplit("/", 1)[-1]
            if file not in workflows or str(run["id"]) == str(exclude_run):
                continue
            age = now - _ts(run.get("run_started_at") or run["created_at"])
            if age >= timedelta(hours=after_hours):
                found[run["id"]] = {"id": run["id"], "name": run.get("name") or file, "status": run["status"],
                                    "url": run["html_url"], "hours": int(age.total_seconds() // 3600)}
    return sorted(found.values(), key=lambda r: -r["hours"])


def report(raw: dict, slack, repo: str, token: str | None, now: datetime | None = None,
           after_hours: int | None = None, session=requests) -> int:
    hours, workflows = settings(raw)
    hours = hours if after_hours is None else after_hours
    if hours <= 0:
        print("stuck_runs.after_hours is 0: watchdog off.")
        return 0
    try:
        stuck = find(repo, token, hours, workflows, now or datetime.now(timezone.utc),
                     os.environ.get("GITHUB_RUN_ID"), session)
    except NoAccess as e:
        print(f"::warning::Can't list workflow runs ({e}). Add `actions: read` to the caller workflow's permissions.")
        return 0
    if not stuck:
        print(f"No release bot runs stuck for more than {hours} h.")
        return 0
    for r in stuck:
        print(f"::warning::{r['name']} has been {r['status']} for {r['hours']} h: {r['url']}")
        if slack.thread_contains(THREAD, r["url"]):
            continue
        slack.post(THREAD, f"⚠️ *{r['name']}* has been *{r['status'].replace('_', ' ')}* for {r['hours']} h: {r['url']}\n"
                           "It holds the queue for its app, so later runs for that app wait and get cancelled. "
                           f"If it isn't waiting for an approval you expect, cancel it: `gh run cancel {r['id']} -R {repo}`",
                   root_text="🧯 Release bot runs stuck in GitHub Actions")
    return 0
