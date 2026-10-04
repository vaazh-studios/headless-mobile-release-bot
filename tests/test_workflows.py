"""Workflow wiring: callers may only pass inputs and secrets the reusable workflow declares
(GitHub refuses to start the run otherwise: 'startup_failure')."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CALLERS = sorted((ROOT / "examples" / "caller-workflows").glob("*.yml")) + sorted(WORKFLOWS.glob("android-*.yml")) \
    + sorted(WORKFLOWS.glob("sandbox-inject-incident.yml"))


def load(p: Path) -> dict:
    data = yaml.safe_load(p.read_text())
    data["on"] = data.pop(True, data.get("on"))   # YAML 1.1 reads `on:` as True
    return data


def reusable(uses: str) -> Path:
    name = uses.split("/.github/workflows/")[-1].split("@")[0]
    return WORKFLOWS / name


@pytest.mark.parametrize("caller", CALLERS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_callers_match_reusable_workflow_interface(caller):
    for job_name, job in load(caller)["jobs"].items():
        uses = job.get("uses", "")
        if "rw-" not in uses:
            continue
        call = load(reusable(uses))["on"]["workflow_call"]
        inputs, secrets = set(call.get("inputs") or {}), set(call.get("secrets") or {})
        passed_inputs = set((job.get("with") or {}).keys())
        passed = job.get("secrets")
        assert passed_inputs <= inputs, f"{caller.name}: undeclared inputs {passed_inputs - inputs}"
        if isinstance(passed, dict):
            assert set(passed) <= secrets, f"{caller.name}: undeclared secrets {set(passed) - secrets}"
        required = {k for k, v in (call.get("inputs") or {}).items() if v.get("required")}
        assert required <= passed_inputs, f"{caller.name}: missing required inputs {required - passed_inputs}"
