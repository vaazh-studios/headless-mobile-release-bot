# Production setup (Google Play)

Do this once per app. Budget an hour. Before relying on the bot, run it in shadow (dry-run) mode for a release or two — see step 7.

## 1. Google Cloud: keyless login for GitHub Actions

Use the GCP project linked to Play Console (or any project) and enable the APIs:

```bash
gcloud services enable androidpublisher.googleapis.com playdeveloperreporting.googleapis.com \
  bigquery.googleapis.com iamcredentials.googleapis.com sts.googleapis.com
```

Create the workload identity pool, the OIDC provider and the service account:

```bash
gcloud iam workload-identity-pools create github --location=global

gcloud iam workload-identity-pools providers create-oidc github-actions \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.environment=assertion.environment,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='ORG/REPO' && assertion.environment.startsWith('play-') && assertion.ref=='refs/heads/main'"

gcloud iam service-accounts create android-release-bot

gcloud iam service-accounts add-iam-policy-binding \
  android-release-bot@PROJECT_ID.iam.gserviceaccount.com \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.environment/play-production"
```

The binding is per **environment**: only jobs running in `play-production` can act as this service
account. With several Play developer accounts, repeat the service-account steps once per account
and bind each one to its own environment (see [multi-app.md](multi-app.md)).

**Crashlytics (BigQuery):** in the Firebase project, give the service account `roles/bigquery.dataViewer` on the `firebase_crashlytics` dataset and `roles/bigquery.jobUser` on the project.

## 2. Play Console

**Users and permissions → Invite new users →** the service account email. Under **App permissions**, add **only this app** with:
- *View app information and download bulk reports (read-only)*, needed for the Vitals API
- *Release to production, exclude devices, and use Play App Signing*

Also turn **off Managed publishing**, or approved changes will wait for a manual "Publish".

## 3. Slack app

- **Bot scopes:** `chat:write`, `channels:history` (`groups:history` for private channels), `usergroups:read`.
- **Invite the bot to** the release channel, the wider announcement channel and the SLO alerts channel.
- **Copy the IDs** of those channels and of the `@android-release-hero` user group into `release-bot.yml`.
- **Map each possible hero** in `access.release_heroes` (`github-login: SLACK_USER_ID`).

## 4. Optional health sources

Play Vitals works with the service account from step 2 and is the only source turned on by
default. Add others in `release-bot.yml` (source + rules, see [configuration.md](configuration.md)):

- **Crashlytics:** Firebase → Project settings → Integrations → BigQuery → enable Crashlytics with
  **streaming**, then give the service account `roles/bigquery.dataViewer` on the
  `firebase_crashlytics` dataset and `roles/bigquery.jobUser` on the project.
- **Grafana:** a service account with the Viewer role; its token as the `GRAFANA_TOKEN` secret,
  the URL as the `GRAFANA_URL` variable. Label the alert rules that matter (`team="mobile"`,
  `severity="critical"`/`"warning"`). Optional instant halts: a webhook contact point that calls
  `POST https://api.github.com/repos/ORG/REPO/dispatches` with
  `{"event_type":"rollout-alert","client_payload":{"alertname":"{{ .CommonLabels.alertname }}"}}`
  and a fine-grained token (this repo only, Contents: write). The bot re-checks real data before
  halting, so a forged call can't halt anything by itself.
- **Datadog:** an API key and an application key with the `monitors_read` scope, as the
  `DD_API_KEY` and `DD_APP_KEY` secrets; your site (e.g. `datadoghq.eu`) as the `DD_SITE` variable.
  Tag the monitors that matter (e.g. `team:mobile`) and set `sources.datadog.query`. For instant
  halts, add a Datadog webhook that calls the same `dispatches` endpoint.

## 5. GitHub repository settings

| Where | Name | Value |
|---|---|---|
| Environment **`play-production`** (deployment branches: `main`) | var `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-actions` |
| | var `GCP_SERVICE_ACCOUNT` | `android-release-bot@PROJECT_ID.iam.gserviceaccount.com` |
| | var `GRAFANA_URL`, secret `GRAFANA_TOKEN` | optional, Grafana source |
| | secrets `DD_API_KEY`, `DD_APP_KEY`, var `DD_SITE` | optional, Datadog source |
| | secret `SLACK_BOT_TOKEN` | `xoxb-…` |
| Environment **`android-signing`** (deployment branches: `main`) | secret `ANDROID_UPLOAD_KEYSTORE_BASE64` | `base64 -i upload.jks` |
| | secrets `ANDROID_UPLOAD_STORE_PASSWORD`, `ANDROID_UPLOAD_KEY_ALIAS`, `ANDROID_UPLOAD_KEY_PASSWORD` | |
| Repo variable | `RELEASE_BOT_ENABLED` | `true`: lets the scheduled rollout and health runs start (they stay idle until set) |

Build settings (Gradle directory, bundle task, `.aab` location, JDK) live in `release-bot.yml`
under each app's `build:`, not in repo variables.

The keystore must be your Play App Signing **upload key**. Flutter: replace the Gradle step with `flutter build appbundle`.

## 6. `release-bot.yml`

Set `package_name`, the Crashlytics table (`<package_with_underscores>_ANDROID_REALTIME`), the channel IDs and the heroes. **Tune `health.min_distinct_users`:** at 2%, an app with 100k daily users only gives about 2k users on the new version. If the minimum is higher than that, Monday will always hold.

## 7. Doctor, then shadow mode during your next manual release

1. **Actions → Android · Doctor.** It checks the Google login, Play publishing, Play Vitals, the
   Crashlytics table, Grafana, the Slack channels and the hero group, and tells you exactly what to
   fix. Run it until it says "All good."
2. Set the repo variable **`RELEASE_BOT_MODE=shadow`** and `RELEASE_BOT_ENABLED=true`.
3. Release by hand as usual. The bot reads the real data, makes every decision and posts what it
   *would have* done in a separate "🫥 Shadow run" thread, without changing anything on the store.
   Identical decisions are posted once.
4. Compare its decisions and numbers with Play Console. Tune `health.rules` until you agree.
5. Remove `RELEASE_BOT_MODE` (or set it to `live`). Consider a first live run on Play's
   **internal** track (`play.track: internal`) before production.

## Commands (local)

```bash
pip install -r release_bot/requirements.txt pytest
python -m pytest -q tests
gcloud auth application-default login   # if you want to try read-only commands locally
python -m release_bot status
python -m release_bot --dry-run check
```
