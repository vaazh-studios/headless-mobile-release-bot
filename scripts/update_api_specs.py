"""Refresh the API spec snapshots used by the contract tests.

Downloads each provider's published OpenAPI spec, keeps only the operations the
bot calls (plus the schemas they reference) and writes them to
tests/contracts/specs/<provider>.json with the source URL and fetch date.

    python scripts/update_api_specs.py

Providers without a published machine-readable spec (Optimizely, Amplitude,
Microsoft Teams Workflows) aren't covered; their docs are linked in docs/integrations.md.
Google Play's discovery documents ship with google-api-python-client, so they
are read from there in the tests instead.
"""

import io
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

import requests
import yaml

OUT = Path(__file__).resolve().parents[1] / "tests" / "contracts" / "specs"

INCIDENT_IO_TAGS = "https://docs.incident.io/openapi/tags/{tag}.json"

PROVIDERS = {
    "incident_io": {
        "sources": [INCIDENT_IO_TAGS.format(tag=t) for t in
                    ("incidents-v2", "alert-events-v2", "schedule-entries-v2", "severities-v1")],
        "ops": [("get", "/v2/incidents"), ("post", "/v2/incidents"),
                ("post", "/v2/alert_events/http/{alert_source_config_id}"),
                ("get", "/v2/schedule_entries"), ("get", "/v1/severities")],
    },
    "pagerduty_rest": {
        "sources": ["https://raw.githubusercontent.com/PagerDuty/api-schema/main/reference/REST/openapiv3.json"],
        "ops": [("get", "/incidents")],
    },
    "pagerduty_events": {
        "sources": ["https://raw.githubusercontent.com/PagerDuty/api-schema/main/reference/events-v2/openapiv3.json"],
        "ops": [("post", "/enqueue")],
    },
    "datadog": {
        "sources": ["https://raw.githubusercontent.com/DataDog/datadog-api-client-python/master/.generator/schemas/v1/openapi.yaml"],
        "ops": [("get", "/api/v1/monitor/search")],
    },
    "sentry": {
        "sources": ["https://raw.githubusercontent.com/getsentry/sentry-api-schema/main/openapi-derefed.json"],
        "ops": [("get", "/api/0/organizations/{organization_id_or_slug}/sessions/")],
    },
    "appstore_connect": {
        "sources": ["https://developer.apple.com/sample-code/app-store-connect/app-store-connect-openapi-specification.zip"],
        "ops": [("get", "/v1/apps"), ("get", "/v1/builds"), ("post", "/v1/appStoreVersions"),
                ("get", "/v1/apps/{id}/appStoreVersions"),
                ("get", "/v1/appStoreVersions/{id}/appStoreVersionLocalizations"),
                ("patch", "/v1/appStoreVersionLocalizations/{id}"),
                ("post", "/v1/appStoreVersionPhasedReleases"), ("patch", "/v1/appStoreVersionPhasedReleases/{id}"),
                ("get", "/v1/reviewSubmissions"), ("post", "/v1/reviewSubmissions"),
                ("post", "/v1/reviewSubmissionItems"), ("patch", "/v1/reviewSubmissions/{id}"),
                ("post", "/v1/appStoreVersionReleaseRequests")],
    },
}


def load(url: str) -> dict:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            name = next(n for n in z.namelist() if n.endswith(".json"))
            return json.loads(z.read(name))
    if url.endswith((".yaml", ".yml")):
        return yaml.safe_load(resp.text)
    return resp.json()


def merge(specs: list[dict]) -> dict:
    out = {"paths": {}, "components": {"schemas": {}, "parameters": {}}}
    for s in specs:
        for p, ops in s.get("paths", {}).items():
            out["paths"].setdefault(p, {}).update(ops)
        for kind in ("schemas", "parameters"):
            out["components"][kind].update(s.get("components", {}).get(kind, {}))
    return out


def refs(node, found: set):
    if isinstance(node, dict):
        r = node.get("$ref")
        if isinstance(r, str) and r.startswith("#/components/"):
            found.add(r)
        for v in node.values():
            refs(v, found)
    elif isinstance(node, list):
        for v in node:
            refs(v, found)


def subset(spec: dict, ops: list[tuple[str, str]]) -> dict:
    paths = {}
    for method, path in ops:
        op = spec["paths"].get(path, {}).get(method)
        if op is None:
            raise SystemExit(f"spec has no {method.upper()} {path}")
        shared = spec["paths"][path].get("parameters")
        if shared:
            op = {**op, "parameters": shared + op.get("parameters", [])}
        paths.setdefault(path, {})[method] = op
    keep: set = set()
    refs(paths, keep)
    pending = list(keep)
    comps: dict = {}
    while pending:
        r = pending.pop()
        _, _, kind, name = r.split("/", 3)
        node = spec.get("components", {}).get(kind, {}).get(name)
        if node is None or name in comps.get(kind, {}):
            continue
        comps.setdefault(kind, {})[name] = node
        more: set = set()
        refs(node, more)
        pending += [m for m in more if m not in keep]
        keep |= more
    return {"paths": paths, "components": comps}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, p in PROVIDERS.items():
        spec = merge([load(u) for u in p["sources"]])
        snap = subset(spec, p["ops"])
        snap["x-source"] = p["sources"]
        snap["x-fetched"] = date.today().isoformat()
        (OUT / f"{name}.json").write_text(json.dumps(snap, indent=1, sort_keys=True))
        print(f"{name}: {len(p['ops'])} operations, {sum(len(v) for v in snap['components'].values())} components")
    return 0


if __name__ == "__main__":
    sys.exit(main())
