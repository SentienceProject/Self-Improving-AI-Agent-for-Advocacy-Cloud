#!/usr/bin/env python3
"""PreToolUse guard for the researcher, helper, critic, verifier, ideator and drafter subagents. Fails closed.

    agent_guard.py researcher   no writes; Bash only for find_text.py and the semantic corpus search (hardened mode)
    agent_guard.py helper       the same as the researcher
    agent_guard.py critic       no writes; Bash only for find_text.py
    agent_guard.py verifier     no writes; Bash only for find_text.py
    agent_guard.py ideator      Write/Edit only under workspace/ideas/; Bash only for find_text.py
    agent_guard.py drafter      Write/Edit only under workspace/outbox/ and workspace/drafts/; Bash only for find_text.py

Bash commands must match the exact forms preflight.py enforces. find_text.py is the read-only text search and file
listing that stands in for the Grep and Glob tools, which some Claude Code builds don't give subagents.

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
WRITE_DIRS = {"ideator": ["ideas"], "drafter": ["outbox", "drafts"], "researcher": [], "helper": [], "critic": [],
              "verifier": []}
SEMANTIC_SEARCH_ROLES = {"researcher", "helper"}
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
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from preflight import CORPUS_SEARCH_RE, FIND_TEXT_RE, FIND_TEXT_USAGE
    if isinstance(cmd, str):
        if FIND_TEXT_RE.fullmatch(cmd.strip()):
            return
        if role in SEMANTIC_SEARCH_ROLES and CORPUS_SEARCH_RE.fullmatch(cmd.strip()):
            return
    also = (", or the semantic corpus search command exactly as the main session gave it"
            if role in SEMANTIC_SEARCH_ROLES else "")
    block(role, f"the only shell for this agent is the read-only search, run as exactly: {FIND_TEXT_USAGE}{also}. "
                "Inside the quotes, no $ ` \\ \" or !. Read opens files.")


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
