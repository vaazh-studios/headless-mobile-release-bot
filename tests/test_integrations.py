"""HTTP checks, PagerDuty, Teams, Optimizely kill switch, and 'below' thresholds."""

import pytest

from release_bot import notify, rules
from release_bot.gate import Level, Verdict
from release_bot.http_source import HttpSource, extract, fill, substitute_env
from release_bot.pagerduty import PagerDuty


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self):
        return self.data


# ---------- generic HTTP source ----------

def test_extract_paths_and_templates():
    data = {"data": {"result": [{"value": [1700000000, "3.5"]}]}}
    assert extract(data, "data.result[0].value[1]") == "3.5"
    assert extract(data, "data.result[3].value") is None
    assert fill("q={version}&p={package}", {"version": "4.12.0", "package": "com.x"}, url=True) == "q=4.12.0&p=com.x"
    assert fill("a {version} b {missing}", {"version": "1"}) == "a 1 b {missing}"
    with pytest.raises(KeyError, match="RELEASE_BOT_HTTP_ENV"):
        substitute_env("Bearer ${NOPE}", {})


class HttpRec:
    def __init__(self):
        self.calls = []
    def request(self, method, url, headers=None, timeout=None, json=None):
        self.calls.append((method, url, headers, json))
        return Resp({"data": {"result": [{"value": [0, "2.0" if "4.11.0" in url else "6.5"]}]}})


def test_http_checks_fetch_new_and_previous_with_secrets_from_env_blob():
    http = HttpRec()
    src = HttpSource({"checks": {"api_5xx": {
        "url": "https://prom/q?v={version}", "headers": {"Authorization": "Bearer ${PROM_TOKEN}"},
        "value": "data.result[0].value[1]", "previous": True}}},
        env={"RELEASE_BOT_HTTP_ENV": "PROM_TOKEN=s3cret\n# comment"}, session=http)
    m = src.metrics({"version": "4.12.0", "previous_version": "4.11.0"})
    assert m == {"new": {"api_5xx": 6.5}, "prev": {"api_5xx": 2.0}}
    assert http.calls[0][2] == {"Authorization": "Bearer s3cret"}
    n = rules.normalize({"sources": {"http": {}}, "rules": [
        {"name": "5xx abs", "source": "http", "metric": "api_5xx", "above": 5, "action": "halt"},
        {"name": "5xx rel", "source": "http", "metric": "api_5xx", "above_previous_by": "50%", "action": "hold"}]})
    f = rules.evaluate(n, {"http": m})
    assert Verdict(f).level == Level.HALT and "api 5xx 6.5 ≥ 5" in f[0].message


def test_http_missing_value_holds():
    n = rules.normalize({"sources": {"http": {}}, "rules": [
        {"source": "http", "metric": "x", "above": 1, "action": "halt"}]})
    assert Verdict(rules.evaluate(n, {"http": {"new": {"x": None}, "prev": {}}})).level == Level.HOLD


def test_below_thresholds_for_conversion_like_metrics():
    n = rules.normalize({"sources": {"http": {}}, "rules": [
        {"name": "floor", "source": "http", "metric": "conv", "below": 0.30, "action": "halt"},
        {"name": "drop", "source": "http", "metric": "conv", "below_previous_by": "10%", "action": "hold"}]})
    level = lambda new, prev: Verdict(rules.evaluate(n, {"http": {"new": {"conv": new}, "prev": {"conv": prev}}})).level
    assert level(0.29, 0.40) == Level.HALT
    assert level(0.35, 0.40) == Level.HOLD     # 12.5% lower
    assert level(0.39, 0.40) == Level.OK


def test_threshold_validation():
    with pytest.raises(rules.RuleError, match="exactly one"):
        rules.parse_rule({"source": "http", "metric": "x", "above": 1, "below": 2}, 1)
    with pytest.raises(rules.RuleError, match="name of a check"):
        rules.parse_rule({"source": "http", "above": 1}, 1)


# ---------- PagerDuty ----------

class PDHttp:
    def __init__(self):
        self.params = None
    def get(self, url, params, timeout, headers):
        self.params, self.headers = params, headers
        return Resp({"incidents": [
            {"title": "Checkout API down", "urgency": "high", "status": "triggered", "service": {"summary": "payments"}},
            {"title": "Slow images", "urgency": "low", "status": "acknowledged", "service": {"summary": "cdn"}}]})


