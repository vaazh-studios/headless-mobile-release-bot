"""Sentry release health: crash-free sessions/users for this release vs the previous one.

    health:
      sources:
        sentry:
          org: my-org                      # slug or id
          project: android-app             # slug or id
          environment: production          # optional
          url: https://sentry.io           # or https://us.sentry.io / https://de.sentry.io
          # release: "{package}@{version}+{version_code}"   # SDK default; iOS uses "+*" (build unknown)
          period: 24h
      rules:
        - {name: Crash-free users floor, source: sentry, metric: crash_free_users, below: "99%", action: halt}
        - {name: Crash-free users drop,  source: sentry, metric: crash_free_users, below_previous_by: "0.5%", action: hold}

Secret: SENTRY_AUTH_TOKEN (internal integration or user token with org:read).
"""

import requests

FIELDS = {"crash_free_rate(session)": "crash_free_sessions", "crash_free_rate(user)": "crash_free_users"}


class Sentry:
    def __init__(self, cfg: dict, token: str, session=None):
        self.base = (cfg.get("url") or "https://sentry.io").rstrip("/")
        self.org, self.project = cfg["org"], cfg["project"]
        self.environment = cfg.get("environment")
        self.template = cfg.get("release")
        self.period = cfg.get("period", "24h")
        self.token = token
        self.http = session or requests.Session()

    def release_name(self, ctx: dict, previous: bool = False) -> str | None:
        version = ctx.get("previous_version" if previous else "version")
        code = ctx.get("previous_version_code" if previous else "version_code")
        if not version:
            return None
        default = "{package}@{version}+{version_code}" if code else "{package}@{version}+*"
        template = self.template or default
        return (template.replace("{package}", str(ctx.get("package") or ""))
                .replace("{version}", str(version)).replace("{version_code}", str(code or "*")))

    def _stats(self, release: str) -> dict | None:
        if not self.token:
            raise RuntimeError("SENTRY_AUTH_TOKEN is not set")
        params = [("field", f) for f in (*FIELDS, "sum(session)", "count_unique(user)")]
        params += [("project", str(self.project)), ("query", f'release:"{release}"'),
                   ("statsPeriod", self.period), ("includeSeries", "0"), ("includeTotals", "1")]
        if self.environment:
            params.append(("environment", self.environment))
        resp = self.http.get(f"{self.base}/api/0/organizations/{self.org}/sessions/", params=params, timeout=30,
                             headers={"Authorization": f"Bearer {self.token}"})
        resp.raise_for_status()
        groups = resp.json().get("groups", [])
        if not groups:
            return None
        totals = groups[0].get("totals", {})
        out = {}
        for field, name in FIELDS.items():
            v = totals.get(field)
            if v is not None:
                v = float(v)
                out[name] = v / 100 if v > 1 else v   # expected 0–1; tolerate percentages
        out["_users"] = totals.get("count_unique(user)") or 0
        return out

    def metrics(self, ctx: dict) -> dict:
        new_rel, prev_rel = self.release_name(ctx), self.release_name(ctx, previous=True)
        return {"new": self._stats(new_rel) if new_rel else None,
                "prev": self._stats(prev_rel) if prev_rel else None}
