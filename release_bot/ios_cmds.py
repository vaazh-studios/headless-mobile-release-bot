"""iOS versions of submit / precheck / check / advance / halt / resume.

Apple drives the percentages (phased release: 1, 2, 5, 10, 20, 50, 100% on
days 1-7). The bot decides when to *start* it (the schedule's first step),
**pauses** it when a halt rule fires, and **releases to everyone** at the
schedule's 100% step when health allows. It never removes an app from sale.
"""

from release_bot import cli as c
from release_bot.appstore import APPROVED_WAITING, IN_REVIEW, LIVE, REJECTED
from release_bot.gate import Level

EPS = 1e-9


def _schedule(deps):
    pol = c.rollout_policy(deps.cfg)
    sched = pol.ios or pol.android
    return pol, sched


def _target(deps, sched, rel, before: float | None = None):
    """Highest fraction allowed now. Mock mode: one step per run, where Apple
    moving to the next day counts as that run's step."""
    if deps.store is not None:
        current = before if before is not None else (rel.fraction or 0.0)
        higher = [f for f in sched.ladder if f > current + EPS]
        return higher[0] if higher else None
    submitted = None
    if sched.kind == "day":
        submitted = c._submitted_at(deps, rel.version, None)
    return sched.target(c.utcnow(), deps.cfg["timezone"], submitted)


def _health(deps, rel):
    return c.collect_health(deps, None, None, ios_version=rel.version)


def precheck(deps, args) -> int:
    rel = deps.appstore.current()
    if rel and (rel.state in IN_REVIEW or (rel.state in LIVE and rel.phased_state in ("ACTIVE", "PAUSED"))):
        what = "in review" if rel.state in IN_REVIEW else f"still in its phased release ({c._p(rel.fraction or 0)})"
        msg = f"iOS {rel.version} is {what}. A new submission would replace it."
        if not args.force:
            print(f"::error::{msg} Re-run with 'supersede unfinished rollout' if that's intended.")
            return 1
        print(f"::warning::{msg} Continuing because --force was given.")
    print(f"Current iOS version: {rel.version + ' ' + rel.state if rel else 'none'}")
    return 0


def submit(deps, args) -> int:
    p = deps.cfg["play"]
    notes = (args.notes or "").strip() or p["default_release_notes"]
    deps.appstore.submit(args.version, notes, p.get("release_notes_language", "en-US"))
    pol, sched = _schedule(deps)
    first = sched.steps[0]
    root = (f"🚀 {c._name(deps)} *{args.version}* submitted to App Store review. "
            f"Apple's phased release starts {first.label()} if healthy.")
    if args.release_url:
        root += f"\nRelease notes: {args.release_url}"
    key = c._key(deps, args.version)
    deps.slack.post(key, f"Submitted. Plan: {sched.summary()} (Apple sets the daily percentages).", root_text=root)
    c._announce(deps, f"📦 {c._name(deps)} *{args.version}* is in App Store review. Plan: {sched.summary()}.")
    return 0


def check(deps, args) -> int:
    rel = deps.appstore.current()
    if not rel or rel.state not in LIVE or rel.phased_state != "ACTIVE":
        print("No active phased release; nothing to check.")
        return 0
    verdict = _health(deps, rel)
    print(verdict.scorecard())
    pct = c._p(rel.fraction or 0)
    c._summary(deps, f"health check · {rel.version} at {pct} (day {rel.day})", verdict, f"**{verdict.level.name}**")
    trigger = f"\nTriggered by: {args.trigger}" if args.trigger else ""
    key = c._key(deps, rel.version)
    if verdict.level == Level.HALT:
        deps.appstore.set_phased(rel, "PAUSED")
        deps.slack.post(key, f"🛑 {c._mention(deps.cfg)}*Phased release PAUSED* at {pct} (day {rel.day}).{trigger}\n"
                             f"{verdict.scorecard()}\nUsers who updated keep this version; automatic updates stop. "
                             "Fix forward, or run *Resume* if this was a false alarm." + c._links(deps))
        c._alert(deps, f"🛑 {c._name(deps)} {rel.version} phased release auto-paused at {pct}.{trigger}\n{verdict.scorecard()}")
        c._announce(deps, f"🛑 {c._name(deps)} *{rel.version}* rollout paused at {pct} while we investigate.")
        c._halt_effects(deps, rel.version, f"phased release auto-paused at {pct}", automatic=True)
    elif verdict.level in (Level.HOLD, Level.NOTIFY) and not deps.slack.thread_contains(key, verdict.scorecard()):
        icon, text = ("⚠️", "Health needs a human look") if verdict.level == Level.HOLD else ("🔔", "FYI, still rolling out")
        deps.slack.post(key, f"{icon} {text}.{trigger}\n{verdict.scorecard()}{c._links(deps)}")
        c._alert(deps, f"{icon} {c._name(deps)} {rel.version}: {text}.{trigger}\n{verdict.scorecard()}")
    return 0


