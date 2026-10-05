# Release hero runbook

You're on duty this week if you're in `@android-release-hero`, or on call for the release hero
schedule in incident.io if your team uses that. You don't need to know the app's
code; you need about 10 minutes on Thursday and to react if Slack pings you.

## Thursday (release day)

1. **Smoke test** the release candidate from Firebase App Distribution (or wherever your CI puts it).
2. GitHub → **Actions → Release · Submit to the store → Run workflow** (branch `main`):
   - **platform**: `android` or `ios`
   - **app**: which app (only if your repo releases several), e.g. `shop`
   - **tag**: the tag CI created, e.g. `v4.12.0`
   - **What's new**: optional, max 500 characters; leave empty for the default text
3. Check Slack: a new thread appears in the release channel and an announcement in the wider one.

If the run fails at the first step, read its message:

| Message | Meaning | Do |
|---|---|---|
| `… is not the on-duty @android-release-hero` | You're not in the Slack group, or not mapped in `release-bot.yml` | Ask whoever manages the rotation |
| `… is still rolling out at N%` | Last week's release never reached 100% | Find out why first. If replacing it is intended, re-run with **supersede unfinished rollout** |
| `… is not a vX.Y.Z tag` | Typo in the tag | Re-run with the exact tag |

## Friday → Wednesday: nothing, unless Slack pings you

| Slack message | Meaning | Do |
|---|---|---|
| ✅ Approved by Google: live at 2% | Play review passed; health checks start | Nothing |
| ❌ Rejected by Google | Play review rejected the release | Play Console → Inbox / Policy status. Fix, tag, then **Submit** with **supersede unfinished rollout** |
| ✅ Passed Play review … click *Publish changes* | Managed publishing is on, so Google waits for you | Click **Publish changes** in Play Console, or turn managed publishing off (docs/setup.md) |
| ⏳ Send it for review in Play Console | `changes_not_sent_for_review: true`, so the release isn't in review yet | Send it for review in Play Console |
| ⬆️ Rollout 2% → 20% | Healthy, moved on schedule | Nothing |
| ⏸ Holding at 2% | Not enough data yet, or a mild regression | Read the ⚠️ lines. Not enough data usually clears by itself the next day |
| ⚠️ Health needs a human look | A soft signal, e.g. ANR 1.5× last version | Look at the linked dashboards. It won't advance until it's green again |
| 🛑 Rollout HALTED | A hard signal: new crash, Google threshold, critical alert | See "After a halt" |
| 🛑 Full release HALTED | The same, after 100% (within `after_full_release.watch_days`) | Play serves the previous release again. See "After a halt" |
| 🎉 Fully released | 100% | Done for the week |

## After a halt

Users who already have the new version keep it; nobody new gets it. Play has no rollback.
If the release was already at 100%, Play serves the previous completed release to new installs
and updates instead.

1. Read the 🛑 lines in the thread: which source and what it saw.
2. Decide with the team:
   - **Real problem** → fix forward: CI builds a new tag (e.g. `v4.12.1`) → **Release · Submit to the store** with
     that tag and **supersede unfinished rollout** checked.
   - **False alarm / unrelated** (e.g. a backend incident) → **Actions → Android · Resume rollout**
     with a reason. It continues from the same percentage; the schedule picks up from there.
     A halted full release goes straight back to 100%.

## Stopping it yourself

**Actions → Android · HALT rollout**, with the app and a reason. Anyone can do this, any time. It's always safe.
It also works on a release that's already at 100% (Android): the previous release serves again.
