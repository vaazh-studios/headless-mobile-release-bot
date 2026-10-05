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

## MCP server: release status and actions from any MCP client

The skills cover setup. For day-to-day release work there's a small local MCP server
(`release_bot/mcp_server.py`). It runs on your machine with **your** `gh` login and only starts the
repo's existing workflows. The release-hero check, GitHub environments and the Actions audit trail
all still apply, and it never sees store credentials.

| Tool | What it does | Changes anything? |
|---|---|---|
| `release_status` | Last decision per app and platform from the newest rollout and health runs, with links | No |
| `plan` | The rollout timeline and health rules from `release-bot.yml` | No |
| `validate` | Schema, schedule and rule checks on `release-bot.yml` | No |
| `doctor` | Starts the read-only Doctor workflow | No |
| `submit_release` | Starts Submit. Returns a preview until it's called again with `confirm=true` | Yes |
| `resume_rollout` | Starts Resume. Needs a reason; preview until `confirm=true` | Yes |
| `halt_rollout` | Starts Halt (Android, also after 100%) or pauses the phased release (iOS). Needs a reason | Yes (always safe) |

Halt has no preview on purpose: stopping a rollout should never be slowed down. Submit and Resume
still fail in the workflow if you aren't the on-duty release hero, whatever the AI client says.

**Requirements:** `gh auth login`, and the `mcp` package for the Python the client starts:

```bash
pip install -r release_bot/requirements.txt -r release_bot/mcp_requirements.txt
```

**Settings (environment variables):**
- `RELEASE_BOT_REPO`: `owner/repo` to act on. Default: the git repo in the working directory.
- `RELEASE_BOT_WORKFLOW_<NAME>`: override a workflow file name if you renamed the callers,
  e.g. `RELEASE_BOT_WORKFLOW_SUBMIT=release-submit.yml`. Names: SUBMIT, ROLLOUT, HEALTH, HALT,
  RESUME, DOCTOR.

### Claude Code

The plugin bundles the server (`.mcp.json`), so after `claude plugin install release-bot@vaazh-studios`
it's there; check with `/mcp`. Without the plugin:

```bash
claude mcp add release-bot -e PYTHONPATH=<bot> -- python3 -m release_bot.mcp_server
```

### Codex

Add it to `~/.codex/config.toml`:

```toml
[mcp_servers.release-bot]
command = "python3"
args = ["-m", "release_bot.mcp_server"]
env = { PYTHONPATH = "/path/to/headless-mobile-release-bot" }
```

### ChatGPT, Slack and other hosted assistants

The local server runs on a developer machine, so hosted assistants can't reach it. They can
drive the bot through GitHub's own hosted MCP server or GitHub app instead: "run the *Release ·
Submit to the store* workflow with app=shop, tag=v2.3.0". Every check runs inside the workflow,
so it's just as safe.
