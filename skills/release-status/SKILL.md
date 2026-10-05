---
name: release-bot-status
description: Report where Headless Mobile Release Bot rollouts stand - per app and platform, current percentage, health, last decision, halts. Use when the user asks about release or rollout status ("where is Shop at?", "did anything halt?").
---

# Release status

Read-only. Don't trigger any workflow unless the user asks.

## Steps

1. Latest runs of the rollout and health workflows:
   `gh run list --workflow android-rollout.yml --limit 3` and
   `gh run list --workflow android-health.yml --limit 3` (plus `android-submit.yml` for recent submits).
2. For the newest of each: `gh run view <id> --log`. Each app/platform job prints its health
   scorecard and decision; look for lines like `Rollout 2% → 20%`, `Holding at`, `HALTED`,
   `PAUSED`, `Released to everyone`, `nothing due today`.
3. Answer with one line per app and platform: version, percentage, health (OK / hold / halt and
   why), last decision and when. Link the run. Mention anything that needs a human (halted,
   holding for a reason other than thin data, failed runs).
4. If the user wants to act (halt, resume, submit), confirm the app, platform and reason, then use
   the matching workflow (`android-halt.yml`, `android-resume.yml`, `android-submit.yml`) only
   after they say yes. Resume and Submit only work for the on-duty release hero.
