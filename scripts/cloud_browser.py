#!/usr/bin/env python3
"""A browser for cloud sessions (claude.ai/code): Microsoft's Playwright MCP server, driving the cloud computer's Chrome.

.mcp.json starts this for every session, and .claude/settings.json enables it (enabledMcpjsonServers), so nobody is
asked to approve it.
  - In a cloud session (CLAUDE_CODE_REMOTE=true) it becomes
        npx -y @playwright/mcp@VERSION --headless --browser chrome --no-sandbox --isolated --output-dir /tmp/...
    Its tools are named mcp__cloud-browser__browser_* (navigate, snapshot, click, type, take_screenshot, ...).
    Chrome runs headless with an in-memory profile, so no logins or cookies are kept, and it holds none of the
    person's: the cloud computer is separate from their devices. Chrome's own sandbox is off (--no-sandbox) because
    cloud sessions run as root, where Chrome refuses to start with it.
  - Anywhere else it's an MCP server with no tools, so sessions on your own computer get nothing new and nothing
    shows as failed. There, the workspace's browser is the sandboxed Firefox (scripts/browser_setup.py).

The version is pinned: a new one is adopted on purpose, after reading what changed.
"""
import json
import os
import shutil
import sys

VERSION = "0.0.86"
OUTPUT = "/tmp/cloud-browser"   # screenshots and other output: copy what's worth keeping into workspace/


def cloud_command():
    npx = shutil.which("npx")
    if not npx:
        return None
    return [npx, "-y", f"@playwright/mcp@{VERSION}", "--headless", "--browser", "chrome", "--no-sandbox",
            "--isolated", "--output-dir", OUTPUT]


def stub():
    """A minimal MCP server (JSON-RPC over stdio, one message per line) that offers no tools."""
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict) or "id" not in msg:
            continue   # notifications (e.g. notifications/initialized) need no answer
        method = msg.get("method")
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "cloud-browser (off: only runs in cloud sessions)", "version": "1"}}
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        elif method == "tools/list":
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": []}}
        elif method == "ping":
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"no {method} here"}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


def main():
    cmd = cloud_command() if os.environ.get("CLAUDE_CODE_REMOTE") == "true" else None
    if cmd:
        os.makedirs(OUTPUT, exist_ok=True)
        os.execv(cmd[0], cmd)   # replaces this process: Playwright's server talks to Claude Code directly
    stub()


if __name__ == "__main__":
    main()
