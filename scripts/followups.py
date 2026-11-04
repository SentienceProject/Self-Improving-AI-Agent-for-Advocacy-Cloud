#!/usr/bin/env python3
"""Follow-up briefs: every few days, a short list of what's waiting on you in the workspace, sent to your phone.

    python3 scripts/followups.py run          write a brief now (and send it, if notifications are on)
    python3 scripts/followups.py schedule on [--days N]  check every hour, and write one whenever it's due
                                              (every N days; 3 if you haven't chosen)
    python3 scripts/followups.py schedule off
    python3 scripts/followups.py status

setup.py asks whether you want this, and how many days apart. The settings are in my/notify.json
("followups_every_days", and optionally "followups_model", "followups_cap_usd", "followups_after_hour",
"followups_details"); turn the whole thing off with  python3 scripts/notify.py --off  or  schedule off.

Each brief is one read-only Claude run (Sonnet, capped at $0.50 by default): it can read the workspace and nothing
else, with no web, no shell and no editing. It reads what changed since the last brief (drafts, outbox, requests,
logs, ...) and writes workspace/followups/<date>.md. This script, not the model, then sends the brief's short phone
version through scripts/notify.py. Set "followups_details": false to send only "your brief is ready" instead, so
none of your work passes through the ntfy server.

The hourly check does nothing until a brief is due (and not before followups_after_hour, 8 by default, so it
doesn't buzz at night). If a run fails, the next hourly check retries it, at most 3 times a day.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
WS = HERE / "workspace"
OUT = WS / "followups"
STATE = HERE / "my" / ".followups-state.json"
LOG = OUT / ".log"
SCHED_LOG = OUT / ".scheduler.log"   # what the scheduler captures (errors only; log() writes LOG itself)
FOLDERS = ("drafts", "outbox", "inbox", "requests", "proposals", "ideas", "staging", "logs")
MAX_TRIES = 3
TAG = f"followups:{HERE.as_posix()}"
KEY = hashlib.sha256(HERE.as_posix().encode()).hexdigest()[:8]


def log(msg):
    OUT.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(s):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1) + "\n", encoding="utf-8")


def setting(cfg, key, default, lo, hi):
    """A whole number from my/notify.json, or the default if it's missing or not a sensible number."""
    try:
        v = int(cfg.get(key, default))
        return v if lo <= v <= hi else default
    except (TypeError, ValueError):
        return default


def due(cfg, s, now=None):
    """Why a scheduled check should write a brief now, or None."""
    now = now or datetime.now()
    days = setting(cfg, "followups_every_days", 0, 0, 365)
    if not notify.ready(cfg) or days < 1:
        return None
    if not (setting(cfg, "followups_after_hour", 8, 0, 23) <= now.hour < setting(cfg, "followups_before_hour", 21, 1, 24)):
        return None   # not at night
    today = now.date().isoformat()
    if s.get("tries", {}).get(today, 0) >= MAX_TRIES or s.get("paused_until", "") > today:
        return None
    last = s.get("last_ok_date")
    if last and date.fromisoformat(last) + timedelta(days=days) > now.date():
        return None
    return "first brief" if not last else f"{days} day(s) since the last one"


def changed_since(ts):
    """Workspace files changed since ts (a POSIX time), or, before the first brief, the 150 newest of the last 30
    days."""
    since = ts or time.time() - 30 * 86400
    found = []
    for name in FOLDERS:
        d = WS / name
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file() and not p.name.startswith(".") and "__pycache__" not in p.parts:
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if m > since:
                    found.append((m, name, p.relative_to(HERE).as_posix()))
    found.sort(reverse=True)
    logs = [x for x in found if x[1] == "logs"][:20]   # logs change often: don't let them crowd out drafts
    return [p for _, _, p in sorted([x for x in found if x[1] != "logs"] + logs, reverse=True)[:150]]


def claude_bin():
    found = shutil.which("claude")
    for p in ([found] if found else []) + [str(Path.home() / ".local/bin/claude"),
                                           str(Path.home() / ".npm-global/bin/claude"), "/usr/local/bin/claude",
                                           "/opt/homebrew/bin/claude"]:
        if p and Path(p).exists():
            return p
    return None


