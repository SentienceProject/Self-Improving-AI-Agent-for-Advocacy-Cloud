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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = re.compile(re.escape(str(ROOT / "workspace" / "ideas")) + r"/20\d\d-\d\d-\d\d-\d\d-wild-[1-6]\.md")


ALWAYS = "--always" in sys.argv


def main(event):
    if not ALWAYS and event.get("agent_type") != "wild-ideator":
        return 0
    raw = event.get("tool_input", {}).get("file_path", "")
    path = str((Path(event.get("cwd") or ROOT) / raw).resolve())
    if ALLOWED.fullmatch(path):
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
