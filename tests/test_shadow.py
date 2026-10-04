"""Shadow mode: real reads, no store writes, labelled Slack posts, no repeats."""

import pytest

from release_bot import cli
from release_bot.slack import ShadowSlack


class Recorder:
    def __init__(self):
        self.posts, self.channels = [], []

    def post(self, key, text, root_text=None):
        self.posts.append((key, text, root_text))

    def announce(self, channel, text):
        self.channels.append((channel, text))

    def thread_contains(self, key, text):
        return any(k == key and text in t for k, t, _ in self.posts)

    def usergroup_members(self, g):
        return set()


def test_shadow_labels_separate_thread_and_dedupes():
    inner = Recorder()
    s = ShadowSlack(inner)
    s.post("shop 2.0.0", "🛑 Rollout HALTED at 2%")
    s.announce("C3", "auto-halted")
    s.post("shop 2.0.0", "🛑 Rollout HALTED at 2%")          # next run, same decision
    s.announce("C3", "auto-halted")
    assert len(inner.posts) == 1 and len(inner.channels) == 1
    key, text, root = inner.posts[0]
    assert key == "shop 2.0.0 (shadow)" and text.startswith("🫥 *Shadow mode*") and root.startswith("🫥 Shadow run")
    assert inner.channels[0][1].startswith("🫥 Shadow mode")


def test_shadow_mode_from_env_or_config(monkeypatch):
    monkeypatch.delenv("RELEASE_BOT_MODE", raising=False)
    assert cli.shadow_mode({"mode": "shadow"}) and not cli.shadow_mode({})
    monkeypatch.setenv("RELEASE_BOT_MODE", "shadow")
    assert cli.shadow_mode({})
    monkeypatch.setenv("RELEASE_BOT_MODE", "yolo")
    with pytest.raises(Exception, match="'live' or 'shadow'"):
        cli.shadow_mode({})


def test_shadow_never_writes_to_mock_play(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    monkeypatch.setenv("RELEASE_BOT_MOCK", "true")
    monkeypatch.setenv("RELEASE_BOT_MODE", "shadow")
    monkeypatch.setenv("MOCK_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("MOCK_REVIEW_MINUTES", "0")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    cfg = str(Path(__file__).resolve().parent / "release-bot.test.yml")
    cli.main(["--config", cfg, "submit", "--aab", "x.aab", "--version", "4.12.0"])
    state = json.loads((tmp_path / "s.json").read_text())
    assert [r["name"] for r in state["releases"]] == ["0.1.0"]   # nothing uploaded


def test_shadow_repeat_detected_despite_different_run_links():
    inner = Recorder()
    s = ShadowSlack(inner)
    s.post("chat 1.0.0", "⬆️ Rollout 2% → 100%\n<https://github.com/o/r/actions/runs/1|View run>")
    s.post("chat 1.0.0", "⬆️ Rollout 2% → 100%\n<https://github.com/o/r/actions/runs/2|View run>")
    assert len(inner.posts) == 1


def test_slack_thread_contains_unescapes_slack_text():
    from release_bot.slack import Slack

    class Resp:
        def __init__(self, d): self.d = d
        def raise_for_status(self): pass
        def json(self): return self.d

    class Http:
        def get(self, url, params, headers, timeout):
            if url.endswith("conversations.history"):
                return Resp({"ok": True, "messages": [{"text": "root _[android-release 1.0.0]_", "ts": "1.0"}]})
            return Resp({"ok": True, "messages": [{"text": "grafana firing: p95 latency &gt; 1.5s"}]})

    slack = Slack("xoxb-test", "C1", session=Http())
    assert slack.thread_contains("1.0.0", "p95 latency > 1.5s")
