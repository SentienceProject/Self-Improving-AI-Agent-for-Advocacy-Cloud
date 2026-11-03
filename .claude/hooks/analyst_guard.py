#!/usr/bin/env python3
"""PreToolUse guard for the analyst subagent. Fails closed.

Wired from the `hooks` field of .claude/agents/analyst.md, so it runs only
while the analyst is active, before every tool call it makes. It enforces
the scoping that the `tools` field can't express:

  Read, Grep, Glob   allowed
  Write              only new files in <repo>/workspace/analysis/<run-id>/...
                     (checked after resolving symlinks; never overwrites)
  Bash               only the exact command
                     python3 <repo>/scripts/run_analysis.py <run-id> workspace/analysis/<run-id>/<file>.py
                     (or `python` instead of `python3`), with no shell syntax at all
  anything else      blocked

Exit 0 lets the call continue to the normal permission checks. Exit 2 blocks
it and shows the reason to the analyst. Any error inside this script also
exits 2, because a crashing hook with any other exit code would NOT block.

The repo root comes from this file's own location, never from the input or
the environment.
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ROOT / "workspace" / "analysis"
RUNNER = str(ROOT / "scripts" / "run_analysis.py")  # absolute: works from any cwd

RUN_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
RUN_ID_RE = re.compile(RUN_ID)
SAFE_CHARS_RE = re.compile(r"[A-Za-z0-9_./ -]+")
READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}


def block(reason: str):
    print(f"Blocked by analyst guard: {reason}", file=sys.stderr)
    sys.exit(2)


def check_bash(tool_input: dict):
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        block("empty Bash command")
    # Letters, digits and _ . / - and plain spaces only: no ; & | $ ` quotes,
    # redirects, newlines, tabs, globs or VAR=value prefixes.
    if not SAFE_CHARS_RE.fullmatch(cmd):
        block(f"Bash may only run: python3 {RUNNER} <run-id> "
              "workspace/analysis/<run-id>/<script>.py (no shell syntax)")
    tokens = cmd.split(" ")
    tokens = [t for t in tokens if t]
    if (len(tokens) != 4 or tokens[0] not in ("python3", "python")
            or tokens[1] != RUNNER):
        block(f"Bash may only run: python3 {RUNNER} <run-id> "
              "workspace/analysis/<run-id>/<script>.py")
    run_id, script = tokens[2], tokens[3]
    if not RUN_ID_RE.fullmatch(run_id):
        block(f"invalid run-id {run_id!r}")
    parts = script.split("/")
    if (parts[:3] != ["workspace", "analysis", run_id] or len(parts) < 4
            or ".." in parts or "." in parts or "" in parts
            or not script.endswith(".py")):
        block(f"script must be a .py file under workspace/analysis/{run_id}/")
    # run_analysis.py re-checks everything, including symlinks.


def check_write(tool_input: dict):
    fp = tool_input.get("file_path")
    if not isinstance(fp, str) or not fp:
        block("Write without a file_path")
    p = Path(fp)
    if not p.is_absolute():
        block(f"Write path must be absolute: {fp!r}")
    if ".." in p.parts:
        block(f"'..' not allowed in Write path: {fp!r}")
    real = Path(os.path.realpath(p))
    analysis_real = Path(os.path.realpath(ANALYSIS))
    if not real.is_relative_to(analysis_real):
        block(f"Write is limited to workspace/analysis/<run-id>/ (got {fp})")
    rel = real.relative_to(analysis_real).parts
    if len(rel) < 2:
        block("files must go inside a run folder: "
              "workspace/analysis/<run-id>/<file>")
    if not RUN_ID_RE.fullmatch(rel[0]):
        block(f"run folder name {rel[0]!r} is not a valid run-id")
    if real.exists():   # the exec logs cite these files: a rewrite would break that record
        block(f"{fp} already exists; write a new version (e.g. script_v2.py) or start a new run-id")


def main():
    try:
        data = json.loads(sys.stdin.read())
        tool = data.get("tool_name")
        tool_input = data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            block("malformed tool_input")
        if tool in READ_ONLY_TOOLS:
            sys.exit(0)
        if tool == "Bash":
            check_bash(tool_input)
            sys.exit(0)
        if tool == "Write":
            check_write(tool_input)
            sys.exit(0)
        block(f"tool {tool!r} is not available to the analyst")
    except SystemExit:
        raise
    except BaseException as e:  # fail closed
        block(f"guard error: {e!r}")


if __name__ == "__main__":
    main()
