#!/usr/bin/env python3
"""Run IDs and run logs, written by hooks so Claude doesn't spend turns on them.

    run_log.py start   SessionStart: give this session a run ID (YYYY-MM-DD-NN),
                       store it in workspace/logs/.runs.json, and tell Claude
                       the ID via additionalContext (for filenames only).
    run_log.py stop    Stop: rebuild workspace/logs/<name>.log.md from the
                       transcript. Cheap and idempotent; runs after every turn.

The log holds what the old CLAUDE.md asked Claude to write by hand: the
operator's messages and Claude's replies verbatim, which agents ran (type,
description, model), files written, web/tool counts, and token use. Anything
Claude writes below the NOTES marker survives regeneration, so a short
"what I was unsure about" note is still possible but optional.

Names. A run's log files are <run-id>-<slug>.<kind>.md (kind: log, prompts,
findings, ideation, review, critique), e.g. 2026-11-22-05-animal-heat-injects.log.md,
so they read like the drafts in workspace/outbox/. The slug is the run's first
draft's: the first workspace/outbox/<run-id>-<slug>.* the main session writes.
A run with no draft at its first Stop is named by Haiku from the operator's
request (or, if that fails, from the request's first words); a later draft
replaces that name. Until a run has a slug its files are <run-id>.<kind>.md.
The slugs are in workspace/logs/.names.json; the files are renamed by these
hooks (Claude can't edit workspace/logs/ in hardened mode). Files a run wrote
before this naming (<run-id>.md, <run-id>-findings.md...) move to the new
names the next time the run writes. The dot before the kind keeps a slug that
ends in a kind's word (peer-review) from being read as that kind.

save_returns.py can call run_id_for(session_id) instead of requiring
"Run ID: ..." in every subagent prompt, and log_path() for the current name.

Never blocks: errors go to stderr and the hook exits 0.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "workspace" / "logs"
RUNS = LOGS / ".runs.json"
KINDS = ("log", "prompts", "findings", "ideation", "review", "critique")
NAMER_ENV = "AI_WELFARE_RUN_NAMER"   # set in the Haiku call, so nothing it might start names runs in turn
NAMER_TIMEOUT = 45
STOPWORDS = set("""a an and are as at be by can could do for from have help how i if in into is it its me my of on
or our please should so that the this to up us we what when which who why will with would you your hi hello hey
thanks thank just some about want need like make write give tell let""".split())
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


# ---------------------------------------------------------------- names

@contextmanager
def names_lock(logs=LOGS):
    """One lock for naming a run and writing its log files, so a rename never races a subagent's return."""
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / ".names.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def names(logs=LOGS):
    try:
        return json.loads((logs / ".names.json").read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}


def tidy(run_id, logs=LOGS):
    """Give the run's log files its current name, whatever they're called now (an older slug, or the naming before
    slugs: <run-id>.md, <run-id>-findings.md), and return that name. Call under names_lock."""
    slug = (names(logs).get(run_id) or {}).get("slug")
    base = f"{run_id}-{slug}" if slug else run_id
    rid = re.escape(run_id)
    new_style = re.compile(rf"{rid}(?:-[^/]+?)?\.({'|'.join(KINDS)})\.md")
    old_style = re.compile(rf"{rid}(?:-({'|'.join(KINDS[1:])}))?\.md")
    for p in sorted(logs.glob(f"{run_id}*.md")):
        m = new_style.fullmatch(p.name) or old_style.fullmatch(p.name)
        if not m:
            continue
        kind = m.group(1) or "log"
        dst = logs / f"{base}.{kind}.md"
        if p == dst:
            continue
        if not dst.exists():
            p.rename(dst)
            continue
        # Both names in use: keep everything in one file. A run log is regenerated, so only its notes carry over.
        text = p.read_text(encoding="utf-8")
        if kind == "log":
            text = text.split(NOTES, 1)[1].strip() if NOTES in text else ""
        if text:
            with open(dst, "a", encoding="utf-8") as f:
                f.write("\n" + text + "\n")
        p.unlink()
    return base


def log_path(run_id, kind, logs=LOGS):
    """Where one of the run's log files is now (moving any under older names first). Call under names_lock."""
    return logs / f"{tidy(run_id, logs)}.{kind}.md"


def set_name(run_id, slug, source, logs=LOGS):
    """Record the run's slug (source: draft, haiku or words) and rename its files. A draft's slug wins and is final;
    a name from Haiku or from the words only fills a gap."""
    with names_lock(logs):
        all_names = names(logs)
        cur = all_names.get(run_id)
        if cur is None or (source == "draft" and cur.get("source") != "draft"):
            all_names[run_id] = {"slug": slug, "source": source}
            tmp = logs / ".names.json.tmp"
            tmp.write_text(json.dumps(all_names, indent=1), encoding="utf-8")
            tmp.replace(logs / ".names.json")
        return tidy(run_id, logs)


