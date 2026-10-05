---
name: release-bot-add-integration
description: Add or change a Headless Mobile Release Bot integration - health sources (Play Vitals, Crashlytics, Sentry, Grafana, Datadog, PagerDuty, incident.io, Amplitude, generic HTTP) and halt actions (PagerDuty / incident.io paging, incident on halt, Optimizely kill switch, Microsoft Teams). Use when the user wants the bot to watch a new signal or do something new when a release halts.
---

# Add an integration to the release bot

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

1. **Read `<bot>/docs/integrations.md`** for the integration: the config block, which secrets it
   needs, which GitHub environment they go in, and its caveats.
2. **Ask what to watch and what to do** if it isn't clear: which metric or alert, the threshold
   (absolute or vs the previous version), and the action (`halt`, `hold` or `notify`). Start
   conservative (hold/notify) unless the user wants halts.
3. **Edit `release-bot.yml`:** add the source under `health.sources` *and* its rules (or the
   `notify` / `on_halt` block). Per-app overrides go under that app. Use placeholders for IDs.
4. **`validate` + `plan`**, show the user the new rules.
5. **Secrets checklist** with exact `gh secret set` / `gh variable set` commands for the user.
   Callers in another GitHub organization must also pass new secrets by name in their caller
   workflows (compare with `<bot>/examples/caller-workflows`).
6. **PR, then Doctor** once secrets are set; fix until the integration shows ✅.
