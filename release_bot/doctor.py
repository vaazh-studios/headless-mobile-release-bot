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
    datadog: object = None           # (api_key, app_key, query, site) -> object with firing_monitors()
    http: object = None              # (source_cfg) -> object with metrics(ctx)
    pagerduty: object = None         # (token, service_ids) -> object with open_incidents()
    sentry: object = None            # (source_cfg, token) -> object with metrics(ctx)
    incident_io: object = None       # (api_key) -> object with open_incidents()
    incident_io_admin: object = None # (api_key) -> object with on_call(schedule_id)
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

    ios = cfg.get("platform") == "ios"
    usable = set(norm["sources"]) & {r.source for r in norm["rules"]}
    if ios:
        usable.discard("play_vitals")
    if not mock and not usable:
        checks.append(Check(WARN, "health signals",
                            f"no health source with rules for {'iOS' if ios else 'Android'}: the bot will advance "
                            "without checking anything",
                            "Add a source and rules (iOS: Crashlytics, Grafana or Datadog; Play Vitals is Android only). "
                            "See docs/configuration.md."))
    if mock:
        checks.append(Check(OK, "store", "mock mode: Google Play / App Store, Play Vitals and Crashlytics are "
                            "simulated; other configured integrations are checked for real below"))
    elif not ios:
        checks += _google_checks(cfg, norm, factory)
    creds = {"grafana": ("GRAFANA_URL", "GRAFANA_TOKEN"), "datadog": ("DD_API_KEY", "DD_APP_KEY"),
             "pagerduty": ("PAGERDUTY_API_TOKEN",), "sentry": ("SENTRY_AUTH_TOKEN",),
             "incident_io": ("INCIDENT_IO_API_KEY",), "amplitude": ("AMPLITUDE_API_KEY", "AMPLITUDE_SECRET_KEY")}
    simulated = set()
    if mock:  # with no credentials, mock mode simulates the integration; with credentials, check it for real
        for src, names in creds.items():
            if src in norm["sources"] and not all(factory.env.get(n) for n in names):
                simulated.add(src)
                checks.append(Check(OK, src.replace("_", "."), "simulated (mock mode, no credentials)"))
        norm = {**norm, "sources": {k: v for k, v in norm["sources"].items() if k not in simulated}}
    if True:  # third-party integrations: checked for real
        if "grafana" in norm["sources"]:
            checks.append(_grafana_check(norm, factory))
        if "datadog" in norm["sources"]:
            checks.append(_datadog_check(norm, factory))
        if "http" in norm["sources"]:
            checks += _http_checks(cfg, norm, factory)
        if "pagerduty" in norm["sources"]:
            checks.append(_pagerduty_check(norm, factory))
        if "sentry" in norm["sources"]:
            checks.append(_sentry_check(cfg, norm, factory))
        if "incident_io" in norm["sources"]:
            checks.append(_incident_io_check(factory))
        if "amplitude" in norm["sources"]:
            ok = bool(factory.env.get("AMPLITUDE_API_KEY") and factory.env.get("AMPLITUDE_SECRET_KEY"))
            funnels = ", ".join((norm["sources"]["amplitude"].get("funnels") or {}).keys()) or "none"
            checks.append(Check(OK if ok else FAIL, "Amplitude", f"funnels: {funnels}" if ok else
                                "AMPLITUDE_API_KEY or AMPLITUDE_SECRET_KEY is not set",
                                "" if ok else "Copy the project's API key and secret key (Settings → Projects) into "
                                "those secrets. Note: each check costs Amplitude API quota."))
    checks += _notify_checks(cfg, factory.env)

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

    def review():
        n = len(f.play(cfg).releases_summary())
        return Check(OK, "Play review status", f"readable · {n} release(s) on the track")
    if hasattr(f.play(cfg), "releases_summary"):
        c = _try("Play review status", review, lambda s, e: (
            f"HTTP {s or '?'} ({e})",
            "The bot can't see 'in review' / 'rejected' and waits for health data instead. Update "
            "google-api-python-client (tracks.releases.list) and check the service account's app access."))
        out.append(c if c.status == OK else Check(WARN, c.name, c.detail, c.fix))

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


def _datadog_check(norm: dict, f: Factory) -> Check:
    api, app, site = f.env.get("DD_API_KEY"), f.env.get("DD_APP_KEY"), f.env.get("DD_SITE") or "datadoghq.com"
    if not api or not app:
        return Check(FAIL, "Datadog", "DD_API_KEY or DD_APP_KEY is not set",
                     "Store a Datadog API key and an application key (scope monitors_read) as the DD_API_KEY and "
                     "DD_APP_KEY secrets; set DD_SITE if you're not on datadoghq.com. Or remove health.sources.datadog.")
    query = norm["sources"]["datadog"].get("query", "")
    def datadog():
        firing = f.datadog(api, app, query, site).firing_monitors()
        return Check(OK, "Datadog", f"{site} readable · {len(firing)} matching monitor(s) alerting now")
    return _try("Datadog", datadog, lambda s, e: (
        f"{site}: HTTP {s or '?'} ({e})",
        "Check the API key, the application key's monitors_read scope, and DD_SITE (e.g. datadoghq.eu)."
        if s in (401, 403) else "Check DD_SITE and that api.<site> is reachable from GitHub-hosted runners."))


