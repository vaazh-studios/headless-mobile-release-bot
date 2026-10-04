"""App Store Connect API client: what the bot needs for an iOS phased release.

Credentials come from the environment (secrets of the `appstore-*` GitHub
environment): ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY (contents of the .p8).

Flow (endpoints per Apple's OpenAPI spec 4.5):
  submit   find build → create appStoreVersion (releaseType MANUAL) → whatsNew →
           appStoreVersionPhasedRelease (INACTIVE) → reviewSubmissions → submitted
  start    POST appStoreVersionReleaseRequests (PENDING_DEVELOPER_RELEASE → phased day 1)
  pause / resume / complete   PATCH appStoreVersionPhasedReleases phasedReleaseState
"""

import os
import time
from dataclasses import dataclass

import requests

API = "https://api.appstoreconnect.apple.com/v1"
# appVersionState values grouped by what the bot should do.
IN_REVIEW = {"READY_FOR_REVIEW", "WAITING_FOR_REVIEW", "IN_REVIEW", "WAITING_FOR_EXPORT_COMPLIANCE"}
APPROVED_WAITING = {"PENDING_DEVELOPER_RELEASE"}
LIVE = {"READY_FOR_DISTRIBUTION", "PROCESSING_FOR_DISTRIBUTION", "PENDING_APPLE_RELEASE"}
REJECTED = {"REJECTED", "METADATA_REJECTED", "INVALID_BINARY", "DEVELOPER_REJECTED"}


@dataclass
class IOSRelease:
    version: str
    version_id: str
    state: str                     # appVersionState
    phased_id: str | None = None
    phased_state: str | None = None  # INACTIVE | ACTIVE | PAUSED | COMPLETE
    day: int | None = None         # currentDayNumber (1..7) while phased

    @property
    def fraction(self) -> float | None:
        """Share of users Apple is offering the update to today (None before release)."""
        from release_bot.policy import APPLE_PHASED
        if self.state not in LIVE:
            return None
        if self.phased_state == "COMPLETE" or not self.phased_id:
            return 1.0
        if self.day:
            return APPLE_PHASED[min(self.day, 7) - 1]
        return 0.0


def _token(key_id: str, issuer: str, private_key: str) -> str:
    import jwt  # PyJWT[crypto]
    now = int(time.time())
    return jwt.encode({"iss": issuer, "iat": now, "exp": now + 15 * 60, "aud": "appstoreconnect-v1"},
                      private_key, algorithm="ES256", headers={"kid": key_id, "typ": "JWT"})