def test_pagerduty_open_incidents_and_rules():
    http = PDHttp()
    incidents = PagerDuty("tok", ["PABC"], session=http).open_incidents()
    assert ("service_ids[]", "PABC") in http.params and ("statuses[]", "triggered") in http.params
    assert http.headers["Authorization"] == "Token token=tok"
    n = rules.normalize({"sources": {"pagerduty": {}}, "rules": [
        {"name": "payments", "source": "pagerduty", "urgency": "high", "service": "pay", "action": "halt"},
        {"name": "any low", "source": "pagerduty", "urgency": "low", "action": "notify"}]})
    f = rules.evaluate(n, {"pagerduty": {"incidents": incidents}})
    assert Verdict(f).level == Level.HALT and any("Checkout API down" in x.message for x in f)


# ---------- notifications & halt side effects ----------

def test_slack_to_markdown_for_teams():
    md = notify.slack_to_markdown("🛑 <!subteam^S1> *Rollout HALTED*\n<https://gh/run/1|View run> · <https://gh/halt|Halt>")
    assert md == "🛑 **Rollout HALTED**\n[View run](https://gh/run/1) · [Halt](https://gh/halt)"


class TeamsRec:
    def __init__(self):
        self.sent = []
    def send(self, title, text):
        self.sent.append((title, text))


class SlackRec:
    def __init__(self, skip=False):
        self.posts, self._last_skipped, self.skip = [], False, skip
    def post(self, key, text, root_text=None):
        self._last_skipped = self.skip
        self.posts.append(text)
    def announce(self, ch, text):
        pass
    def thread_contains(self, key, text):
        return False
    def usergroup_members(self, g):
        return set()


def test_teams_mirrors_posted_messages_only():
    teams = TeamsRec()
    notify.Fanout(SlackRec(), teams).post("shop 2.0.0", "⬆️ Rollout 1% → 2%")
    assert teams.sent == [("Release · shop 2.0.0", "⬆️ Rollout 1% → 2%")]
    teams2 = TeamsRec()
    notify.Fanout(SlackRec(skip=True), teams2).post("shop 2.0.0", "repeat")
    assert teams2.sent == []


