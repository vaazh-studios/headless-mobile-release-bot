---
name: release-bot-troubleshoot
description: Diagnose and fix Headless Mobile Release Bot problems - failed Submit / Rollout / Health / Doctor workflow runs, unexpected holds or halts, Slack messages not arriving, release-bot.yml errors. Use when a release-bot workflow fails or behaves unexpectedly.
---

# Troubleshoot the release bot

## Find the bot code

- **Claude Code plugin:** the bot is at `${CLAUDE_PLUGIN_ROOT}`.
- **Otherwise:** `git clone --depth 1 https://github.com/vaazh-studios/headless-mobile-release-bot .release-bot`
  and add `.release-bot/` to `.gitignore`.

Install its dependencies once (a virtualenv is fine):
`pip install -r <bot>/release_bot/requirements.txt`, then run commands as
`PYTHONPATH=<bot> python -m release_bot <command>` from the app repo root.

## Rules (always)

- **Never ask for, print, store or paste secret values** (tokens, keys, passwords). Tell the user
  which secret to set and give the command, e.g. `pbpaste | gh secret set NAME -R owner/repo`;
  they run it.
- **Never trigger release actions** (Submit, Resume, Halt) unless the user explicitly asks in this
  conversation. Doctor and dry runs are fine.
- **Config changes go through the repo's normal review** (branch + PR unless the user says otherwise).
- **Don't invent IDs, URLs or emails.** Use placeholders the user fills in.
- Run `validate` and `plan` after every config change and show the user the plan.

## Steps

1. **Find the run:** `gh run list --limit 10` (or the URL the user gives), then
   `gh run view <id> --log-failed`. For holds/halts that "shouldn't have happened", read the
   health scorecard in `gh run view <id> --log` and the Slack thread.
2. **Match it against the known causes below**, explain the cause in one or two sentences, and
   propose the fix. Config fixes: edit `release-bot.yml`, run `validate` + `plan`, open a PR.
3. **Permissions and connections:** run Doctor (`gh workflow run android-doctor.yml -f app=…`);
   its ❌ lines include the exact fix.

## Known causes

| Symptom | Cause | Fix |
|---|---|---|
| `startup_failure` on a caller workflow | The caller passes an input or secret the reusable workflow doesn't declare | Compare with `<bot>/examples/caller-workflows`; remove the extra entry |
| Secrets empty in the run although set | `secrets: inherit` across GitHub organizations | Pass secrets by name (example callers do) |
| "not the on-duty @android-release-hero" / "not on call" | Actor not mapped in `access.release_heroes`, or not on duty | Map their GitHub login; check the Slack group / incident.io schedule |
| "is still rolling out at N%" | Previous release unfinished | Find out why; re-run Submit with *supersede unfinished rollout* if intended |
| "doesn't match vX.Y.Z" | Tag format vs `tag_prefix` | Use the app's tag format or set `tag_prefix` |
| Holding: "N daily users < minimum" | Not enough data at a small step | Wait, lower `health.min_users`, or `when_data_is_thin: advance` |
| Play 403 / 404 in Doctor | Service account not invited for this app / wrong account environment | Doctor's fix line; docs/setup.md step 2 |
| Slack `not_in_channel` / `missing_scope` | Bot not in a private channel / missing scope | `/invite` the bot; scopes in slack-app-manifest.yml |
| A weekday schedule step fires early or never | Rollout cron doesn't cover that weekday | Adjust the cron in android-rollout.yml |
| `validate`: "must fit within one week" | Weekday schedule longer than a week | Use `day N` steps |
| `validate`: iOS "phased-release day N" | Schedule impossible on Apple | Use Apple's percentages, 100%, or `platforms: separate` |
| A rule seems ignored | Its source isn't under `health.sources` | Add the source (a source without rules is never queried, and vice versa) |
