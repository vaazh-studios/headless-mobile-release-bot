"""Health rules: which signal to watch, what threshold, and what to do.

    health:
      min_users: 1000                     # Play Vitals below this → not enough data yet
      sources:                            # where signals come from (omit a source to turn it off)
        play_vitals: {}
        crashlytics: {project: …, dataset: firebase_crashlytics, table: …, lookback_days: 30}
        grafana: {matchers: ['team="mobile"']}
      rules:
        - {name: Google ANR line, source: play_vitals, metric: user_perceived_anr_rate, above: "0.47%", action: halt}
        - {name: ANR regression,  source: play_vitals, metric: user_perceived_anr_rate, above_previous_by: "25%", action: hold}
        - {name: New crash,       source: crashlytics, metric: new_fatal_issue_users, at_least: 25, action: halt}
        - {name: Pager alert,     source: grafana, severity: critical, action: halt}
        - {name: Latency,         source: grafana, severity: warning, alert: "latency", action: notify}

Actions: halt (stop the rollout), hold (don't advance, ask a human), notify
(tell the channel, keep going). The older per-source threshold format is still
read and converted to rules.

Everything here is pure (no I/O).
"""

import re
from dataclasses import dataclass

from release_bot.gate import Finding, Level
from release_bot.policy import PolicyError, parse_percent

ACTIONS = {"halt": Level.HALT, "hold": Level.HOLD, "notify": Level.NOTIFY}
SOURCES = ("play_vitals", "crashlytics", "grafana", "datadog", "sentry", "http", "pagerduty", "amplitude")
# Sources that yield numbers per version (compared with thresholds / the previous version).
METRIC_SOURCES = ("play_vitals", "sentry", "http", "amplitude")
SENTRY_METRICS = {"crash_free_sessions": "crash-free sessions", "crash_free_users": "crash-free users"}
# Sources whose values are rates (shown as %); http values are shown as plain numbers.
RATE_SOURCES = ("play_vitals", "sentry", "amplitude")
THRESHOLDS = ("above", "below", "above_previous_by", "below_previous_by")
VITALS_METRICS = {   # rule name → Play Developer Reporting API metric
    "user_perceived_crash_rate": "userPerceivedCrashRate",
    "user_perceived_anr_rate": "userPerceivedAnrRate",
    "crash_rate": "crashRate",
    "anr_rate": "anrRate",
}
VITALS_LABELS = {
    "userPerceivedCrashRate": "crash rate",
    "userPerceivedAnrRate": "ANR rate",
    "crashRate": "crash rate (all)",
    "anrRate": "ANR rate (all)",
}
CRASHLYTICS_METRICS = ("new_fatal_issue_users",)


class RuleError(ValueError):
    pass


@dataclass
class Rule:
    name: str
    source: str
    action: str
    metric: str | None = None
    above: float | None = None              # absolute: value ≥ above
    below: float | None = None              # absolute: value ≤ below (e.g. crash-free, conversion)
    above_previous_by: float | None = None  # relative: 0.25 = 25% higher than the previous version
    below_previous_by: float | None = None  # relative: 0.10 = 10% lower than the previous version
    at_least: int | None = None             # count (crashlytics users)
    severity: tuple[str, ...] = ()
    alert: str | None = None                # regex on Grafana alertname
    status: tuple[str, ...] = ()            # Datadog: alert / warn
    priority: tuple[int, ...] = ()          # Datadog: P1..P5 (empty = any)
    monitor: str | None = None              # regex on Datadog monitor name
    urgency: tuple[str, ...] = ()           # PagerDuty: high / low
    service: str | None = None              # PagerDuty: regex on service name
    title: str | None = None                # PagerDuty: regex on incident title

    @property
    def level(self) -> Level:
        return ACTIONS[self.action]


def _rate(value, what):
    try:
        return parse_percent(value, what) if isinstance(value, str) else float(value)
    except PolicyError as e:
        raise RuleError(str(e)) from None


