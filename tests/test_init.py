"""release_bot init: detects the repo, writes a valid release-bot.yml and the caller workflows."""

import subprocess

import yaml

from release_bot import cli, config, schema


def make_repo(tmp_path, kind="android", tag="v2.3.0"):
    root = tmp_path / "app-repo"
    base = root / ("android" if kind == "react-native" else "")
    (base / "app").mkdir(parents=True)
    (base / "gradlew").write_text("#!/bin/sh\n")
    (base / "app" / "build.gradle.kts").write_text('android {\n  defaultConfig {\n    applicationId = "com.acme.shop"\n  }\n}\n')
    if kind == "react-native":
        (root / "package.json").write_text('{"dependencies": {"react-native": "0.80.0"}}')
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    if tag:
        subprocess.run(["git", "-C", str(root), "tag", tag], check=True)
    return root


def test_init_native_android(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert cli.main(["init", "--dir", str(root), "--yes"]) == 0
    raw = yaml.safe_load((root / "release-bot.yml").read_text())
    assert schema.errors(raw) == []
    cfg = config.resolve(config.load(root / "release-bot.yml"))
    assert cfg["package_name"] == "com.acme.shop" and cfg["app_id"] == "shop"
    assert cfg["build"]["project_dir"] == "." and cfg["tag_prefix"] == ""
    assert config.version_from_tag(cfg, "v2.4.0") == "2.4.0"
    wf = {p.name for p in (root / ".github" / "workflows").glob("*.yml")}
    assert {"android-submit.yml", "android-rollout.yml", "android-doctor.yml"} <= wf
    assert not any(n.startswith("sandbox-") for n in wf)
    out = capsys.readouterr().out
    assert "Detected: android app" in out and "pbpaste | gh secret set SLACK_BOT_TOKEN" in out
    assert cli.main(["--config", str(root / "release-bot.yml"), "validate"]) == 0


def test_init_react_native_ios_and_plain_tags(tmp_path):
    root = make_repo(tmp_path, kind="react-native", tag="2.3.0")
    assert cli.main(["init", "--dir", str(root), "--yes", "--platforms", "android,ios", "--schedule", "5-day"]) == 0
    cfg_ios = config.resolve(config.load(root / "release-bot.yml"), None, "ios")
    assert cfg_ios["environment"] == "appstore-production" and cfg_ios["build"]["project_dir"] == "android"
    assert config.version_from_tag(cfg_ios, "2.4.0") == "2.4.0"
    assert cfg_ios["rollout"]["platforms"] == "separate"          # 5% → 25% isn't possible on iOS
    assert cli.main(["--config", str(root / "release-bot.yml"), "validate"]) == 0


def test_init_refuses_to_overwrite(tmp_path):
    root = make_repo(tmp_path)
    (root / "release-bot.yml").write_text("existing: true\n")
    assert cli.main(["init", "--dir", str(root), "--yes"]) == 1
    assert (root / "release-bot.yml").read_text() == "existing: true\n"
