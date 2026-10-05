# Headless Mobile Release Bot

**Staged Google Play rollouts that run themselves, on GitHub Actions and Slack. No server, no on-call.**

> **Status: beta (v0).** Google Play only. The logic is covered by tests and a simulator, and the
> full flow has run end to end in mock mode. It has **not yet run against a real Play account**,
> so start in dry-run mode (see [setup](docs/setup.md#7-dry-run-during-your-next-manual-release)).

Your release hero clicks one button on Thursday. After that the bot:

- builds the signed `.aab` from your tag and publishes **GitHub release notes**
- uploads to Play production at your first step (e.g. **1%**)
- **checks health every few hours** (Play Console vitals by default; Crashlytics, Sentry, Grafana, Datadog, PagerDuty, Amplitude or any HTTP/JSON API optional) and **halts automatically**
  on a new crash, Google's ANR/crash thresholds, or a critical alert
- moves through **your schedule** (e.g. 1% Mon → 2% Tue → 100% Wed), only when **your health rules** are green
- keeps your team in the loop in Slack: one thread per release, announcements, SLO alerts

```
Thu   hero: "Submit v4.12.0" ─▶ build ─▶ release notes ─▶ Play 1% ─▶ Slack thread
Fri   Google approves → 1% live
Sat…  health check every 3h ───────────────▶ 🛑 auto-halt if a halt rule fires
Tue   ⬆️ 2%    Wed ⬆️ 100% 🎉   (your schedule; holds when a hold rule fires)
```

## Why

| | Hosting / on-call | Health-gated rollout | Cost |
|---|---|---|---|
| Self-hosted release platform | Server, database, workers: yours to run | Varies | Your infra + time |
| Commercial release tools | None | Yes | Often expensive |
| fastlane scripts | None | No: you decide when to move | Free |
| **This** | **None: GitHub Actions + Slack** | **Yes** | **Actions minutes** |

## Try it in 10 minutes (mock mode)

Mock mode fakes Google Play, Play Vitals, Crashlytics and Grafana. The workflows, the build of the
example app, the GitHub release notes and the Slack messages are all real.

1. Click **Use this template** → create a public repo.
2. Run `scripts/quickstart.sh` (needs the [GitHub CLI](https://cli.github.com)).
3. **Actions → Sandbox · Create release tag** → `1.0.0`, then `1.1.0`.
4. **Actions → Release · Submit to the store** → `v1.1.0`.
5. After ~5 min ("Google review"): **Android · Rollout step**. Each run moves one step.
6. **Sandbox · Inject incident** → `new-crash` and watch it halt. `none` + **Android · Resume rollout** to recover.

Optional Slack: create an app from [`slack-app-manifest.yml`](slack-app-manifest.yml), then set the
`SLACK_BOT_TOKEN` secret and the `SLACK_CHANNEL_ID` variable. The bot joins public channels itself.

Prefer no GitHub at all? `python -m sim` replays eight release-week scenarios locally and writes a
Slack-style HTML report. See [docs/simulator.md](docs/simulator.md).

## Use it for real

In your app repo (with this repo checked out next to it, or `pip install -r release_bot/requirements.txt`):

```bash
python -m release_bot init        # detects your app, writes release-bot.yml + caller workflows
python -m release_bot validate    # schema + schedule + rules
python -m release_bot plan        # each app's timeline per platform and its health rules
```

Editors with the YAML language server (VS Code's YAML extension, IntelliJ) autocomplete and check
`release-bot.yml` from its [JSON Schema](release_bot/schema/release-bot.schema.json).

Then → **[docs/setup.md](docs/setup.md)**: Google Cloud (keyless), Play Console permissions, Slack,
Crashlytics/BigQuery, Grafana, GitHub settings and a dry-run checklist.

| Doc | For |
|---|---|
| [How it works](docs/how-it-works.md) | Multi-day rollouts without a server, the health gate, what halts vs holds |
| [Release hero runbook](docs/release-hero-runbook.md) | The weekly person: what to click, what Slack messages mean |
| [Configuration](docs/configuration.md) | Rollout schedules (aligned/separate, weekdays or day N) and health rules (halt/hold/notify), per team |
| [Setup](docs/setup.md) | One-time production setup |
| [Multiple apps & accounts](docs/multi-app.md) | Several apps across several Play developer accounts from one repo |
| [Integrations](docs/integrations.md) | Sentry, PagerDuty, Amplitude, generic HTTP checks, Microsoft Teams, Optimizely kill switch |
| [iOS](docs/ios.md) | App Store phased releases with the same schedule and rules (beta) |
| [Claude Code & Codex](docs/ai-assistants.md) | Skills + plugin: "set up the release bot for this repo"; MCP server: "where are our rollouts?", "halt shop" |
| [Use from your repo](examples/caller-workflows) | Short caller workflows for the reusable `rw-*.yml` workflows |
| [Simulator](docs/simulator.md) | Replaying release weeks with mock data, writing scenarios |
| [Security](SECURITY.md) | Credentials, permissions, who can release |
| [Contributing](CONTRIBUTING.md) | Dev setup, adding a health source |

## Security in one paragraph

No long-lived Google keys: GitHub Actions gets short-lived tokens through Workload Identity
Federation, accepted only from this repo, the `play-production` environment and `main`. The Play
service account is scoped to one app. Only the on-duty `@android-release-hero` (Slack user group)
can submit or resume, and anyone can halt. The upload keystore lives in a separate environment
the Play jobs never see. Nothing listens for inbound traffic. Details: [SECURITY.md](SECURITY.md).

## Roadmap

- [ ] Run against real Play releases (v0.1)
- [x] `doctor` command: verifies every permission and connection with fix-it messages
- [x] Shadow mode: decide and post "would have…" without touching the store
- [x] Halt/Resume links in Slack messages and a GitHub job summary
- [x] Reusable workflows (`uses: vaazh-studios/headless-mobile-release-bot/.github/workflows/rw-*.yml@main`)
- [x] Multiple apps and multiple Play developer accounts from one repo
- [x] iOS (beta): App Store phased release, started on schedule, paused by halt rules — [docs/ios.md](docs/ios.md)
- [x] Datadog monitors as a health source
- [x] Sentry, PagerDuty (signal + paging), Amplitude guardrails, generic HTTP/JSON checks
- [x] Microsoft Teams notifications, Optimizely kill switch on halt
- [ ] Bugsnag, Embrace, store ratings; Jira/Linear release tickets

## License

[Apache-2.0](LICENSE). The release-train ideas were inspired by [Tramline](https://github.com/tramlinehq/tramline)
(Apache-2.0); no code was copied.