def parse_rule(raw: dict, i: int) -> Rule:
    what = f"health.rules[{i}]" + (f" ({raw.get('name')})" if isinstance(raw, dict) and raw.get("name") else "")
    if not isinstance(raw, dict):
        raise RuleError(f"{what}: must be a mapping")
    source, action = raw.get("source"), raw.get("action", "halt")
    if source not in SOURCES:
        raise RuleError(f"{what}: source must be one of {', '.join(SOURCES)}")
    if action not in ACTIONS:
        raise RuleError(f"{what}: action must be halt, hold or notify")
    rule = Rule(name=raw.get("name") or f"rule {i}", source=source, action=action, metric=raw.get("metric"))

    if source in METRIC_SOURCES:
        if source == "play_vitals" and rule.metric not in VITALS_METRICS:
            raise RuleError(f"{what}: metric must be one of {', '.join(VITALS_METRICS)}")
        if source == "sentry" and rule.metric not in SENTRY_METRICS:
            raise RuleError(f"{what}: metric must be one of {', '.join(SENTRY_METRICS)}")
        if source in ("http", "amplitude") and not rule.metric:
            raise RuleError(f"{what}: set `metric` to the name of a check under health.sources.{source}")
        given = [k for k in THRESHOLDS if k in raw]
        if len(given) != 1:
            raise RuleError(f"{what}: set exactly one of `above` / `below` (e.g. '0.47%') or "
                            "`above_previous_by` / `below_previous_by` (e.g. '25%')")
        setattr(rule, given[0], _rate(raw[given[0]], what))
    elif source == "crashlytics":
        rule.metric = rule.metric or "new_fatal_issue_users"
        if rule.metric not in CRASHLYTICS_METRICS:
            raise RuleError(f"{what}: metric must be one of {', '.join(CRASHLYTICS_METRICS)}")
        rule.at_least = int(raw.get("at_least", 1))
    elif source == "datadog":
        status = raw.get("status", "alert")
        rule.status = tuple(x.lower() for x in ([status] if isinstance(status, str) else status))
        if not set(rule.status) <= {"alert", "warn"}:
            raise RuleError(f"{what}: Datadog status must be alert and/or warn")
        prio = raw.get("priority", [])
        rule.priority = tuple(int(x) for x in ([prio] if isinstance(prio, (int, str)) else prio))
        rule.monitor = raw.get("monitor")
        if rule.monitor:
            re.compile(rule.monitor)
    elif source == "pagerduty":
        urg = raw.get("urgency", "high")
        rule.urgency = tuple(x.lower() for x in ([urg] if isinstance(urg, str) else urg))
        if not set(rule.urgency) <= {"high", "low"}:
            raise RuleError(f"{what}: PagerDuty urgency must be high and/or low")
        rule.service, rule.title = raw.get("service"), raw.get("title")
        for rx in (rule.service, rule.title):
            if rx:
                re.compile(rx)
    else:  # grafana
        sev = raw.get("severity")
        if not sev:
            raise RuleError(f"{what}: set `severity` (e.g. critical) for Grafana rules")
        rule.severity = tuple([sev] if isinstance(sev, str) else sev)
        rule.alert = raw.get("alert")
        if rule.alert:
            re.compile(rule.alert)
    return rule


def _legacy_rules(health: dict) -> list[Rule]:
    rules = []
    pv = health.get("play_vitals", {}) or {}
    halt, hold = pv.get("halt", {}) or {}, pv.get("hold", {}) or {}
    for metric, label in (("user_perceived_crash_rate", "crash"), ("user_perceived_anr_rate", "anr")):
        if metric in halt:
            rules.append(Rule("Google threshold", "play_vitals", "halt", metric, above=float(halt[metric])))
        ratio = hold.get(f"{label}_rate_increase_ratio")
        if ratio:
            rules.append(Rule("previous version", "play_vitals", "hold", metric,
                              above_previous_by=float(ratio) - 1))
    cr = health.get("crashlytics", {}) or {}
    rules.append(Rule("new crash", "crashlytics", "halt", "new_fatal_issue_users",
                      at_least=int(cr.get("new_issue_min_users", 25))))
    gf = health.get("grafana", {}) or {}
    for sev in gf.get("halt_severities", ["critical"]):
        rules.append(Rule("alert", "grafana", "halt", severity=(sev,)))
    for sev in gf.get("hold_severities", ["warning"]):
        rules.append(Rule("alert", "grafana", "hold", severity=(sev,)))
    return rules


def normalize(health: dict) -> dict:
    """{'min_users', 'sources': {name: cfg}, 'rules': [Rule]} from either format."""
    health = health or {}
    if "rules" in health or "sources" in health:
        sources = {name: (cfg or {}) for name, cfg in (health.get("sources") or {}).items()
                   if name in SOURCES and (cfg or {}).get("enabled", True)}
        rules = [parse_rule(r, i) for i, r in enumerate(health.get("rules") or [], 1)]
        return {"min_users": int(health.get("min_users", 1000)), "sources": sources, "rules": rules}

    sources = {}
    for name in SOURCES:
        cfg = health.get(name, {}) or {}
        if cfg.get("enabled", False):
            sources[name] = {k: v for k, v in cfg.items() if k not in ("enabled", "halt", "hold")}
    return {"min_users": int(health.get("min_distinct_users", 1000)), "sources": sources,
            "rules": _legacy_rules(health)}