class AppStore:
    def __init__(self, bundle_id: str, dry_run: bool = False, session=None, env=None):
        env = env or os.environ
        self.bundle_id = bundle_id
        self.dry_run = dry_run
        self.http = session or requests.Session()
        self._creds = (env.get("ASC_KEY_ID"), env.get("ASC_ISSUER_ID"), env.get("ASC_PRIVATE_KEY"))
        self._app_id = None

    # ---- HTTP
    def _req(self, method: str, path: str, **kw) -> dict:
        if not all(self._creds):
            raise RuntimeError("ASC_KEY_ID, ASC_ISSUER_ID and ASC_PRIVATE_KEY must be set")
        headers = {"Authorization": f"Bearer {_token(*self._creds)}", "Content-Type": "application/json"}
        resp = self.http.request(method, f"{API}{path}", headers=headers, timeout=60, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"App Store Connect {method} {path}: {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def _write(self, method: str, path: str, body: dict) -> dict:
        if self.dry_run:
            print(f"[dry-run] would {method} {path}: {body}")
            return {"data": {"id": "dry-run"}}
        return self._req(method, path, json=body)

    # ---- lookups
    @property
    def app_id(self) -> str:
        if not self._app_id:
            data = self._req("GET", "/apps", params={"filter[bundleId]": self.bundle_id})["data"]
            if not data:
                raise RuntimeError(f"No App Store Connect app with bundle id {self.bundle_id}")
            self._app_id = data[0]["id"]
        return self._app_id

    def find_build(self, version: str, build_number: str | None = None) -> str:
        params = {"filter[app]": self.app_id, "filter[preReleaseVersion.version]": version,
                  "filter[processingState]": "VALID", "sort": "-uploadedDate", "limit": 1}
        if build_number:
            params["filter[version]"] = build_number   # CFBundleVersion
        data = self._req("GET", "/builds", params=params)["data"]
        if not data:
            raise RuntimeError(f"No processed TestFlight build for {version}"
                               + (f" ({build_number})" if build_number else "") + " yet")
        return data[0]["id"]

    def current(self) -> IOSRelease | None:
        """The newest iOS App Store version and its phased release."""
        res = self._req("GET", f"/apps/{self.app_id}/appStoreVersions",
                        params={"filter[platform]": "IOS", "include": "appStoreVersionPhasedRelease", "limit": 5})
        versions = res.get("data", [])
        if not versions:
            return None
        included = {i["id"]: i for i in res.get("included", []) if i["type"] == "appStoreVersionPhasedReleases"}

        def key(v):
            return tuple(int(x) for x in v["attributes"]["versionString"].split(".") if x.isdigit())
        v = max(versions, key=key)
        rel = IOSRelease(v["attributes"]["versionString"], v["id"], v["attributes"].get("appVersionState", ""))
        pr = (v.get("relationships", {}).get("appStoreVersionPhasedRelease", {}) or {}).get("data")
        if pr and pr["id"] in included:
            a = included[pr["id"]]["attributes"]
            rel.phased_id, rel.phased_state, rel.day = pr["id"], a.get("phasedReleaseState"), a.get("currentDayNumber")
        return rel

    # ---- writes
    def submit(self, version: str, whats_new: str, locale: str, build_number: str | None = None) -> IOSRelease:
        build = self.find_build(version, build_number)
        ver = self._write("POST", "/appStoreVersions", {"data": {
            "type": "appStoreVersions",
            "attributes": {"platform": "IOS", "versionString": version, "releaseType": "MANUAL"},
            "relationships": {"app": {"data": {"type": "apps", "id": self.app_id}},
                              "build": {"data": {"type": "builds", "id": build}}}}})["data"]["id"]
        if not self.dry_run:
            locs = self._req("GET", f"/appStoreVersions/{ver}/appStoreVersionLocalizations")["data"]
            loc = next((l for l in locs if l["attributes"].get("locale") == locale), locs[0] if locs else None)
            if loc:
                self._write("PATCH", f"/appStoreVersionLocalizations/{loc['id']}", {"data": {
                    "type": "appStoreVersionLocalizations", "id": loc["id"],
                    "attributes": {"whatsNew": whats_new[:4000]}}})
        phased = self._write("POST", "/appStoreVersionPhasedReleases", {"data": {
            "type": "appStoreVersionPhasedReleases", "attributes": {"phasedReleaseState": "INACTIVE"},
            "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": ver}}}}})["data"]["id"]
        sub = self._open_submission()
        self._write("POST", "/reviewSubmissionItems", {"data": {"type": "reviewSubmissionItems", "relationships": {
            "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": sub}},
            "appStoreVersion": {"data": {"type": "appStoreVersions", "id": ver}}}}})
        self._write("PATCH", f"/reviewSubmissions/{sub}", {"data": {
            "type": "reviewSubmissions", "id": sub, "attributes": {"submitted": True}}})
        return IOSRelease(version, ver, "WAITING_FOR_REVIEW", phased, "INACTIVE")

    def _open_submission(self) -> str:
        if not self.dry_run:
            open_ = self._req("GET", "/reviewSubmissions", params={
                "filter[app]": self.app_id, "filter[platform]": "IOS", "filter[state]": "READY_FOR_REVIEW"})["data"]
            if open_:
                return open_[0]["id"]  # one open submission per platform: reuse it
        return self._write("POST", "/reviewSubmissions", {"data": {
            "type": "reviewSubmissions", "attributes": {"platform": "IOS"},
            "relationships": {"app": {"data": {"type": "apps", "id": self.app_id}}}}})["data"]["id"]

    def start(self, rel: IOSRelease) -> None:
        """Release an approved version: Apple's phased release starts at day 1 (1%)."""
        self._write("POST", "/appStoreVersionReleaseRequests", {"data": {
            "type": "appStoreVersionReleaseRequests",
            "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": rel.version_id}}}}})

    def set_phased(self, rel: IOSRelease, state: str) -> None:
        """ACTIVE (resume), PAUSED (halt) or COMPLETE (release to everyone; can't be undone)."""
        if not rel.phased_id:
            raise RuntimeError(f"{rel.version} has no phased release")
        self._write("PATCH", f"/appStoreVersionPhasedReleases/{rel.phased_id}", {"data": {
            "type": "appStoreVersionPhasedReleases", "id": rel.phased_id,
            "attributes": {"phasedReleaseState": state}}})