def advance(deps, args) -> int:
    rel = deps.appstore.current()
    if not rel:
        print("No iOS version yet; nothing to do.")
        return 0
    before = rel.fraction
    if deps.store is not None and rel.state in LIVE and rel.phased_state == "ACTIVE":
        deps.appstore.next_day()          # mock: one run = one phased-release day
        rel = deps.appstore.current()
    key = c._key(deps, rel.version)

    if rel.state in IN_REVIEW:
        print(f"{rel.version}: {rel.state}; waiting for Apple.")
        c._summary(deps, f"rollout step · {rel.version}", extra=f"In App Store review ({rel.state}).")
        return 0
    if rel.state in REJECTED:
        msg = f"❌ {c._mention(deps.cfg)}App Store review: *{rel.state}*. Check App Store Connect → Resolution Center."
        if not deps.slack.thread_contains(key, msg):
            deps.slack.post(key, msg + c._links(deps))
        return 0

    pol, sched = _schedule(deps)
    target = _target(deps, sched, rel, before)

    if rel.state in APPROVED_WAITING:
        if target is None:
            print(f"{rel.version} approved; Apple's phased release starts {sched.steps[0].label()}.")
            return 0
        verdict = _health(deps, rel)
        print(verdict.scorecard())
        if verdict.level in (Level.HALT, Level.HOLD):
            deps.slack.post(key, f"⏸ {c._mention(deps.cfg)}Approved, but not starting the phased release yet.\n"
                                 f"{verdict.scorecard()}{c._links(deps)}")
            return 0
        deps.appstore.start(rel)
        deps.slack.post(key, f"▶️ Released: Apple's phased release started (day 1, 1%).\n{verdict.scorecard()}{c._links(deps)}")
        c._summary(deps, f"rollout step · {rel.version}", verdict, "Released; phased release day 1 (1%).")
        return 0

    if rel.state not in LIVE:
        print(f"{rel.version}: {rel.state}; nothing to do.")
        return 0
    if rel.phased_state == "PAUSED":
        deps.slack.post(key, "⏸ Phased release is paused — not advancing. Resume manually when it's safe.")
        return 0
    if rel.phased_state == "COMPLETE" or (rel.fraction or 0) >= 1.0:
        print(f"{rel.version} is released to everyone.")
        return 0

    current = rel.fraction or 0.0
    if target is not None and target >= 1.0:
        verdict = _health(deps, rel)
        print(verdict.scorecard())
        thin = verdict.level == Level.NOT_ENOUGH_DATA and pol.when_data_is_thin == "hold"
        if verdict.level == Level.HALT:
            deps.appstore.set_phased(rel, "PAUSED")
            deps.slack.post(key, f"🛑 {c._mention(deps.cfg)}*Phased release PAUSED* instead of releasing to everyone.\n"
                                 f"{verdict.scorecard()}{c._links(deps)}")
            c._announce(deps, f"🛑 {c._name(deps)} *{rel.version}* rollout paused at {c._p(current)} while we investigate.")
            c._halt_effects(deps, rel.version, f"phased release auto-paused at {c._p(current)}", automatic=True)
        elif verdict.level == Level.HOLD or thin:
            deps.slack.post(key, f"⏸ {c._mention(deps.cfg)}Holding at {c._p(current)} (day {rel.day}); "
                                 f"Apple keeps going on its own schedule.\n{verdict.scorecard()}{c._links(deps)}")
        else:
            deps.appstore.set_phased(rel, "COMPLETE")
            deps.slack.post(key, f"⬆️ Released to everyone (was {c._p(current)}, day {rel.day}) 🎉\n"
                                 f"{verdict.scorecard()}{c._links(deps)}")
            c._announce(deps, f"🎉 {c._name(deps)} *{rel.version}* is released to all users.")
        c._summary(deps, f"rollout step · {rel.version}", verdict, f"Day {rel.day} ({c._p(current)}) → 100% step")
        return 0

    status = f"📈 Apple's phased release: day {rel.day}, {c._p(current)} of users."
    print(status)
    if not deps.slack.thread_contains(key, status):
        deps.slack.post(key, status)
    c._summary(deps, f"rollout step · {rel.version}", extra=status)
    return 0


def halt(deps, args) -> int:
    rel = deps.appstore.current()
    who = c.os.environ.get("GITHUB_ACTOR", "someone")
    if not rel or rel.state not in LIVE or rel.phased_state != "ACTIVE":
        print(f"::error::No active iOS phased release to pause (state: {rel.state if rel else 'none'}).")
        return 1
    deps.appstore.set_phased(rel, "PAUSED")
    key = c._key(deps, rel.version)
    deps.slack.post(key, f"🛑 {c._mention(deps.cfg)}Phased release paused manually by {who}. Reason: {args.reason or 'n/a'}")
    c._alert(deps, f"🛑 {c._name(deps)} {rel.version} phased release paused by {who}. Reason: {args.reason or 'n/a'}")
    c._announce(deps, f"🛑 {c._name(deps)} *{rel.version}* rollout paused while we investigate.")
    c._halt_effects(deps, rel.version, f"phased release paused manually by {who}", automatic=False)
    return 0


def resume(deps, args) -> int:
    rel = deps.appstore.current()
    if not rel or rel.phased_state != "PAUSED":
        print(f"::error::No paused iOS phased release (state: {rel.phased_state if rel else 'none'}).")
        return 1
    deps.appstore.set_phased(rel, "ACTIVE")
    who = c.os.environ.get("GITHUB_ACTOR", "someone")
    deps.slack.post(c._key(deps, rel.version), f"▶️ Phased release resumed by {who} at day {rel.day}. "
                                               f"Reason: {args.reason or 'n/a'}")
    return 0
