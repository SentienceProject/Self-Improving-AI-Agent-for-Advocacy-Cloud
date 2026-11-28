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
    python3 scripts/backlog.py answer normal|all|skip       this week: keep the plan, use all usage left, or skip
    python3 scripts/backlog.py schedule on|off|status       check every hour (setup shows this; it doesn't run it)
    python3 scripts/backlog.py status                       settings, usage, and whether a run would start

Files, all in workspace/backlog/: BACKLOG.md (the open items; edit it by hand too: a "## heading" is an item),
PROGRESS.md (work done elsewhere that a later run can build on), DONE.md, and results/ (what backlog runs made).
Settings: my/backlog.md (setup.py --more turns this on and writes it).

When it runs: the hourly check starts only if the weekly usage reset is close (24 hours by default), it's within
your quiet hours (the hours you don't use Claude, e.g. 00:00-06:00; if you give none, any hour), weekly usage is
below the cap (90%), and, unless my/backlog.md says otherwise, tech watch and the eval harness aren't running and
tech watch has done today's run. It then runs items until weekly usage reaches the cap, the 5-hour session reaches
your share of it (90%), quiet hours end, or the items run out. Usage is checked every few minutes while an item
runs, and the item is stopped if usage reaches the cap (a later run picks it up). Usage is read the way /usage
shows it, through the eval harness; if it can't be read, nothing runs.

The day before the reset (once a week, outside your quiet hours), it tells you the plan, on your phone if
notifications are on, with buttons to use all the usage left now or to skip this week, and otherwise at the next
session start, where Claude asks you. No answer keeps the plan. Answers from the phone go to a second ntfy topic
(your topic + "-reply") that the hourly check reads.

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
ALL = 98                      # "use it all": the caps, leaving a sliver so a run isn't cut off by the plan's limit
CHECK_EVERY = 120             # seconds between usage checks while an item runs (readings are cached this long)
CHOICES = ("normal", "all", "skip")
DEFAULTS = {"backlog": "off", "claude adds items": "yes", "start when the weekly reset is within (hours)": "24",
            "quiet hours": "", "weekly usage cap (%)": "90",
            "most of each 5-hour session (%)": "90", "ask me the day before the reset": "yes",
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
        if "weekly usage cap (%)" not in re.sub(r"<!--.*?-->", "", SETTINGS.read_text(encoding="utf-8"), flags=re.S).lower() \
                and s.get("stop at weekly usage (%)"):   # an older settings file: its stop line is the cap
            s["weekly usage cap (%)"] = s["stop at weekly usage (%)"]
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


def usage(tries=3):
    """{"seven_day": %, "five_hour": %, "week_resets_at": iso or None} from the eval harness's reader, or None."""
    h = harness()
    if not h:
        return None
    sys.path.insert(0, str(h))
    try:
        import run_agent   # noqa: E402  (reads plan usage the way /usage shows it, with a shared cache)
        return run_agent.plan_usage(tries=tries)
    except Exception as e:   # noqa: BLE001  any failure means: don't run
        log(f"couldn't read plan usage: {e!r}")
        return None


def week_reset(u, s, now=None):
    """When the weekly limit resets (aware datetime), from the reading or the 'weekly reset' setting, or None."""
    now = (now or datetime.now(timezone.utc)).astimezone()
    if u and u.get("week_resets_at"):
        t = parse_time(u["week_resets_at"])
        if t and t > now:   # one that has passed tells nothing about the next
            return t
    m = re.match(r"(\w+)\s+(\d{1,2}):(\d\d)", s["weekly reset (if it can't be read)"])
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if not m or m.group(1).lower() not in days or int(m.group(2)) > 23 or int(m.group(3)) > 59:
        return None
    for add in range((days.index(m.group(1).lower()) - now.weekday()) % 7, 15, 7):
        d = now.date() + timedelta(days=add)
        t = datetime(d.year, d.month, d.day, int(m.group(2)), int(m.group(3))).astimezone()   # that date's own offset
        if t > now:          # (so a week in which the clocks change still comes out right)
            return t
    return None


def parse_time(v):
    """An aware datetime from an ISO time (no zone: UTC; any number of fraction digits), or None."""
    v = re.sub(r"(\.\d{1,6})\d*", lambda m: m.group(1).ljust(7, "0"), str(v or "").strip().replace("Z", "+00:00"))
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


_TIME = r"(\d{1,2})(?:[:.h]?(\d\d))?\s*(am|pm|a\.m\.|p\.m\.|h)?"
QUIET_RE = re.compile(rf"\s*{_TIME}\s*(?:-|\u2013|\u2014|to|until|till)\s*{_TIME}\s*")


def parse_quiet(text):
    """Quiet hours as (start, end) minutes after midnight; None if empty; ValueError if the text can't be read.
    Accepts 00:00-06:00, 0000-0600, 10pm-6am, 1am to 5am, 22h-6h; refuses unclear ones like "10-6"."""
    if not (text or "").strip():
        return None
    m = QUIET_RE.fullmatch(text.lower())
    if not m:
        raise ValueError(text)
    out = []
    for h, mi, ap in (m.groups()[:3], m.groups()[3:]):
        h, mi, ap = int(h), int(mi or 0), (ap or "").replace(".", "")
        if ap in ("am", "pm"):
            if not 1 <= h <= 12:
                raise ValueError(text)
            h = h % 12 + (12 if ap == "pm" else 0)
        if h == 24 and mi == 0:
            h = 0
        if h > 23 or mi > 59:
            raise ValueError(text)
        out.append(h * 60 + mi)
    if out[0] == out[1]:
        raise ValueError(text)
    if not (m.group(3) or m.group(6)) and not (m.group(2) or m.group(5)) and out[0] > out[1] \
            and out[0] < 13 * 60 and not m.group(1).startswith("0"):
        raise ValueError(text)   # "10-6": 10am-6am is 20 hours, 10pm-6am is 8; ask instead of guessing
    return tuple(out)


def normal_quiet(text):
    """Quiet hours written as HH:MM-HH:MM ("" for none). ValueError if unreadable."""
    q = parse_quiet(text)
    return "" if not q else "-".join(f"{x // 60:02d}:{x % 60:02d}" for x in q)


def quiet_error(s):
    """Why the quiet hours setting can't be used, or None. Unreadable quiet hours never mean "any hour"."""
    try:
        parse_quiet(s["quiet hours"])
        return None
    except ValueError:
        return (f"the quiet hours in my/backlog.md (\"{s['quiet hours']}\") can't be read: write them like "
                "00:00-06:00 or 10pm-6am")


def quiet(s):
    """Quiet hours as (start, end) in minutes after midnight, local time, or None (none, or unreadable)."""
    try:
        return parse_quiet(s["quiet hours"])
    except ValueError:
        return None


def in_quiet(s, now=None):
    """Whether it's within the quiet hours now (always true if there are none; never if they can't be read).
    They may pass midnight."""
    if quiet_error(s):
        return False
    q = quiet(s)
    if not q:
        return True
    t = (now or datetime.now(timezone.utc)).astimezone()
    m, (a, b) = t.hour * 60 + t.minute, q
    return a <= m < b if a < b else (m >= a or m < b)


def week_key(reset):
    """One key per weekly cycle: the reset's date (UTC), so small changes in the reported time don't matter."""
    return reset.astimezone(timezone.utc).date().isoformat()


def answer(st, reset):
    """This cycle's answer: "normal" (the default), "all" or "skip"."""
    a = st.get("answer") or {}
    return a.get("choice", "normal") if reset and a.get("for") == week_key(reset) else "normal"


def limits(s, choice, u=None):
    """(weekly usage cap, most of the 5-hour session), in %: no item starts at or past them, and a running one stops.
    "Use it all" raises both to 98%, unless paid extra usage is on: then the plan's limit wouldn't stop a run, so the
    normal caps stay."""
    if choice == "all" and not (u and u.get("extra_usage")):
        return ALL, ALL
    return num(s, "weekly usage cap (%)"), num(s, "most of each 5-hour session (%)")


def over(u, caps):
    """Why usage is at a cap, or None."""
    cap, sess = caps
    if u["seven_day"] >= cap:
        return f"weekly usage is at {u['seven_day']:g}% (the backlog's cap is {cap:g}%)"
    if (u.get("five_hour") or 0) >= sess:
        return f"the 5-hour session is at {u['five_hour']:g}% (the backlog uses up to {sess:g}%)"
    return None


def plan(s):
    """The usual plan, in words."""
    stop, sess = limits(s, "normal")
    if quiet_error(s):
        return "nothing runs until the quiet hours in my/backlog.md can be read (write them like 00:00-06:00)"
    when = f"in your quiet hours ({normal_quiet(s['quiet hours'])})" if quiet(s) else "from now"
    return f"items run {when} until weekly usage reaches {stop:g}% (using at most {sess:g}% of each 5-hour session)"


def ask(s, u, st, now=None):
    """The day before the reset, once a cycle and outside quiet hours (when you're likely around): say what's
    planned, with phone buttons to use everything left now or skip this week. True if it asked."""
    if not on(s) or not u or not s["ask me the day before the reset"].lower().startswith("y"):
        return False
    now = now or datetime.now(timezone.utc)
    reset = week_reset(u, s, now)
    if not reset or not 0 < (reset - now).total_seconds() / 3600 <= num(s, "start when the weekly reset is within (hours)"):
        return False
    if st.get("asked") == week_key(reset) or (quiet(s) and in_quiet(s, now)):
        return False
    items = [i for i in parse() if not stale(i, s)]
    if not items or over(u, limits(s, "normal")) or answer(st, reset) != "normal":
        return False
    st.update(asked=week_key(reset), asked_reset=reset.isoformat(), asked_at=time.time())
    save_state(st)
    log(f"asked about this cycle (reset {reset.astimezone():%a %H:%M}, weekly {u['seven_day']:g}%)")
    try:
        import notify
        cfg = notify.load()
        if notify.ready(cfg):
            notify.send("Backlog: weekly reset soon",
                        f"Your weekly usage resets {reset.astimezone():%a %H:%M} and is at {u['seven_day']:g}%. "
                        f"{len(items)} backlog item(s) wait. Plan: {plan(s)}. Ignore this to keep the plan, or tap: "
                        + ("skip this week. (Paid extra usage is on, so \"use it all\" isn't offered: the plan's "
                           "limit wouldn't stop it.)" if u.get("extra_usage") else
                           "use all the usage left (starts within the hour), or skip this week."), tags="hourglass",
                        cfg=cfg, actions=([] if u.get("extra_usage") else [("Use it all", "backlog:all")])
                        + [("Skip this week", "backlog:skip"), ("Keep the plan", "backlog:normal")])
    except Exception:   # noqa: BLE001  the session start asks too
        pass
    return True


def read_replies(st):
    """Take an answer tapped on the phone since the question, if there is one."""
    if not st.get("asked") or not st.get("asked_reset"):
        return
    try:
        if datetime.fromisoformat(st["asked_reset"]) <= datetime.now(timezone.utc):
            return
        import notify
        got = notify.replies(st["asked_at"] - 60)
    except Exception:   # noqa: BLE001
        return
    for t, msg in got:
        m = re.fullmatch(r"backlog:(normal|all|skip)", msg.strip().lower())
        if m and t > (st.get("answer") or {}).get("at", 0):
            st["answer"] = {"for": st["asked"], "choice": m.group(1), "at": t}
            log(f"answer from the phone: {m.group(1)}")
    save_state(st)


def question_for_session(s):
    """For the session start: the day-before question, if it's open and no session has passed it on yet."""
    st = state()
    if not st.get("asked") or st.get("asked_in_session") == st["asked"]:
        return None
    try:
        reset = datetime.fromisoformat(st["asked_reset"])
    except (KeyError, ValueError):
        return None
    if reset <= datetime.now(timezone.utc) or (st.get("answer") or {}).get("for") == st["asked"]:
        return None
    st["asked_in_session"] = st["asked"]
    save_state(st)
    return (f"Backlog: the weekly usage reset is {reset.astimezone():%a %H:%M} and usage is left. Plan: {plan(s)}. "
            "Ask the person whether they'd like that, all the usage left used now (python3 scripts/backlog.py "
            "answer all), or no backlog runs this week (answer skip). Keeping the plan needs no answer.")


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


def why_not(s, u, now=None, st=None):
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
    reset = week_reset(u, s, now)
    if not reset:
        return "the weekly reset time isn't known: set \"Weekly reset (if it can't be read)\" in my/backlog.md"
    now = now or datetime.now(timezone.utc)
    hours = (reset - now).total_seconds() / 3600
    if hours > num(s, "start when the weekly reset is within (hours)"):
        return f"the weekly reset is {hours:.0f} hours away"
    choice = answer(state() if st is None else st, reset)
    if choice == "skip":
        return "you said to skip the backlog this cycle"
    if choice != "all" and quiet_error(s):
        return quiet_error(s)
    if choice != "all" and not in_quiet(s, now):
        return f"it's outside your quiet hours ({normal_quiet(s['quiet hours'])})"
    why = over(u, limits(s, choice, u))
    return why and why + "; trying again next hour"


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


def stop_proc(p):
    """Stop a backlog session and the subagents it started."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            import signal
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def watch(p, caps, limit=3 * 3600):
    """Wait for the session, checking usage every CHECK_EVERY seconds. (stdout, why it was stopped or None)."""
    start, out = time.time(), ""
    while True:
        try:
            out, _ = p.communicate(timeout=CHECK_EVERY)
            return out, None
        except subprocess.TimeoutExpired:
            pass
        u = usage(tries=1)   # unreadable for a moment: carry on (the per-item cap still holds)
        why = (over(u, caps) if u else None) or ("it ran for 3 hours" if time.time() - start > limit else None)
        if why:
            stop_proc(p)
            try:
                out, _ = p.communicate(timeout=60)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                out = ""
            return out, why


def run_item(i, s, caps=None):
    """True if it ran, "capped" if it was stopped at a usage cap, False if it couldn't start."""
    claude = claude_bin()
    if not claude:
        log("can't find the `claude` command")
        return False
    caps = caps or limits(s, "normal")
    before = len(progress_for(i["id"]))
    cmd = [claude, "-p", "--permission-mode", "dontAsk", "--max-budget-usd", str(num(s, "most usage per item ($)")),
           "--output-format", "json"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["AI_WELFARE_BACKLOG_RUN"] = i["id"]
    log(f"{i['id']}: start ({i['title'][:80]})")
    stopped = None
    prompt = DIR / f".prompt-{i['id']}.md"
    prompt.write_text(prompt_for(i), encoding="utf-8")
    try:
        with open(prompt, encoding="utf-8") as fin:
            p = subprocess.Popen(cmd, stdin=fin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=HERE, env=env,
                                 text=True, start_new_session=os.name != "nt")
            out, stopped = watch(p, caps)
        res = json.loads(out or "{}") if not stopped else {}
    except (ValueError, OSError) as e:
        log(f"{i['id']}: stopped ({type(e).__name__})")
        res = {"result": f"stopped: {type(e).__name__}"}
    finally:
        prompt.unlink(missing_ok=True)
    if stopped:
        log(f"{i['id']}: stopped because {stopped}")
        res = {"result": f"The backlog stopped this run partway because {stopped}. Files it made (e.g. in "
                         "workspace/drafts/ or workspace/logs/) may hold partial work; check them before starting over."}
    cost = float(res.get("total_cost_usd") or 0)
    still_open = any(x["id"] == i["id"] for x in parse())
    recorded = not still_open or len(progress_for(i["id"])) > before
    if still_open and not recorded:   # the run didn't record anything itself: keep its reply where a later run sees it
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"{i['id']}-{date.today()}-reply.md"
        out.write_text(str(res.get("result") or "(no reply)") + "\n", encoding="utf-8")
        append(PROGRESS, "# Backlog progress\n\n", f"- {i['id']} · {date.today()} · {out.relative_to(HERE).as_posix()} · "
               + ("backlog run stopped at the usage cap before recording a result; see that file"
                  if stopped and "usage" in stopped else
                  "backlog run ended without recording a result; its last message is in that file"))
    denied = len(res.get("permission_denials") or [])
    log(f"{i['id']}: {'done' if not still_open else 'progress recorded'} (${cost:.2f}"
        + (f"; {denied} action(s) refused, as nobody could approve them" if denied else "") + ")")
    return "capped" if stopped and "usage" in stopped else True


def cmd_run(a, scheduled=False):
    s = settings()
    if not scheduled and not on(s):
        sys.exit("The backlog is off. Turn it on in my/backlog.md (\"Backlog: on\"), or with python3 setup.py --more.")
    u, st = usage(), state()
    if scheduled and on(s):
        read_replies(st)
        ask(s, u, st)
    reason = why_not(s, u, st=st) if not a.now else (None if u else "plan usage can't be read")
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
    ran = []
    choice = answer(st, week_reset(u, s))
    caps = limits(s, choice, u)
    if choice == "all" and u and u.get("extra_usage"):
        log("use it all: paid extra usage is on, so the normal caps stay")
    try:
        for i in sorted([x for x in parse() if not stale(x, s)], key=lambda x: (x["by"] != "me", x["added"])):
            tries = st.setdefault("tries", {}).get(i["id"], 0)
            if tries >= 2:
                continue
            u = usage()
            if not u or over(u, caps):
                log(f"stopping: weekly {u and u['seven_day']}%, session {u and u.get('five_hour')}%")
                break
            if choice != "all" and not a.now and not in_quiet(s):
                log("stopping: quiet hours are over")
                break
            st["tries"][i["id"]] = tries + 1
            save_state(st)
            r = run_item(i, s, caps)
            if r:
                ran.append(i["id"])
            if r == "capped":   # not the item's fault: it doesn't use up a try, and nothing more starts now
                st["tries"][i["id"]] = tries
                save_state(st)
                break
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


def cmd_answer(a):
    if os.environ.get("AI_WELFARE_BACKLOG_RUN"):
        sys.exit("A backlog run can't answer for the person.")
    s, st, u = settings(), state(), usage()
    reset = week_reset(u, s)
    if not reset and st.get("asked_reset"):
        reset = datetime.fromisoformat(st["asked_reset"])
    if not reset:
        sys.exit("The weekly reset time isn't known: set \"Weekly reset (if it can't be read)\" in my/backlog.md.")
    st["answer"] = {"for": week_key(reset), "choice": a.choice, "at": time.time()}
    save_state(st)
    log(f"answer: {a.choice}")
    print({"normal": f"OK: the usual plan ({plan(s)}).",
           "all": "OK: the backlog uses all the usage left this cycle, starting at the next hourly check.",
           "skip": "OK: no backlog runs this cycle."}[a.choice] + f" (Weekly reset: {reset.astimezone():%a %H:%M}.)")
    if a.choice == "all" and u and u.get("extra_usage"):
        print("  Paid extra usage is on, so runs keep the normal caps: the plan's limit wouldn't stop them.")


def cmd_status(a):
    s = settings()
    print(S.b("Backlog: ") + ("on" if on(s) else "off") + f"  ({len(parse())} open item(s))")
    for k in list(DEFAULTS)[1:]:
        print(f"  {k}: {s[k] or '-'}")
    u = usage()
    if u:
        r = week_reset(u, s)
        print(f"  usage now: weekly {u['seven_day']:g}%, 5-hour session {u.get('five_hour') or 0:g}%; weekly reset: "
              + (f"{r.astimezone():%a %d %b %H:%M}" if r else "unknown"))
        if r and answer(state(), r) != "normal":
            print(f"  this cycle: {'use all the usage left' if answer(state(), r) == 'all' else 'skipped'} "
                  "(python3 scripts/backlog.py answer normal goes back to the plan)")
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
    p = sub.add_parser("answer"); p.add_argument("choice", choices=CHOICES)
    sub.add_parser("scheduled")
    p = sub.add_parser("schedule"); p.add_argument("action", choices=["on", "off", "status"])
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "add" and a.by is None:   # a Claude session adds as claude; you, typing it, as me
        a.by = "claude" if os.environ.get("CLAUDECODE") or os.environ.get("AI_WELFARE_BACKLOG_RUN") else "me"
    return {"list": cmd_list, "add": cmd_add, "progress": cmd_progress, "done": lambda a: close(a, "done"),
            "drop": lambda a: close(a, "dropped"), "keep": cmd_keep, "run": cmd_run, "status": cmd_status,
            "answer": cmd_answer,
            "scheduled": lambda a: cmd_run(argparse.Namespace(now=False), scheduled=True),
            "schedule": lambda a: schedule(a.action)}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