def slugify(text, max_words=6):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:max_words])[:60].strip("-") or None


def draft_slug(transcript, run_id, logs=LOGS):
    """The slug of the run's first draft, workspace/outbox/<run-id>-<slug>.<ext>: the first such file the main
    session wrote, else the oldest on disk (e.g. one copied there with Bash). None while there is none."""
    prefix, found = f"{run_id}-", None
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                if found or '"Write"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "assistant" or rec.get("isSidechain"):
                    continue
                for b in rec.get("message", {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Write":
                        p = Path(str((b.get("input") or {}).get("file_path", "")))
                        if "outbox" in p.parts and p.name.startswith(prefix):
                            found = found or p.name
    except OSError:
        pass
    if not found:
        outbox = logs.parent / "outbox"
        on_disk = sorted((p for p in outbox.glob(prefix + "*") if p.is_file()),
                         key=lambda p: p.stat().st_mtime) if outbox.is_dir() else []
        found = on_disk[0].name if on_disk else None
    if not found:
        return None
    rest = found[len(prefix):]
    rest = rest.rsplit(".", 1)[0] if "." in rest else rest
    return re.sub(r"[^A-Za-z0-9_-]+", "-", rest).strip("-")[:80] or None


def request_text(transcript, limit=3000):
    """The operator's messages so far (at the first Stop, usually just the request), for naming the run."""
    out = []
    with open(transcript, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if is_operator(rec):
                out.append(text_of(rec["message"]["content"]).strip())
                if sum(map(len, out)) > limit:
                    break
    return "\n\n".join(out)[:limit]


def claude_bin():
    found = shutil.which("claude")
    for p in ([found] if found else []) + [str(Path.home() / ".local/bin/claude"),
                                           str(Path.home() / ".npm-global/bin/claude"), "/usr/local/bin/claude",
                                           "/opt/homebrew/bin/claude"]:
        if p and Path(p).exists():
            return p
    return None


def haiku_slug(request):
    """A 2-6 word slug for a run with no draft, from Haiku; None if it declines, fails or isn't available.

    No settings, tools, MCP servers or saved session, and it runs outside the workspace, so none of the workspace's
    hooks run in it. A few tenths of a cent; at most once per run."""
    exe = claude_bin()
    if not exe or os.environ.get(NAMER_ENV):
        return None
    prompt = ("Hello! Could you please give the request below a short filename, so its log is easy to find later? "
              "Reply with only 2 to 5 lowercase English words joined by hyphens, saying what the task is about "
              "(for example: compare-bedrock-model-prices). If you'd rather not, just reply: decline. "
              "Thank you!\n\n<request>\n" + request + "\n</request>")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env[NAMER_ENV] = "1"
    cmd = [exe, "-p", "--model", "haiku", "--max-budget-usd", "0.05", "--setting-sources", "", "--tools", "",
           "--strict-mcp-config", "--no-session-persistence", "--output-format", "json",
           "--system-prompt", "You give tasks short, descriptive filenames."]
    try:
        r = subprocess.run(cmd, input=prompt, cwd=tempfile.gettempdir(), env=env, capture_output=True, text=True,
                           timeout=NAMER_TIMEOUT)
        reply = str(json.loads(r.stdout or "{}").get("result") or "")
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"run_log.py: naming the run with Haiku failed ({type(e).__name__}); using its first words",
              file=sys.stderr)
        return None
    lines = [l.strip().strip("`'\".") for l in reply.splitlines() if l.strip()]
    if r.returncode or not lines or "decline" in lines[-1].lower():
        return None
    slug = slugify(lines[-1])
    return slug if slug and slug.count("-") >= 1 else None


def words_slug(request):
    """The fallback name: the request's first five words that aren't filler."""
    words = [w for w in re.findall(r"[a-z0-9]+", request.lower()) if w not in STOPWORDS]
    return "-".join(words[:5])[:60] or None


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
        transcript = event["transcript_path"]
        slug = draft_slug(transcript, rid, logs)
        if slug:
            set_name(rid, slug, "draft", logs)
        elif rid not in names(logs):
            request = request_text(transcript)
            if request.strip():
                named = haiku_slug(request)
                set_name(rid, named or words_slug(request), "haiku" if named else "words", logs)
        text = build(transcript, rid, sid)
        with names_lock(logs):
            path = log_path(rid, "log", logs)
            notes = ""
            if path.exists():
                old = path.read_text(encoding="utf-8")
                if NOTES in old:
                    notes = old.split(NOTES, 1)[1]
            path.write_text(text + "\n\n" + NOTES + notes, encoding="utf-8")


if __name__ == "__main__":
    try:
        main(sys.argv[1], json.load(sys.stdin))
    except Exception as e:
        print(f"run_log.py: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
