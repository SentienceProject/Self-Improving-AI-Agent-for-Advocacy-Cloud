#!/usr/bin/env python3
"""Tech-watch runner: notices new technology that could help this workspace's work, and prepares changes for you.

    python3 techwatch/scripts/techwatch.py run          do whatever is due now: the sensor, a scan if one is due
                                                        (daily and weekly by default), staging (see AUTONOMY.md),
                                                        and what my/techwatch.md says to do with ready changes
    python3 techwatch/scripts/techwatch.py weekly       a weekly scan now (Opus, up to $4 of usage)
    python3 techwatch/scripts/techwatch.py daily        a daily scan now (Opus, up to $1.50)
    python3 techwatch/scripts/techwatch.py stage <id>   prepare the exact change for one proposal (Opus, up to $3),
                                                        then check it (below)
    python3 techwatch/scripts/techwatch.py check <id>   a second, fresh run reviews a prepared change and fixes or
                                                        improves it (it may widen it) before it's tested (Opus, up
                                                        to $2); runs by itself after every staging
    python3 techwatch/scripts/techwatch.py sensor       check the watched pages for changes (free, no model)
    python3 techwatch/scripts/techwatch.py schedule on|off|status
                                                        run `run` automatically every hour (it acts at most once a
                                                        day): cron on Linux/WSL, launchd on macOS, Task Scheduler
                                                        on Windows
    python3 techwatch/scripts/techwatch.py selftest     check the setup; spends nothing
    python3 techwatch/scripts/techwatch.py doctor       one tiny call (~$0.01): login works, scouts get the right tools

Then `python3 techwatch/scripts/review.py` shows what needs you.

Every scan is a headless `claude -p` with a hard budget cap, no shell, no connectors, and writes limited to
techwatch/inbox, proposals and staged (a settings file generated per run plus a guard hook). Scouts load none of
your other Claude Code settings, so nothing you've allowed elsewhere widens what they can do. Nothing here changes
the workspace: only review.py does that, when you tell it to.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "scripts"))
try:
    from style import S  # noqa: E402  (readable colours in a terminal; plain text otherwise)
except ImportError:      # an older copy without scripts/style.py
    class S:
        ok = warn = ask = b = staticmethod(str)
import time
from datetime import date, datetime, timezone
from pathlib import Path

TW = Path(__file__).resolve().parent.parent
WS = TW.parent
STATE, LOGS, INBOX = TW / "state", TW / "logs", TW / "inbox"
CONFIG = json.loads((TW / "config.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(TW / "scripts"))
import outcome   # noqa: E402
sys.path.insert(0, str(WS / "scripts"))
from rule_paths import rule_path   # noqa: E402
import review    # noqa: E402

TOOLS = "Read,Glob,Grep,Edit,Write,WebSearch,WebFetch"
SCANS = ("daily", "weekly")
TODAY = date.today().isoformat()


def log(msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOGS / "runner.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def st(name):
    try:
        return (STATE / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def mark(name, value):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(str(value), encoding="utf-8")


def problem(text):
    log(f"PROBLEM: {text}")
    mark("last_problem", f"{datetime.now():%Y-%m-%d %H:%M} {text}")


# ---------------------------------------------------------------- lock (portable: no fcntl)
class Lock:
    def __init__(self):
        self.path = STATE / "run.lock"

    def __enter__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    pid, started = self.path.read_text().split()
                    alive = _alive(int(pid)) and time.time() - float(started) < 6 * 3600
                except (OSError, ValueError):
                    alive = False
                if alive:
                    print("techwatch: another run is in progress; exiting")
                    sys.exit(0)
                self.path.unlink(missing_ok=True)   # stale: its process is gone
        sys.exit("techwatch: couldn't take the lock")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _alive(pid):
    if os.name == "nt":   # os.kill(pid, 0) on Windows sends Ctrl-C instead of checking
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.GetLastError() == 5      # access denied: it exists
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259     # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- money
def month_spend():
    total, month = 0.0, TODAY[:7]
    try:
        for line in (STATE / "runs.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("ts", "")[:7] == month:
                total += float(r.get("cost_usd") or 0)
    except (OSError, ValueError):
        pass
    return total


def within_cap(mode):
    cap = float(CONFIG.get("monthly_cap_usd") or 0)
    budget = float(CONFIG["runs"][mode]["max_budget_usd"])
    if cap and month_spend() + budget > cap:
        problem(f"skipped a {mode} run: this month's spend (${month_spend():.2f}) plus its cap (${budget:.2f}) would "
                f"pass monthly_cap_usd (${cap:.2f}) in techwatch/config.json")
        return False
    return True


# ---------------------------------------------------------------- scouts
def scout_settings(writable):
    """Settings for one scout run: read the workspace, write only `writable`, nothing else. Absolute paths, so
    they don't depend on where Claude Code thinks the project is."""
    ws, home = WS.as_posix(), Path.home().as_posix()
    wsr, homer = rule_path(WS), rule_path(Path.home())   # permission rules take "//" absolute paths (rule_paths.py)
    py = f'"{Path(sys.executable).as_posix()}"'   # not "python3": on Windows it may be missing, and the hook would fail open
    guard = (f'{py} "{(TW / "scripts" / "scout_guard.py").as_posix()}" '
             + " ".join(f'"{(TW / w).as_posix()}"' for w in writable))
    read_guard = f'{py} "{(TW / "scripts" / "scout_guard.py").as_posix()}" --read "{ws}"'
    return {
        "autoMemoryEnabled": False,   # scouts keep no Claude Code memory; their notes go in techwatch/inbox
        "permissions": {
            "defaultMode": "dontAsk",
            "allow": [f"Read({wsr}/**)", "Glob", "Grep", "WebSearch", "WebFetch"]
                     + [f"Edit({rule_path(TW / w)}/**)" for w in writable],
            "deny": ["Bash", "PowerShell", "NotebookEdit", "CronCreate", "RemoteTrigger", "ScheduleWakeup",
                     f"Read({homer}/.ssh/**)", f"Read({homer}/.aws/**)", f"Read({homer}/.claude/.credentials.json)",
                     f"Read({homer}/.config/anthropic/**)", f"Read({homer}/.git-credentials)",
                     f"Read({homer}/.config/gh/**)", f"Read({homer}/ai-welfare-evals/**)",
                     f"Read({rule_path(WS.parent / 'ai-welfare-eval-harness')}/**)",
                     f"Read({wsr}/.env)", f"Read({wsr}/**/.env)", f"Read({homer}/.codex/**)",
                     f"Read({homer}/.config/gcloud/**)", f"Read({homer}/.azure/**)", f"Read({homer}/.kube/**)",
                     f"Read({homer}/.netrc)", f"Read({homer}/.gnupg/**)", f"Read({homer}/.claude/settings.json)",
                     f"Read({homer}/ai-welfare-checkpoints/**)"],
        },
        "hooks": {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit",
                                  "hooks": [{"type": "command", "command": guard}]},
                                 {"matcher": "Read|Glob|Grep",
                                  "hooks": [{"type": "command", "command": read_guard}]}]},
    }


