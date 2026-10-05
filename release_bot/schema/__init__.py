"""JSON Schema for release-bot.yml (also used by editors via yaml-language-server)."""

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("release-bot.schema.json")


def _normalize(node):
    """PyYAML reads a bare `on:` key as True (YAML 1.1); editors use YAML 1.2. Undo that."""
    if isinstance(node, dict):
        return {("on" if k is True else k): _normalize(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_normalize(v) for v in node]
    return node


def errors(raw: dict) -> list[str]:
    """Human-readable schema errors ('apps.shop.rollout.schedule[0].percent: …')."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import best_match
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    data = _normalize(raw)
    top = list(validator.iter_errors(data))
    if not top:
        return []
    out = []
    for err in top:
        # oneOf (multi-app vs single-app): report the branch that fits best.
        if err.validator == "oneOf" and err.context:
            branch = 0 if isinstance(data, dict) and "apps" in data else 1
            err = best_match([e for e in err.context if e.schema_path and e.schema_path[0] == branch] or err.context)
        path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path).lstrip(".")
        out.append(f"{path or '(top level)'}: {err.message}")
    return out
