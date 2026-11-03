#!/usr/bin/env python3
"""Run IDs and run logs, written by hooks so Claude doesn't spend turns on them.

    run_log.py start   SessionStart: give this session a run ID (YYYY-MM-DD-NN),
                       store it in workspace/logs/.runs.json, and tell Claude
                       the ID via additionalContext (for filenames only).
    run_log.py stop    Stop: rebuild workspace/logs/<run-id>.md from the
                       transcript. Cheap and idempotent; runs after every turn.

The log holds what the old CLAUDE.md asked Claude to write by hand: the
operator's messages and Claude's replies verbatim, which agents ran (type,
description, model), files written, web/tool counts, and token use. Anything
Claude writes below the NOTES marker survives regeneration, so a short
"what I was unsure about" note is still possible but optional.

save_returns.py can call run_id_for(session_id) instead of requiring
"Run ID: ..." in every subagent prompt.

Never blocks: errors go to stderr and the hook exits 0.
"""

import fcntl
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "workspace" / "logs"
RUNS = LOGS / ".runs.json"
NOTES = "<!-- NOTES: anything below this line is kept when the log is regenerated -->"
# $ per million tokens (input, output, cache write, cache read). Rough; edit as prices change.
PRICE = {"opus": (5, 25, 6.25, 0.5), "sonnet": (3, 15, 3.75, 0.3), "haiku": (1, 5, 1.25, 0.1)}


def run_id_for(session_id, create=False, logs=LOGS):
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / ".runs.json", "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        runs = json.loads(f.read() or "{}")
        if session_id in runs or not create:
            return runs.get(session_id)
        today = datetime.now().strftime("%Y-%m-%d")
        taken = {r for r in runs.values() if r.startswith(today)}
        taken |= {m.group(1) for p in logs.glob(f"{today}-*")
                  if (m := re.match(r"(\d{4}-\d\d-\d\d-\d\d)", p.name))}
        n = 1
        while f"{today}-{n:02d}" in taken:
            n += 1
        runs[session_id] = f"{today}-{n:02d}"
        f.seek(0), f.truncate(), f.write(json.dumps(runs, indent=1))
        return runs[session_id]


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if b.get("type") == "text")


def is_operator(rec):
    """A real operator message: user text, not a tool result or harness injection."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return False
    c = rec.get("message", {}).get("content")
    if isinstance(c, list) and any(b.get("type") == "tool_result" for b in c):
        return False
    t = text_of(c).strip()
    return bool(t) and not t.startswith(("<command-", "<local-command", "<system-reminder>", "<task-notification>"))


def price_for(model):
    for k, v in PRICE.items():
        if k in (model or ""):
            return v
    return PRICE["opus"]


def build(transcript, run_id, session_id):
    convo, agents, files, tools = [], [], [], Counter()
    seen, cost, tok = set(), 0.0, Counter()
    first = last = None
    for line in open(transcript, encoding="utf-8"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        ts = rec.get("timestamp")
        if ts:
            first, last = first or ts, ts
        if is_operator(rec):
            convo.append(("Operator", text_of(rec["message"]["content"]).strip()))
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        msg = rec.get("message", {})
        mid, u = msg.get("id"), msg.get("usage") or {}
        if mid and mid not in seen:  # one API call can span several records
            seen.add(mid)
            i, o, cw, cr = price_for(msg.get("model"))
            tk = (u.get("input_tokens", 0), u.get("output_tokens", 0),
                  u.get("cache_creation_input_tokens", 0), u.get("cache_read_input_tokens", 0))
            cost += (tk[0] * i + tk[1] * o + tk[2] * cw + tk[3] * cr) / 1e6
            tok.update(dict(zip(("in", "out", "cache_write", "cache_read"), tk)))
        for b in msg.get("content") or []:
            if b.get("type") == "text" and b.get("text", "").strip():
                convo.append(("Claude", b["text"].strip()))
            elif b.get("type") == "tool_use":
                name, inp = b.get("name"), b.get("input") or {}
                tools[name] += 1
                if name in ("Agent", "Task"):
                    agents.append((inp.get("subagent_type") or "general-purpose",
                                   inp.get("model") or "", inp.get("description", "")))
                elif name in ("Write", "Edit") and inp.get("file_path"):
                    p = inp["file_path"].replace(str(ROOT) + "/", "")
                    if p not in files:
                        files.append(p)
    out = [f"# Run {run_id}", "",
           f"Session `{session_id}` · {first or '?'} → {last or '?'} · "
           f"main session ≈ ${cost:.2f} (subagents not included) · "
           f"{len(seen)} API calls, {tok['out']:,} output tokens", "",
           "*Generated by .claude/hooks/run_log.py after every turn. Don't edit above the NOTES line.*", ""]
    out += ["## Agents", ""] + ([f"- {t}{f' ({m})' if m else ''}: {d}" for t, m, d in agents] or ["- none"])
    out += ["", "## Files written or edited", ""] + ([f"- `{p}`" for p in files] or ["- none"])
    out += ["", "## Tool calls (main session)", "",
            ", ".join(f"{k} {v}" for k, v in tools.most_common()) or "none"]
    out += ["", "## Conversation (verbatim)", ""]
    for who, t in convo:
        out += [f"**{who}:**", "", t, ""]
    return "\n".join(out)


def main(mode, event, logs=LOGS):
    sid = event.get("session_id", "")
    if mode == "start":
        rid = run_id_for(sid, create=True, logs=logs)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"This session's run ID is {rid}. Use it in output filenames. "
                                 f"The run log is written automatically; you don't need to maintain it."}}))
    elif mode == "stop":
        rid = run_id_for(sid, logs=logs)
        if not rid or event.get("stop_hook_active"):
            return
        path = logs / f"{rid}.md"
        notes = ""
        if path.exists():
            old = path.read_text(encoding="utf-8")
            if NOTES in old:
                notes = old.split(NOTES, 1)[1]
        path.write_text(build(event["transcript_path"], rid, sid) + "\n\n" + NOTES + notes, encoding="utf-8")


if __name__ == "__main__":
    try:
        main(sys.argv[1], json.load(sys.stdin))
    except Exception as e:
        print(f"run_log.py: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
