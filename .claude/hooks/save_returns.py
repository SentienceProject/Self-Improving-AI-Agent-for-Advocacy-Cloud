#!/usr/bin/env python3
"""SubagentStop hook: write subagent prompts and returns to the run's logs.

CLAUDE.md asks for the ideator's and researchers' returns verbatim in
workspace/logs/<run-id>-ideation.md and -findings.md. Before this hook the
main session copied them with Write, so each return sat in its context twice
and was re-read on every later turn. This hook writes the files itself, at no
token cost to anyone.

    ideator     -> <run-id>-ideation.md   (return, verbatim)
    researcher  -> <run-id>-findings.md   (return, verbatim)
    verifier    -> <run-id>-review.md     (return, verbatim; Claude no longer copies it)
    critic      -> <run-id>-critique.md   (return, verbatim)
    every type  -> <run-id>-prompts.md    (the main session's prompt, verbatim)

The run ID is the first YYYY-MM-DD-NN in the subagent's prompt, which is the
first line of its transcript (subagents run in the background, so the Agent
tool's own result is only "async_launched" and has no return in it).

A subagent spawned with a `name` (an in-process teammate) reports that name as
agent_type, e.g. "translator" for an ideator (run 2026-10-07-02 lost its dry
run this way). The type it was spawned as is in the agent-<id>.meta.json file
next to its transcript, so that wins when present.

Idempotent: each entry is keyed by agent ID plus a hash of the text, so a
re-fired hook adds nothing, while a SendMessage continuation (new text)
appends a new entry. Concurrent researchers are serialised with flock.

Never blocks: any failure goes to stderr and the hook exits 0.
"""

import fcntl
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_log import run_id_for  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "workspace" / "logs"
RUN_ID_RE = re.compile(r"\b(20\d\d-\d\d-\d\d-\d\d)\b")
RETURN_FILES = {"ideator": "ideation", "researcher": "findings",
                "verifier": "review", "critic": "critique"}


def subagent_transcript(event):
    """Path of this subagent's own transcript."""
    if event.get("agent_transcript_path"):
        return Path(event["agent_transcript_path"])
    main = Path(event["transcript_path"])
    return main.parent / event["session_id"] / "subagents" / f"agent-{event['agent_id']}.jsonl"


def spawned_type(transcript, reported):
    """The agent type it was spawned as; a named teammate reports its name instead."""
    try:
        meta = json.loads(transcript.with_suffix(".meta.json").read_text(encoding="utf-8"))
        return (meta.get("spawnedAgentType") or meta.get("customAgentType") or meta.get("agentType")
                or reported)
    except (OSError, ValueError, AttributeError):
        return reported


def handback_text(path):
    """The report a subagent delivered with SubagentHandback, if it used it (Claude Code 2.1.3xx and later).

    Then the report is that tool call's `message`, and the event's last_assistant_message is empty or only a
    leftover. The last handback that wasn't refused wins. None if there was none."""
    calls, refused = [], set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    content = (json.loads(line).get("message") or {}).get("content")
                except ValueError:
                    continue
                for b in content if isinstance(content, list) else []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and b.get("name") == "SubagentHandback":
                        msg = (b.get("input") or {}).get("message")
                        if isinstance(msg, str) and msg.strip():
                            calls.append((b.get("id"), msg))
                    elif b.get("type") == "tool_result" and b.get("is_error"):
                        refused.add(b.get("tool_use_id"))
    except OSError:
        return None
    ok = [msg for cid, msg in calls if cid not in refused]
    return ok[-1] if ok else None


def read_prompt(path):
    """The first user message in the transcript is the main session's prompt."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("type") != "user":
                continue
            content = rec.get("message", {}).get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def append_once(path, agent_id, heading, text):
    key = f"<!-- {agent_id} {hashlib.sha256(text.encode()).hexdigest()[:12]} -->"
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        if key in f.read():
            return False
        f.write(f"\n{key}\n## {heading}\n\n{text.rstrip()}\n")
        return True


def main(event, logs=LOGS):
    reported = event.get("agent_type", "")
    agent_id = event.get("agent_id", "")
    transcript = subagent_transcript(event)
    agent_type = spawned_type(transcript, reported)
    prompt = read_prompt(transcript)
    m = RUN_ID_RE.search(prompt)
    # Prompts no longer need "Run ID: ..."; the SessionStart hook maps session -> run ID.
    run_id = m.group(1) if m else run_id_for(event.get("session_id", ""), logs=logs)
    if not run_id:
        return None  # not a workspace run (e.g. a meta session); nothing to file
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = f" '{reported}'" if reported and reported != agent_type else ""
    heading = f"{agent_type}{name} {agent_id} ({stamp})"
    logs.mkdir(parents=True, exist_ok=True)
    append_once(logs / f"{run_id}-prompts.md", agent_id, f"Prompt to {heading}", prompt)
    if agent_type in RETURN_FILES:
        text = handback_text(transcript) or event.get("last_assistant_message") or ""
        append_once(logs / f"{run_id}-{RETURN_FILES[agent_type]}.md", agent_id, heading, text)
    return run_id


if __name__ == "__main__":
    try:
        main(json.load(sys.stdin))
    except Exception as e:  # never block a subagent from stopping
        print(f"save_returns.py: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
