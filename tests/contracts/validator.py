"""Check recorded HTTP requests against a provider's OpenAPI spec snapshot.

Catches the bugs a mocked test can't: a misspelt path, an unknown query
parameter, a body field the API doesn't have, a missing required field, or an
enum value the API won't accept.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

SPECS = Path(__file__).parent / "specs"


def load_spec(name: str) -> dict:
    return json.loads((SPECS / f"{name}.json").read_text())


@dataclass
class Call:
    method: str
    url: str
    params: list = field(default_factory=list)
    body: object = None


class Response:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status
        self.content = b"x" if data is not None else b""
        self.text = json.dumps(data) if data is not None else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class Recorder:
    """A `requests.Session` stand-in that records calls and answers from `responder(call) -> dict`."""

    def __init__(self, responder):
        self.calls: list[Call] = []
        self.responder = responder

    def request(self, method, url, params=None, json=None, **_):
        items = list(params.items()) if isinstance(params, dict) else list(params or [])
        items += parse_qsl(urlsplit(url).query)
        call = Call(method.upper(), url.split("?")[0], items, json)
        self.calls.append(call)
        return Response(self.responder(call))

    def get(self, url, params=None, **kw):
        return self.request("GET", url, params=params, **kw)

    def post(self, url, json=None, **kw):
        return self.request("POST", url, json=json, **kw)

    def patch(self, url, json=None, **kw):
        return self.request("PATCH", url, json=json, **kw)


def _template_regex(path: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")


def _resolve(node, spec):
    while isinstance(node, dict) and "$ref" in node:
        _, _, kind, name = node["$ref"].split("/", 3)
        node = spec["components"][kind][name]
    return node


def _check_schema(value, schema, spec, where: str, errors: list):
    schema = _resolve(schema, spec) or {}
    if "allOf" in schema:
        merged = {"type": "object", "properties": {}, "required": []}
        for part in schema["allOf"]:
            part = _resolve(part, spec)
            merged["properties"].update(part.get("properties", {}))
            merged["required"] += part.get("required", [])
        schema = merged
    for key in ("oneOf", "anyOf"):
        if key in schema:
            options = []
            for opt in schema[key]:
                errs: list = []
                _check_schema(value, opt, spec, where, errs)
                if not errs:
                    return
                options.append(errs)
            errors.append(f"{where}: matches none of {key} ({options[0][0] if options and options[0] else ''})")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in {schema['enum']}")
    typ = schema.get("type")
    if typ == "object" or "properties" in schema:
        if not isinstance(value, dict):
            errors.append(f"{where}: expected an object")
            return
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{where}: missing required field '{req}'")
        for k, v in value.items():
            if k in props:
                _check_schema(v, props[k], spec, f"{where}.{k}", errors)
            elif props and schema.get("additionalProperties") in (None, False):
                errors.append(f"{where}: unknown field '{k}' (known: {sorted(props)})")
    elif typ == "array" and isinstance(value, list):
        for i, item in enumerate(value):
            _check_schema(item, schema.get("items", {}), spec, f"{where}[{i}]", errors)
    elif typ == "string" and not isinstance(value, str):
        errors.append(f"{where}: expected a string, got {type(value).__name__}")
    elif typ == "boolean" and not isinstance(value, bool):
        errors.append(f"{where}: expected a boolean")
    elif typ == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
        if not (isinstance(value, str) and value.isdigit()):
            errors.append(f"{where}: expected an integer")


def validate(spec: dict, base: str, call: Call) -> list[str]:
    """Errors for one call. `base` is a regex for the URL prefix before the spec's paths."""
    m = re.match(base, call.url)
    if not m:
        return [f"{call.url}: doesn't start with {base}"]
    path = call.url[m.end():] or "/"
    for template, ops in spec["paths"].items():
        if _template_regex(template).match(path):
            op = ops.get(call.method.lower())
            if op is None:
                return [f"{call.method} {template}: method not in spec (has {sorted(ops)})"]
            break
    else:
        return [f"{call.method} {path}: path not in spec snapshot"]

    errors = []
    known = {p["name"] for p in (_resolve(p, spec) for p in op.get("parameters", [])) if p.get("in") == "query"}
    for name, _ in call.params:
        if name not in known:
            errors.append(f"{call.method} {template}: unknown query parameter '{name}' (known: {sorted(known)})")
    for p in (_resolve(p, spec) for p in op.get("parameters", [])):
        if p.get("in") == "query" and p.get("required") and p["name"] not in {n for n, _ in call.params}:
            errors.append(f"{call.method} {template}: missing required query parameter '{p['name']}'")
    if call.body is not None:
        content = (op.get("requestBody") or {}).get("content", {})
        schema = (content.get("application/json") or next(iter(content.values()), {})).get("schema")
        if schema is None:
            errors.append(f"{call.method} {template}: sends a body but the spec defines none")
        else:
            _check_schema(call.body, schema, spec, f"{call.method} {template} body", errors)
    return errors


# ---------- Google discovery documents (Play) ----------

def check_discovery(value, schema_name: str, doc: dict, where: str = "") -> list[str]:
    errors: list = []
    schemas = doc["schemas"]

    def walk(v, s, w):
        if "$ref" in s:
            s = schemas[s["$ref"]]
        if "enum" in s and v not in s["enum"]:
            errors.append(f"{w}: {v!r} not in {s['enum']}")
        if s.get("type") == "object" or "properties" in s:
            if not isinstance(v, dict):
                errors.append(f"{w}: expected an object")
                return
            props = s.get("properties", {})
            for k, x in v.items():
                if k not in props:
                    errors.append(f"{w}: unknown field '{k}' (known: {sorted(props)})")
                else:
                    walk(x, props[k], f"{w}.{k}")
        elif s.get("type") == "array" and isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, s.get("items", {}), f"{w}[{i}]")

    walk(value, {"$ref": schema_name}, where or schema_name)
    return errors