def _http_checks(cfg: dict, norm: dict, f: Factory) -> list[Check]:
    src = norm["sources"]["http"]
    out = []
    names = list((src.get("checks") or {}).keys())
    if not names:
        return [Check(FAIL, "HTTP checks", "health.sources.http has no checks", "Add checks: {name: {url, value}}")]
    ctx = {"version": "0.0.0", "version_code": 0, "package": cfg.get("package_name"), "app": cfg.get("app_id"),
           "platform": cfg.get("platform", "android")}
    for name in names:
        def one(name=name):
            client = f.http({"checks": {name: src["checks"][name]}})
            value = client.metrics(ctx)["new"].get(name)
            if value is None:
                return Check(WARN, f"HTTP check {name}", "reachable, but the `value` path found nothing "
                             "(fine if there's no data for a placeholder version)", "Check the `value` path against the JSON.")
            return Check(OK, f"HTTP check {name}", f"reachable · value {value:g}")
        out.append(_try(f"HTTP check {name}", one, lambda s, e: (
            f"HTTP {s or '?'} ({e})",
            "Check the URL, and that every ${NAME} it uses is in the RELEASE_BOT_HTTP_ENV secret.")))
    return out


def _pagerduty_check(norm: dict, f: Factory) -> Check:
    token = f.env.get("PAGERDUTY_API_TOKEN")
    if not token:
        return Check(FAIL, "PagerDuty", "PAGERDUTY_API_TOKEN is not set",
                     "Create a read-only REST API key in PagerDuty and store it as PAGERDUTY_API_TOKEN.")
    ids = norm["sources"]["pagerduty"].get("service_ids", [])
    def pd():
        n = len(f.pagerduty(token, ids).open_incidents())
        return Check(OK, "PagerDuty", f"readable · {n} open incident(s) on {len(ids) or 'all'} service(s)")
    return _try("PagerDuty", pd, lambda s, e: (f"HTTP {s or '?'} ({e})",
                "Check PAGERDUTY_API_TOKEN and the service IDs (Service → Settings → ID)."))


def _sentry_check(cfg: dict, norm: dict, f: Factory) -> Check:
    token = f.env.get("SENTRY_AUTH_TOKEN")
    if not token:
        return Check(FAIL, "Sentry", "SENTRY_AUTH_TOKEN is not set",
                     "Create an internal integration or user token with org:read and project:read, "
                     "store it as SENTRY_AUTH_TOKEN.")
    src = norm["sources"]["sentry"]
    def sentry():
        client = f.sentry(src, token)
        client.metrics({"version": "0.0.0", "version_code": 0, "package": cfg.get("package_name"),
                        "platform": cfg.get("platform", "android")})
        return Check(OK, "Sentry", f"sessions API readable for {src.get('org')}/{src.get('project')}")
    return _try("Sentry", sentry, lambda s, e: (f"HTTP {s or '?'} ({e})",
                "Check org, project (numeric id), the token's scopes, and `url` for EU orgs (https://de.sentry.io)."))


def _incident_io_check(f: Factory) -> Check:
    key = f.env.get("INCIDENT_IO_API_KEY")
    if not key:
        return Check(FAIL, "incident.io", "INCIDENT_IO_API_KEY is not set",
                     "incident.io → Settings → API keys → create a key that can view incidents; store it as "
                     "INCIDENT_IO_API_KEY.")
    def inc():
        n = len(f.incident_io(key).open_incidents())
        return Check(OK, "incident.io", f"readable · {n} open incident(s) right now")
    return _try("incident.io", inc, lambda s, e: (f"HTTP {s or '?'} ({e})",
                "Check the API key and that it may view incidents (Settings → API keys)."))