def pending_reports():
    since = st("scout_ok") or "0000"
    out = []
    for f in sorted(INBOX.glob("sensor-*.md")):
        d = f.stem.removeprefix("sensor-")
        if d == TODAY or d > since:
            out.append(f"techwatch/inbox/{f.name}")
    return " ".join(out[-7:]) or "(none)"


def scout(mode, prompt_file, extra="", writable=("inbox", "proposals")):
    """One headless run. Returns (outcome, retry_at, reason): outcome is ok | done (failed, don't retry: money was
    spent) | retry (failed before spending anything)."""
    if not within_cap(mode):
        return "done", "", "monthly cap"
    run = CONFIG["runs"][mode]
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    if mode in SCANS:
        extra = (f"Sensor reports to triage (today's, plus any a failed run never read): {pending_reports()}\n"
                 f"Proposals: {'all' if review.prefs()['proposals'] == 'all' else 'only ones for me'} "
                 f"(my/techwatch.md; see \"Which proposals to file\")\n{extra}")
    prompt = (f"Run ID: techwatch-{stamp}-{mode}\nToday: {TODAY}\nMode: {mode}\n"
              f"Budget cap for this run: ${run['max_budget_usd']} (hard; the run is cut off at the cap, so write "
              f"your files early and refine after)\n{extra}\n\n"
              + (TW / "prompts" / prompt_file).read_text(encoding="utf-8"))
    STATE.mkdir(parents=True, exist_ok=True)
    settings = STATE / f"scout-settings-{mode}.json"
    settings.write_text(json.dumps(scout_settings(writable), indent=1), encoding="utf-8")
    out, err = LOGS / f"{stamp}-{mode}.json", LOGS / f"{stamp}-{mode}.stderr"
    LOGS.mkdir(parents=True, exist_ok=True)
    cmd = [claude_bin(), "-p", "--model", run["model"], "--effort", run["effort"],
           "--max-budget-usd", str(run["max_budget_usd"]), "--settings", str(settings), "--setting-sources", "",
           "--permission-mode", "dontAsk", "--tools", TOOLS, "--strict-mcp-config",
           "--name", f"techwatch-{mode}-{stamp}", "--output-format", "json"]
    log(f"start {mode} model={run['model']} effort={run['effort']} cap=${run['max_budget_usd']}")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"   # also set in the settings; either one is enough
    with open(out, "w", encoding="utf-8") as fo, open(err, "w", encoding="utf-8") as fe:
        rc = subprocess.run(cmd, input=prompt, cwd=WS, env=env, stdout=fo, stderr=fe, text=True).returncode
    try:
        result = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = None
    cost = round(float((result or {}).get("total_cost_usd") or 0), 3)
    res = outcome.classify(result, err.read_text(encoding="utf-8", errors="replace"))
    kind, retry_at, reason = res[0], (res[1] if res[0] == "retry" else ""), res[-1] if len(res) > 1 else ""
    log(f"end {mode} exit={rc} cost=${cost} outcome={kind}{f' ({reason})' if reason else ''} log={out.name}")
    with open(STATE / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": mode,
                            "exit": rc, "cost_usd": cost, "outcome": kind}) + "\n")
    if kind == "ok" and mode in SCANS:
        mark("scout_ok", TODAY)
    elif kind == "done":
        if mode in SCANS:
            mark("scout_ok", TODAY)
        problem(f"{mode} run failed and won't be retried (it already spent ${cost}): {reason}")
    elif kind == "retry":
        problem(f"{mode} run failed: {reason}; it will be retried")
    return kind, retry_at, reason