def crashlytics_query_min_users(norm: dict) -> int:
    """Smallest `at_least` among Crashlytics rules, so the query returns every candidate."""
    values = [r.at_least for r in norm["rules"] if r.source == "crashlytics" and r.at_least]
    return min(values) if values else 25


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def evaluate(norm: dict, signals: dict) -> list[Finding]:
    """`signals`: {source: data | Exception} for each source that was queried.
    play_vitals → {"new": {...} | None, "prev": {...} | None}
    crashlytics → {"new_issues": [{"title", "users"}]}
    grafana     → {"alerts": [{"labels", "annotations"}]}
    A source that failed produces HOLD (can't prove it's healthy), never HALT."""
    findings = []
    for source, data in signals.items():
        if not any(r.source == source for r in norm["rules"]):
            continue  # no rule uses this source: its data (or outage) can't change the decision
        if isinstance(data, Exception):
            findings.append(Finding(source, Level.HOLD, f"could not fetch: {data}"))
            continue
        rules = [r for r in norm["rules"] if r.source == source]
        if source in METRIC_SOURCES:
            min_users = (norm["sources"].get(source) or {}).get("min_users", norm["min_users"])
            findings += _eval_metrics(source, rules, data, min_users)
        elif source == "pagerduty":
            findings += _eval_pagerduty(rules, data)
        elif source == "crashlytics":
            findings += _eval_crashlytics(rules, data)
        elif source == "grafana":
            findings += _eval_grafana(rules, data)
        elif source == "datadog":
            findings += _eval_datadog(rules, data)
    return findings


def _metric_key(source: str, metric: str) -> str:
    return VITALS_METRICS[metric] if source == "play_vitals" else metric


def _metric_label(source: str, key: str) -> str:
    if source == "play_vitals":
        return VITALS_LABELS[key]
    if source == "sentry":
        return SENTRY_METRICS.get(key, key)
    return key.replace("_", " ")


def _fmt(source: str, x: float) -> str:
    return f"{x * 100:.2f}%" if source in RATE_SOURCES else f"{x:g}"


def _eval_metrics(source: str, rules: list[Rule], data: dict, min_users: int) -> list[Finding]:
    """Numbers per version: `data` = {"new": {metric: value, "_users": n}, "prev": {...}}.
    `_users` (when present) gates on min_users; http checks usually have none."""
    src = source.replace("_", "-")
    new, prev = data.get("new"), data.get("prev") or {}
    if not new:
        return [Finding(src, Level.NOT_ENOUGH_DATA, "no data for this version yet (review pending or data lag)")]
    users = new.get("_users", new.get("distinctUsers"))
    if users is not None and users < min_users:
        who = "daily users" if source == "play_vitals" else "users"
        return [Finding(src, Level.NOT_ENOUGH_DATA, f"{int(users)} {who} < {min_users} minimum")]

    findings, keys = [], []
    for r in rules:
        key = _metric_key(source, r.metric)
        if key not in keys:
            keys.append(key)
    for key in keys:
        value = new.get(key)
        if value is None:
            if source == "http":
                findings.append(Finding(src, Level.HOLD, f"{key}: no value returned"))
            continue
        label, base, f = _metric_label(source, key), prev.get(key), (lambda x: _fmt(source, x))
        breached = []
        for r in (r for r in rules if _metric_key(source, r.metric) == key):
            if r.above is not None and value >= r.above:
                breached.append(Finding(src, r.level, f"{label} {f(value)} ≥ {f(r.above)} ({r.name})"))
            elif r.below is not None and value <= r.below:
                breached.append(Finding(src, r.level, f"{label} {f(value)} ≤ {f(r.below)} ({r.name})"))
            elif r.above_previous_by is not None and base and value > base * (1 + r.above_previous_by):
                breached.append(Finding(src, r.level,
                    f"{label} {f(value)} is {value / base:.2f}× previous ({f(base)}), "
                    f"limit +{r.above_previous_by * 100:g}% ({r.name})"))
            elif r.below_previous_by is not None and base and value < base * (1 - r.below_previous_by):
                breached.append(Finding(src, r.level,
                    f"{label} {f(value)} is {(1 - value / base) * 100:.1f}% below previous ({f(base)}), "
                    f"limit −{r.below_previous_by * 100:g}% ({r.name})"))
        if breached:
            findings.append(max(breached, key=lambda x: x.level))
        else:
            findings.append(Finding(src, Level.OK, f"{label} {f(value)}" + (f" vs {f(base)} previous" if base else "")))
    return findings


