"""incident.io: open incidents as a health signal, and an alert when the bot halts.

    health:
      sources:
        incident_io: {}
      rules:
        - {name: Major incident, source: incident_io, severity: [Critical, Major], action: halt}
        - {name: Any incident,   source: incident_io, action: hold}
    notify:
      incident_io: {alert_source_config_id: 01HXYZ...}   # HTTP alert source to page through

Secrets: INCIDENT_IO_API_KEY (API key that can view incidents) for the signal;
INCIDENT_IO_ALERT_TOKEN (the HTTP alert source's token) for alerts on halt.
Test and tutorial incidents are ignored (incident.io's default).
"""

import requests

API = "https://api.incident.io"
OPEN_CATEGORIES = {"triage", "live", "active"}   # "live" is shown as Active in the app


class IncidentIO:
    def __init__(self, api_key: str, session=None, max_pages: int = 3):
        self.key = api_key
        self.http = session or requests.Session()
        self.max_pages = max_pages

    def open_incidents(self) -> list[dict]:
        """[{"name", "severity", "rank", "status", "permalink"}] for triage/active incidents."""
        if not self.key:
            raise RuntimeError("INCIDENT_IO_API_KEY is not set")
        out, after = [], None
        for _ in range(self.max_pages):
            params = {"page_size": 100, **({"after": after} if after else {})}
            resp = self.http.get(f"{API}/v2/incidents", params=params, timeout=30,
                                 headers={"Authorization": f"Bearer {self.key}"})
            resp.raise_for_status()
            data = resp.json()
            for inc in data.get("incidents", []):
                status = (inc.get("incident_status") or {}).get("category", "")
                if status in OPEN_CATEGORIES:
                    sev = inc.get("severity") or {}
                    out.append({"name": inc.get("name", ""), "severity": sev.get("name", ""),
                                "rank": sev.get("rank"), "status": status, "permalink": inc.get("permalink", "")})
            after = (data.get("pagination_meta") or {}).get("after")
            if not after:
                break
        return out


def alert(alert_source_config_id: str, token: str, title: str, description: str, dedup_key: str,
          source_url: str | None = None, session=None) -> None:
    """Fire an alert on an incident.io HTTP alert source (routes to on-call like any alert)."""
    body = {"title": title[:255], "description": description, "status": "firing", "deduplication_key": dedup_key}
    if source_url:
        body["source_url"] = source_url
    resp = (session or requests).post(f"{API}/v2/alert_events/http/{alert_source_config_id}", json=body,
                                      headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
