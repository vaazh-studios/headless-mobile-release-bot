# How it works

## How a rollout runs over several days

There is **no long-running job**. Each workflow run is short (about 1 minute) and stateless:

```
cron fires ─▶ read the production track from Play ─▶ check health ─▶ write one change to Play ─▶ exit
```

**Google Play is the database.** The track already stores which version is rolling out, its status (`inProgress` / `halted` / `completed`) and the current `userFraction`. Every run reads that, decides, and writes at most one change. So:

- Monday's run sees `4.12.0 inProgress 2%`, the target for Monday is 20%, health is green, and it sets 20%.
- If Monday held (for example, not enough users yet), Tuesday's run sees 2% and moves **one** step to 20%, not 50%. A held day shifts the plan by a day; it never jumps.
- A halted release is never touched by the schedule. Only a human resumes it.
- The Slack thread is found again by a marker in its root message, so that needs no state either.

GitHub Actions cost: roughly 60 one-minute Linux runs a week.

---

## Who can do what

| Action | Allowed | How it's enforced |
|---|---|---|
| Submit, Resume | The person in **`@android-release-hero` right now** | `release_bot authorize` asks Slack for the user group's members and maps GitHub login → Slack ID (`access.release_heroes` in `release-bot.yml`). Rotate the Slack group and the permission moves with it. |
| Halt | Anyone with repo access | Stopping a rollout is always safe. |
| Rollout steps, auto-halts | The bot (cron) | Always runs the reviewed code on `main`. |

The hero check is only trustworthy if nobody can run a modified copy of the workflow with Play credentials. Lock that down like this:

1. **Branch protection on `main`** (PR review required). Add **CODEOWNERS** for `.github/workflows/`, `release_bot/` and `release-bot.yml`.
2. **Environment `play-production` → Deployment branches: `main` only.** Its secrets and the Google login are unavailable to runs from any other branch.
3. **The Workload Identity Federation condition** (below) only accepts tokens from this repo, environment `play-production` and `refs/heads/main`.
4. **No JSON keys anywhere.** Google access uses short-lived OIDC tokens. The upload keystore lives in a separate `android-signing` environment, and the build job never sees Play credentials.

---

## What it checks

You choose: see [configuration.md](configuration.md#health-rules). Out of the box the template
watches **Play Console's Android vitals** only, with these rules:

| Rule | Effect |
|---|---|
| User-perceived ANR rate ≥ 0.47% or crash rate ≥ 1.09% (Google's bad-behaviour lines) | **Halt** |
| ANR or crash rate more than 25% worse than the previous version | Hold |
| Fewer than `min_users` daily users on the new version yet | Hold (not enough data) |
| A source that can't be reached | Hold, never halt |

Optional sources add faster signals: **Crashlytics** (a fatal crash group new in this build),
**Grafana** (alerts by severity and name) and **Datadog** (monitors by status, priority and name).

Good to know:
- **Play Vitals data is 1–2 days old,** so Monday's decision uses the weekend's data. Crashlytics,
  Grafana and Datadog are near real time and can halt within minutes (with a webhook).
- **iOS can't use Play Vitals.** Give iOS apps at least one of the optional sources.
- **There's no rollback on Play.** A halt stops new users from getting the update; users who
  already have it keep it. Fix forward with a higher `versionCode`. On iOS the bot pauses the
  phased release.
