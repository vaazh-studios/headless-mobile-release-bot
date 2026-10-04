# Use the bot from your own repo

Copy these files into your repo's `.github/workflows/` and add a `release-bot.yml` at the root
(start from the one in this repo). Each file is a short trigger that calls a reusable workflow
here (`rw-*.yml`), so you get fixes by bumping one ref.

| File | Does |
|---|---|
| `android-submit.yml` | Release day: submit Android (build + upload) or iOS (TestFlight build → review) |
| `android-rollout.yml` | Scheduled: one rollout step per app and platform, when health allows |
| `android-health.yml` | Scheduled + Grafana webhook: health check, automatic halt/pause |
| `android-halt.yml` / `android-resume.yml` | Manual halt (anyone) / resume (release hero) |
| `android-doctor.yml` | Checks permissions and connections, with fixes |
| `sandbox-inject-incident.yml` | Mock mode only |

**Pin a version.** Replace `@main` with a tag (e.g. `@v0.1.0`) in `uses:` once releases are tagged,
and pass the same value as `bot_ref:` under `with:`.

**Secrets are passed by name**, because `secrets: inherit` only works when the caller is in the
same GitHub organization as this repo. Environment secrets (signing keys, App Store keys) are read
from your repo's environments directly.

**Permissions:** the callers grant `id-token: write` (keyless Google login) and, for submit,
`contents: write` (GitHub releases).