def test_teams_payload_is_an_adaptive_card():
    class H:
        def post(self, url, json, timeout):
            self.body = json
            return Resp({})
    h = H()
    notify.Teams("https://example.webhook", session=h).send("Release · shop 2.0.0", "*hi*")
    att = h.body["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["content"]["body"][1]["text"] == "**hi**"


def test_on_halt_pages_only_automatic_and_flips_flags(monkeypatch):
    paged, flipped = [], []
    monkeypatch.setattr("release_bot.pagerduty.page", lambda *a, **k: paged.append(k.get("dedup_key")))
    monkeypatch.setattr(notify.Optimizely, "set_flags", lambda self, flags, enabled: flipped.append((flags, enabled)) or flags)
    cfg = {"notify": {"pagerduty": {"severity": "critical"}},
           "on_halt": {"optimizely": {"project_id": 1, "environment": "production", "flags": ["new_checkout"]}}}
    env = {"PAGERDUTY_ROUTING_KEY": "rk", "OPTIMIZELY_TOKEN": "t"}
    lines = notify.on_halt(cfg, "Shop 2.0.0 halted", "k1", dry_run=False, automatic=True, env=env)
    assert paged == ["k1"] and flipped == [(["new_checkout"], False)]
    assert any("Paged" in l for l in lines) and any("new_checkout" in l for l in lines)
    paged.clear()
    notify.on_halt(cfg, "manual", "k2", dry_run=False, automatic=False, env=env)
    assert paged == []                                   # humans halting don't page anyone


def test_optimizely_calls_disable_endpoint():
    class H:
        def __init__(self):
            self.urls = []
        def post(self, url, timeout, headers):
            self.urls.append((url, headers))
            return Resp({})
    h = H()
    notify.Optimizely("tok", 42, "production", session=h).set_flags(["new_checkout"], enabled=False)
    assert h.urls[0][0] == ("https://api.optimizely.com/flags/v1/projects/42/flags/new_checkout/"
                            "environments/production/ruleset/disabled")
    assert h.urls[0][1] == {"Authorization": "Bearer tok"}


# ---------- Sentry ----------

from release_bot.sentry import Sentry  # noqa: E402


class SentryHttp:
    def __init__(self):
        self.calls = []
    def get(self, url, params, timeout, headers):
        self.calls.append((url, params, headers))
        release = dict(params)["query"]
        rate = 0.985 if "4.12.0" in release else 0.995
        return Resp({"groups": [{"by": {}, "totals": {"crash_free_rate(session)": rate + 0.003,
                                                       "crash_free_rate(user)": rate,
                                                       "sum(session)": 90000, "count_unique(user)": 4200}}]})


def test_sentry_release_names_and_metrics():
    http = SentryHttp()
    s = Sentry({"org": "acme", "project": "android", "url": "https://de.sentry.io", "environment": "production"},
               "tok", session=http)
    ctx = {"package": "com.acme.shop", "version": "4.12.0", "version_code": 41200,
           "previous_version": "4.11.0", "previous_version_code": 41100}
    assert s.release_name(ctx) == "com.acme.shop@4.12.0+41200"
    assert s.release_name({"package": "com.acme.shop", "version": "4.12.0"}) == "com.acme.shop@4.12.0+*"   # iOS
    m = s.metrics(ctx)
    url, params, headers = http.calls[0]
    assert url == "https://de.sentry.io/api/0/organizations/acme/sessions/"
    assert ("field", "crash_free_rate(user)") in params and ("environment", "production") in params
    assert headers == {"Authorization": "Bearer tok"}
    assert m["new"]["crash_free_users"] == 0.985 and m["prev"]["crash_free_users"] == 0.995 and m["new"]["_users"] == 4200
    n = rules.normalize({"sources": {"sentry": {}}, "rules": rules.DEFAULT_RULES["sentry"]})
    f = rules.evaluate(n, {"sentry": m})
    assert Verdict(f).level == Level.HALT and "crash-free users 98.50% ≤ 99.00%" in f[0].message


# ---------- Amplitude ----------

from datetime import date  # noqa: E402

from release_bot.amplitude import Amplitude  # noqa: E402


class AmpHttp:
    def __init__(self):
        self.calls = []
    def get(self, url, params, auth, timeout):
        self.calls.append((url, params, auth))
        seg = dict(params)["s"]
        conv = 0.31 if "4.12.0" in seg else 0.40
        return Resp({"data": [{"cumulative": [1.0, conv], "cumulativeRaw": [800, int(800 * conv)], "events": []}]})


def test_amplitude_funnel_per_version():
    http = AmpHttp()
    amp = Amplitude({"region": "eu", "days": 2, "funnels": {"checkout_conversion": {"steps": ["Checkout Started", "Purchase Completed"]}}},
                    "k", "s", session=http, today=date(2026, 10, 12))
    m = amp.metrics({"version": "4.12.0", "previous_version": "4.11.0"})
    url, params, auth = http.calls[0]
    assert url == "https://analytics.eu.amplitude.com/api/2/funnels" and auth == ("k", "s")
    assert [v for k, v in params if k == "e"] == ['{"event_type": "Checkout Started"}', '{"event_type": "Purchase Completed"}']
    assert ("start", "20261011") in params and ("end", "20261012") in params
    assert m == {"new": {"checkout_conversion": 0.31, "_users": 800}, "prev": {"checkout_conversion": 0.40}}
    n = rules.normalize({"sources": {"amplitude": {"min_users": 300}}, "rules": [
        {"name": "Checkout drop", "source": "amplitude", "metric": "checkout_conversion", "below_previous_by": "10%", "action": "hold"}]})
    f = rules.evaluate(n, {"amplitude": m})
    assert Verdict(f).level == Level.HOLD and "22.5% below previous" in f[0].message


def test_amplitude_below_its_min_users_waits():
    n = rules.normalize({"sources": {"amplitude": {"min_users": 300}}, "rules": [
        {"source": "amplitude", "metric": "conv", "below": "20%", "action": "halt"}]})
    f = rules.evaluate(n, {"amplitude": {"new": {"conv": 0.1, "_users": 120}, "prev": {}}})
    assert f[0].level == Level.NOT_ENOUGH_DATA and "120 users < 300 minimum" in f[0].message


# ---------- Play tracks: internal has no staged rollout ----------

from release_bot.play import Play  # noqa: E402


class FakeEdits:
    def __init__(self):
        self.track_body = None
    def insert(self, **kw):
        return self
    def bundles(self):
        return self
    def upload(self, **kw):
        class X:
            def execute(self_inner):
                return {"versionCode": 4200}
        return X()
    def tracks(self):
        return self
    def update(self, **kw):
        self.track_body = kw["body"]
        return self
    def commit(self, **kw):
        return self
    def execute(self):
        return {"id": "edit1"}


class FakeService:
    def __init__(self):
        self.e = FakeEdits()
    def edits(self):
        return self.e


@pytest.mark.parametrize("track, staged", [("internal", False), ("alpha", True), ("production", True)])
def test_internal_track_releases_without_user_fraction(track, staged, monkeypatch, tmp_path):
    monkeypatch.setattr("release_bot.play.MediaFileUpload", lambda *a, **k: None)
    svc = FakeService()
    Play("com.x", track, service=svc).upload_and_start("a.aab", "1.0.0", 0.01, "notes", "en-US")
    release = svc.e.track_body["releases"][0]
    assert ("userFraction" in release) == staged
    assert release["status"] == ("inProgress" if staged else "completed")
