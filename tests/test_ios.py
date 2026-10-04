"""iOS: Apple's phased release driven by the schedule, paused by halt rules (mock App Store)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from release_bot import cli, config
from release_bot.appstore import IOSRelease, AppStore

HERE = Path(__file__).resolve().parent
TEAMS = str(HERE / "release-bot.teams.test.yml")


def test_resolve_ios_environment_bundle_and_name():
    raw = config.load(TEAMS)
    ios = config.resolve(raw, "checkout-team", "ios")
    assert ios["platform"] == "ios" and ios["environment"] == "appstore-main"
    assert ios["bundle_id"] == "com.example.checkout"
    assert ios["display_name"] == "Checkout Team iOS"
    with pytest.raises(config.ConfigError, match="ios"):
        config.resolve(config.load(HERE / "release-bot.multi.test.yml"), "shop", "ios")   # android-only app


def test_apps_matrix_has_one_row_per_platform(capsys):
    cli.main(["--config", TEAMS, "apps"])
    rows = json.loads(capsys.readouterr().out)
    assert {"app": "checkout-team", "platform": "ios", "environment": "appstore-main",
            "account": "main", "state": "checkout-team-ios"} in rows
    assert sum(r["app"] == "checkout-team" for r in rows) == 2


def test_ios_release_fraction_follows_apple_days():
    assert IOSRelease("1.0", "v", "WAITING_FOR_REVIEW").fraction is None
    assert IOSRelease("1.0", "v", "READY_FOR_DISTRIBUTION", "p", "ACTIVE", 3).fraction == 0.05
    assert IOSRelease("1.0", "v", "READY_FOR_DISTRIBUTION", "p", "COMPLETE", 4).fraction == 1.0


@pytest.fixture
def ios(tmp_path, monkeypatch):
    monkeypatch.setenv("RELEASE_BOT_MOCK", "true")
    monkeypatch.delenv("MOCK_STATE_FILE", raising=False)
    monkeypatch.delenv("RELEASE_BOT_MODE", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MOCK_ON_DUTY", "hero")
    monkeypatch.setenv("MOCK_REVIEW_MINUTES", "5")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    clock = {"now": datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(cli, "utcnow", lambda: clock["now"])

    def run(*argv):
        return cli.main(["--config", TEAMS, "--app", "checkout-team", "--platform", "ios", *argv])
    run.state = lambda: json.loads((tmp_path / ".mock-state" / "checkout-team-ios.json").read_text())["ios"]
    run.later = lambda **kw: clock.__setitem__("now", clock["now"] + timedelta(**kw))
    return run


def test_full_ios_phased_release(ios, capsys):
    assert ios("submit", "--aab", "-", "--version", "3.0.0") == 0
    assert ios.state()["state"] == "WAITING_FOR_REVIEW"
    ios("advance")                                   # still in review
    assert ios.state()["state"] == "WAITING_FOR_REVIEW"
    ios.later(minutes=10)
    ios("advance")                                   # approved → bot starts phased release (1%)
    s = ios.state()
    assert (s["state"], s["phased_state"], s["day"]) == ("READY_FOR_DISTRIBUTION", "ACTIVE", 1)
    ios("advance")                                   # Apple day 2 → 2% (a schedule step; nothing to do)
    assert ios.state()["day"] == 2
    ios("advance")                                   # next step is 100% → release to everyone
    assert ios.state()["phased_state"] == "COMPLETE"
    out = capsys.readouterr().out
    assert "checkout-team ios 3.0.0" in out and "Released to everyone" in out


def test_ios_halt_rule_pauses_never_removes(ios, capsys):
    ios("submit", "--aab", "-", "--version", "3.0.0")
    ios.later(minutes=10)
    ios("advance")
    ios("mock-inject", "--incident", "new-crash")
    ios("check")
    assert ios.state()["phased_state"] == "PAUSED"
    ios("advance")                                   # paused: the schedule never touches it
    assert ios.state()["phased_state"] == "PAUSED" and ios.state()["day"] == 1
    assert "PAUSED" in capsys.readouterr().out
    ios("mock-inject", "--incident", "none")
    assert ios("resume", "--reason", "fixed") == 0
    assert ios.state()["phased_state"] == "ACTIVE"


def test_ios_precheck_blocks_while_phased_release_runs(ios):
    ios("submit", "--aab", "-", "--version", "3.0.0")
    assert ios("precheck-submit") == 1
    assert ios("precheck-submit", "--force") == 0


class FakeHTTP:
    """Records App Store Connect calls; answers just enough for submit/start/pause."""
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url.split("/v1", 1)[1]
        self.calls.append((method, path, json))
        body = {"data": [{"id": "app1"}]} if path == "/apps" else \
               {"data": [{"id": "build1"}]} if path == "/builds" else \
               {"data": [{"id": "loc1", "attributes": {"locale": "en-US"}}]} if path.endswith("Localizations") else \
               {"data": []} if (method == "GET" and path == "/reviewSubmissions") else \
               {"data": {"id": f"new-{len(self.calls)}"}}

        class R:
            status_code, content, text = 200, b"x", ""
            def json(self_inner):
                return body
        return R()


def test_appstore_client_uses_review_submissions_and_phased_release(monkeypatch):
    monkeypatch.setattr("release_bot.appstore._token", lambda *a: "jwt")
    http = FakeHTTP()
    store = AppStore("com.example.app", session=http, env={"ASC_KEY_ID": "k", "ASC_ISSUER_ID": "i", "ASC_PRIVATE_KEY": "p"})
    rel = store.submit("3.0.0", "Fixes", "en-US")
    seq = [(m, p) for m, p, _ in http.calls]
    assert ("POST", "/appStoreVersions") in seq and ("POST", "/appStoreVersionPhasedReleases") in seq
    assert ("POST", "/reviewSubmissionItems") in seq and seq[-1][0] == "PATCH"
    version_body = next(b for m, p, b in http.calls if p == "/appStoreVersions")
    assert version_body["data"]["attributes"]["releaseType"] == "MANUAL"
    assert http.calls[-1][2]["data"]["attributes"] == {"submitted": True}
    store.set_phased(rel, "PAUSED")
    assert http.calls[-1][2]["data"]["attributes"] == {"phasedReleaseState": "PAUSED"}
    store.start(rel)
    assert http.calls[-1][1] == "/appStoreVersionReleaseRequests"


def test_appstore_token_is_es256_jwt():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import jwt
    from release_bot.appstore import _token
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    tok = _token("KEY123", "issuer-uuid", pem)
    header = jwt.get_unverified_header(tok)
    claims = jwt.decode(tok, key.public_key(), algorithms=["ES256"], audience="appstoreconnect-v1")
    assert header["kid"] == "KEY123" and header["alg"] == "ES256"
    assert claims["iss"] == "issuer-uuid" and claims["exp"] - claims["iat"] <= 20 * 60