def settings():
    """Read the workspace and nothing else; the guard hook also keeps Glob and Grep inside it."""
    ws, home = HERE.as_posix(), Path.home().as_posix()
    # The same Python as this script: on Windows "python3" may be missing, and a hook that can't start fails open.
    guard = f'"{Path(sys.executable).as_posix()}" "{(HERE / "techwatch" / "scripts" / "scout_guard.py").as_posix()}" --read "{ws}"'
    return {
        "permissions": {
            "defaultMode": "dontAsk",
            "allow": [f"Read(/{ws}/**)", "Glob", "Grep"],
            "deny": ["Bash", "PowerShell", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "CronCreate",
                     "RemoteTrigger", "ScheduleWakeup", f"Read(/{ws}/my/notify.json)", f"Read(/{ws}/.env)",
                     f"Read(/{ws}/**/.env)", f"Read(/{home}/.ssh/**)", f"Read(/{home}/.claude/**)"],
        },
        "hooks": {"PreToolUse": [{"matcher": "Read|Glob|Grep", "hooks": [{"type": "command", "command": guard}]}]},
    }


def online():
    try:
        socket.getaddrinfo("api.anthropic.com", 443)
        return True
    except OSError:
        return False


def split_text(reply):
    """(brief without the phone block, phone text)."""
    lines, phone, brief, inside = reply.splitlines(), [], [], False
    for l in lines:
        if l.strip() == "TEXT:":
            inside = True
        elif l.strip() == "END TEXT":
            inside = False
        elif inside:
            phone.append(l)
        else:
            brief.append(l)
    return "\n".join(brief).strip() + "\n", "\n".join(phone).strip()


URL_ETC = re.compile(r"(https?://\S+|www\.\S+|\b[\w.+-]+@[\w-]+\.[\w.]+\b|\+?\d[\d ()-]{7,}\d)")
PHONE_MAX = 600


def phone_text(text):
    """The brief's phone version, made safe to send: no links, email addresses or phone numbers (text from a file
    could otherwise reach your phone looking like your own tool's advice), and at most PHONE_MAX characters."""
    text = URL_ETC.sub("[see the brief]", text).strip()
    return text if len(text) <= PHONE_MAX else text[:PHONE_MAX - 3].rstrip() + "..."


def failed(s, cfg, today, why, log_line):
    """A failed try. After MAX_TRIES failures on two days running, briefs pause for one period and you're told once,
    so a brief that keeps failing doesn't keep costing usage."""
    log(log_line)
    if s.get("tries", {}).get(today, 0) >= MAX_TRIES:
        days = s.setdefault("failed_days", [])
        if today not in days:
            days.append(today)
        recent = [d for d in days if d >= date.fromordinal(date.today().toordinal() - 2).isoformat()]
        s["failed_days"] = recent
        if len(recent) >= 2:
            every = max(setting(cfg, "followups_every_days", 3, 1, 365), 1)
            s["paused_until"] = date.fromordinal(date.today().toordinal() + every).isoformat()
            s["failed_days"] = []
            log(f"paused until {s['paused_until']}: the brief failed {MAX_TRIES} times on two days ({why})")
            if notify.ready(cfg):
                notify.send("Follow-up briefs are failing", "Paused for now. The reason is in "
                            "workspace/followups/.log.", tags="warning", cfg=cfg)
    save_state(s)
    return 1