def claude_bin():
    found = shutil.which("claude")
    for p in ([found] if found else []) + [str(Path.home() / ".local/bin/claude"),
                                           str(Path.home() / ".npm-global/bin/claude"), "/usr/local/bin/claude",
                                           "/opt/homebrew/bin/claude"]:
        if p and Path(p).exists():
            return p
    sys.exit("techwatch: can't find the `claude` command. Is Claude Code installed?")


def run_sensor():
    rc = subprocess.run([sys.executable, "-I", str(TW / "scripts" / "sensor.py")]).returncode
    log(f"sensor exit {rc}")
    if rc != 1:
        mark("sensor_ok", TODAY)
    return rc != 1


def stage(pid):
    if not (TW / "proposals" / pid / "proposal.md").exists():
        log(f"stage: no proposal {pid}")
        return "done", "", "no such proposal"
    if not review.can_stage(pid):
        log(f"stage: {pid} isn't approved or due for auto-staging; refusing")
        return "done", "", "not approved"
    bundle = TW / "staged" / pid
    keep = [x for x in ("backup", "applied.json", "applied-before-update.json") if (bundle / x).exists()]
    if keep:   # an earlier application's backups: keep them where rollback and update.py can still find them
        arch = STATE / "applied-archive" / pid / datetime.now().strftime("%Y%m%d-%H%M%S")
        arch.mkdir(parents=True, exist_ok=True)
        for x in keep:
            shutil.move(str(bundle / x), str(arch / x))
    shutil.rmtree(bundle, ignore_errors=True)   # start empty, so nothing another run wrote can ride along
    bundle.mkdir(parents=True)
    kind, retry_at, reason = scout("stage", "stage.md", f"Proposal to stage: techwatch/proposals/{pid}/\n"
                                   f"Write the bundle to: techwatch/staged/{pid}/", writable=(f"staged/{pid}",))
    if kind != "retry":
        log(f"stage {pid}: {review.mark_stage_result(pid, reason)}")
        record_bases(pid)
        if review.status_of(pid) == "staged" and checks_on():
            review.record(pid, "check_pending", "the check run reviews the change before anything else happens")
            ckind, cretry, _ = check(pid)
            if ckind == "retry":   # the change stays waiting for its check; the next run tries again
                return ckind, cretry, reason
    return kind, retry_at, reason


