# Integrations

Everything here is optional. Play Console vitals is the only health source turned on by default.
Each integration needs a source under `health.sources` (or a `notify` / `on_halt` block) plus
secrets in the right GitHub environment. `python -m release_bot doctor` checks them all.

| Integration | Kind | Platforms | Secrets / variables |
|---|---|---|---|
| [Play Console vitals](configuration.md#health-rules) | health (default) | Android | the Play service account |
| [Crashlytics](configuration.md#health-rules) | health | both | BigQuery read |
| [Grafana](setup.md#4-optional-health-sources) | health | both | `GRAFANA_URL`, `GRAFANA_TOKEN` |
| [Datadog](setup.md#4-optional-health-sources) | health | both | `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` |
| [Sentry](#sentry) | health | both | `SENTRY_AUTH_TOKEN` |
| [Generic HTTP/JSON](#generic-httpjson-checks) | health | both | `RELEASE_BOT_HTTP_ENV` |
| [PagerDuty](#pagerduty) | health + paging | both | `PAGERDUTY_API_TOKEN`, `PAGERDUTY_ROUTING_KEY` |
| [Amplitude](#amplitude) | health (business) | both | `AMPLITUDE_API_KEY`, `AMPLITUDE_SECRET_KEY` |
| [Microsoft Teams](#microsoft-teams) | notification | — | `TEAMS_WEBHOOK_URL` |
| [Optimizely kill switch](#optimizely-kill-switch) | action on halt | — | `OPTIMIZELY_TOKEN` |

Callers in another GitHub organization pass these secrets by name (see
[examples/caller-workflows](../examples/caller-workflows)).

## Sentry

Crash-free sessions and users for the new release, compared with the previous one, from Sentry's
release-health (sessions) API. Works for Android and iOS.

```yaml
health:
  sources:
    sentry:
      org: my-org                 # slug or id
      project: android-app        # slug or id
      environment: production     # optional
      url: https://de.sentry.io   # your region: https://us.sentry.io or https://de.sentry.io
      min_users: 500              # users on the new release before judging
      # release: "{package}@{version}+{version_code}"
  rules:
    - {name: Crash-free users floor, source: sentry, metric: crash_free_users, below: "99%", action: halt}
    - {name: Crash-free users drop,  source: sentry, metric: crash_free_users, below_previous_by: "0.5%", action: hold}
```

- **Metrics:** `crash_free_users`, `crash_free_sessions` (shown as %).
- **Release names** follow the Sentry SDK default, `package@versionName+versionCode` on Android.
  On iOS the bot doesn't know the build number, so it asks for `bundle@version+*`. Set `release:`
  if you name releases differently.
- **Previous release:** on Android it's the version Play is serving everyone else, so
  `below_previous_by` works. On iOS only absolute rules (`below`) apply for now.
- **Secret:** `SENTRY_AUTH_TOKEN`, an internal-integration or user token with `org:read`. Org auth
  (CI) tokens don't have it.

## Amplitude

Funnel conversion per app version, compared with the previous version: a business guardrail.

```yaml
health:
  sources:
    amplitude:
      region: us                   # or eu
      days: 2                      # today plus the previous day
      funnels:
        checkout_conversion: {steps: ["Checkout Started", "Purchase Completed"]}
      min_users: 300               # users entering the funnel on the new version
  rules:
    - {name: Checkout drop, source: amplitude, metric: checkout_conversion, below_previous_by: "10%", action: hold}
```

- One query per version and funnel, filtered on Amplitude's `version` property
  (`version_property:` to change it).
- **Quota:** each funnel query costs Amplitude API quota; a few funnels every 3 hours is fine.
- **Secrets:** `AMPLITUDE_API_KEY`, `AMPLITUDE_SECRET_KEY`.

## RevenueCat: not supported (yet)

RevenueCat's API can't split purchases or trial conversion by **app version**, so there's nothing
per-release to compare. Options:
- Send purchase events to Amplitude and use an [Amplitude funnel](#amplitude) (paywall viewed →
  purchase).
- Set an `app_version` subscriber attribute in the SDK, collect RevenueCat webhooks or data
  exports in your warehouse, and expose a per-version number to a [generic HTTP check](#generic-httpjson-checks).

## Generic HTTP/JSON checks

Turn any API that returns a number into a metric: Prometheus, Honeycomb, New Relic (NerdGraph),
Dynatrace, an internal dashboard.

```yaml
health:
  sources:
    http:
      checks:
        api_5xx_rate:
          url: "https://prom.example.com/api/v1/query?query=sum(rate(http_requests_total{status=~'5..',app_version='{version}'}[1h]))"
          headers: {Authorization: "Bearer ${PROM_TOKEN}"}
          value: data.result[0].value[1]     # where the number is in the JSON
          previous: true                     # also fetch it for the previous version
        checkout_conversion:
          method: POST
          url: "https://analytics.internal/api/query"
          body: {metric: checkout_conversion, app_version: "{version}"}
          value: result.value
  rules:
    - {name: API 5xx,          source: http, metric: api_5xx_rate,        above: 2.5,               action: halt}
    - {name: 5xx regression,   source: http, metric: api_5xx_rate,        above_previous_by: "50%", action: hold}
    - {name: Checkout drop,    source: http, metric: checkout_conversion, below_previous_by: "10%", action: hold}
```

- **Placeholders** in `url`, `headers` and `body`: `{version}`, `{version_code}`,
  `{previous_version}`, `{previous_version_code}`, `{package}`, `{app}`, `{platform}`.
- **Secrets:** `${NAME}` comes from the `RELEASE_BOT_HTTP_ENV` secret, one `NAME=value` per line, so
  the reusable workflows don't need to know your secret names.
- **Values** are plain numbers (`above: 2.5`); write `"2.5%"` only if the API returns a fraction.
- A check that returns no value counts as **hold**.

## PagerDuty

Two independent features:

```yaml
health:
  sources:
    pagerduty: {service_ids: [PABC123, PDEF456]}    # the services your app depends on
  rules:
    - {name: Backend incident, source: pagerduty, urgency: high, action: hold}
    - {name: Payments down,    source: pagerduty, urgency: high, service: payments, action: halt}
notify:
  pagerduty: {severity: critical}                  # page on automatic halts
```

- **Signal:** open (triggered or acknowledged) incidents on those services, filtered by
  `urgency`, optional `service` and `title` regexes. Secret: `PAGERDUTY_API_TOKEN` (read-only REST key).
- **Paging:** when the bot halts or pauses a release by itself, it triggers an alert through the
  Events API v2 (one alert per release, de-duplicated). Manual halts don't page. Secret:
  `PAGERDUTY_ROUTING_KEY` (an Events API v2 integration key on a service).

## Microsoft Teams

Set the `TEAMS_WEBHOOK_URL` secret and every release message the bot posts in Slack is mirrored to
a Teams channel as a card. Without Slack, Teams receives every message, including repeats, because
Teams has no thread history the bot can check. Turn it off per app with `notify: {teams: false}`.

Create the URL in Teams: channel → ••• → **Workflows** → "Send webhook alerts to a channel" (or
Power Automate: "When a Teams webhook request is received" → "Post card in a chat or channel").
The old Office 365 connector webhooks are retired.

## Optimizely kill switch

```yaml
on_halt:
  optimizely:
    project_id: 123456
    environment: production
    flags: [new_checkout, redesigned_feed]
```

When a release halts or pauses (automatically or by hand), the bot turns these Feature
Experimentation flags **off** in that environment and says so in the thread. Turning them back on
is up to you. Secret: `OPTIMIZELY_TOKEN` (a personal access token).

Use it for flags that only exist for this release; a flag that older versions also read will be
turned off for them too.
