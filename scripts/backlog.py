#!/usr/bin/env python3
"""The backlog: ideas, unfinished work and things to look into that aren't worth usage now. Near the end of each
weekly usage cycle, if there's usage left, they're run one by one in full, unattended workspace sessions.

    python3 scripts/backlog.py list [--short]               the open items (--short: one line each)
    python3 scripts/backlog.py add "one-line goal" [--size small|medium|large] [--details "..."] [--by me|claude]
    python3 scripts/backlog.py progress B3 --note "what it adds; what's left" [--path workspace/drafts/x.md]
    python3 scripts/backlog.py done B3 [--result workspace/backlog/results/B3-2026-11-06.md] [--note "..."]
    python3 scripts/backlog.py drop B3 [--note "why"]      no longer wanted
    python3 scripts/backlog.py keep B3                      still wanted (an old item runs again)
    python3 scripts/backlog.py run [--now]                  run due items now (--now: ignore the timing rules)
    python3 scripts/backlog.py schedule on|off|status       check every hour (setup shows this; it doesn't run it)
    python3 scripts/backlog.py status                       settings, usage, and whether a run would start

Files, all in workspace/backlog/: BACKLOG.md (the open items; edit it by hand too: a "## heading" is an item),
PROGRESS.md (work done elsewhere that a later run can build on), DONE.md, and results/ (what backlog runs made).
Settings: my/backlog.md (setup.py --more turns this on and writes it).

When it runs: the hourly check starts only if the weekly usage reset is close (24 hours by default), weekly usage is
below the start line (85%), and, unless my/backlog.md says otherwise, tech watch and the eval harness aren't
running and tech watch has done today's run. It then runs items until weekly usage reaches the stop line (90%),
the 5-hour session is nearly used up, or the items run out. Usage is read the way /usage shows it, through the eval
harness; if it can't be read, nothing runs.

Each item runs as a normal workspace session (CLAUDE.md, subagents, critics, verifier) with no one to ask:
anything that would ask you first is refused, so it can't send, post or share. Results go to workspace/backlog/
results/, never over your drafts, and the next session start (and your phone, if notifications are on) says so.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from style import S  # noqa: E402  (readable colours in a terminal; plain text otherwise)
except ImportError:
    class S:
        ok = warn = ask = b = staticmethod(str)

HERE = Path(__file__).resolve().parents[1]
DIR = HERE / "workspace" / "backlog"
ITEMS, PROGRESS, DONE, RESULTS = DIR / "BACKLOG.md", DIR / "PROGRESS.md", DIR / "DONE.md", DIR / "results"
STATE, LOG, LOCK = DIR / ".state.json", DIR / ".log", DIR / ".run.lock"
SETTINGS = HERE / "my" / "backlog.md"
KEY = hashlib.sha256(HERE.as_posix().encode()).hexdigest()[:8]
TAG = f"backlog:{HERE.as_posix()}"
SESSION_CEILING = 90          # don't start an item when the 5-hour session is past this
DEFAULTS = {"backlog": "off", "claude adds items": "yes", "start when the weekly reset is within (hours)": "24",
            "start below weekly usage (%)": "85", "stop at weekly usage (%)": "90",
            "tech watch and evals first": "yes", "most usage per item ($)": "15",
            "ask me about items older than (days)": "21", "weekly reset (if it can't be read)": ""}
HEAD = ("# Backlog\n\nIdeas, unfinished work and things to look into, saved for spare weekly usage. One item per "
        "\"## \" heading; add your own the same way (an id is given on the next change). Run order: yours first, "
        "then Claude's, oldest first.\n")


# ---------------------------------------------------------------- settings and state

def settings():
    s = dict(DEFAULTS)
    if SETTINGS.exists():
        for m in re.finditer(r"^\*\*(.+?):\*\*[ \t]*(.*)$", SETTINGS.read_text(encoding="utf-8"), re.M):
            s[m.group(1).strip().lower()] = m.group(2).strip()
    return s


def on(s=None):
    return (s or settings())["backlog"].lower().startswith("on")


def num(s, key):
    try:
        return float(re.findall(r"[\d.]+", s[key])[0])
    except (IndexError, ValueError):
        return float(DEFAULTS[key] or 0)


def state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st):
    DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")


def log(msg):
    DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M} {msg}\n")


# ---------------------------------------------------------------- the items file

def parse():
    """[{"id", "title", "added", "by", "size", "details", "kept"}], and gives hand-written items an id."""
    if not ITEMS.exists():
        return []
    text = ITEMS.read_text(encoding="utf-8")
    blocks = re.split(r"^## ", text, flags=re.M)[1:]
    items, used = [], {int(m) for m in re.findall(r"^## B(\d+)\b", text, re.M)}
    used |= {int(m) for f in (DONE, PROGRESS) if f.exists() for m in re.findall(r"\bB(\d+)\b", f.read_text("utf-8"))}
    nxt = max(used, default=0) + 1
    changed = False
    for b in blocks:
        head, _, rest = b.partition("\n")
        m = re.match(r"(B\d+)\s*[·:-]\s*(.*)", head.strip())
        if m:
            iid, title = m.group(1), m.group(2).strip()
        else:
            iid, title, changed, nxt = f"B{nxt}", head.strip(), True, nxt + 1
        meta = re.search(r"^added (\d{4}-\d\d-\d\d) by (\w+)(?: · size (\w+))?(?: · kept (\d{4}-\d\d-\d\d))?", rest, re.M)
        details = re.sub(r"^added \d{4}-\d\d-\d\d by .*$\n?", "", rest, count=1, flags=re.M).strip()
        if not meta:
            changed = True
        items.append({"id": iid, "title": title, "added": meta.group(1) if meta else date.today().isoformat(),
                      "by": meta.group(2) if meta else "me", "size": (meta.group(3) if meta else None) or "medium",
                      "kept": meta.group(4) if meta and meta.group(4) else "", "details": details})
    if changed:
        write(items)
    return items


def write(items):
    DIR.mkdir(parents=True, exist_ok=True)
    out = [HEAD]
    for i in items:
        out.append(f"\n## {i['id']} · {i['title']}\nadded {i['added']} by {i['by']} · size {i['size']}"
                   + (f" · kept {i['kept']}" if i.get("kept") else "") + "\n" + (i["details"] + "\n" if i["details"] else ""))
    ITEMS.write_text("".join(out), encoding="utf-8")


def find(items, iid):
    iid = iid.upper() if iid.upper().startswith("B") else f"B{iid}"
    for i in items:
        if i["id"] == iid:
            return i
    sys.exit(f"No open item {iid} (python3 scripts/backlog.py list).")


def stale(i, s=None):
    days = num(s or settings(), "ask me about items older than (days)")
    since = i.get("kept") or i["added"]
    return days > 0 and (date.today() - date.fromisoformat(since)).days > days


def append(f, title, line):
    DIR.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_text(title, encoding="utf-8")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


def progress_for(iid):
    if not PROGRESS.exists():
        return []
    return [l for l in PROGRESS.read_text(encoding="utf-8").splitlines() if re.match(rf"- {iid}\b", l)]


def short(limit=8):
    """The session-start version: one line per item, at most `limit`."""
    items = parse()
    if not items:
        return []
    s = settings()
    order = sorted(items, key=lambda i: (i["by"] != "me", i["added"]))
    lines = [f"{i['id']} ({i['size']}{', old: still wanted?' if stale(i, s) else ''}"
             f"{', has progress' if progress_for(i['id']) else ''}): {i['title'][:80]}" for i in order[:limit]]
    if len(items) > limit:
        lines.append(f"... and {len(items) - limit} more (workspace/backlog/BACKLOG.md)")
    return lines


# ---------------------------------------------------------------- commands for sessions and the person

def cmd_add(a):
    s = settings()
    if a.by == "claude" and not s["claude adds items"].lower().startswith("y"):
        sys.exit("The person approves backlog items themselves (my/backlog.md: \"Claude adds items: suggest only\"). "
                 "Suggest it to them instead; they can add it with: python3 scripts/backlog.py add \"...\"")
    items = parse()
    used = [int(i["id"][1:]) for i in items] + [int(m) for f in (DONE, PROGRESS) if f.exists()
                                                for m in re.findall(r"\bB(\d+)\b", f.read_text("utf-8"))]
    iid = f"B{max(used, default=0) + 1}"
    items.append({"id": iid, "title": " ".join(a.title.split()), "added": date.today().isoformat(), "by": a.by,
                  "size": a.size, "kept": "", "details": (a.details or "").strip()})
    write(items)
    print(f"Added {iid}: {a.title}")


def cmd_progress(a):
    i = find(parse(), a.id)
    append(PROGRESS, "# Backlog progress\n\nWork done in other sessions that a backlog run can build on. One line "
           "each: item, date, where the work is, what it adds and what's left.\n\n",
           f"- {i['id']} · {date.today()} · {a.path or '-'} · {' '.join(a.note.split())}")
    print(f"Noted progress on {i['id']}.")


def close(a, how):
    items = parse()
    i = find(items, a.id)
    write([x for x in items if x["id"] != i["id"]])
    append(DONE, "# Backlog: done and dropped\n\n",
           f"- {i['id']} · {how} {date.today()} · {i['title']}" + (f" · result: {a.result}" if getattr(a, "result", None)
                                                                     else "") + (f" · {a.note}" if a.note else ""))
    print(f"{i['id']} {how}.")


def cmd_keep(a):
    items = parse()
    i = find(items, a.id)
    i["kept"] = date.today().isoformat()
    write(items)
    print(f"{i['id']} kept: it can run again.")


def cmd_list(a):
    if a.short:
        print("\n".join(short(1000)) or "The backlog is empty.")
        return
    items = parse()
    if not items:
        print("The backlog is empty.")
    for i in sorted(items, key=lambda i: (i["by"] != "me", i["added"])):
        print(S.b(f"{i['id']} · {i['title']}") + f"  ({i['size']}, added {i['added']} by {i['by']}"
              + (", old: still wanted? (keep or drop)" if stale(i) else "") + ")")
        for p in progress_for(i["id"]):
            print("    " + p[2:])


# ---------------------------------------------------------------- when to run

def harness():
    for h in (HERE.parent / "ai-welfare-eval-harness", HERE / "ai-welfare-eval-harness"):
        if (h / "evals" / "ClaudeImproverHarness" / "run_agent.py").exists():
            return h / "evals" / "ClaudeImproverHarness"
    return None


def usage():
    """{"seven_day": %, "five_hour": %, "week_resets_at": iso or None} from the eval harness's reader, or None."""
    h = harness()
    if not h:
        return None
    sys.path.insert(0, str(h))
    try:
        import run_agent   # noqa: E402  (reads plan usage the way /usage shows it, with a shared cache)
        return run_agent.plan_usage()
    except Exception as e:   # noqa: BLE001  any failure means: don't run
        log(f"couldn't read plan usage: {e!r}")
        return None