def checks_on():
    return "check" in CONFIG.get("runs", {}) and (TW / "prompts" / "check.md").exists()


def check(pid):
    """A fresh run reviews pid's prepared change against the proposal and the workspace as it is now, fixes what's
    wrong and improves it (it may widen it), and says whether it's worth testing. Everything after this (the
    reviewers' four criteria, AUTO_NEVER, the narrowness count, the test's bundle hash) judges the change as the
    check left it. The stage run's version is kept in state/pre-check/<id>/ so review.py can show what changed."""
    bundle = TW / "staged" / pid
    if review.status_of(pid) != "staged" or not (bundle / "manifest.json").exists():
        log(f"check: {pid} has no prepared change waiting (status {review.status_of(pid)})")
        return "done", "", "no prepared change waiting"
    if review.check_of(pid) != "pending":
        review.record(pid, "check_pending", "check run asked for by hand")
    pre = review.PRE_CHECK / pid
    shutil.rmtree(pre, ignore_errors=True)
    shutil.copytree(bundle, pre, ignore=shutil.ignore_patterns("CHECK.md", "backup", "applied*.json"))
    (bundle / "CHECK.md").unlink(missing_ok=True)   # an earlier check's verdict mustn't count for this one
    kind, retry_at, reason = scout("check", "check.md", f"Proposal: techwatch/proposals/{pid}/\n"
                                   f"Prepared change to check: techwatch/staged/{pid}/", writable=(f"staged/{pid}",))
    if kind == "retry":
        return kind, retry_at, reason
    verdict = review.check_verdict(pid) if kind == "ok" else None
    if not (bundle / "manifest.json").exists() and verdict != "CANNOT_FIX":
        # a check that broke the manifest leaves no change at all: put the stage run's version back
        shutil.rmtree(bundle, ignore_errors=True)
        shutil.copytree(pre, bundle)
        verdict, reason = "UNCHECKED", "the check run left no manifest.json; the stage run's version was restored"
    note = first_line_of_check(bundle) if verdict in review.CHECK_VERDICTS else (reason or "no verdict in CHECK.md")
    review.record(pid, "checked", note, verdict=verdict or "UNCHECKED")
    # The check may have added targets or rebuilt a target's new contents from the file as it is now: record those
    # as they are now. A target whose part the check left alone still rests on the version the stage run read.
    record_bases(pid, keep=untouched_targets(pre, bundle))
    log(f"check {pid}: {verdict or 'UNCHECKED'}{f' ({note})' if note else ''}")
    if not verdict:
        problem(f"the check run of {pid} gave no verdict ({reason or 'see its CHECK.md'}); it waits for you as staged")
    return kind, retry_at, reason


def untouched_targets(pre, bundle):
    """Targets whose change (action and new contents) is the same before and after the check run."""
    def changes(root):
        try:
            mf = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out = {}
        for ch in mf.get("changes", []):
            p = review.resolve(ch.get("target", "")) if ch.get("action") in ("write", "append") else None
            if p is not None:
                src = root / str(ch.get("source", ""))
                out[p.relative_to(WS).as_posix()] = (ch.get("action"), src.read_bytes() if src.is_file() else None)
        return out
    before, after = changes(pre), changes(bundle)
    return {rel for rel, v in after.items() if before.get(rel) == v}


