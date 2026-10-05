---
name: release-bot-setup
description: Set up Headless Mobile Release Bot in a mobile app repo (staged Google Play / App Store rollouts on GitHub Actions + Slack, health-gated, auto-halting). Use when the user wants to set up, install or onboard the release bot, automate staged rollouts, or create release-bot.yml.
---

# Set up the release bot in this repo

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

1. **Look at the repo first.** Platforms (Android / iOS / React Native / Flutter), where `gradlew`
   lives, the `applicationId`, how release tags look (`git tag --sort=-creatordate | head`), and
   whether CI already builds and tags releases (Firebase App Distribution, TestFlight uploads).
   Check for an existing `release-bot.yml` and stop to ask before replacing it.
2. **Ask only what you can't detect,** in one short message:
   - Android only, or Android + iOS?
   - Rollout schedule: weekly train (1% Mon → 2% Tue → 100% Wed), 5-day, fast, or their own.
   - Which tools they already use: Play Console vitals is the default; Crashlytics, Sentry,
     Grafana, Datadog, PagerDuty, incident.io, Amplitude, Optimizely are optional.
   - How the release hero is chosen: a Slack user group, an incident.io on-call schedule, or a list.
   - One app or several (several Play developer accounts?).
3. **Generate the config:**
   `python -m release_bot init --yes --platforms … --schedule … [--package …] [--skills]`
   Then add the integrations they named, following `<bot>/docs/integrations.md` and
   `<bot>/docs/configuration.md`. Multiple apps/accounts: `<bot>/docs/multi-app.md`.
4. **Check it:** `python -m release_bot validate` and `python -m release_bot plan`. Show the plan
   (timeline per platform + health rules) and fix anything the user disagrees with.
5. **Secrets and environments checklist:** list every GitHub environment, variable and secret the
   config needs (init prints the basics; each integration in docs/integrations.md lists its own),
   with the exact `gh` commands. The user runs them. Point to `<bot>/docs/setup.md` for Google
   Cloud (keyless login) and Play Console access, and `<bot>/docs/ios.md` for App Store Connect.
6. **Open a PR** with `release-bot.yml` and `.github/workflows/`.
7. **When they say secrets are set:** run Doctor (`gh workflow run android-doctor.yml`), read the
   result (`gh run view <id> --log`), and fix config problems until it's green.
8. **Recommend a safe first run:** `RELEASE_BOT_MODE=shadow` for one or two releases, and a
   closed testing track (`play.track: alpha`) before production.
