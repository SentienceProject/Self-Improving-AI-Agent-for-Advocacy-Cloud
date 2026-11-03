#!/usr/bin/env python3
"""Keep each task in a fresh context, as far as hooks can. Claude Code doesn't let a hook run /clear, so:

    context_nudge.py prompt     UserPromptSubmit: if the session already holds a lot of context (NUDGE_TOKENS,
                                from the token counts Claude Code records), show the operator a one-line tip to
                                /clear before an unrelated task. Shown to the person only, never to Claude, and
                                again only after the context grows by another NUDGE_TOKENS.
    context_nudge.py cleared    SessionStart after /clear: give the fresh context a short pickup note (the memory
                                index and the latest outbox drafts), so clearing loses nothing that matters.

A long context costs more per message and dilutes attention on the current task; a finished task's research
rarely helps the next one, and what should carry over belongs in workspace/memory.md anyway.
Never blocks anything: any error just means no tip.
"""
import json
import sys
from pathlib import Path

NUDGE_TOKENS = 60_000
ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / ".claude" / "context-nudge.json"   # {session_id: tokens at the last tip}


def context_tokens(transcript):
    """Tokens in the context at the last assistant message: its input plus cached input."""
    last = 0
    with open(transcript, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                u = json.loads(line).get("message", {}).get("usage") or {}
            except ValueError:
                continue
            n = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                            "cache_creation_input_tokens"))
            last = n or last
    return last


def prompt(event):
    tokens = context_tokens(event["transcript_path"])
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    sid = event.get("session_id", "")
    if tokens < NUDGE_TOKENS or tokens < state.get(sid, 0) + NUDGE_TOKENS:
        return
    state[sid] = tokens
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(dict(list(state.items())[-50:])), encoding="utf-8")
    print(json.dumps({"systemMessage": f"Tip: this session already holds about {tokens // 1000}k tokens. If this "
                      "is a new, unrelated task, press Esc, type /clear, and send it again: it'll be cheaper and "
                      "more focused. (Ignore this for a follow-up on the same task.)"}))


def cleared(event):
    lines = ["The operator just cleared the context to start something new. Earlier tasks' details are gone on "
             "purpose; don't try to recover them unless asked."]
    memory = ROOT / "workspace" / "memory.md"
    if memory.is_file():
        lines.append("workspace/memory.md holds facts carried between tasks; skim its index if this task touches "
                     "past work.")
    drafts = sorted((ROOT / "workspace" / "outbox").glob("*.md"), key=lambda p: p.stat().st_mtime)[-5:]
    if drafts:
        lines.append("Latest drafts in workspace/outbox/: " + ", ".join(p.name for p in reversed(drafts)) + ".")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": " ".join(lines)}}))


if __name__ == "__main__":
    try:
        event = json.loads(sys.stdin.read())
        {"prompt": prompt, "cleared": cleared}[sys.argv[1]](event)
    except Exception:
        pass
    sys.exit(0)
