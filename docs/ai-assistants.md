# Using the bot from Claude Code or Codex

Four skills let a developer set up and run the bot by asking for it in plain words. They call the
bot's own commands (`init`, `validate`, `plan`, Doctor), so every check is the same code CI runs.

| Skill | Ask for | It… |
|---|---|---|
| `release-bot-setup` | "Set up the release bot for this repo" | inspects the repo, asks what it can't detect, runs `init`, adds integrations, shows the plan, lists the secrets to set, opens a PR, runs Doctor |
| `release-bot-add-integration` | "Add Sentry and page PagerDuty on halts" | adds the source/rules or halt action, lists its secrets, validates, runs Doctor |
| `release-bot-troubleshoot` | "Why did Submit fail?" | reads the failed run, matches known causes, proposes or applies the fix |
| `release-bot-status` | "Where are our rollouts?" | summarizes each app and platform from the latest runs |

**What the skills never do:** ask for, print or store secrets (you run `gh secret set` yourself),
trigger Submit / Resume / Halt unless you ask in that conversation, or invent IDs.

## Claude Code

```bash
claude plugin marketplace add vaazh-studios/headless-mobile-release-bot
claude plugin install release-bot@vaazh-studios
```

The plugin also adds a hook: whenever the agent edits `release-bot.yml`, it runs
`release_bot validate` and sends any errors back, so the agent fixes them before you see them.
(The hook needs the bot's Python dependencies; without them it skips quietly.)

## Codex (and Claude Code without the plugin)

Copy the skills into your app repo; everyone who opens the repo gets them:

```bash
python -m release_bot init --skills      # or, for an existing setup:
cp -r <bot>/skills/setup <app>/.agents/skills/release-bot-setup   # etc.
```

Codex reads `.agents/skills/`, Claude Code reads `.claude/skills/`; `init --skills` writes both.
