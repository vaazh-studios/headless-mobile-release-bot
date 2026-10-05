"""Local MCP server: release status and actions from Claude Code, Codex or any MCP client.

Runs on the developer's machine with *their* `gh` login. Actions start the repo's
existing workflows, so the release-hero check, environments and audit trail all
still apply. It never touches store credentials.

    pip install -r release_bot/mcp_requirements.txt
    PYTHONPATH=<bot> python -m release_bot.mcp_server        # stdio

Env: RELEASE_BOT_REPO (owner/repo; default: the git repo in the working dir),
RELEASE_BOT_WORKFLOW_<NAME> to override workflow file names.
"""

import json
import os
import re
import subprocess
import sys

WORKFLOWS = {
    "submit": "android-submit.yml", "rollout": "android-rollout.yml", "health": "android-health.yml",
    "halt": "android-halt.yml", "resume": "android-resume.yml", "doctor": "android-doctor.yml",
}
DECISION = re.compile(r"(Rollout \d+(\.\d+)?% → [^\n]+|Holding at [^\n]+|Rollout HALTED[^\n]*|Phased release PAUSED[^\n]*|"
                      r"Released to everyone[^\n]*|nothing due today[^\n]*|No (staged|in-progress) rollout[^\n]*|"
                      r"Still halted[^\n]*|Apple's phased release: day[^\n]*|Released: Apple's phased release started[^\n]*)")


def workflow(name: str) -> str:
    return os.environ.get(f"RELEASE_BOT_WORKFLOW_{name.upper()}", WORKFLOWS[name])


def project_dir() -> str | None:
    """The app repo to act on (the plugin sets RELEASE_BOT_PROJECT_DIR; otherwise the working dir)."""
    return os.environ.get("RELEASE_BOT_PROJECT_DIR") or None


def gh(*args: str, run=subprocess.run) -> str:
    proc = run(["gh", *args], capture_output=True, text=True, cwd=project_dir())
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc.stdout


def repo(run=subprocess.run) -> str:
    return os.environ.get("RELEASE_BOT_REPO") or gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner", run=run).strip()


def release_status(run=subprocess.run) -> str:
    """Latest decision per app/platform from the newest rollout and health runs."""
    r = repo(run)
    lines = []
    for kind in ("rollout", "health"):
        runs = json.loads(gh("run", "list", "-R", r, "--workflow", workflow(kind), "-L", "1",
                             "--json", "databaseId,createdAt,conclusion,url", run=run) or "[]")
        if not runs:
            lines.append(f"{kind}: no runs yet")
            continue
        latest = runs[0]
        log = gh("run", "view", str(latest["databaseId"]), "-R", r, "--log", run=run)
        per_job: dict[str, str] = {}
        for raw in log.splitlines():
            job = raw.split("\t", 1)[0]
            m = DECISION.search(raw)
            if m:
                label = job.replace("release / ", "")
                per_job[label] = m.group(0).strip()
        lines.append(f"{kind} run {latest['createdAt'][:16]} ({latest['conclusion'] or 'running'}) {latest['url']}")
        for job, decision in sorted(per_job.items()):
            lines.append(f"  {job}: {decision}")
        if not per_job:
            lines.append("  (no decisions in this run)")
    return "\n".join(lines)


def start(kind: str, fields: dict, run=subprocess.run) -> str:
    args = ["workflow", "run", workflow(kind), "-R", repo(run)]
    for k, v in fields.items():
        if v not in (None, ""):
            args += ["-f", f"{k}={v}"]
    gh(*args, run=run)
    return f"Started {workflow(kind)} with {fields}. Follow it in the Actions tab or the Slack thread."


def submit(app: str, tag: str, platform: str = "android", whats_new: str = "", confirm: bool = False,
           run=subprocess.run) -> str:
    preview = f"Submit {app or '(single app)'} {tag} on {platform}" + (f" with notes {whats_new!r}" if whats_new else "")
    if not confirm:
        return (f"Preview: {preview}. This starts a store release. Only the on-duty release hero can run it. "
                "Call again with confirm=true to proceed.")
    return start("submit", {"app": app, "tag": tag, "platform": platform, "whats_new": whats_new}, run)


def resume(app: str, reason: str, platform: str = "android", confirm: bool = False, run=subprocess.run) -> str:
    if not reason.strip():
        return "A reason is required (it's posted to Slack)."
    if not confirm:
        return (f"Preview: resume {app or '(single app)'} on {platform} — reason: {reason!r}. "
                "Release hero only. Call again with confirm=true to proceed.")
    return start("resume", {"app": app, "platform": platform, "reason": reason}, run)


def halt(app: str, reason: str, platform: str = "android", run=subprocess.run) -> str:
    if not reason.strip():
        return "A reason is required (it's posted to Slack)."
    return start("halt", {"app": app, "platform": platform, "reason": reason}, run)


def run_doctor(app: str = "", run=subprocess.run) -> str:
    return start("doctor", {"app": app}, run) + " Doctor is read-only."


def local(command: str, app: str = "") -> str:
    """plan / validate on the local release-bot.yml (no network, no credentials)."""
    args = [sys.executable, "-m", "release_bot"] + (["--app", app] if app else []) + [command]
    proc = subprocess.run(args, capture_output=True, text=True, cwd=project_dir(),
                          env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "")})
    return (proc.stdout + proc.stderr).strip()


def build_server():
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(name="release-bot", instructions=(
        "Release status and actions for Headless Mobile Release Bot. Actions start the repo's GitHub "
        "workflows as the signed-in user. Ask the user before submit/resume/halt; never handle secrets."))
    ro = ToolAnnotations(readOnlyHint=True)
    change = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

    @server.tool(name="release_status", description="Where each app/platform rollout stands: last decision, halts, links.", annotations=ro)
    def release_status_tool() -> str:
        return release_status()

    @server.tool(description="Show the rollout timeline (Android/iOS) and health rules from release-bot.yml.", annotations=ro)
    def plan(app: str = "") -> str:
        return local("plan", app)

    @server.tool(description="Validate release-bot.yml (schema, schedules, rules).", annotations=ro)
    def validate() -> str:
        return local("validate")

    @server.tool(description="Start the read-only Doctor workflow (checks permissions/connections).", annotations=ro)
    def doctor(app: str = "") -> str:
        return run_doctor(app)

    @server.tool(description="Submit a release tag to the store. Returns a preview unless confirm=true.", annotations=change)
    def submit_release(tag: str, app: str = "", platform: str = "android", whats_new: str = "", confirm: bool = False) -> str:
        return submit(app, tag, platform, whats_new, confirm)

    @server.tool(description="Halt (Android, also at 100%) or pause (iOS) a rollout. Always safe; needs a reason.", annotations=change)
    def halt_rollout(reason: str, app: str = "", platform: str = "android") -> str:
        return halt(app, reason, platform)

    @server.tool(description="Resume a halted rollout (release hero only). Preview unless confirm=true.", annotations=change)
    def resume_rollout(reason: str, app: str = "", platform: str = "android", confirm: bool = False) -> str:
        return resume(app, reason, platform, confirm)

    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