def week_reset(u, s):
    """When the weekly limit resets (aware datetime), from the reading or the 'weekly reset' setting, or None."""
    if u and u.get("week_resets_at"):
        try:
            return datetime.fromisoformat(str(u["week_resets_at"]).replace("Z", "+00:00"))
        except ValueError:
            pass
    m = re.match(r"(\w+)\s+(\d{1,2}):(\d\d)", s["weekly reset (if it can't be read)"])
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if not m or m.group(1).lower() not in days:
        return None
    now = datetime.now().astimezone()
    d = (days.index(m.group(1).lower()) - now.weekday()) % 7
    t = now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0) + timedelta(days=d)
    return t if t > now else t + timedelta(days=7)


def others_busy():
    """Why tech watch or the eval harness should go first right now, or None."""
    tw = HERE / "techwatch" / "state"
    lock = tw / "run.lock"
    if lock.exists():
        try:
            pid, started = lock.read_text().split()
            if time.time() - float(started) < 6 * 3600:
                return "tech watch is running"
        except (OSError, ValueError):
            pass
    h = harness()
    if h and (h / "runs" / ".agent-runs.lock").exists():
        try:
            import fcntl
            with open(h / "runs" / ".agent-runs.lock") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(f, fcntl.LOCK_UN)
                except BlockingIOError:
                    return "the eval harness is running agent batches"
        except ImportError:   # Windows: no flock; tech watch's lock and daily run still go first
            pass
    prefs = (HERE / "my" / "techwatch.md")
    auto = prefs.exists() and re.search(r"^\*\*Tech watch runs:\*\*\s*automatic", prefs.read_text("utf-8"), re.M | re.I)
    try:
        last = (tw / "last_auto").read_text().strip()
    except OSError:
        last = ""
    if auto and last and last != date.today().isoformat():
        return "tech watch hasn't done today's run yet"
    return None


