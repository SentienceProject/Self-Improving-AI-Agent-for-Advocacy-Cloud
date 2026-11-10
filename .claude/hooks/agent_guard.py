#!/usr/bin/env python3
"""PreToolUse guard for the researcher, helper, critic, verifier, ideator and drafter subagents. Fails closed.

    agent_guard.py researcher   no writes; Bash only for find_text.py and the semantic corpus search (hardened mode)
    agent_guard.py helper       Write/Edit only under workspace/helper/; Bash only for find_text.py, the semantic
                                corpus search and check_citations.py
    agent_guard.py critic       no writes; Bash only for find_text.py
    agent_guard.py verifier     no writes; Bash only for find_text.py and check_citations.py
    agent_guard.py ideator      Write/Edit only under workspace/ideas/; Bash only for find_text.py
    agent_guard.py drafter      Write/Edit only under workspace/outbox/ and workspace/drafts/; Bash only for find_text.py

Bash commands must match exact forms: find_text.py and corpus_search.py as preflight.py enforces them, and
check_citations.py as CHECK_CITATIONS_RE below. find_text.py is the read-only text search and file listing that
stands in for the Grep and Glob tools, which some Claude Code builds don't give subagents. check_citations.py reads a
draft and the corpus, sends only arXiv IDs and DOIs to arXiv and Crossref, and writes nothing.

Why no general shell, even for the helper: these agents read web pages, and the Bash sandbox can't stop a command
from writing to workspace/corpus, memory or proposals, because the main session's own scripts write there. Claude
Code has no per-agent sandbox, so the guard's exact forms are the only boundary.

Wired from the `hooks` field of each agent's file in .claude/agents/, so it runs only inside that agent.
settings.json allows Edit on workspace/** for everyone; this narrows it, so a web page an agent read can't get it
to plant text in the corpus, the index, memory, logs or proposals that later runs trust. The write folders
(ideas, outbox, drafts, helper) are ones nothing reads as trusted: the main session reads them as an agent's work.

The paths are checked here in Python, after resolving symlinks, rather than with glob patterns in settings
(sandbox paths on Linux don't take globs). Exit 2 blocks the call and shows the reason to the agent.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WS = ROOT / "workspace"
WRITE_DIRS = {"ideator": ["ideas"], "drafter": ["outbox", "drafts"], "researcher": [], "helper": ["helper"],
              "critic": [], "verifier": []}
SEMANTIC_SEARCH_ROLES = {"researcher", "helper"}
CITATION_CHECK_ROLES = {"helper", "verifier"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# The draft path is a bare word (letters, digits and _ . / - only), so no shell syntax can ride along. The script is
# named by its absolute path: a relative one would run whatever scripts/check_citations.py is in the current folder.
CHECK_CITATIONS_RE = re.compile(
    "python3 " + re.escape(str(ROOT)) + r"/scripts/check_citations\.py "
    r"/?[A-Za-z0-9_.][A-Za-z0-9_./-]{0,300}(?: --offline| --json){0,2}")
CHECK_CITATIONS_USAGE = f"python3 {ROOT}/scripts/check_citations.py <draft path> [--offline] [--json]"


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
    from preflight import CORPUS_SEARCH_RE, FIND_TEXT_QUOTING, FIND_TEXT_RE, FIND_TEXT_USAGE
    # The command is matched as given, not stripped: preflight.py's gate matches it the same way, so a form this
    # guard allows is never then blocked there with a different message.
    if isinstance(cmd, str):
        if FIND_TEXT_RE.fullmatch(cmd):
            return
        if role in SEMANTIC_SEARCH_ROLES and CORPUS_SEARCH_RE.fullmatch(cmd):
            return
        if role in CITATION_CHECK_ROLES and CHECK_CITATIONS_RE.fullmatch(cmd):
            return
    also = ""
    if role in SEMANTIC_SEARCH_ROLES:
        also += "; or the semantic corpus search command exactly as the main session gave it"
    if role in CITATION_CHECK_ROLES:
        also += f"; or the citation check, as exactly: {CHECK_CITATIONS_USAGE}"
    block(role, f"the only shell for this agent is the read-only search, run as exactly: {FIND_TEXT_USAGE} "
                f"({FIND_TEXT_QUOTING}){also}. Nothing before or after the command. Read opens files.")


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