def _eval_crashlytics(rules: list[Rule], data: dict) -> list[Finding]:
    src = "crashlytics"
    issues = data.get("new_issues", [])
    findings = []
    for r in rules:
        for issue in [i for i in issues if i["users"] >= (r.at_least or 1)][:5]:
            findings.append(Finding(src, r.level, f"new fatal crash in this build: {issue['title']} ({issue['users']} users)"))
    # One finding per issue: keep the most severe rule that matched it.
    best: dict[str, Finding] = {}
    for f in findings:
        if f.message not in best or f.level > best[f.message].level:
            best[f.message] = f
    return list(best.values()) or [Finding(src, Level.OK, "no new fatal crash groups")]


def _eval_grafana(rules: list[Rule], data: dict) -> list[Finding]:
    src = "grafana"
    findings = []
    for a in data.get("alerts", []):
        labels = a.get("labels", {})
        name = labels.get("alertname", "unnamed alert")
        matched = [r for r in rules
                   if labels.get("severity", "") in r.severity and (not r.alert or re.search(r.alert, name, re.I))]
        if not matched:
            continue
        rule = max(matched, key=lambda r: r.level)
        summary = a.get("annotations", {}).get("summary", "")
        findings.append(Finding(src, rule.level, f"firing: {name}" + (f" — {summary}" if summary else "")))
    return findings or [Finding(src, Level.OK, "no matching alerts firing")]


def _eval_datadog(rules: list[Rule], data: dict) -> list[Finding]:
    src = "datadog"
    findings = []
    for m in data.get("monitors", []):
        matched = [r for r in rules
                   if m["status"] in r.status
                   and (not r.priority or (m.get("priority") or 0) in r.priority)
                   and (not r.monitor or re.search(r.monitor, m["name"], re.I))]
        if matched:
            rule = max(matched, key=lambda r: r.level)
            prio = f" P{m['priority']}" if m.get("priority") else ""
            findings.append(Finding(src, rule.level, f"{m['status']}{prio}: {m['name']}"))
    return findings or [Finding(src, Level.OK, "no matching monitors alerting")]


# Recommended starting rules per source. Copy them into health.rules, or let
# mock mode use them for sources you haven't written rules for.
DEFAULT_RULES = {
    "play_vitals": [
        {"name": "Google ANR line", "source": "play_vitals", "metric": "user_perceived_anr_rate", "above": "0.47%", "action": "halt"},
        {"name": "Google crash line", "source": "play_vitals", "metric": "user_perceived_crash_rate", "above": "1.09%", "action": "halt"},
        {"name": "ANR regression", "source": "play_vitals", "metric": "user_perceived_anr_rate", "above_previous_by": "25%", "action": "hold"},
        {"name": "Crash regression", "source": "play_vitals", "metric": "user_perceived_crash_rate", "above_previous_by": "25%", "action": "hold"},
    ],
    "crashlytics": [
        {"name": "New crash", "source": "crashlytics", "metric": "new_fatal_issue_users", "at_least": 25, "action": "halt"},
    ],
    "grafana": [
        {"name": "Critical alert", "source": "grafana", "severity": "critical", "action": "halt"},
        {"name": "Warning alert", "source": "grafana", "severity": "warning", "action": "hold"},
    ],
    "datadog": [
        {"name": "Monitor alerting", "source": "datadog", "status": "alert", "action": "halt"},
        {"name": "Monitor warning", "source": "datadog", "status": "warn", "action": "hold"},
    ],
    "sentry": [
        {"name": "Crash-free users floor", "source": "sentry", "metric": "crash_free_users", "below": "99%", "action": "halt"},
        {"name": "Crash-free users drop", "source": "sentry", "metric": "crash_free_users", "below_previous_by": "0.5%", "action": "hold"},
    ],
    "pagerduty": [
        {"name": "Open high-urgency incident", "source": "pagerduty", "urgency": "high", "action": "hold"},
    ],
}


def with_default_rules(norm: dict, sources) -> dict:
    """Add DEFAULT_RULES for each of `sources` that has no rule of its own."""
    have = {r.source for r in norm["rules"]}
    extra = [parse_rule(r, i) for src in sources if src not in have
             for i, r in enumerate(DEFAULT_RULES.get(src, []), 1)]
    return {**norm, "rules": norm["rules"] + extra}


def _eval_pagerduty(rules: list[Rule], data: dict) -> list[Finding]:
    src = "pagerduty"
    findings = []
    for inc in data.get("incidents", []):
        matched = [r for r in rules
                   if inc.get("urgency", "high") in r.urgency
                   and (not r.service or re.search(r.service, inc.get("service", ""), re.I))
                   and (not r.title or re.search(r.title, inc.get("title", ""), re.I))]
        if matched:
            rule = max(matched, key=lambda r: r.level)
            findings.append(Finding(src, rule.level,
                                    f"open {inc.get('urgency', '')} incident on {inc.get('service', '?')}: {inc.get('title', '')}"))
    return findings or [Finding(src, Level.OK, "no matching open incidents")]
