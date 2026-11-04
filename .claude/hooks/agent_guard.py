#!/usr/bin/env python3
"""PreToolUse guard for the researcher, ideator and drafter subagents. Fails closed.

    agent_guard.py researcher   Bash only for the semantic corpus search (hardened mode), in the exact form
                                preflight.py enforces; no other shell
    agent_guard.py ideator      Write/Edit only under workspace/ideas/
    agent_guard.py drafter      Write/Edit only under workspace/outbox/ and workspace/drafts/

Wired from the `hooks` field of each agent's file in .claude/agents/, so it runs only inside that agent.
settings.json allows Edit on workspace/** for everyone; this narrows it, so a web page an agent read can't get it
to plant text in the corpus, the index, memory, logs or proposals that later runs trust.

The paths are checked here in Python, after resolving symlinks, rather than with glob patterns in settings
(sandbox paths on Linux don't take globs). Exit 2 blocks the call and shows the reason to the agent.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WS = ROOT / "workspace"
WRITE_DIRS = {"ideator": ["ideas"], "drafter": ["outbox", "drafts"], "researcher": []}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def block(role, reason):
    print(f"agent_guard ({role}): {reason}", file=sys.stderr)
    sys.exit(2)


def check_write(role, tool_input):
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        block(role, "no file path")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from guard_paths import inside   # handles Windows paths, spaces and Git Bash/WSL path styles
    for d in WRITE_DIRS[role]:
        if inside(raw, WS / d, ROOT):
            return
    where = " or ".join(f"workspace/{d}/" for d in WRITE_DIRS[role]) or "nowhere"
    block(role, f"this agent may write only in {where}, not {raw}. If the task needs another file, say so in your "
                "reply and the main session will handle it.")


def check_bash(role, tool_input):
    cmd = tool_input.get("command")
    if role == "researcher" and isinstance(cmd, str):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from preflight import CORPUS_SEARCH_RE
        if CORPUS_SEARCH_RE.fullmatch(cmd.strip()):
            return
    block(role, "no shell for this agent, except the semantic corpus search command exactly as the main session "
                "gave it. Use Grep, Glob and Read for files.")


def main():
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if role not in WRITE_DIRS:
            block(role or "?", "unknown role")
        data = json.loads(sys.stdin.read())
        tool, tool_input = data.get("tool_name"), data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            block(role, "malformed tool_input")
        if tool in WRITE_TOOLS:
            check_write(role, tool_input)
        elif tool in ("Bash", "PowerShell"):
            check_bash(role, tool_input)
        sys.exit(0)
    except SystemExit:
        raise
    except BaseException as e:   # a crash with any exit code but 2 would not block
        block(role or "?", f"guard error: {e!r}")


if __name__ == "__main__":
    main()
