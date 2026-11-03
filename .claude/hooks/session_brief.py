#!/usr/bin/env python3
"""SessionStart hook: a few lines for the person, never for Claude, about things that need them.

- First run: point at setup.
- Tech watch: changes ready to decide or being tested, changes it applied for you (with how to undo them), a
  missing schedule (or, if you start scans yourself, a scan that's due), and any problem with the last run.
- Updates: whether the workspace's upstream has new commits (checked at most once a day, 5 s timeout; only for a
  git clone with an upstream branch).

Never blocks, never fails a session: any error just means fewer lines.
"""
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TW = ROOT / "techwatch"
CHECK = TW / "state" / "update-check.json"


SEEN = TW / "state" / "brief_seen.json"
NEWLY_SEEN = set()   # marked as seen only after the message has been printed (main)


def applied_lines():
    """The one notice that tech watch applied something on its own: shown until it has actually been printed."""
    sys.path.insert(0, str(TW / "scripts"))
    import review
    try:
        seen = set(json.loads(SEEN.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    out = []
    for pid, note in review.recent_auto():
        if pid in seen:
            continue
        title = review.meta(review.PROPOSALS / pid).get("title", "")
        out.append(f"Tech watch applied {pid} ({note.split('; ', 1)[-1]}): {title[:120]}. "
                   f"Undo: ! python3 techwatch/scripts/review.py rollback {pid}")
        NEWLY_SEEN.add(pid)
    return out


def mark_seen():
    if not NEWLY_SEEN:
        return
    try:
        seen = set(json.loads(SEEN.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(seen | NEWLY_SEEN)), encoding="utf-8")


def techwatch_lines():
    out = []
    sys.path.insert(0, str(TW / "scripts"))
    import review
    pr = review.prefs()
    s = review.summary()
    parts = []
    if s["staged"]:
        parts.append(f"{s['staged']} change(s) ready")
    if s["decide"]:
        parts.append(f"{s['decide']} idea(s) waiting")
    if s["stuck"]:
        parts.append(f"{s['stuck']} that couldn't be prepared")
    if s["testing"]:
        parts.append(f"{s['testing']} being tested")
    if s.get("trial_over"):
        parts.append(f"{s['trial_over']} trial(s) over, to keep or roll back")
    if parts:
        out.append(f"Tech watch: {', '.join(parts)}{' (includes a MAJOR one)' if s['major'] else ''}. "
                   "Decide (test, apply or skip): ! python3 techwatch/scripts/review.py")
    out += review.health()
    if pr["runs"] == "automatically":
        import techwatch
        if not techwatch.is_scheduled():
            out.append("Tech watch is set to run automatically but isn't scheduled on this computer yet. Turn it on: "
                       "! python3 techwatch/scripts/techwatch.py schedule on   (or set \"Tech watch runs: when I "
                       "start it\" or \"off\" in my/techwatch.md)")
    elif pr["runs"] == "when I start it":
        cfg = json.loads((TW / "config.json").read_text(encoding="utf-8"))
        every = int(cfg.get("cadence", {}).get("weekly_every_days", 7))
        try:
            last = (TW / "state" / "last_weekly").read_text(encoding="utf-8").strip()
            age = (date.today() - date.fromisoformat(last)).days
        except (OSError, ValueError):
            age = None
        if age is None or age >= every:
            out.append(f"Tech watch: a weekly scan is due ({'never run' if age is None else f'last {age} days ago'};"
                       f" up to ${cfg['runs']['weekly']['max_budget_usd']:g} of usage). "
                       "Run it: ! python3 techwatch/scripts/techwatch.py run")
    return out


def update_lines():
    try:   # the workspace may be the repository or a folder inside it; no git at all means no updates
        inside = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"], capture_output=True,
                                text=True, timeout=5).stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        inside = False
    if not inside:
        return []
    try:
        last = json.loads(CHECK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if time.time() - last.get("at", 0) > 20 * 3600:
        # Recorded even when offline or timed out, so a session start doesn't wait on it again for a day.
        CHECK.parent.mkdir(parents=True, exist_ok=True)
        CHECK.write_text(json.dumps({**last, "at": time.time()}), encoding="utf-8")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}   # never prompt
        subprocess.run(["git", "-C", str(ROOT), "fetch", "--quiet"], capture_output=True, timeout=5, env=env,
                       stdin=subprocess.DEVNULL)
        r = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", "HEAD..@{u}"], capture_output=True,
                           text=True, timeout=5)
        last = {"at": time.time(), "behind": int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0}
        CHECK.write_text(json.dumps(last), encoding="utf-8")
    n = last.get("behind", 0)
    return [f"Updates to this workspace are available ({n} new commit(s)). Get them: ! python3 scripts/update.py   "
            "(your files in my/ and workspace/ aren't touched)"] if n else []


def first_run():
    """Never set up (the short way: download and open): set up casual mode quietly, so the ask-first rules exist."""
    if (ROOT / "my" / ".setup-state.json").exists():
        return []
    r = subprocess.run([sys.executable, str(ROOT / "setup.py")], stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=60)
    if r.returncode:
        return ["First time here? Run  ! python3 setup.py  (a minute), or just tell Claude about your work."]
    return ["Welcome! The workspace set itself up in casual mode, with its ask-first rules (before sending, "
            "posting, reading key files or changing its own instructions). They take effect right away; if Claude "
            "ever does one of those without asking, start a new session (/exit, then claude) and please report it. "
            "For updates and scheduled tech watch, see the README's full setup."]


def compat_lines():
    """After Claude Code updates: a reminder to run the compatibility check (until it's run for this version)."""
    import shutil
    exe = shutil.which("claude")
    if not exe:
        return []
    r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
    now = r.stdout.strip().split()[0] if r.returncode == 0 and r.stdout.strip() else None
    try:
        state = json.loads((TW / "state" / "compat.json").read_text(encoding="utf-8"))
        checked = state.get("claude_version") if state.get("ok") is not False else state.get("ok_version")
    except (OSError, ValueError):
        checked = None
    if not now or now == checked:
        return []
    if checked is None:   # first run: record the version quietly, no reminder
        (TW / "state").mkdir(parents=True, exist_ok=True)
        (TW / "state" / "compat.json").write_text(json.dumps({"claude_version": now, "ok": None}), encoding="utf-8")
        return []
    return [f"Claude Code was updated ({checked} → {now}). Check the workspace still works with it (free, a minute): "
            "! python3 scripts/compat_check.py"]


def main():
    lines = []
    try:
        lines += first_run()
    except Exception:
        pass
    for part in (applied_lines, techwatch_lines, update_lines, compat_lines):
        try:
            lines += part()
        except Exception:
            pass
    if lines:
        print(json.dumps({"systemMessage": "\n".join(lines)}), flush=True)
        mark_seen()


if __name__ == "__main__":
    try:
        sys.stdin.read()
        main()
    except Exception:
        pass
    sys.exit(0)
