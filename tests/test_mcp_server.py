"""MCP server: status parsing, previews before release actions, workflow dispatch via gh."""

import asyncio
import json
import subprocess

import pytest

from release_bot import mcp_server as srv


class FakeGh:
    def __init__(self):
        self.calls = []
    def __call__(self, cmd, capture_output=True, text=True, cwd=None):
        self.calls.append(cmd)
        args = cmd[1:]
        out = ""
        if args[:2] == ["run", "list"]:
            wf = args[args.index("--workflow") + 1]
            out = json.dumps([{"databaseId": 11 if "rollout" in wf else 22, "createdAt": "2026-10-05T07:00:00Z",
                               "conclusion": "success", "url": f"https://github.com/o/r/actions/runs/{11 if 'rollout' in wf else 22}"}])
        elif args[:2] == ["run", "view"] and args[2] == "11":
            out = ("release / shop · android\tadvance\t2026-10-05T07:00:01Z [slack] ⬆️ Rollout 2% → 20%\n"
                   "release / shop · ios\tadvance\t2026-10-05T07:00:02Z 📈 Apple's phased release: day 3, 5% of users.\n"
                   "release / chat · android\tadvance\t2026-10-05T07:00:03Z At 100%; nothing due today (schedule: …).\n")
        elif args[:2] == ["run", "view"] and args[2] == "22":
            out = "release / partner-app · android\tcheck\t2026-10-05T07:10:00Z 🛑 *Rollout HALTED* at 20%.\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture(autouse=True)
def repo_env(monkeypatch):
    monkeypatch.setenv("RELEASE_BOT_REPO", "o/r")


def test_release_status_summarizes_each_app_and_platform():
    text = srv.release_status(run=FakeGh())
    assert "shop · android: Rollout 2% → 20%" in text
    assert "shop · ios: Apple's phased release: day 3, 5% of users." in text
    assert "partner-app · android: Rollout HALTED at 20%." in text.replace("*", "")
    assert "https://github.com/o/r/actions/runs/11" in text


def test_submit_and_resume_preview_until_confirmed():
    gh = FakeGh()
    out = srv.submit("shop", "v2.3.0", "ios", run=gh)
    assert "Preview" in out and "confirm=true" in out and gh.calls == []
    srv.submit("shop", "v2.3.0", "ios", confirm=True, run=gh)
    assert gh.calls[-1] == ["gh", "workflow", "run", "android-submit.yml", "-R", "o/r",
                            "-f", "app=shop", "-f", "tag=v2.3.0", "-f", "platform=ios"]
    assert "Preview" in srv.resume("shop", "backend fixed", run=gh)
    assert "reason is required" in srv.resume("shop", " ", confirm=True, run=gh)


def test_halt_needs_reason_and_starts_halt_workflow(monkeypatch):
    gh = FakeGh()
    assert "reason is required" in srv.halt("shop", "", run=gh) and gh.calls == []
    monkeypatch.setenv("RELEASE_BOT_WORKFLOW_HALT", "my-halt.yml")
    srv.halt("shop", "crash spike", run=gh)
    assert gh.calls[-1][:4] == ["gh", "workflow", "run", "my-halt.yml"]
    assert ["-f", "reason=crash spike"] == gh.calls[-1][-2:]


def test_mcp_server_lists_tools_with_read_only_hints():
    pytest.importorskip("mcp")
    tools = {t.name: t for t in asyncio.run(srv.build_server().list_tools())}
    assert {"release_status", "plan", "validate", "doctor", "submit_release", "halt_rollout", "resume_rollout"} <= set(tools)
    assert tools["plan"].annotations.read_only_hint is True
    assert tools["submit_release"].annotations.read_only_hint is False
