"""Amplitude funnel conversion per app version (business guardrails).

    health:
      sources:
        amplitude:
          region: us                     # or eu
          days: 2                        # today and the previous day(s)
          version_property: version      # Amplitude's built-in app version property
          funnels:
            checkout_conversion: {steps: ["Checkout Started", "Purchase Completed"]}
          min_users: 300                 # users entering the funnel on the new version
      rules:
        - {name: Checkout drop, source: amplitude, metric: checkout_conversion, below_previous_by: "10%", action: hold}

Secrets: AMPLITUDE_API_KEY, AMPLITUDE_SECRET_KEY (Dashboard REST API, Basic auth).
One query per version and funnel (segment filter), so results never get mixed up.
"""

import json
from datetime import datetime, timedelta, timezone

import requests

HOSTS = {"us": "https://amplitude.com", "eu": "https://analytics.eu.amplitude.com"}


class Amplitude:
    def __init__(self, cfg: dict, api_key: str, secret_key: str, session=None, today=None):
        self.base = HOSTS.get(cfg.get("region", "us"), HOSTS["us"])
        self.funnels = cfg.get("funnels", {}) or {}
        self.days = int(cfg.get("days", 2))
        self.prop = cfg.get("version_property", "version")
        self.auth = (api_key, secret_key)
        self.http = session or requests.Session()
        self.today = today or datetime.now(timezone.utc).date()

    def _conversion(self, steps: list[str], version: str) -> tuple[float | None, int]:
        if not all(self.auth):
            raise RuntimeError("AMPLITUDE_API_KEY and AMPLITUDE_SECRET_KEY must be set")
        start = (self.today - timedelta(days=self.days - 1)).strftime("%Y%m%d")
        params = [("e", json.dumps({"event_type": s})) for s in steps]
        params += [("start", start), ("end", self.today.strftime("%Y%m%d")),
                   ("s", json.dumps([{"prop": self.prop, "op": "is", "values": [version]}]))]
        resp = self.http.get(f"{self.base}/api/2/funnels", params=params, auth=self.auth, timeout=60)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            return None, 0
        row = data[0]
        cumulative, raw = row.get("cumulative") or [], row.get("cumulativeRaw") or []
        users = int(raw[0]) if raw else 0
        return (float(cumulative[-1]) if cumulative and users else None), users

    def metrics(self, ctx: dict) -> dict:
        new, prev = {}, {}
        users = None
        for name, funnel in self.funnels.items():
            value, n = self._conversion(funnel["steps"], ctx["version"])
            new[name] = value
            users = n if users is None else min(users, n)
            if ctx.get("previous_version"):
                prev[name] = self._conversion(funnel["steps"], ctx["previous_version"])[0]
        if users is not None:
            new["_users"] = users
        return {"new": new, "prev": prev}
