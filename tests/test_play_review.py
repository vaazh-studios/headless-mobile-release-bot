"""Android: the bot follows Play's review state (tracks.releases.list) like it follows App Store review."""

import json

from release_bot.play import Play
from test_full_release_halt import Exec, Svc
from test_mock_mode import sandbox  # noqa: F401 — fixture


def staged(run):
    return next((r for r in json.loads(run.state.read_text())["releases"] if r["status"] in ("inProgress", "halted")), None)


def test_waits_in_review_then_announces_approval(sandbox, capsys):
    sandbox("submit", "--aab", "x.aab", "--version", "4.12.0")
    sandbox("check")
    sandbox("advance")
    out = capsys.readouterr().out
    assert out.count("is in Play review; waiting") == 2
    assert staged(sandbox)["userFraction"] == 0.02

    sandbox.advance_time(minutes=10)                     # mock Google approves
    sandbox("check")
    out = capsys.readouterr().out
    assert "Approved by Google: 4.12.0 is live at 2%" in out
    sandbox("advance")
    assert staged(sandbox)["userFraction"] == 0.20


def test_rejection_alerts_and_stops(sandbox, capsys):
    sandbox("submit", "--aab", "x.aab", "--version", "4.12.0")
    sandbox("mock-inject", "--incident", "play-rejected")
    sandbox.advance_time(minutes=10)
    sandbox("advance")
    out = capsys.readouterr().out
    assert "rejected by Google" in out and "supersede unfinished rollout" in out
    assert staged(sandbox)["userFraction"] == 0.02
    assert sandbox("precheck-submit") == 1                # needs supersede
    assert sandbox("precheck-submit", "--force") == 0


def test_managed_publishing_waits_for_publish(sandbox, capsys):
    sandbox("submit", "--aab", "x.aab", "--version", "4.12.0")
    sandbox("mock-inject", "--incident", "play-awaiting-publish")
    sandbox.advance_time(minutes=10)
    sandbox("advance")
    assert "click *Publish changes*" in capsys.readouterr().out
    assert staged(sandbox)["userFraction"] == 0.02


class TracksSvc(Svc):
    def __init__(self, summaries=None, error=None):
        super().__init__([])
        self.summaries, self.error, self.parent = summaries, error, None
    def applications(self): return self
    def tracks(self): return self
    def releases(self): return self
    def list(self, parent):
        self.parent = parent
        if self.error:
            raise self.error
        return Exec({"releases": self.summaries})


def test_play_review_state_matches_by_version_code():
    svc = TracksSvc([
        {"releaseName": "4.11.0", "activeArtifacts": [{"versionCode": 41100}], "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_PUBLISHED"},
        {"releaseName": "4.12.0", "activeArtifacts": [{"versionCode": "41200"}], "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_IN_REVIEW"},
    ])
    play = Play("com.x", "production", service=svc)
    assert play.review_state({"name": "4.12.0", "versionCodes": ["41200"]}) == "in_review"
    assert play.review_state({"name": "4.11.0", "versionCodes": ["41100"]}) == "published"
    assert play.review_state({"name": "9.9.9", "versionCodes": ["99999"]}) is None
    assert svc.parent == "applications/com.x/tracks/production"


def test_play_review_state_falls_back_when_unavailable(capsys):
    play = Play("com.x", "production", service=TracksSvc(error=RuntimeError("403 forbidden")))
    assert play.review_state({"name": "4.12.0", "versionCodes": ["41200"]}) is None
    assert "going by health data" in capsys.readouterr().out


class MemorySlack:
    def __init__(self):
        self.posts = []
    def post(self, key, text, root_text=None):
        self.posts.append(text)
    def thread_contains(self, key, needle):
        return any(needle in p for p in self.posts)
    def announce(self, channel, text):
        self.posts.append("ANNOUNCE " + text)
    def alert(self, channel, text):
        self.posts.append("ALERT " + text)


class ReviewPlay:
    def __init__(self, state):
        self.state = state
    def review_state(self, release):
        return self.state


def gate(state, slack):
    from release_bot import cli
    cfg = {"app_id": "shop", "display_name": "Shop", "platform": "android",
           "slack": {"hero_usergroup_id": "S1", "announce_channel_id": "C2", "alerts_channel_id": "C3"}}
    deps = cli.Deps(cfg=cfg, play=ReviewPlay(state), vitals=None, crashlytics=None, grafana=None, slack=slack)
    return cli._play_review_gate(deps, {"name": "4.12.0", "versionCodes": ["41200"], "userFraction": 0.02})


def test_review_messages_are_posted_once():
    slack = MemorySlack()
    assert gate("published", slack) is False and gate("published", slack) is False
    assert sum("Approved by Google" in p for p in slack.posts) == 1
    slack = MemorySlack()
    assert gate("not_approved", slack) is True and gate("not_approved", slack) is True
    assert sum("rejected by Google" in p for p in slack.posts) == 1
    assert gate("in_review", MemorySlack()) is True
    assert gate(None, MemorySlack()) is False          # unknown: fall back to health data
