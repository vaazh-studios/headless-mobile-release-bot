"""Claude Code plugin + portable skills + the release-bot.yml validation hook."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "validate-release-bot.sh"


def test_plugin_and_marketplace_manifests():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert re.fullmatch(r"[a-z0-9-]+", plugin["name"])
    assert market["plugins"][0]["name"] == plugin["name"] and market["plugins"][0]["source"] == "./"
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    assert hooks["hooks"]["PostToolUse"][0]["matcher"] == "Write|Edit"
    assert "${CLAUDE_PLUGIN_ROOT}" in hooks["hooks"]["PostToolUse"][0]["hooks"][0]["command"]


@pytest.mark.parametrize("skill", sorted((ROOT / "skills").glob("*/SKILL.md")), ids=lambda p: p.parent.name)
def test_skill_frontmatter(skill):
    text = skill.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "missing frontmatter"
    meta = dict(line.split(": ", 1) for line in m.group(1).splitlines())
    assert meta["name"].startswith("release-bot") and 20 < len(meta["description"]) <= 1024
    if skill.parent.name != "release-status":
        assert "Never ask for, print, store or paste secret values" in text


def run_hook(file_path: str):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT),
           "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run([str(HOOK)], input=json.dumps({"tool_input": {"file_path": file_path}}),
                          text=True, capture_output=True, env=env)


def test_hook_ignores_other_files():
    assert run_hook("/tmp/README.md").returncode == 0


def test_hook_passes_valid_config():
    r = run_hook(str(ROOT / "release-bot.yml"))
    assert r.returncode == 0, r.stderr


def test_hook_blocks_invalid_config_with_feedback(tmp_path):
    bad = tmp_path / "release-bot.yml"
    bad.write_text((ROOT / "release-bot.yml").read_text().replace("percent: 2}", "percentt: 2}"))
    r = run_hook(str(bad))
    assert r.returncode == 2 and "release-bot.yml is invalid" in r.stderr and "percentt" in r.stderr


def test_init_skills_for_codex_and_claude(tmp_path):
    from release_bot import cli
    sys.path.insert(0, str(Path(__file__).parent))
    from test_init import make_repo
    root = make_repo(tmp_path)
    assert cli.main(["init", "--dir", str(root), "--yes", "--skills"]) == 0
    for base in (".agents/skills", ".claude/skills"):
        assert (root / base / "release-bot-setup" / "SKILL.md").exists()
        assert (root / base / "release-bot-troubleshoot" / "SKILL.md").exists()


def test_plugin_bundles_mcp_server():
    servers = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    assert servers["release-bot"]["args"] == ["-m", "release_bot.mcp_server"]
    assert "${CLAUDE_PLUGIN_ROOT}" in servers["release-bot"]["env"]["PYTHONPATH"]
