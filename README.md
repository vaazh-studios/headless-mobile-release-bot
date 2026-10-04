# Headless Mobile Release Bot

**Staged Google Play rollouts that run themselves, on GitHub Actions and Slack. No server, no on-call.**

> **Status: beta (v0).** Google Play only. The logic is covered by tests and a simulator, and the
> full flow has run end to end in mock mode. It has **not yet run against a real Play account**,
> so start in dry-run mode (see [setup](docs/setup.md#7-dry-run-during-your-next-manual-release)).

Your release hero clicks one button on Thursday. After that the bot:

- builds the signed `.aab` from your tag and publishes **GitHub release notes**
- uploads to Play production at **2%**
- **checks health every few hours** (Crashlytics, Play Vitals, Grafana) and **halts automatically**
  on a new crash, Google's ANR/crash thresholds, or a critical alert
- moves **2% → 20% → 50% → 100%** on your schedule, only when everything is green
- keeps your team in the loop in Slack: one thread per release, announcements, SLO alerts

```
Thu   hero: "Submit v4.12.0" ─▶ build ─▶ release notes ─▶ Play 2% ─▶ Slack thread
Fri   Google approves → 2% live
Sat…  health check every 3h ───────────────▶ 🛑 auto-halt if unhealthy
Mon   ⬆️ 20%   Tue ⬆️ 50%   Wed ⬆️ 100% 🎉   (only when green; holds otherwise)
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
4. **Actions → Android · Submit to Play** → `v1.1.0`.
5. After ~5 min ("Google review"): **Android · Rollout step**. Each run moves one step.
6. **Sandbox · Inject incident** → `new-crash` and watch it halt. `none` + **Android · Resume rollout** to recover.

Optional Slack: create an app from [`slack-app-manifest.yml`](slack-app-manifest.yml), then set the
`SLACK_BOT_TOKEN` secret and the `SLACK_CHANNEL_ID` variable. The bot joins public channels itself.

Prefer no GitHub at all? `python -m sim` replays eight release-week scenarios locally and writes a
Slack-style HTML report. See [docs/simulator.md](docs/simulator.md).

## Use it for real

→ **[docs/setup.md](docs/setup.md)**: Google Cloud (keyless), Play Console permissions, Slack,
Crashlytics/BigQuery, Grafana, GitHub settings and a dry-run checklist.

| Doc | For |
|---|---|
| [How it works](docs/how-it-works.md) | Multi-day rollouts without a server, the health gate, what halts vs holds |
| [Release hero runbook](docs/release-hero-runbook.md) | The weekly person: what to click, what Slack messages mean |
| [Setup](docs/setup.md) | One-time production setup |
| [Multiple apps & accounts](docs/multi-app.md) | Several apps across several Play developer accounts from one repo |
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
- [ ] `doctor` command: verifies every permission and connection with fix-it messages
- [ ] Shadow mode: decide and post "would have…" without touching Play
- [ ] Halt/Resume links in Slack messages and a GitHub job summary
- [ ] Reusable workflows (`uses: vaazh-studios/headless-mobile-release-bot/...@v1`)
- [x] Multiple apps and multiple Play developer accounts from one repo
- [ ] iOS: App Store phased release (pause/resume) with the same health gate
- [ ] More health sources: Sentry, Datadog, Bugsnag

## License

[Apache-2.0](LICENSE). The release-train ideas were inspired by [Tramline](https://github.com/tramlinehq/tramline)
(Apache-2.0); no code was copied.
