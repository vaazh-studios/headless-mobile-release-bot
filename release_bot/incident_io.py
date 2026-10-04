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


class IncidentIOAdmin:
    """On-call lookups and incident creation (needs INCIDENT_IO_API_KEY)."""

    def __init__(self, api_key: str, session=None):
        if not api_key:
            raise RuntimeError("INCIDENT_IO_API_KEY is not set")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.http = session or requests.Session()

    def on_call(self, schedule_id: str, now=None) -> list[dict]:
        """Who is on call for a schedule right now (after overrides): [{"name", "email", "slack_user_id"}]."""
        from datetime import datetime, timedelta, timezone
        now = now or datetime.now(timezone.utc)
        params = {"schedule_id": schedule_id, "entry_window_start": now.isoformat(),
                  "entry_window_end": (now + timedelta(minutes=1)).isoformat()}
        resp = self.http.get(f"{API}/v2/schedule_entries", params=params, headers=self.headers, timeout=30)
        resp.raise_for_status()
        out = []
        for e in (resp.json().get("schedule_entries") or {}).get("final", []):
            start, end = e.get("start_at"), e.get("end_at")
            if start and end and not (datetime.fromisoformat(start.replace("Z", "+00:00")) <= now
                                      < datetime.fromisoformat(end.replace("Z", "+00:00"))):
                continue
            u = e.get("user") or {}
            out.append({"name": u.get("name", ""), "email": u.get("email", ""), "slack_user_id": u.get("slack_user_id", "")})
        return out

    def severity_id(self, name: str) -> str:
        resp = self.http.get(f"{API}/v1/severities", headers=self.headers, timeout=30)
        resp.raise_for_status()
        for s in resp.json().get("severities", []):
            if s.get("name", "").lower() == name.lower():
                return s["id"]
        raise RuntimeError(f"incident.io has no severity called '{name}'")

    def declare(self, name: str, summary: str, idempotency_key: str, severity: str | None = None,
                mode: str = "standard", visibility: str = "public", incident_type_id: str | None = None) -> dict:
        """Create an incident. The idempotency key stops a re-checked halt from opening a second one."""
        body = {"idempotency_key": idempotency_key[:100], "visibility": visibility, "mode": mode,
                "name": name[:255], "summary": summary}
        if severity:
            body["severity_id"] = self.severity_id(severity)
        if incident_type_id:
            body["incident_type_id"] = incident_type_id
        resp = self.http.post(f"{API}/v2/incidents", json=body, headers=self.headers, timeout=30)
        resp.raise_for_status()
        inc = resp.json().get("incident", {})
        return {"id": inc.get("id"), "reference": inc.get("reference"), "permalink": inc.get("permalink")}
