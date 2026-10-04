"""Datadog monitors as a health source: which monitors are alerting right now.

    health:
      sources:
        datadog:
          query: 'tag:"team:mobile" tag:"service:android-app"'   # Datadog monitor search syntax
      rules:
        - {name: Pager monitor, source: datadog, status: alert, priority: [1, 2], action: halt}
        - {name: Warnings,      source: datadog, status: warn, action: notify}

Credentials (GitHub secrets/variables): DD_API_KEY, DD_APP_KEY (an application key with
monitors_read), DD_SITE (datadoghq.com, datadoghq.eu, us5.datadoghq.com, …).
"""

import requests


class Datadog:
    def __init__(self, api_key: str, app_key: str, query: str = "", site: str = "datadoghq.com", session=None):
        self.base = f"https://api.{site or 'datadoghq.com'}"
        self.headers = {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key}
        self.query = query
        self.http = session or requests.Session()

    def firing_monitors(self) -> list[dict]:
        """Monitors in Alert or Warn state matching the query: [{"name", "status", "priority", "tags"}]."""
        out, page = [], 0
        while True:
            q = " ".join(x for x in (self.query, "status:(alert OR warn)") if x)
            resp = self.http.get(f"{self.base}/api/v1/monitor/search", headers=self.headers, timeout=30,
                                 params={"query": q, "page": page, "per_page": 100})
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("monitors", []):
                status = str(m.get("status", "")).lower()
                if status in ("alert", "warn"):
                    out.append({"name": m.get("name", "unnamed monitor"), "status": status,
                                "priority": m.get("priority"), "tags": m.get("tags", [])})
            meta = data.get("metadata", {})
            if page + 1 >= int(meta.get("page_count", 1) or 1):
                return out
            page += 1
