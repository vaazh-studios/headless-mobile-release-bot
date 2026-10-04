"""Where messages go besides Slack, and what happens when a release halts.

    notify:
      teams: true                   # mirror release messages to Microsoft Teams (TEAMS_WEBHOOK_URL secret)
      pagerduty:                    # page on automatic halts (PAGERDUTY_ROUTING_KEY secret)
        severity: critical
    on_halt:
      optimizely:                   # kill switch: turn flags off when a release halts (OPTIMIZELY_TOKEN secret)
        project_id: 123456
        environment: production
        flags: [new_checkout, redesigned_feed]
"""

import os
import re

import requests


def slack_to_markdown(text: str) -> str:
    """Slack mrkdwn → standard markdown (Teams): *bold* → **bold**, <url|label> → [label](url), <!subteam^X> removed."""
    text = re.sub(r"<!subteam\^[A-Z0-9]+>\s?", "", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"[\2](\1)", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"**\1**", text)
    return text


class Teams:
    """Posts to a Teams channel through a Workflows ("When a Teams webhook request is received") URL."""

    def __init__(self, url: str, dry_run: bool = False, session=None):
        self.url = url
        self.dry_run = dry_run
        self.http = session or requests.Session()

    def send(self, title: str, text: str) -> None:
        body = {"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4",
                "body": [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
                         {"type": "TextBlock", "text": slack_to_markdown(text), "wrap": True}],
            }}]}
        if self.dry_run:
            print(f"[teams not posted] {title}: {text.splitlines()[0]}")
            return
        resp = self.http.post(self.url, json=body, timeout=30)
        resp.raise_for_status()


class Fanout:
    """Slack-compatible notifier that mirrors every *posted* release message to Teams.
    Repeat detection stays with Slack (Teams has no thread history to search)."""

    def __init__(self, slack, teams: Teams):
        self.slack = slack
        self.teams = teams

    def post(self, key: str, text: str, root_text: str | None = None) -> None:
        self.slack.post(key, text, root_text=root_text)
        if getattr(self.slack, "_last_skipped", False):
            return
        self.teams.send(f"Release · {key}", (root_text + "\n\n" if root_text else "") + text)

    def announce(self, channel, text: str) -> None:
        self.slack.announce(channel, text)

    def thread_contains(self, key: str, text: str) -> bool:
        return self.slack.thread_contains(key, text)

    def usergroup_members(self, usergroup_id: str) -> set[str]:
        return self.slack.usergroup_members(usergroup_id)

    @property
    def _last_skipped(self):
        return getattr(self.slack, "_last_skipped", False)


class Optimizely:
    """Feature Experimentation REST API: turn flags off (or on) in one environment."""

    API = "https://api.optimizely.com/flags/v1"

    def __init__(self, token: str, project_id, environment: str, dry_run: bool = False, session=None):
        self.token = token
        self.project_id = project_id
        self.environment = environment
        self.dry_run = dry_run
        self.http = session or requests.Session()

    def set_flags(self, flags: list[str], enabled: bool) -> list[str]:
        """Returns the flags it changed."""
        state = "enabled" if enabled else "disabled"
        if self.dry_run:
            print(f"[dry-run] would set flags {flags} {state} in {self.environment}")
            return list(flags)
        changed = []
        for flag in flags:
            url = (f"{self.API}/projects/{self.project_id}/flags/{flag}/environments/"
                   f"{self.environment}/ruleset/{state}")
            resp = self.http.post(url, timeout=30, headers={"Authorization": f"Bearer {self.token}"})
            resp.raise_for_status()
            changed.append(flag)
        return changed


def on_halt(cfg: dict, summary: str, dedup_key: str, dry_run: bool, automatic: bool, env=None) -> list[str]:
    """Run the configured halt side effects. Returns human-readable lines for Slack."""
    env = env if env is not None else os.environ
    lines = []
    pd = (cfg.get("notify") or {}).get("pagerduty")
    if pd and automatic:
        key = env.get("PAGERDUTY_ROUTING_KEY")
        if not key:
            lines.append("⚠️ PagerDuty paging is configured but PAGERDUTY_ROUTING_KEY is not set")
        elif dry_run:
            lines.append("📟 (dry run) would page on-call via PagerDuty")
        else:
            from release_bot.pagerduty import page
            try:
                page(key, summary, source="headless-mobile-release-bot",
                     severity=(pd or {}).get("severity", "critical"), dedup_key=dedup_key)
                lines.append("📟 Paged on-call via PagerDuty")
            except Exception as e:  # noqa: BLE001 — a paging failure mustn't undo the halt
                lines.append(f"⚠️ PagerDuty page failed: {e}")
    inc = (cfg.get("notify") or {}).get("incident_io")
    if inc and automatic:
        token = env.get("INCIDENT_IO_ALERT_TOKEN")
        if not token or not inc.get("alert_source_config_id"):
            lines.append("⚠️ incident.io alerting is configured but INCIDENT_IO_ALERT_TOKEN or "
                         "alert_source_config_id is missing")
        elif dry_run:
            lines.append("📟 (dry run) would raise an incident.io alert")
        else:
            from release_bot.incident_io import alert
            try:
                alert(inc["alert_source_config_id"], token, summary, summary, dedup_key)
                lines.append("📟 Raised an incident.io alert")
            except Exception as e:  # noqa: BLE001
                lines.append(f"⚠️ incident.io alert failed: {e}")
    opt = (cfg.get("on_halt") or {}).get("optimizely")
    if opt and opt.get("flags"):
        token = env.get("OPTIMIZELY_TOKEN")
        if not token and not dry_run:
            lines.append("⚠️ Optimizely kill switch is configured but OPTIMIZELY_TOKEN is not set")
        else:
            try:
                changed = Optimizely(token or "", opt["project_id"], opt.get("environment", "production"),
                                     dry_run=dry_run).set_flags(list(opt["flags"]), enabled=False)
                lines.append(f"🚩 Turned off feature flags in {opt.get('environment', 'production')}: "
                             + ", ".join(f"`{f}`" for f in changed))
            except Exception as e:  # noqa: BLE001
                lines.append(f"⚠️ Optimizely kill switch failed: {e}")
    return lines
