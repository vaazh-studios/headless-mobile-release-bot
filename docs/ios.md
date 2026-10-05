# iOS (App Store) — beta

The same schedule and health rules drive iOS, through Apple's **phased release**.

| | What happens |
|---|---|
| Release day | Your CI uploads the build to TestFlight (as today). The hero runs **Submit** with `platform: ios`: the bot finds the processed build for the tag's version, creates the App Store version with **manual release**, sets "What's New", adds a phased release and submits for review |
| First schedule step | When Apple has approved it and the first step's day comes, the bot releases it if health allows. Apple's phased release starts: day 1 = 1% |
| Every run after | Apple moves on by itself (2%, 5%, 10%, 20%, 50%, 100% on days 2–7). The bot reports the day and percentage |
| A halt rule fires | The bot **pauses** the phased release (Apple allows up to 30 days of pauses in total). Users who updated keep the version; automatic updates stop. It never removes the app from sale |
| Your 100% step | If healthy, the bot releases to everyone (can't be undone) |
| Resume | Release hero only; sets the phased release back to active |
| After 100% | Apple can't halt a version that's released to everyone (Android can: see `after_full_release` in [configuration.md](configuration.md)). Fix forward with a new version |

Your schedule must be something Apple can do: `python -m release_bot validate` checks it
(see [configuration.md](configuration.md#android-vs-ios)). With `platforms: aligned`, the shared
schedule is checked against Apple's days; use `separate` to let Android move faster.

## Health signals on iOS

Apple's API has no per-version crash rate, so iOS uses **Crashlytics** (new fatal crash groups in
this version, matched on the version string) and **Grafana**. Play Vitals rules are skipped.
Crashlytics reads `health.sources.crashlytics.ios_table`, by default
`<bundle_id_with_underscores>_IOS_REALTIME`.

## Setup

1. **App Store Connect API key:** App Store Connect → Users and Access → Integrations → Team Keys →
   generate a key with the **App Manager** role. Note the Key ID and Issuer ID, download the `.p8`.
   Team keys cover every app in the team, so keep the key in a protected environment.
2. **GitHub environment** `appstore-<account>` (or set `ios_environment:` on the account or app),
   deployment branches `main`, with secrets `ASC_KEY_ID`, `ASC_ISSUER_ID` and `ASC_PRIVATE_KEY`
   (the full `.p8` text).
3. **release-bot.yml:** add `ios` to the app's platforms, and `bundle_id` if it differs from
   `package_name`:
   ```yaml
   apps:
     shop:
       account: vaazh
       package_name: com.vaazh.shop
       bundle_id: com.vaazh.Shop
       platforms: [android, ios]
   accounts:
     vaazh: {environment: play-vaazh, ios_environment: appstore-vaazh}
   ```
4. Run **Android · Doctor**, then a release in shadow mode (`RELEASE_BOT_MODE=shadow`).

Callers in another organization must pass the `ASC_*` secrets by name (see
[examples/caller-workflows](../examples/caller-workflows)).

## Not verified yet

The App Store Connect calls follow Apple's OpenAPI spec (4.5) and are covered by tests against a
recorded request flow and the mock App Store, but haven't run against a real App Store Connect
account. Start with shadow mode. Two details come from community reports rather than Apple's
docs: a new phased release is always created `INACTIVE`, and releasing the approved version is
what starts day 1.