def _notify_checks(cfg: dict, env: dict) -> list[Check]:
    out = []
    n = cfg.get("notify") or {}
    mock = env.get("RELEASE_BOT_MOCK", "").lower() == "true"
    if mock and env.get("MOCK_LIVE_ACTIONS", "").lower() != "true":
        actions = [name for name, on in (("PagerDuty paging", n.get("pagerduty") is not None),
                                          ("incident.io", bool(n.get("incident_io"))),
                                          ("Optimizely kill switch", bool((cfg.get("on_halt") or {}).get("optimizely"))))
                   if on]
        if actions:
            out.append(Check(OK, "halt actions", f"{', '.join(actions)}: simulated in mock mode "
                             "(set MOCK_LIVE_ACTIONS=true to test them for real)"))
        return out
    if env.get("TEAMS_WEBHOOK_URL"):
        out.append(Check(OK, "Microsoft Teams", "webhook configured (messages are mirrored; not test-posted)"))
    if n.get("pagerduty") is not None:
        out.append(Check(OK if env.get("PAGERDUTY_ROUTING_KEY") else FAIL, "PagerDuty paging",
                         "routing key set" if env.get("PAGERDUTY_ROUTING_KEY") else "PAGERDUTY_ROUTING_KEY is not set",
                         "" if env.get("PAGERDUTY_ROUTING_KEY") else "Add an Events API v2 integration to a service and "
                         "store its integration key as PAGERDUTY_ROUTING_KEY."))
    inc = n.get("incident_io")
    if inc and inc.get("declare_incident") is not None:
        ok = bool(env.get("INCIDENT_IO_API_KEY"))
        d = inc["declare_incident"] or {}
        out.append(Check(OK if ok else FAIL, "incident.io incidents on halt",
                         f"will declare a {d.get('severity', 'default-severity')} incident (mode {d.get('mode', 'standard')})"
                         if ok else "INCIDENT_IO_API_KEY is not set",
                         "" if ok else "Store an incident.io API key that can create incidents as INCIDENT_IO_API_KEY. "
                         "Use mode: test while trying it out."))
    if inc and inc.get("alert_source_config_id") is not None:
        ok = bool(env.get("INCIDENT_IO_ALERT_TOKEN") and inc.get("alert_source_config_id"))
        out.append(Check(OK if ok else FAIL, "incident.io alerts",
                         f"alert source {inc.get('alert_source_config_id')}" if ok else
                         "INCIDENT_IO_ALERT_TOKEN or alert_source_config_id is missing",
                         "" if ok else "incident.io → Alerts → Sources → add an HTTP source; put its ID in "
                         "notify.incident_io.alert_source_config_id and its token in INCIDENT_IO_ALERT_TOKEN."))
    opt = (cfg.get("on_halt") or {}).get("optimizely")
    if opt:
        ok = bool(env.get("OPTIMIZELY_TOKEN"))
        out.append(Check(OK if ok else FAIL, "Optimizely kill switch",
                         f"will turn off {', '.join(opt.get('flags', []))} in {opt.get('environment', 'production')}"
                         if ok else "OPTIMIZELY_TOKEN is not set",
                         "" if ok else "Create a personal access token in Optimizely and store it as OPTIMIZELY_TOKEN."))
    return out


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

    schedule = (cfg.get("access", {}).get("on_duty") or {}).get("incident_io_schedule_id")
    if schedule:
        out.append(_on_call_check(schedule, f))
        return out
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


def _on_call_check(schedule: str, f: Factory) -> Check:
    key = f.env.get("INCIDENT_IO_API_KEY")
    if not key:
        return Check(FAIL, "Release hero (incident.io)", "INCIDENT_IO_API_KEY is not set",
                     "Create an incident.io API key that can view schedules and store it as INCIDENT_IO_API_KEY.")
    def oc():
        users = f.incident_io_admin(key).on_call(schedule)
        who = ", ".join(u["name"] or u["email"] for u in users) or "nobody"
        missing = [u["name"] or u["email"] for u in users if not u.get("slack_user_id")]
        status = WARN if not users or missing else OK
        detail = f"on call now: {who}" + (f" (no Slack account linked: {', '.join(missing)})" if missing else "")
        return Check(status, "Release hero (incident.io)", detail,
                     "" if status == OK else "Make sure the schedule has someone on call and their incident.io "
                     "user is linked to Slack (for @mentions). Map their GitHub login to their email in "
                     "access.release_heroes.")
    return _try("Release hero (incident.io)", oc, lambda s, e: (f"HTTP {s or '?'} ({e})",
                "Check the schedule ID (On-call → Schedules → the schedule's URL) and that the key can view schedules."))


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

    def datadog(api_key, app_key, query, site):
        from release_bot.datadog import Datadog
        return Datadog(api_key, app_key, query, site)

    def http(src):
        from release_bot.http_source import HttpSource
        return HttpSource(src)

    def pagerduty(token, ids):
        from release_bot.pagerduty import PagerDuty
        return PagerDuty(token, ids)

    def sentry(src, token):
        from release_bot.sentry import Sentry
        return Sentry(src, token)

    def incident_io(key):
        from release_bot.incident_io import IncidentIO
        return IncidentIO(key)

    def incident_io_admin(key):
        from release_bot.incident_io import IncidentIOAdmin
        return IncidentIOAdmin(key)

    def slack_call(method, **params):
        import requests
        resp = requests.get(f"https://slack.com/api/{method}", params=params, timeout=30,
                            headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"})
        return resp.json()

    return Factory(google_identity, play, vitals, bigquery_table, grafana, slack_call=slack_call, datadog=datadog,
                   http=http, pagerduty=pagerduty, sentry=sentry, incident_io=incident_io,
                   incident_io_admin=incident_io_admin)


def render(app_label: str, checks: list[Check]) -> str:
    lines = [f"Doctor · {app_label}"]
    for c in checks:
        lines.append(f"  {ICON[c.status]} {c.name}: {c.detail}")
        if c.fix and c.status != OK:
            lines.append(f"       → {c.fix}")
    fails = sum(c.status == FAIL for c in checks)
    lines.append(f"  {'All good.' if not fails else f'{fails} problem(s) to fix.'}")
    return "\n".join(lines)
