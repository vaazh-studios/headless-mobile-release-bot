"""`release_bot doctor`: check every permission and connection one app needs,
and say exactly how to fix what's wrong. Read-only: it never changes anything.

Each check returns a Check(status, name, detail, fix). Clients are created
through a small factory so tests can hand in fakes.
"""

import os
from dataclasses import dataclass, field

from release_bot import config as config_mod
from release_bot import policy, rules

OK, WARN, FAIL = "ok", "warn", "fail"
ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌"}


@dataclass
class Check:
    status: str
    name: str
    detail: str
    fix: str = ""


@dataclass
class Factory:
    """How doctor builds clients. Tests replace these callables."""
    google_identity: object = None   # () -> service account email (or raises)
    play: object = None              # (cfg) -> object with track_state()
    vitals: object = None            # (cfg) -> object with latest_by_version()
    bigquery_table: object = None    # (project, dataset, table) -> raises if missing
    grafana: object = None           # (url, token, matchers) -> object with active_alerts()
    slack_call: object = None        # (method, **params) -> dict (Slack Web API response)
    env: dict = field(default_factory=lambda: dict(os.environ))


def http_status(e: Exception) -> int | None:
    for attr in ("status_code", "code"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(e, "resp", None) or getattr(e, "response", None)
    v = getattr(resp, "status", None) or getattr(resp, "status_code", None)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _try(name: str, fn, on_error) -> Check:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — doctor reports every failure
        status = http_status(e)
        detail, fix = on_error(status, e)
        return Check(FAIL, name, detail, fix)


def run(cfg: dict, factory: Factory) -> list[Check]:
    env = factory.env
    mock = env.get("RELEASE_BOT_MOCK", "").lower() == "true"
    checks: list[Check] = []

    # 1. Config
    platforms = cfg.get("platforms", ["android"])
    try:
        norm = rules.normalize(cfg.get("health"))
        pol = policy.from_config(cfg)
        errors, warnings = policy.check(pol, platforms)
        if errors:
            checks.append(Check(FAIL, "release-bot.yml", "; ".join(errors), "Run `python -m release_bot plan` and fix the schedule"))
        else:
            checks.append(Check(OK, "release-bot.yml", f"schedule {pol.android.summary()} · {len(norm['rules'])} health rules"))
        for w in warnings:
            checks.append(Check(WARN, "release-bot.yml", w))
    except (policy.PolicyError, rules.RuleError, config_mod.ConfigError) as e:
        checks.append(Check(FAIL, "release-bot.yml", str(e), "Fix the setting named above; `python -m release_bot validate` re-checks"))
        norm = {"sources": {}, "rules": []}

    mode = env.get("RELEASE_BOT_MODE") or cfg.get("mode", "live")
    checks.append(Check(OK if mode in ("live", "shadow") else FAIL, "mode",
                        f"{mode}" + (": decisions are posted, nothing is changed on the store" if mode == "shadow" else ""),
                        "" if mode in ("live", "shadow") else "mode must be live or shadow"))

    if mock:
        checks.append(Check(OK, "store + health sources", "mock mode: Play, Play Vitals, Crashlytics and Grafana are simulated"))
    else:
        checks += _google_checks(cfg, norm, factory)
        if "grafana" in norm["sources"]:
            checks.append(_grafana_check(norm, factory))

    checks += _slack_checks(cfg, factory)
    return checks


def _google_checks(cfg: dict, norm: dict, f: Factory) -> list[Check]:
    out = []
    env_name = cfg.get("environment", "play-production")

    def identity():
        email = f.google_identity()
        return Check(OK, "Google login", f"keyless login as {email}")
    ident = _try("Google login", identity, lambda s, e: (
        f"no Google credentials ({e})",
        f"Run this in the `{env_name}` environment with GCP_WORKLOAD_IDENTITY_PROVIDER and GCP_SERVICE_ACCOUNT set, "
        "from the main branch. Check the provider's attribute condition and the service account's "
        f"workloadIdentityUser binding for attribute.environment/{env_name} (docs/setup.md step 1)."))
    out.append(ident)
    if ident.status == FAIL:
        return out
    sa = ident.detail.removeprefix("keyless login as ")
    pkg = cfg["package_name"]

    def play():
        state = f.play(cfg).track_state()
        live = state.live
        done = state.completed
        detail = (f"production track readable · live: {live['name']} {live['status']}" if live
                  else f"production track readable · current: {done['name'] if done else 'nothing released yet'}")
        return Check(OK, "Play publishing", detail)
    out.append(_try("Play publishing", play, lambda s, e: (
        f"{pkg}: HTTP {s or '?'} ({e})",
        {403: f"Play Console → Users and permissions → invite {sa} → App permissions → {pkg} → "
              "'Release to production, exclude devices, and use Play App Signing'. Enable the "
              "Google Play Android Developer API in the service account's Google Cloud project.",
         404: f"Play can't find {pkg} for {sa}. Check package_name, and that the service account was "
              "invited in the Play developer account that owns this app (multi-account: right environment?)."
         }.get(s, "Check the Play Android Developer API is enabled and the service account is invited."))))

    if "play_vitals" in norm["sources"]:
        def vitals():
            data = f.vitals(cfg).latest_by_version()
            return Check(OK, "Play Vitals", f"readable · {len(data)} version(s) with data")
        out.append(_try("Play Vitals", vitals, lambda s, e: (
            f"HTTP {s or '?'} ({e})",
            f"Grant {sa} 'View app information and download bulk reports (read-only)' for {pkg}, and enable "
            "the Google Play Developer Reporting API in its Google Cloud project." if s == 403 else
            "Enable the Google Play Developer Reporting API and check the service account's app access.")))

    if "crashlytics" in norm["sources"]:
        c = norm["sources"]["crashlytics"]
        table = f"{c.get('project')}.{c.get('dataset', 'firebase_crashlytics')}.{c.get('table')}"
        def crashlytics():
            f.bigquery_table(c.get("project"), c.get("dataset", "firebase_crashlytics"), c.get("table"))
            return Check(OK, "Crashlytics (BigQuery)", f"table {table} readable")
        expected = cfg["package_name"].replace(".", "_") + "_ANDROID_REALTIME"
        out.append(_try("Crashlytics (BigQuery)", crashlytics, lambda s, e: (
            f"{table}: HTTP {s or '?'} ({e})",
            {404: f"Firebase → Project settings → Integrations → BigQuery → enable Crashlytics with "
                  f"streaming. The table is usually `{expected}`; set health.sources.crashlytics.table.",
             403: f"Give {sa} roles/bigquery.dataViewer on the dataset and roles/bigquery.jobUser on "
                  f"project {c.get('project')}."}.get(s, "Check the project, dataset and table names."))))
    return out


def _grafana_check(norm: dict, f: Factory) -> Check:
    url, token = f.env.get("GRAFANA_URL"), f.env.get("GRAFANA_TOKEN")
    if not url or not token:
        return Check(FAIL, "Grafana", "GRAFANA_URL or GRAFANA_TOKEN is not set",
                     "Set the GRAFANA_URL variable and GRAFANA_TOKEN secret in the account's environment, "
                     "or remove health.sources.grafana.")
    matchers = norm["sources"]["grafana"].get("matchers", [])
    def grafana():
        alerts = f.grafana(url, token, matchers).active_alerts()
        return Check(OK, "Grafana", f"{url} readable · {len(alerts)} matching alert(s) firing now")
    return _try("Grafana", grafana, lambda s, e: (
        f"{url}: HTTP {s or '?'} ({e})",
        "Create a Grafana service account with the Viewer role and store its token as GRAFANA_TOKEN."
        if s in (401, 403) else "Check GRAFANA_URL is reachable from GitHub-hosted runners."))


def _slack_checks(cfg: dict, f: Factory) -> list[Check]:
    if not f.env.get("SLACK_BOT_TOKEN"):
        return [Check(WARN, "Slack", "SLACK_BOT_TOKEN is not set: messages go to the run logs only",
                      "Create an app from slack-app-manifest.yml and store its bot token as the SLACK_BOT_TOKEN secret.")]
    out = []
    try:
        me = f.slack_call("auth.test")
        if not me.get("ok"):
            raise RuntimeError(me.get("error"))
        out.append(Check(OK, "Slack", f"bot {me.get('user')} in workspace {me.get('team')}"))
    except Exception as e:  # noqa: BLE001
        return [Check(FAIL, "Slack", f"token rejected ({e})", "Reinstall the Slack app and update SLACK_BOT_TOKEN.")]

    slack = cfg.get("slack", {})
    for key, label in (("channel_id", "release channel"), ("announce_channel_id", "announcement channel"),
                       ("alerts_channel_id", "alerts channel")):
        cid = slack.get(key)
        if not cid:
            out.append(Check(WARN if key != "channel_id" else FAIL, f"Slack {label}", "not configured",
                             f"Set slack.{key} in release-bot.yml or the {config_mod_env(key)} repo variable."))
            continue
        info = f.slack_call("conversations.info", channel=cid)
        if not info.get("ok"):
            err = info.get("error")
            if err == "missing_scope":
                out.append(Check(WARN, f"Slack {label}", f"{cid}: can't verify (no channels:read scope)",
                                 "Optional: add channels:read and groups:read to the Slack app (see "
                                 "slack-app-manifest.yml) so doctor can check channels. Posting doesn't need them."))
            else:
                out.append(Check(FAIL, f"Slack {label}", f"{cid}: {err}",
                                 "Check the channel ID (channel details → About → bottom), and that the bot "
                                 "can see it (private channels: /invite the bot)."))
            continue
        ch = info["channel"]
        if ch.get("is_member"):
            out.append(Check(OK, f"Slack {label}", f"#{ch.get('name')}"))
        elif ch.get("is_private"):
            out.append(Check(FAIL, f"Slack {label}", f"#{ch.get('name')} is private and the bot isn't in it",
                             f"In #{ch.get('name')}: /invite @<your bot>"))
        else:
            out.append(Check(OK, f"Slack {label}", f"#{ch.get('name')} (bot joins on first post via channels:join)"))

    group = slack.get("hero_usergroup_id")
    if group:
        res = f.slack_call("usergroups.users.list", usergroup=group)
        if res.get("ok"):
            out.append(Check(OK, "Release hero group", f"{len(res.get('users', []))} member(s) on duty"))
        else:
            out.append(Check(FAIL, "Release hero group", f"{group}: {res.get('error')}",
                             "Needs the usergroups:read scope and a paid Slack plan (user groups)."))
    elif not f.env.get("MOCK_ON_DUTY"):
        out.append(Check(WARN, "Release hero group", "not set: nobody can submit or resume",
                         "Set slack.hero_usergroup_id and access.release_heroes."))
    return out


def config_mod_env(key: str) -> str:
    return {v: k for k, v in config_mod.ENV_OVERRIDES.items()}.get(key, key.upper())


def default_factory() -> Factory:
    """Real clients (imported lazily so mock mode and tests don't need Google libraries)."""
    def google_identity():
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return getattr(creds, "service_account_email", None) or os.environ.get("GCP_SERVICE_ACCOUNT", "unknown")

    def play(cfg):
        from release_bot.play import Play
        return Play(cfg["package_name"], cfg["play"]["track"], dry_run=True)

    def vitals(cfg):
        from release_bot.vitals import Vitals
        return Vitals(cfg["package_name"])

    def bigquery_table(project, dataset, table):
        from google.cloud import bigquery
        bigquery.Client(project=project).get_table(f"{project}.{dataset}.{table}")

    def grafana(url, token, matchers):
        from release_bot.grafana import Grafana
        return Grafana(url, token, matchers)

    def slack_call(method, **params):
        import requests
        resp = requests.get(f"https://slack.com/api/{method}", params=params, timeout=30,
                            headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"})
        return resp.json()

    return Factory(google_identity, play, vitals, bigquery_table, grafana, slack_call)


def render(app_label: str, checks: list[Check]) -> str:
    lines = [f"Doctor · {app_label}"]
    for c in checks:
        lines.append(f"  {ICON[c.status]} {c.name}: {c.detail}")
        if c.fix and c.status != OK:
            lines.append(f"       → {c.fix}")
    fails = sum(c.status == FAIL for c in checks)
    lines.append(f"  {'All good.' if not fails else f'{fails} problem(s) to fix.'}")
    return "\n".join(lines)
