"""PagerDuty: open incidents as a health signal, and paging on a halt.

    health:
      sources:
        pagerduty: {service_ids: [PABC123, PDEF456]}     # services your app depends on
      rules:
        - {name: Backend incident, source: pagerduty, urgency: high, action: hold}
    notify:
      pagerduty: {severity: critical}                  # page on halts (PAGERDUTY_ROUTING_KEY)

Secrets: PAGERDUTY_API_TOKEN (REST API, read-only is enough) for the signal;
PAGERDUTY_ROUTING_KEY (Events API v2 integration key) for paging.
"""

import requests

API = "https://api.pagerduty.com"
EVENTS = "https://events.pagerduty.com/v2/enqueue"


class PagerDuty:
    def __init__(self, api_token: str, service_ids: list[str] | None = None, session=None):
        self.token = api_token
        self.service_ids = service_ids or []
        self.http = session or requests.Session()

    def open_incidents(self) -> list[dict]:
        """Triggered or acknowledged incidents: [{"title", "urgency", "status", "service", "priority"}]."""
        if not self.token:
            raise RuntimeError("PAGERDUTY_API_TOKEN is not set")
        params = [("statuses[]", "triggered"), ("statuses[]", "acknowledged"), ("limit", "100")]
        params += [("service_ids[]", sid) for sid in self.service_ids]
        resp = self.http.get(f"{API}/incidents", params=params, timeout=30, headers={
            "Authorization": f"Token token={self.token}", "Accept": "application/vnd.pagerduty+json;version=2"})
        resp.raise_for_status()
        return [{"title": i.get("title", ""), "urgency": i.get("urgency", "high"), "status": i.get("status", ""),
                 "service": (i.get("service") or {}).get("summary", ""),
                 "priority": ((i.get("priority") or {}).get("summary"))}
                for i in resp.json().get("incidents", [])]


def page(routing_key: str, summary: str, source: str, severity: str = "critical", dedup_key: str | None = None,
         session=None) -> None:
    """Trigger a PagerDuty alert through the Events API v2."""
    body = {"routing_key": routing_key, "event_action": "trigger",
            "payload": {"summary": summary[:1024], "source": source, "severity": severity}}
    if dedup_key:
        body["dedup_key"] = dedup_key
    resp = (session or requests).post(EVENTS, json=body, timeout=30)
    resp.raise_for_status()
