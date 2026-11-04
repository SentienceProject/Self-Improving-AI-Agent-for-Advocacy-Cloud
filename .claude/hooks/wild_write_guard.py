#!/usr/bin/env python3
"""PreToolUse hook (Write|Edit): the wild-ideator may write only its own file.

The wild-ideator has Write so its ~30 ideas go straight to disk instead of
through the main session's context. settings.json allows Write on
workspace/** for everyone, so this hook narrows it for that one agent to

    workspace/ideas/<run-id>-wild-<1-6>.md

Exit 2 blocks the call and shows stderr to the agent.

Wired from the `hooks` field of .claude/agents/wild-ideator.md with --always,
so it runs only inside that agent and doesn't need to work out who's calling.
Without --always (e.g. wired from settings.json), it relies on PreToolUse input
carrying "agent_type" for subagent calls and passes everyone else through.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard_paths import name_inside, norm   # noqa: E402  (Windows paths, spaces, Git Bash/WSL styles)
# <run-id>-wild-<n>.md: a hardened-mode run ID (2026-11-04-01) or a casual one the session chose (2026-11-04-oped)
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}-wild-[1-6]\.md", re.I)


ALWAYS = "--always" in sys.argv


def main(event):
    if not ALWAYS and event.get("agent_type") != "wild-ideator":
        return 0
    raw = event.get("tool_input", {}).get("file_path", "")
    ideas = norm(os.path.realpath(ROOT / "workspace" / "ideas")).rstrip("/")
    path = name_inside(raw, ROOT, event.get("cwd")) if raw else ""
    rel = path[len(ideas) + 1:] if path.startswith(ideas + "/") else ""
    if rel and "/" not in rel and NAME.fullmatch(rel):   # directly in workspace/ideas/, no subfolder
        return 0
    print(f"wild_write_guard: the wild-ideator may only write "
          f"workspace/ideas/<run-id>-wild-<n>.md, not {raw}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raw_event = sys.stdin.read()
    try:
        sys.exit(main(json.loads(raw_event)))
    except Exception as e:  # fail closed only if the guarded agent might be calling
        print(f"wild_write_guard: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2 if ALWAYS or "wild-ideator" in raw_event else 0)