def first_line_of_check(bundle):
    """The one-line summary under CHECK.md's verdict, for the ledger."""
    try:
        lines = (bundle / "CHECK.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for i, line in enumerate(lines):
        if re.match(r"^\W*VERDICT:", line, re.I):
            rest = [x.strip() for x in lines[i + 1:] if x.strip()]
            return (rest[0] if rest else "")[:200]
    return ""


def record_bases(pid, keep=()):
    """What each target looked like when the change was prepared (the runner writes this, not the scout), so
    review.py can tell if a file changed since and refuse to undo those edits automatically. keep: targets whose
    earlier record stays (the check run didn't touch their part of the change, so it still rests on that version)."""
    try:
        mf = json.loads((TW / "staged" / pid / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    d = STATE / "stage-bases"
    try:
        old = json.loads((d / f"{pid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    bases = {}
    for ch in mf.get("changes", []):
        p = review.resolve(ch.get("target", "")) if ch.get("action") in ("write", "append") else None
        if p is not None:
            rel = p.relative_to(WS).as_posix()
            bases[rel] = old[rel] if rel in keep and rel in old else (
                hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None)
            if p.is_file() and not (rel in keep and rel in old):
                # the file itself too, so a test can merge the change into a later version of it (techtest.py)
                (d / pid / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, d / pid / "files" / rel)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps(bases, indent=1), encoding="utf-8")


def weekly_due():
    last = st("last_weekly")
    try:
        age = (date.today() - date.fromisoformat(last)).days
    except ValueError:
        return True
    return age >= int(CONFIG.get("cadence", {}).get("weekly_every_days", 7))


def scan(mode):
    kind, retry_at, reason = scout(mode, f"{mode}.md")
    if kind == "ok":
        try:
            review.announce_major(log)   # a phone notification for a new major proposal, straight away
        except Exception as e:   # noqa: BLE001  (a notification never stops a scan)
            problem(f"major-change notification: {e!r}")
    if mode == "weekly" and kind != "retry":
        mark("last_weekly", TODAY)
    return kind, retry_at


def cmd_run(scheduled):
    """What's due now, then what my/techwatch.md says to do with ready changes (every hour when scheduled: test
    results come in at any time)."""
    if scheduled and review.prefs()["runs"] != "automatically":
        return log("my/techwatch.md doesn't say 'Tech watch runs: automatically'; nothing done "
                   "(`techwatch.py schedule off` stops these calls)")
    scans_and_staging(scheduled)
    review.auto_actions(log)
    docs_sync_if_due()


def docs_sync_if_due():
    """The weekly docs sync (scripts/docs_sync.py): free unless its checks find something; then one small model call,
    counted in this month's spend. Its failures are logged, never raised."""
    try:
        sys.path.insert(0, str(WS / "scripts"))
        import docs_sync
        result = docs_sync.run()
        if result != "docs sync: not due":
            log(result)
    except Exception as e:   # noqa: BLE001
        problem(f"docs sync: {e!r}")


def scans_and_staging(scheduled):
    """Scheduled runs (hourly) scan at most once a day and retry failures that cost nothing. Checks and staging run
    every hour, so a proposal filed later in the day (by a scan you started, say) isn't left until tomorrow; each
    proposal is staged and checked once, so that adds no runs."""
    scanned = False
    if scheduled:
        ra = st("retry_after")
        if ra and time.time() < float(ra):
            return
        scanned = st("last_auto") == TODAY
    if scheduled and not scanned:
        adate, _, n = st("attempts").partition(" ")
        n = (int(n) if adate == TODAY and n.isdigit() else 0) + 1
        mark("attempts", f"{TODAY} {n}")
        if n > int(CONFIG.get("retry", {}).get("max_attempts_per_day", 12)):
            problem(f"gave up on {TODAY} after {n - 1} attempts; tomorrow's run reads anything missed")
            mark("last_auto", TODAY)
            return
        if st("sensor_ok") != TODAY and not run_sensor() and n < 3:
            problem("every sensor source failed (network down?); will retry next hour")
            return
    elif not scheduled:
        run_sensor()
    cadence = "weekly_only" if review.prefs()["scans"] == "weekly only" else "daily_and_weekly"
    if not scanned and (st("scout_ok") != TODAY or not scheduled):
        mode = "weekly" if weekly_due() else ("daily" if cadence == "daily_and_weekly" and st("scout_ok") != TODAY
                                              else None)
        if mode:
            kind, retry_at = scan(mode)
            if kind == "retry":
                if scheduled:
                    mark("retry_after", retry_at or time.time() + 3600)
                return
        elif not scheduled:
            log(f"no scan due (last weekly {st('last_weekly') or 'never'}); run `weekly` or `daily` to scan anyway")
    for pid in review.to_check()[:3] if checks_on() else []:
        kind, retry_at, _ = check(pid)
        if kind == "retry":
            if scheduled:
                mark("retry_after", retry_at or time.time() + 3600)
            return
    for pid in review.to_stage(int(CONFIG.get("autonomy_level", 0)))[:3]:
        kind, retry_at, _ = stage(pid)
        if kind == "retry":
            if scheduled:
                mark("retry_after", retry_at or time.time() + 3600)
            return
    if scheduled and not scanned:
        mark("last_auto", TODAY)
        (STATE / "retry_after").unlink(missing_ok=True)


# ---------------------------------------------------------------- scheduling
CRON_TAG = f"# techwatch:{WS.as_posix()}"
PLIST = (Path.home() / "Library" / "LaunchAgents"
         / f"org.welfare-workspace.techwatch.{hashlib.sha256(WS.as_posix().encode()).hexdigest()[:8]}.plist")


def is_scheduled():
    if sys.platform == "darwin":
        return PLIST.exists()
    if os.name == "nt":
        name = f"techwatch-{hashlib.sha256(WS.as_posix().encode()).hexdigest()[:8]}"
        return subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True).returncode == 0
    if not shutil.which("crontab"):
        return False
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5).stdout
    return any(l.endswith(CRON_TAG) for l in cur.splitlines())


def note_schedule_off():
    """`schedule off` also updates my/techwatch.md, so setup.py doesn't offer to schedule it again."""
    f = WS / "my" / "techwatch.md"
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return
    new = re.sub(r"^(\*\*Tech watch runs:\*\*)[ \t]*automatic\w*", r"\1 when I start it", text, flags=re.M | re.I)
    if new != text:
        f.write_text(new, encoding="utf-8")
        print("my/techwatch.md now says \"Tech watch runs: when I start it\" (Claude reminds you when a scan is due; "
              "set it to off for no reminders).")


def schedule(action):
    claude_dir = str(Path(claude_bin()).parent)
    path = f"{claude_dir}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    script = (TW / "scripts" / "techwatch.py").as_posix()
    if sys.platform == "darwin":
        if action == "on":
            PLIST.parent.mkdir(parents=True, exist_ok=True)
            PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{PLIST.stem}</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>{script}</string>
    <string>run</string><string>--scheduled</string></array>
  <key>WorkingDirectory</key><string>{WS.as_posix()}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>{(LOGS / 'scheduled.log').as_posix()}</string>
  <key>StandardErrorPath</key><string>{(LOGS / 'scheduled.log').as_posix()}</string>
</dict></plist>
""", encoding="utf-8")
            subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
            subprocess.run(["launchctl", "load", "-w", str(PLIST)], check=False)
            print(S.ok(f"Scheduled hourly with launchd ({PLIST}).") + " It acts at most once a day, while your Mac is awake.")
        elif action == "off":
            subprocess.run(["launchctl", "unload", "-w", str(PLIST)], capture_output=True)
            PLIST.unlink(missing_ok=True)
            print("Unscheduled.")
        else:
            print("scheduled (launchd)" if PLIST.exists() else "not scheduled")
        return
    if os.name == "nt":
        name = f"techwatch-{hashlib.sha256(WS.as_posix().encode()).hexdigest()[:8]}"
        if action == "on":
            # A small .cmd file keeps the task's command short and free of nested quotes (schtasks /TR is fussy
            # about both, and caps the command at 261 characters).
            runner = STATE / "scheduled-run.cmd"
            STATE.mkdir(parents=True, exist_ok=True)
            runner.write_text(f'@echo off\r\ncd /d "{WS}"\r\n"{sys.executable}" "{TW / "scripts" / "techwatch.py"}" run '
                              f'--scheduled >> "{LOGS / "scheduled.log"}" 2>&1\r\n', encoding="utf-8")
            r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "HOURLY", "/TN", name, "/TR", f'"{runner}"'],
                               capture_output=True, text=True)
            print(S.ok(f"Scheduled hourly with Task Scheduler ({name}); it acts at most once a day.") if r.returncode == 0
                  else S.warn(f"Task Scheduler refused: {(r.stderr or r.stdout).strip()}"))
        elif action == "off":
            subprocess.run(["schtasks", "/Delete", "/F", "/TN", name], capture_output=True)
            print("Unscheduled.")
        else:
            print("scheduled (Task Scheduler)" if is_scheduled() else "not scheduled")
        return
    if not shutil.which("crontab"):
        sys.exit("No `crontab` command here. Install cron (e.g. sudo apt install cron), then run this again.")
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    keep = [l for l in cur.splitlines() if not l.endswith(CRON_TAG)]
    if action == "on":
        q = shlex.quote   # paths with spaces or quotes in them
        line = (f"17 * * * * cd {q(WS.as_posix())} && PATH={q(path)} {q(sys.executable)} {q(script)} run "
                f"--scheduled >> {q((LOGS / 'scheduled.log').as_posix())} 2>&1 {CRON_TAG}")
        keep.append(line)
    if action in ("on", "off"):
        subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", text=True, check=True)
        print(S.ok("Scheduled hourly with cron (it acts at most once a day).") if action == "on" else "Unscheduled.")
        if action == "on" and subprocess.run(["pgrep", "-x", "cron"], capture_output=True).returncode != 0:
            print(S.warn("Note: the cron service doesn't seem to be running. On WSL: sudo service cron start "
                         "(or enable systemd so it starts by itself)."))
    else:
        print("scheduled (cron)" if any(l.endswith(CRON_TAG) for l in cur.splitlines()) else "not scheduled")


# ---------------------------------------------------------------- checks
def selftest():
    ok = True
    def fail(msg):
        nonlocal ok
        ok = False
        print(f"FAIL: {msg}")
    if not shutil.which("claude"):
        fail("`claude` isn't on PATH")
    for m in ("daily", "weekly", "stage", "check"):
        if m not in CONFIG.get("runs", {}):
            fail(f"config.json has no runs.{m}")
        if not (TW / "prompts" / f"{m}.md").exists():
            fail(f"missing prompts/{m}.md")
    json.dumps(scout_settings(("inbox", "proposals")))
    review.status_of("x")
    print("selftest OK (no model calls made)" if ok else "selftest found problems")
    return ok


def doctor():
    """Starts a scout exactly as runs do (its settings, guard and tools) and checks it can save a file to the inbox:
    a scout whose saves are refused does its work and loses it (Windows paths once caused that)."""
    probe = TW / "inbox" / ".doctor-write-check.md"
    probe.unlink(missing_ok=True)
    settings = STATE / "scout-settings-doctor.json"
    STATE.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(scout_settings(("inbox",))), encoding="utf-8")
    r = subprocess.run([claude_bin(), "-p", "--model", "haiku", "--max-budget-usd", "0.05", "--settings", str(settings),
                        "--setting-sources", "", "--permission-mode", "dontAsk", "--tools", TOOLS,
                        "--strict-mcp-config", "--no-session-persistence", "--output-format", "stream-json",
                        "--verbose"], input=(f"Hi! This is a setup check. If you're willing, please use the Write "
                                             f"tool to create the file {probe.as_posix()} containing just the word "
                                             "ok, then reply with just OK. Thank you!"),
                       cwd=WS, capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"})
    for line in r.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            tools = e.get("tools", [])
            print("permission mode:", e.get("permissionMode"))
            print("Bash available (should be False):", "Bash" in tools)
            print("connectors loaded (should be none):", [t for t in tools if t.startswith("mcp__")] or "none")
        if e.get("type") == "result":
            print("login/result:", "OK" if not e.get("is_error") else e.get("result"))
            if e.get("permission_denials"):
                print("refused calls:", [d.get("tool_name") for d in e["permission_denials"]])
    saved = probe.exists()
    print("can save to techwatch/inbox (should be yes):", "yes" if saved else
          "NO: scouts' notes and proposals would be lost. Please report this, with the refused calls above.")
    probe.unlink(missing_ok=True)


def main(a):
    cmd = a[0] if a else ""
    if cmd == "schedule":
        schedule(a[1] if len(a) > 1 else "status")
        if len(a) > 1 and a[1] == "off":
            note_schedule_off()
        return
    if cmd == "selftest":
        return sys.exit(0 if selftest() else 1)
    if cmd == "doctor":
        return doctor()
    if cmd not in ("run", "sensor", "daily", "weekly", "stage", "check"):
        print(__doc__)
        return sys.exit(2)
    with Lock():
        if cmd == "run":
            cmd_run("--scheduled" in a)
        elif cmd == "sensor":
            run_sensor()
        elif cmd in ("daily", "weekly"):
            run_sensor()
            if scan(cmd)[0] == "ok":
                (STATE / "last_problem").unlink(missing_ok=True)
        elif cmd == "stage":
            if len(a) < 2:
                sys.exit("usage: techwatch.py stage <proposal-id>")
            stage(a[1])
        elif cmd == "check":
            if len(a) < 2:
                sys.exit("usage: techwatch.py check <proposal-id>")
            check(a[1])
    print("\n" + S.b("What needs you:") + f"  python3 {(TW / 'scripts' / 'review.py').relative_to(WS).as_posix()}")


if __name__ == "__main__":
    main(sys.argv[1:])
