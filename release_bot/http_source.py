"""Generic HTTP/JSON health source: turn any API that returns a number into a metric.

    health:
      sources:
        http:
          checks:
            api_5xx_rate:
              url: "https://prom.example.com/api/v1/query?query=sum(rate(http_5xx{app_version='{version}'}[1h]))"
              headers: {Authorization: "Bearer ${PROM_TOKEN}"}
              value: data.result[0].value[1]     # path to the number in the JSON response
              previous: true                     # also fetch it for the previous version
      rules:
        - {name: API 5xx, source: http, metric: api_5xx_rate, above: 2.5, action: halt}

URL, headers and body may use {version}, {version_code}, {previous_version},
{previous_version_code}, {package}, {app} and {platform}. `${NAME}` is read from
the environment, or from the RELEASE_BOT_HTTP_ENV secret (KEY=value lines), so
reusable workflows don't need to know your secret names.
"""

import json
import os
import re
from urllib.parse import quote

import requests

_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def env_lookup(env=None) -> dict:
    env = dict(env if env is not None else os.environ)
    for line in (env.get("RELEASE_BOT_HTTP_ENV") or "").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def substitute_env(text: str, env: dict) -> str:
    def repl(m):
        if m.group(1) not in env:
            raise KeyError(f"${{{m.group(1)}}} is not set (add it to RELEASE_BOT_HTTP_ENV)")
        return env[m.group(1)]
    return re.sub(r"\$\{([A-Z0-9_]+)\}", repl, text)


def fill(template: str, ctx: dict, url: bool = False) -> str:
    """Replace {placeholders}; values are URL-encoded inside URLs."""
    def repl(m):
        key = m.group(1)
        if key not in ctx or ctx[key] is None:
            return m.group(0)
        v = str(ctx[key])
        return quote(v, safe="") if url else v
    return re.sub(r"\{([a-z_]+)\}", repl, template)


def extract(data, path: str):
    """`data.result[0].value[1]` → that element, or None if any step is missing."""
    cur = data
    for name, index in _PATH_TOKEN.findall(path):
        try:
            cur = cur[int(index)] if index else cur[name]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return cur


class HttpSource:
    def __init__(self, cfg: dict, env=None, session=None):
        self.checks = (cfg or {}).get("checks", {}) or {}
        self.env = env_lookup(env)
        self.http = session or requests.Session()

    def _fetch(self, check: dict, ctx: dict) -> float | None:
        url = substitute_env(fill(check["url"], ctx, url=True), self.env)
        headers = {k: substitute_env(fill(str(v), ctx), self.env) for k, v in (check.get("headers") or {}).items()}
        body = check.get("body")
        kwargs = {"headers": headers, "timeout": int(check.get("timeout", 30))}
        if body is not None:
            kwargs["json"] = json.loads(substitute_env(fill(json.dumps(body), ctx), self.env))
        resp = self.http.request(check.get("method", "GET").upper(), url, **kwargs)
        resp.raise_for_status()
        value = extract(resp.json(), check["value"])
        return None if value is None else float(value)

    def metrics(self, ctx: dict) -> dict:
        """{"new": {check: value}, "prev": {check: value}} for the rule engine."""
        new, prev = {}, {}
        prev_ctx = {**ctx, "version": ctx.get("previous_version"), "version_code": ctx.get("previous_version_code")}
        for name, check in self.checks.items():
            new[name] = self._fetch(check, ctx)
            if check.get("previous") and ctx.get("previous_version"):
                prev[name] = self._fetch(check, prev_ctx)
        return {"new": new, "prev": prev}
