#!/usr/bin/env python3
"""PreToolUse hook for tech-watch scouts: writes only inside the folders given, reads only inside the workspace.

    scout_guard.py <allowed-dir> [<allowed-dir> ...]      Edit, Write, MultiEdit, NotebookEdit
    scout_guard.py --read <workspace>                     Read, Glob, Grep

techwatch.py generates each scout's settings with this hook and the folders that run may write (a daily or weekly
scan: techwatch/inbox and techwatch/proposals; a staging run: its own techwatch/staged/<id>/). It backs up the
permission rules, which a user-level "allow" could otherwise widen, and keeps Glob and Grep (allowed without a path
in the rules) inside the workspace, away from .env files. Exit 2 blocks the call and tells the scout why.
Any error blocks too: a guard that fails open isn't a guard.
"""
import json
import sys
from pathlib import Path


def inside(target, dirs):
    return any(target == a or a in target.parents for a in dirs)


def check_read(ws, event):
    ti, cwd = event.get("tool_input") or {}, Path(event.get("cwd") or ws)
    paths = [ti.get("file_path"), ti.get("path")]
    pattern = ti.get("pattern") if event.get("tool_name") == "Glob" else ti.get("glob")
    if pattern and (pattern.startswith(("/", "~")) or ".." in pattern):
        paths.append(pattern.split("*")[0] or "/")
    for raw in filter(None, paths):
        target = (cwd / Path(raw).expanduser()).resolve()
        if not inside(target, [ws]):
            print(f"scout_guard: scouts read only inside the workspace ({ws}), not {raw}", file=sys.stderr)
            return 2
        if target.name.startswith(".env"):
            print(f"scout_guard: scouts don't read {target.name} files", file=sys.stderr)
            return 2
    return 0


def main():
    event = json.loads(sys.stdin.read())
    if sys.argv[1:2] == ["--read"]:
        return check_read(Path(sys.argv[2]).resolve(), event)
    allowed = [Path(a).resolve() for a in sys.argv[1:]]
    ti = event.get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("notebook_path") or ""
    if not raw:
        print("scout_guard: no file path in the call", file=sys.stderr)
        return 2
    target = (Path(event.get("cwd") or ".") / raw).resolve()
    if inside(target, allowed):
        return 0
    print(f"scout_guard: tech-watch scouts may only write inside {', '.join(map(str, allowed))}, not {target}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"scout_guard: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
