#!/usr/bin/env python3
# Auto-approves WebSearch and WebFetch so research isn't interrupted.
#
# Wired in .claude/settings.json as a PreToolUse hook. Claude Code passes the
# tool call as JSON on stdin; returning permissionDecision "allow" skips the
# prompt. Anything that isn't a web tool gets no decision, so it falls through
# to the normal permission system untouched.
#
# This is deliberately narrow. It approves two read-only tools that cannot
# change anything on your machine. Do not extend it to Write, Edit, or Bash --
# at that point you have rebuilt bypassPermissions with extra steps.

import json
import sys


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        tool = data.get("tool_name")
    except (json.JSONDecodeError, ValueError):
        # Malformed input: stay silent and let the normal permission flow handle it.
        sys.exit(0)

    if tool == "WebSearch" or tool == "WebFetch":
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Read-only web tool, pre-approved for research.",
            }
        }
        print(json.dumps(decision))

    sys.exit(0)


if __name__ == "__main__":
    main()