def trusted():
    """Whether Claude Code trusts this folder. Until you trust it (by opening `claude` here once and accepting), a
    headless session ignores the workspace's allow rules, so a backlog run could do nothing at all."""
    try:
        projects = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8")).get("projects", {})
    except (OSError, ValueError):
        return False
    return any((projects.get(k) or {}).get("hasTrustDialogAccepted") for k in {str(HERE), HERE.as_posix()})


def why_not(s, u, now=None):
    """None if a run should start now, else the reason it doesn't."""
    if not on(s):
        return "the backlog is off (my/backlog.md)"
    if not [i for i in parse() if not stale(i, s)]:
        return "no items to run (old ones wait for keep or drop)"
    if not trusted():
        return ("Claude Code doesn't trust this folder yet, so an unattended session couldn't use the workspace's "
                "rules: open `claude` here once and accept the trust question")
    if s["tech watch and evals first"].lower().startswith("y"):
        busy = others_busy()
        if busy:
            return busy
    if not u:
        return "plan usage can't be read (it's read through the eval harness, beside this folder)"
    reset = week_reset(u, s)
    if not reset:
        return "the weekly reset time isn't known: set \"Weekly reset (if it can't be read)\" in my/backlog.md"
    now = now or datetime.now(timezone.utc)
    hours = (reset - now).total_seconds() / 3600
    if hours > num(s, "start when the weekly reset is within (hours)"):
        return f"the weekly reset is {hours:.0f} hours away"
    if u["seven_day"] >= num(s, "start below weekly usage (%)"):
        return f"weekly usage is at {u['seven_day']:g}% (starts below {num(s, 'start below weekly usage (%)'):g}%)"
    if u.get("five_hour", 0) >= SESSION_CEILING:
        return f"the 5-hour session is at {u['five_hour']:g}%; trying again next hour"
    return None


