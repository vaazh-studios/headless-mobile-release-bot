"""Contract tests: every request the bot's API clients send must match the
provider's published OpenAPI spec (snapshots in tests/contracts/specs,
refreshed with scripts/update_api_specs.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
import validator as v  # noqa: E402


def check(spec_name: str, base: str, recorder: v.Recorder):
    spec = v.load_spec(spec_name)
    assert recorder.calls, "the client made no calls"
    errors = [e for c in recorder.calls for e in v.validate(spec, base, c)]
    assert not errors, "\n".join(errors)


# ---------------- incident.io ----------------

def incident_io_responder(call):
    if call.url.endswith("/v2/incidents") and call.method == "GET":
        return {"incidents": [], "pagination_meta": {}}
    if call.url.endswith("/v2/schedule_entries"):
        return {"schedule_entries": {"final": []}}
    if call.url.endswith("/v1/severities"):
        return {"severities": [{"id": "01SEV", "name": "Minor"}]}
    if call.url.endswith("/v2/incidents"):
        return {"incident": {"id": "01INC", "reference": "INC-1", "permalink": "https://app.incident.io/x/1"}}
    return {}


def test_incident_io_contract():
    from release_bot.incident_io import IncidentIO, IncidentIOAdmin, alert
    rec = v.Recorder(incident_io_responder)
    IncidentIO("key", session=rec).open_incidents()
    admin = IncidentIOAdmin("key", session=rec)
    admin.on_call("01SCHED")
    admin.declare("Shop Android 2.0.0 halted", "details", "release-bot-shop-android-2.0.0", severity="Minor", mode="test")
    alert("01SRC", "token", "Shop halted", "details", "dedup-1", source_url="https://github.com/x", session=rec)
    check("incident_io", r"https://api\.incident\.io", rec)


# ---------------- PagerDuty ----------------

def test_pagerduty_contract():
    from release_bot.pagerduty import PagerDuty, page
    rest = v.Recorder(lambda c: {"incidents": [], "more": False})
    PagerDuty("token", ["PABC123"], session=rest).open_incidents()
    check("pagerduty_rest", r"https://api\.pagerduty\.com", rest)

    events = v.Recorder(lambda c: {"status": "success", "dedup_key": "k"})
    page("R" * 32, "Shop Android 2.0.0 halted", "headless-mobile-release-bot", "critical", "dedup-1", session=events)
    check("pagerduty_events", r"https://events\.pagerduty\.com/v2", events)


# ---------------- Datadog ----------------

def test_datadog_contract():
    from release_bot.datadog import Datadog
    rec = v.Recorder(lambda c: {"monitors": [], "metadata": {"page": 0, "page_count": 1}})
    Datadog("api", "app", 'tag:"team:mobile"', "datadoghq.eu", session=rec).firing_monitors()
    check("datadog", r"https://api\.[a-z0-9.]+", rec)


# ---------------- Sentry ----------------

def test_sentry_contract():
    from release_bot.sentry import Sentry
    rec = v.Recorder(lambda c: {"groups": [{"by": {}, "totals": {"crash_free_rate(user)": 0.99, "count_unique(user)": 10}}]})
    Sentry({"org": "acme", "project": "android", "environment": "production"}, "tok", session=rec).metrics(
        {"package": "com.acme", "version": "1.2.0", "version_code": 120,
         "previous_version": "1.1.0", "previous_version_code": 110})
    check("sentry", r"https://(us\.|de\.)?sentry\.io", rec)


# ---------------- App Store Connect ----------------

def asc_responder(call):
    path = call.url.split("/v1", 1)[1]
    if path == "/apps":
        return {"data": [{"id": "app1", "type": "apps"}]}
    if path == "/builds":
        return {"data": [{"id": "build1", "type": "builds"}]}
    if path.endswith("/appStoreVersionLocalizations"):
        return {"data": [{"id": "loc1", "type": "appStoreVersionLocalizations", "attributes": {"locale": "en-US"}}]}
    if path == "/reviewSubmissions" and call.method == "GET":
        return {"data": []}
    if path.endswith("/appStoreVersions") and call.method == "GET":
        return {"data": [{"id": "v1", "type": "appStoreVersions",
                          "attributes": {"versionString": "3.0.0", "appVersionState": "PENDING_DEVELOPER_RELEASE"},
                          "relationships": {"appStoreVersionPhasedRelease": {"data": {"id": "p1", "type": "appStoreVersionPhasedReleases"}}}}],
                "included": [{"id": "p1", "type": "appStoreVersionPhasedReleases",
                              "attributes": {"phasedReleaseState": "INACTIVE", "currentDayNumber": None}}]}
    return {"data": {"id": "new1"}}


def test_appstore_connect_contract(monkeypatch):
    from release_bot.appstore import AppStore
    monkeypatch.setattr("release_bot.appstore._token", lambda *a: "jwt")
    rec = v.Recorder(asc_responder)
    store = AppStore("com.acme.shop", session=rec, env={"ASC_KEY_ID": "k", "ASC_ISSUER_ID": "i", "ASC_PRIVATE_KEY": "p"})
    rel = store.submit("3.0.0", "Bug fixes", "en-US")
    store.current()
    store.start(rel)
    for state in ("PAUSED", "ACTIVE", "COMPLETE"):
        store.set_phased(rel, state)
    check("appstore_connect", r"https://api\.appstoreconnect\.apple\.com", rec)


# ---------------- Google Play (discovery documents) ----------------

@pytest.fixture(scope="module")
def discovery():
    from googleapiclient.discovery import build
    from google.auth.credentials import AnonymousCredentials
    pub = build("androidpublisher", "v3", credentials=AnonymousCredentials(), static_discovery=True)._rootDesc
    rep = build("playdeveloperreporting", "v1beta1", credentials=AnonymousCredentials(), static_discovery=True)._rootDesc
    return pub, rep


@pytest.mark.parametrize("track", ["production", "alpha", "internal"])
def test_play_track_update_contract(track, discovery, monkeypatch):
    from release_bot.play import Play
    sys.path.insert(0, str(Path(__file__).parent))
    from test_integrations import FakeService
    monkeypatch.setattr("release_bot.play.MediaFileUpload", lambda *a, **k: None)
    svc = FakeService()
    Play("com.acme", track, service=svc).upload_and_start("a.aab", "1.0.0", 0.01, "Fixes", "en-US")
    assert not v.check_discovery(svc.e.track_body, "Track", discovery[0])


def test_play_vitals_query_contract(discovery):
    from release_bot.vitals import Vitals
    bodies = []

    class Res:
        def __init__(self, kind):
            self.kind = kind
        def get(self, name):
            class X:
                def execute(self_inner):
                    return {"freshnessInfo": {"freshnesses": [{"aggregationPeriod": "DAILY",
                                              "latestEndTime": {"year": 2026, "month": 10, "day": 10}}]}}
            return X()
        def query(self, name, body):
            bodies.append((self.kind, body))
            class X:
                def execute(self_inner):
                    return {"rows": []}
            return X()

    class Svc:
        def vitals(self):
            class V:
                def crashrate(self):
                    return Res("GooglePlayDeveloperReportingV1beta1QueryCrashRateMetricSetRequest")
                def anrrate(self):
                    return Res("GooglePlayDeveloperReportingV1beta1QueryAnrRateMetricSetRequest")
            return V()

    Vitals("com.acme", service=Svc()).latest_by_version()
    assert len(bodies) == 2
    for schema, body in bodies:
        assert not v.check_discovery(body, schema, discovery[1]), (schema, body)


# ---------------- the validator itself catches mistakes ----------------

@pytest.mark.parametrize("spec_name, base, call, expected", [
    ("appstore_connect", r"https://api\.appstoreconnect\.apple\.com",
     v.Call("PATCH", "https://api.appstoreconnect.apple.com/v1/appStoreVersionPhasedReleases/p1", [],
            {"data": {"type": "appStoreVersionPhasedReleases", "id": "p1", "attributes": {"phasedReleaseState": "HALTED"}}}),
     "not in"),                                                     # bad enum value
    ("appstore_connect", r"https://api\.appstoreconnect\.apple\.com",
     v.Call("POST", "https://api.appstoreconnect.apple.com/v1/appStoreVersions", [],
            {"data": {"type": "appStoreVersions", "attributes": {"platform": "IOS", "versionString": "1.0", "releaseKind": "MANUAL"},
                      "relationships": {"app": {"data": {"type": "apps", "id": "a"}}}}}),
     "unknown field 'releaseKind'"),                                 # misspelt field
    ("appstore_connect", r"https://api\.appstoreconnect\.apple\.com",
     v.Call("GET", "https://api.appstoreconnect.apple.com/v1/builds", [("filter[buildNumber]", "12")]),
     "unknown query parameter"),                                    # wrong filter name
    ("incident_io", r"https://api\.incident\.io",
     v.Call("POST", "https://api.incident.io/v2/incidents", [], {"name": "x", "mode": "standard"}),
     "missing required field 'idempotency_key'"),                   # required field missing
    ("pagerduty_events", r"https://events\.pagerduty\.com/v2",
     v.Call("POST", "https://events.pagerduty.com/v2/enqueue", [],
            {"routing_key": "r", "event_action": "fire", "payload": {"summary": "s", "source": "x", "severity": "critical"}}),
     "not in"),                                                     # bad event_action
    ("incident_io", r"https://api\.incident\.io",
     v.Call("GET", "https://api.incident.io/v2/incident"), "path not in spec"),   # wrong path
])
def test_validator_catches(spec_name, base, call, expected):
    errors = v.validate(v.load_spec(spec_name), base, call)
    assert any(expected in e for e in errors), errors


def test_discovery_check_catches_unknown_play_field(discovery):
    bad = {"track": "production", "releases": [{"name": "1.0", "status": "inProgress", "userFractionn": 0.1}]}
    assert any("userFractionn" in e for e in v.check_discovery(bad, "Track", discovery[0]))
    assert any("not in" in e for e in v.check_discovery({"releases": [{"status": "paused"}]}, "Track", discovery[0]))


def test_play_halt_full_release_contract(discovery):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_full_release_halt import Svc
    from release_bot.play import Play
    svc = Svc([{"name": "2.0.0", "versionCodes": ["200"], "status": "completed"}])
    Play("com.acme", "production", service=svc).halt(include_completed=True)
    assert not v.check_discovery(svc.e.body, "Track", discovery[0])