def run(scheduled=False):
    cfg, s = notify.load(), state()
    if scheduled:
        why = due(cfg, s)
        if not why:
            return 0
        if not online():
            return 0      # try again at the next hourly check; doesn't count as a try
    else:
        why = "asked for"
    claude = claude_bin()
    if not claude:
        log("can't find the `claude` command; is Claude Code installed?")
        return 1
    today = date.today().isoformat()
    s["tries"] = {today: s.get("tries", {}).get(today, 0) + 1}     # only today's count is kept
    save_state(s)
    started = time.time()
    files = changed_since(s.get("last_ok_ts"))
    prompt = ((HERE / "scripts" / "followups_prompt.md").read_text(encoding="utf-8")
              + f"\n\nToday is {today}.\n"
              + ("This is the first brief: please also sum up the workspace's current state (open questions, "
                 "pending work), not just the files below.\n" if not s.get("last_ok_ts") else "")
              + "Files changed since the last brief (a starting point, not a limit):\n"
              + ("\n".join(files) if files else "(none)") + "\n")
    OUT.mkdir(parents=True, exist_ok=True)
    sfile = OUT / ".settings.json"
    sfile.write_text(json.dumps(settings(), indent=1), encoding="utf-8")
    cmd = [claude, "-p", "--model", str(cfg.get("followups_model", "sonnet")),
           "--max-budget-usd", str(cfg.get("followups_cap_usd", 0.5)), "--settings", str(sfile),
           "--setting-sources", "", "--permission-mode", "dontAsk", "--tools", "Read,Glob,Grep",
           "--strict-mcp-config", "--output-format", "json"]
    log(f"start ({why}; {len(files)} changed file(s))")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        r = subprocess.run(cmd, input=prompt, cwd=HERE, env=env, capture_output=True, text=True, timeout=1800)
        result = json.loads(r.stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        return failed(s, cfg, today, type(e).__name__, f"failed: {type(e).__name__}")
    reply = str(result.get("result") or "").strip()
    cost = float(result.get("total_cost_usd") or 0)
    if r.returncode or result.get("is_error"):
        return failed(s, cfg, today, "error", f"failed (${cost:.2f}): {(reply or r.stderr or 'no reply').strip()[:200]}")
    start = re.search(r"^# ", reply, re.M)
    if not start:
        # It declined, or answered oddly. A decline is its choice: keep what it said, and don't ask again until the
        # next brief is due.
        out = unique(OUT / f"{today}.declined.md")
        out.write_text(reply + "\n", encoding="utf-8")
        s.update(last_ok_date=today, tries={})
        save_state(s)
        log(f"no brief this time (${cost:.2f}); its reply is in {out.relative_to(HERE).as_posix()}")
        return 0
    reply = reply[start.start():]
    denied = result.get("permission_denials") or []
    if len(denied) >= 3:
        # Most likely it couldn't read the workspace at all, so its brief is about that, not your work.
        out = unique(OUT / f"{today}.failed.md")
        out.write_text(reply + "\n", encoding="utf-8")
        return failed(s, cfg, today, "reads refused", f"failed (${cost:.2f}): {len(denied)} read(s) refused, e.g. "
                      f"{json.dumps(denied[0])[:200]}; its reply is in {out.relative_to(HERE).as_posix()}")
    if denied:
        log(f"note: {len(denied)} read(s) refused (fine if they were private files): {json.dumps(denied[0])[:200]}")
    brief, phone = split_text(reply)
    path = unique(OUT / f"{today}.md")
    path.write_text(brief, encoding="utf-8")
    s.update(last_ok_date=today, last_ok_ts=started, tries={}, failed_days=[])
    s.pop("paused_until", None)
    save_state(s)
    log(f"wrote {path.relative_to(HERE).as_posix()} (${cost:.2f})")
    if notify.ready(cfg):
        body = (phone_text(phone) if cfg.get("followups_details", True) and phone_text(phone) else
                f"Your follow-up brief is ready: workspace/followups/{path.name}")
        ok = notify.send(f"Follow-ups {today}", body, tags="clipboard", cfg=cfg)
        log("phone notified" if ok else "phone notification failed (the brief is saved)")
    return 0


def unique(p):
    """p, or p with -2, -3... if it exists, so a second brief the same day doesn't replace the first."""
    n = 2
    while p.exists():
        p = p.with_name(f"{p.name.split('.')[0].split('-v')[0]}-v{n}.{'.'.join(p.name.split('.')[1:])}")
        n += 1
    return p


# ---------------------------------------------------------------- scheduling (hourly; run() decides if it's due)
def schedule(action, days=None):
    if action == "on":
        cfg = notify.load()
        if days or not setting(cfg, "followups_every_days", 0, 1, 365):
            cfg["followups_every_days"] = days or 3
            notify.save(cfg)
            print(f"Follow-up briefs every {cfg['followups_every_days']} day(s) (change it: schedule on --days N).")
        if not notify.ready(cfg):
            print("Note: phone notifications are off, so briefs won't be written until you turn them on: "
                  "python3 scripts/notify.py --setup")
    py, script = sys.executable, (HERE / "scripts" / "followups.py").as_posix()
    claude = claude_bin()
    path = f"{Path(claude).parent if claude else '/usr/local/bin'}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"org.welfare-workspace.followups.{KEY}.plist"
        if action == "on":
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{plist.stem}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{script}</string>
    <string>run</string><string>--scheduled</string></array>
  <key>WorkingDirectory</key><string>{HERE.as_posix()}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>{SCHED_LOG.as_posix()}</string>
</dict></plist>
""", encoding="utf-8")
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            subprocess.run(["launchctl", "load", "-w", str(plist)], check=False)
            print("Follow-ups: checked hourly with launchd while your Mac is awake.")
        elif action == "off":
            subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
            print("Follow-ups unscheduled.")
        else:
            print("scheduled (launchd)" if plist.exists() else "not scheduled")
        return 0
    if os.name == "nt":
        name = f"followups-{KEY}"
        if action == "on":
            runner = OUT / ".scheduled-run.cmd"   # keeps schtasks' /TR short and free of nested quotes
            runner.write_text(f'@echo off\r\ncd /d "{HERE}"\r\n"{py}" "{HERE / "scripts" / "followups.py"}" run '
                              f'--scheduled > NUL 2>> "{SCHED_LOG}"\r\n', encoding="utf-8")
            r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "HOURLY", "/TN", name, "/TR", f'"{runner}"'],
                               capture_output=True, text=True)
            print(f"Follow-ups: checked hourly with Task Scheduler ({name})." if r.returncode == 0
                  else f"Task Scheduler refused: {(r.stderr or r.stdout).strip()}")
            return r.returncode
        if action == "off":
            subprocess.run(["schtasks", "/Delete", "/F", "/TN", name], capture_output=True)
            print("Follow-ups unscheduled.")
        else:
            q = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True).returncode == 0
            print("scheduled (Task Scheduler)" if q else "not scheduled")
        return 0
    if not shutil.which("crontab"):
        print("No `crontab` command here. Install cron (e.g. sudo apt install cron), then run: "
              "python3 scripts/followups.py schedule on")
        return 1
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    keep = [l for l in cur.splitlines() if not l.endswith(f"# {TAG}")]
    if action == "status":
        print("scheduled (cron)" if len(keep) < len(cur.splitlines()) else "not scheduled")
        return 0
    if action == "on":
        q = shlex.quote
        keep.append(f"23 * * * * cd {q(HERE.as_posix())} && PATH={q(path)} {q(py)} {q(script)} run --scheduled "
                    f"> /dev/null 2>> {q(SCHED_LOG.as_posix())} # {TAG}")
    subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", text=True, check=True)
    print("Follow-ups: checked hourly with cron." if action == "on" else "Follow-ups unscheduled.")
    if action == "on" and subprocess.run(["pgrep", "-x", "cron"], capture_output=True).returncode != 0:
        print("Note: the cron service doesn't seem to be running. On WSL: sudo service cron start "
              "(or enable systemd so it starts by itself).")
    return 0


def status():
    cfg, s = notify.load(), state()
    days = cfg.get("followups_every_days")
    print(f"Follow-up briefs: {'every ' + str(days) + ' day(s)' if days else 'off'}; phone notifications "
          f"{'on' if notify.ready(cfg) else 'off'}; last brief {s.get('last_ok_date') or 'never'}")
    schedule("status")


def main(a):
    if a[:1] == ["run"]:
        return run(scheduled="--scheduled" in a)
    if a[:1] == ["schedule"] and a[1:2] and a[1] in ("on", "off", "status"):
        days = int(a[a.index("--days") + 1]) if "--days" in a and a.index("--days") + 1 < len(a) \
            and a[a.index("--days") + 1].isdigit() and int(a[a.index("--days") + 1]) >= 1 else None
        return schedule(a[1], days)
    if a[:1] == ["status"]:
        status()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
