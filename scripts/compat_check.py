#!/usr/bin/env python3
"""Check that the workspace still works with the Claude Code installed here (run it after Claude Code updates;
the session-start note reminds you when it has).

    python3 scripts/compat_check.py          free checks: settings, hooks, scripts and the test suite
    python3 scripts/compat_check.py --live   also one tiny Claude session (~$0.01) that reports any warnings Claude
                                             Code prints about this workspace's settings

The result is saved in techwatch/state/compat.json with the Claude Code version, so the reminder stops until the
next update. If something fails, please report it (README, "Reporting problems") with the output below.
"""
import json
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "techwatch" / "state" / "compat.json"


def claude_version():
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip().split()[0] if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def last_checked():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check(name, fn, results):
    try:
        problem = fn()
    except Exception as e:   # a crashed check is a failed check
        problem = f"{type(e).__name__}: {e}"
    results.append((name, problem))
    print(f"  {'ok  ' if not problem else 'FAIL'} {name}" + (f": {problem}" if problem else ""), flush=True)


def settings_ok():
    bad = []
    for f in [ROOT / ".claude" / "settings.json", ROOT / ".claude" / "settings.local.json",
              *sorted((ROOT / "layers").glob("*.json"))]:
        if f.exists():
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except ValueError as e:
                bad.append(f"{f.relative_to(ROOT)}: {e}")
    return "; ".join(bad)


def code_ok():
    bad = []
    for f in [*ROOT.glob("scripts/*.py"), *ROOT.glob(".claude/hooks/*.py"), *ROOT.glob("techwatch/scripts/*.py"),
              ROOT / "setup.py"]:
        try:
            with tempfile.TemporaryDirectory() as d:
                py_compile.compile(str(f), cfile=str(Path(d) / "x.pyc"), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append(f"{f.relative_to(ROOT)}: {e.msg.strip().splitlines()[-1]}")
    return "; ".join(bad)


def tests_ok():
    out = []
    for args in (["discover", "tests"], ["discover", "-s", "techwatch/tests"]):
        r = subprocess.run([sys.executable, "-m", "unittest", *args], cwd=ROOT, capture_output=True, text=True,
                           timeout=900)
        if r.returncode:
            out.append(re.findall(r"^(?:FAIL|ERROR): .*$", r.stderr, re.M)[:5] or [r.stderr.strip()[-300:]])
    return "; ".join(x for o in out for x in o)


def live_ok():
    """One tiny session with this workspace's settings; Claude Code prints settings problems at startup."""
    exe = shutil.which("claude")
    r = subprocess.run([exe, "-p", "--model", "haiku", "--max-budget-usd", "0.05", "--output-format", "json",
                        "Hi! This is a quick compatibility check. Please reply with just: ok. Thank you!"],
                       cwd=ROOT, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    warnings = [l for l in r.stderr.splitlines()
                if re.search(r"(?i)invalid|matches no tool|unknown|deprecated|error|not recogni", l)
                and "has not been trusted" not in l]
    if r.returncode:
        return f"the session failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()[:300]}"
    return "; ".join(warnings[:5])


def main(a):
    version = claude_version()
    print(f"Claude Code {version or '(not found)'}; workspace {(ROOT / 'VERSION').read_text().strip() if (ROOT / 'VERSION').exists() else '?'}")
    results = []
    check("settings files are valid JSON", settings_ok, results)
    check("scripts and hooks compile", code_ok, results)
    check("test suites pass", tests_ok, results)
    if "--live" in a:
        check("Claude Code accepts the settings (one tiny session)", live_ok, results)
    failed = [n for n, p in results if p]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    prev = last_checked()
    STATE.write_text(json.dumps({"claude_version": version, "ok": not failed, "failed": failed,
                                 "ok_version": version if not failed else prev.get("ok_version",
                                                                                    prev.get("claude_version")),
                                 "live": "--live" in a,
                                 "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=1),
                     encoding="utf-8")
    if failed:
        print("\nSomething doesn't work with this Claude Code version. Please report it (README, \"Reporting "
              "problems\"), with the lines above. Meanwhile the workspace may still mostly work.")
        sys.exit(1)
    print("\nAll good." + ("" if "--live" in a else " (Add --live to also check that Claude Code accepts the settings.)"))


if __name__ == "__main__":
    main(sys.argv[1:])
