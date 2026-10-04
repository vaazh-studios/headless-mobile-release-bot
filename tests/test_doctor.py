"""doctor: every check, with the fix it suggests, using fake clients."""

from pathlib import Path

from release_bot import config, doctor
from release_bot.play import TrackState

HERE = Path(__file__).resolve().parent
CFG = config.resolve(config.load(HERE / "release-bot.teams.test.yml"), "checkout-team")


class HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


class Play:
    def track_state(self):
        return TrackState([{"name": "4.11.0", "versionCodes": ["1"], "status": "completed"}])


class Vitals:
    def latest_by_version(self):
        return {1: {}}


class Grafana:
    def active_alerts(self):
        return []


def slack(responses):
    def call(method, **params):
        return responses.get((method, params.get("channel") or params.get("usergroup")), responses.get(method, {"ok": False, "error": "unexpected"}))
    return call


GOOD_SLACK = {
    "auth.test": {"ok": True, "user": "release-bot", "team": "test"},
    ("conversations.info", "C1"): {"ok": True, "channel": {"name": "android-releases", "is_member": True}},
    ("conversations.info", "C2"): {"ok": True, "channel": {"name": "announce", "is_member": False, "is_private": False}},
    ("conversations.info", "C3"): {"ok": True, "channel": {"name": "slo", "is_member": False, "is_private": True}},
}


def factory(**over):
    env = {"SLACK_BOT_TOKEN": "x", "GRAFANA_URL": "https://g", "GRAFANA_TOKEN": "t"}
    base = dict(google_identity=lambda: "bot@p.iam.gserviceaccount.com", play=lambda c: Play(),
                vitals=lambda c: Vitals(), bigquery_table=lambda *a: None,
                grafana=lambda *a: Grafana(), slack_call=slack(GOOD_SLACK), env=env)
    base.update(over)
    return doctor.Factory(**base)


def by_name(checks):
    return {c.name: c for c in checks}


def test_happy_path_except_private_channel():
    checks = by_name(doctor.run(CFG, factory()))
    assert checks["Google login"].status == doctor.OK
    assert "production track readable" in checks["Play publishing"].detail
    assert checks["Crashlytics (BigQuery)"].status == doctor.OK
    assert checks["Slack release channel"].status == doctor.OK
    assert "joins on first post" in checks["Slack announcement channel"].detail
    alerts = checks["Slack alerts channel"]
    assert alerts.status == doctor.FAIL and "/invite" in alerts.fix


def test_play_403_says_which_permission_to_grant():
    def denied(cfg):
        class P:
            def track_state(self):
                raise HttpError(403)
        return P()
    c = by_name(doctor.run(CFG, factory(play=denied)))["Play publishing"]
    assert c.status == doctor.FAIL
    assert "bot@p.iam.gserviceaccount.com" in c.fix and "Release to production" in c.fix


def test_missing_google_login_stops_store_checks():
    def none():
        raise RuntimeError("Could not automatically determine credentials")
    checks = by_name(doctor.run(CFG, factory(google_identity=none)))
    assert checks["Google login"].status == doctor.FAIL and "play-main" in checks["Google login"].fix
    assert "Play publishing" not in checks


def test_crashlytics_table_missing_suggests_streaming_export():
    def missing(*a):
        raise HttpError(404)
    c = by_name(doctor.run(CFG, factory(bigquery_table=missing)))["Crashlytics (BigQuery)"]
    assert "streaming" in c.fix


def test_grafana_needs_url_and_token():
    f = factory()
    f.env = {"SLACK_BOT_TOKEN": "x"}
    assert by_name(doctor.run(CFG, f))["Grafana"].status == doctor.FAIL


def test_no_slack_token_is_a_warning_not_a_failure():
    f = factory()
    f.env = {"GRAFANA_URL": "https://g", "GRAFANA_TOKEN": "t"}
    c = by_name(doctor.run(CFG, f))["Slack"]
    assert c.status == doctor.WARN


def test_mock_mode_skips_store_checks():
    f = factory()
    f.env = {"RELEASE_BOT_MOCK": "true", "SLACK_BOT_TOKEN": "x", "MOCK_ON_DUTY": "me",
             "GRAFANA_URL": "https://g", "GRAFANA_TOKEN": "t"}
    checks = by_name(doctor.run(CFG, f))
    assert "simulated" in checks["store"].detail and "Play publishing" not in checks
    assert checks["Grafana"].status == doctor.OK          # third-party integrations still checked for real


def test_cli_doctor_exit_code(capsys):
    from release_bot import cli
    raw = config.load(HERE / "release-bot.teams.test.yml")
    assert cli.cmd_doctor(raw, "checkout-team", factory()) == 1      # private alerts channel
    good = dict(GOOD_SLACK)
    good[("conversations.info", "C3")] = {"ok": True, "channel": {"name": "slo", "is_member": True}}
    assert cli.cmd_doctor(raw, "checkout-team", factory(slack_call=slack(good))) == 0
    assert "All good." in capsys.readouterr().out


def test_missing_channels_read_scope_is_a_warning():
    resp = dict(GOOD_SLACK)
    resp[("conversations.info", "C1")] = {"ok": False, "error": "missing_scope"}
    c = by_name(doctor.run(CFG, factory(slack_call=slack(resp))))["Slack release channel"]
    assert c.status == doctor.WARN and "channels:read" in c.fix