# ---------------------------------------------------------------- running items

def claude_bin():
    found = shutil.which("claude")
    for p in ([found] if found else []) + [str(Path.home() / ".local/bin/claude"), "/usr/local/bin/claude",
                                           "/opt/homebrew/bin/claude"]:
        if p and Path(p).exists():
            return p
    return None


def prompt_for(i):
    today = date.today().isoformat()
    prog = progress_for(i["id"])
    tpl = (HERE / "scripts" / "backlog_prompt.md").read_text(encoding="utf-8")
    return (tpl.replace("{id}", i["id"]).replace("{date}", today).replace("{title}", i["title"])
            .replace("{details}", i["details"] or "(none)")
            .replace("{progress}", "\n".join(prog) if prog else "(none)"))


def run_item(i, s):
    claude = claude_bin()
    if not claude:
        log("can't find the `claude` command")
        return False
    before = len(progress_for(i["id"]))
    cmd = [claude, "-p", "--permission-mode", "dontAsk", "--max-budget-usd", str(num(s, "most usage per item ($)")),
           "--output-format", "json"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["AI_WELFARE_BACKLOG_RUN"] = i["id"]
    log(f"{i['id']}: start ({i['title'][:80]})")
    try:
        r = subprocess.run(cmd, input=prompt_for(i), cwd=HERE, env=env, capture_output=True, text=True,
                           timeout=3 * 3600)
        res = json.loads(r.stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        log(f"{i['id']}: stopped ({type(e).__name__})")
        res, r = {"result": f"stopped: {type(e).__name__}"}, None
    cost = float(res.get("total_cost_usd") or 0)
    still_open = any(x["id"] == i["id"] for x in parse())
    recorded = not still_open or len(progress_for(i["id"])) > before
    if still_open and not recorded:   # the run didn't record anything itself: keep its reply where a later run sees it
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"{i['id']}-{date.today()}-reply.md"
        out.write_text(str(res.get("result") or "(no reply)") + "\n", encoding="utf-8")
        append(PROGRESS, "# Backlog progress\n\n", f"- {i['id']} · {date.today()} · {out.relative_to(HERE).as_posix()} · "
               "backlog run ended without recording a result; its last message is in that file")
    denied = len(res.get("permission_denials") or [])
    log(f"{i['id']}: {'done' if not still_open else 'progress recorded'} (${cost:.2f}"
        + (f"; {denied} action(s) refused, as nobody could approve them" if denied else "") + ")")
    return True


def cmd_run(a, scheduled=False):
    s = settings()
    if not scheduled and not on(s):
        sys.exit("The backlog is off. Turn it on in my/backlog.md (\"Backlog: on\"), or with python3 setup.py --more.")
    u = usage()
    reason = why_not(s, u) if not a.now else (None if u else "plan usage can't be read")
    if reason:
        if not scheduled:
            print(f"Not running: {reason}.")
        return 0
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
        os.close(fd)
    except FileExistsError:
        try:
            if time.time() - float(LOCK.read_text().split()[1]) < 4 * 3600:
                return 0
        except (OSError, ValueError, IndexError):
            pass
        LOCK.unlink(missing_ok=True)
        return 0
    ran, st = [], state()
    try:
        for i in sorted([x for x in parse() if not stale(x, s)], key=lambda x: (x["by"] != "me", x["added"])):
            tries = st.setdefault("tries", {}).get(i["id"], 0)
            if tries >= 2:
                continue
            u = usage()
            if not u or u["seven_day"] >= num(s, "stop at weekly usage (%)") or u.get("five_hour", 0) >= SESSION_CEILING:
                log(f"stopping: weekly {u and u['seven_day']}%, session {u and u.get('five_hour')}%")
                break
            st["tries"][i["id"]] = tries + 1
            save_state(st)
            if run_item(i, s):
                ran.append(i["id"])
    finally:
        LOCK.unlink(missing_ok=True)
    if ran:
        st.setdefault("unseen", []).extend(ran)
        save_state(st)
        try:
            import notify
            cfg = notify.load()
            if notify.ready(cfg):
                notify.send("Backlog", f"Backlog runs finished: {', '.join(ran)}. Results: workspace/backlog/",
                            tags="inbox_tray", cfg=cfg)
        except Exception:   # noqa: BLE001  a notification failing doesn't matter
            pass
    if not scheduled:
        print(f"Ran {', '.join(ran)}." if ran else "Nothing ran (see workspace/backlog/.log).")
    return 0


def cmd_status(a):
    s = settings()
    print(S.b("Backlog: ") + ("on" if on(s) else "off") + f"  ({len(parse())} open item(s))")
    for k in list(DEFAULTS)[1:]:
        print(f"  {k}: {s[k] or '-'}")
    u = usage()
    if u:
        r = week_reset(u, s)
        print(f"  usage now: weekly {u['seven_day']:g}%, 5-hour session {u.get('five_hour', 0):g}%; weekly reset: "
              + (f"{r.astimezone():%a %d %b %H:%M}" if r else "unknown"))
    why = why_not(s, u)
    print("  " + (S.ok("A run would start now.") if not why else f"A run wouldn't start now: {why}."))
    schedule("status")


# ---------------------------------------------------------------- scheduling (hourly, like tech watch)

def schedule(action):
    py, script = sys.executable, (HERE / "scripts" / "backlog.py").as_posix()
    claude = claude_bin()
    path = f"{Path(claude).parent if claude else '/usr/local/bin'}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    DIR.mkdir(parents=True, exist_ok=True)
    errlog = DIR / ".scheduler.log"
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"org.welfare-workspace.backlog.{KEY}.plist"
        if action == "on":
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{plist.stem}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{script}</string><string>scheduled</string></array>
  <key>WorkingDirectory</key><string>{HERE.as_posix()}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>{errlog.as_posix()}</string>
</dict></plist>
""", encoding="utf-8")
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            subprocess.run(["launchctl", "load", "-w", str(plist)], check=False)
            print(S.ok("Backlog: checked hourly with launchd while your Mac is awake."))
        elif action == "off":
            subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
            print("Backlog unscheduled.")
        else:
            print("  " + ("scheduled (launchd)" if plist.exists() else "not scheduled"))
        return 0
    if os.name == "nt":
        name = f"backlog-{KEY}"
        if action == "on":
            runner = DIR / ".scheduled-run.cmd"
            runner.write_text(f'@echo off\r\ncd /d "{HERE}"\r\n"{py}" "{HERE / "scripts" / "backlog.py"}" scheduled '
                              f'> NUL 2>> "{errlog}"\r\n', encoding="utf-8")
            r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "HOURLY", "/TN", name, "/TR", f'"{runner}"'],
                               capture_output=True, text=True)
            print(S.ok(f"Backlog: checked hourly with Task Scheduler ({name}).") if r.returncode == 0
                  else S.warn(f"Task Scheduler refused: {(r.stderr or r.stdout).strip()}"))
            return r.returncode
        if action == "off":
            subprocess.run(["schtasks", "/Delete", "/F", "/TN", name], capture_output=True)
            print("Backlog unscheduled.")
        else:
            q = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True).returncode == 0
            print("  " + ("scheduled (Task Scheduler)" if q else "not scheduled"))
        return 0
    if not shutil.which("crontab"):
        print("  No `crontab` command here. Install cron (e.g. sudo apt install cron), then run: "
              "python3 scripts/backlog.py schedule on")
        return 1
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    keep = [l for l in cur.splitlines() if not l.endswith(f"# {TAG}")]
    if action == "status":
        print("  " + ("scheduled (cron)" if len(keep) < len(cur.splitlines()) else "not scheduled"))
        return 0
    if action == "on":
        q = shlex.quote
        keep.append(f"41 * * * * cd {q(HERE.as_posix())} && PATH={q(path)} {q(py)} {q(script)} scheduled "
                    f"> /dev/null 2>> {q(errlog.as_posix())} # {TAG}")
    subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", text=True, check=True)
    print(S.ok("Backlog: checked hourly with cron.") if action == "on" else "Backlog unscheduled.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--short", action="store_true")
    p = sub.add_parser("add"); p.add_argument("title"); p.add_argument("--details", default="")
    p.add_argument("--size", choices=["small", "medium", "large"], default="medium")
    p.add_argument("--by", choices=["me", "claude"], default=None)
    p = sub.add_parser("progress"); p.add_argument("id"); p.add_argument("--note", required=True); p.add_argument("--path")
    p = sub.add_parser("done"); p.add_argument("id"); p.add_argument("--result"); p.add_argument("--note", default="")
    p = sub.add_parser("drop"); p.add_argument("id"); p.add_argument("--note", default="")
    p = sub.add_parser("keep"); p.add_argument("id")
    p = sub.add_parser("run"); p.add_argument("--now", action="store_true")
    sub.add_parser("scheduled")
    p = sub.add_parser("schedule"); p.add_argument("action", choices=["on", "off", "status"])
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "add" and a.by is None:   # a Claude session adds as claude; you, typing it, as me
        a.by = "claude" if os.environ.get("CLAUDECODE") or os.environ.get("AI_WELFARE_BACKLOG_RUN") else "me"
    return {"list": cmd_list, "add": cmd_add, "progress": cmd_progress, "done": lambda a: close(a, "done"),
            "drop": lambda a: close(a, "dropped"), "keep": cmd_keep, "run": cmd_run, "status": cmd_status,
            "scheduled": lambda a: cmd_run(argparse.Namespace(now=False), scheduled=True),
            "schedule": lambda a: schedule(a.action)}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
