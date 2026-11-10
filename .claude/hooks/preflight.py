#!/usr/bin/env python3
"""Sandbox preflight for Layer A, as three hooks in one script.

    preflight.py start    SessionStart: mark this session "pending" and tell
                          Claude its first Bash command must be the probe.
    preflight.py gate     PreToolUse (Bash): block every Bash command except
                          the probe until the probe has PASSED this session.
                          Also enforces the exact form of the commands that
                          are excluded from the sandbox: run_analysis.py,
                          corpus_search.py, fetch_pdf.py, fetch_repo.py and
                          docs_sync.py apply/reject, and of find_text.py
                          (sandboxed; the subagents' search).
    preflight.py record   PostToolUse (Bash): when the probe ran, record PASS
                          or FAIL, write workspace/logs/preflight-*.json, and
                          warn loudly on FAIL.

Why a gate: hooks run outside the sandbox, so a hook can't itself run "a
sandboxed command". The probe has to run through Claude's own Bash tool,
and the gate makes sure it does, first, before anything else.

Subagents: they get their own session_id but no SessionStart of their own,
so a session_id with no marker falls back to "current", the state set by the
most recent session start or probe. Every new session start resets "current"
to pending, so a new session can never ride on an older session's pass.

Fails closed: if anything goes wrong, the marker isn't PASS and Bash stays
blocked. The marker lives in ~/.claude/ai-welfare-preflight/, which the
sandbox protects and an Edit deny rule should cover.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "workspace" / "logs"
MARKERS = Path.home() / ".claude" / "ai-welfare-preflight"

# Absolute paths, so the commands work whatever directory Bash is in.
PROBE_CMD = f"bash {ROOT}/scripts/sandbox_probe.sh"
R = re.escape(str(ROOT))
RUN_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
SEARCH_PY = re.escape(str(Path.home() / ".venvs" / "ai-welfare-search" / "bin" / "python"))
# The query goes inside double quotes. Only characters bash never expands
# inside double quotes are allowed: no $ ` \ " ! and nothing exotic.
CORPUS_SEARCH_RE = re.compile(
    rf"{SEARCH_PY} {R}/scripts/corpus_search\.py \"[A-Za-z0-9 ,.?'():;-]{{1,300}}\""
    rf"( --k [1-9][0-9]{{0,2}})?")
# fetch_pdf.py: the URL goes inside double quotes, so only characters bash
# leaves alone there (no $ ` \\ " ! or spaces). fetch_pdf.py itself enforces
# https and the host allowlist.
_URL_CHARS = r"[A-Za-z0-9._~:/?#\[\]@&'()*+,;=%-]"
FETCH_PDF_RE = re.compile(
    "python3 " + R + r"/scripts/fetch_pdf\.py "
    + '"https://' + _URL_CHARS + '{1,490}"'
    + r" [A-Za-z0-9][A-Za-z0-9_-]{0,80}")
# fetch_repo.py: same URL and name rules as fetch_pdf.py. fetch_repo.py
# itself enforces the pinned GitHub / Zenodo / Hugging Face URL shapes.
FETCH_REPO_RE = re.compile(
    "python3 " + R + r"/scripts/fetch_repo\.py "
    + '"https://' + _URL_CHARS + '{1,490}"'
    + r" [A-Za-z0-9][A-Za-z0-9_-]{0,80}")
# find_text.py: read-only search for subagents with no Grep/Glob tool. Runs in
# the sandbox like any command. The pattern and globs go inside double quotes;
# paths are bare words of letters, digits and _ . / - only. find_text.py keeps
# every path inside the workspace itself.
# Inside double quotes bash expands only $ and `, treats \ as special only
# before $ ` " \ or a newline, and (interactively) expands !. So a pattern may
# use the plain regex characters below, plus two forms bash leaves exactly as
# written: \ followed by one plain character (\b \d \s \. \( ...), and $ just
# before ) | or the closing quote (an end anchor: "^---$", "(fish|crab)$").
# Never ` " or !, and never \\ \$ \" or $name / $( ).
_FT_PLAIN = r"[A-Za-z0-9 ,.?'():;|*+\[\]{}^/_&%#@=<>~-]"
_FT_GLOB = r'"[A-Za-z0-9_./*?-]{1,200}"'
_FT_PAT = rf'"(?:{_FT_PLAIN}|\\{_FT_PLAIN}|\$(?=[)|"])){{1,300}}"'
FIND_TEXT_QUOTING = ('inside the quotes no ` " or !; \\ only before a letter, digit or regex symbol (\\b \\d \\.); '
                     '$ only at the end or before ) or |')
_FT_PATH = r"/?[A-Za-z0-9_.][A-Za-z0-9_./-]{0,300}"
FIND_TEXT_RE = re.compile(
    "python3 " + R + r"/scripts/find_text\.py "
    + rf"(--list {_FT_GLOB}|(?:-[iwl] |-C [0-9] |--glob {_FT_GLOB} )*{_FT_PAT}(?: {_FT_PATH})*)")
FIND_TEXT_USAGE = (f'python3 {ROOT}/scripts/find_text.py [-i] [-w] [-l] [-C <0-9>] [--glob "<glob>"] '
                   f'"<regex>" [<path> ...]   or   python3 {ROOT}/scripts/find_text.py --list "<glob>"')
RUN_ANALYSIS_RE = re.compile(
    rf"python3? {R}/scripts/run_analysis\.py ({RUN_ID}) "
    rf"workspace/analysis/\1/[A-Za-z0-9_][A-Za-z0-9_./-]*\.py")
# docs_sync.py apply/reject: applies or drops a docs change the person said yes
# or no to. Runs outside the sandbox (it writes CLAUDE.md and agent files, which
# the sandbox protects), so an ask rule makes Claude Code ask the person too.
# The change itself was written by the weekly sync into techwatch/state/, which
# sessions can't write; the command only names it by number.
DOCS_DECIDE_RE = re.compile(rf"python3 {R}/scripts/docs_sync\.py (apply|reject) [1-9][0-9]{{0,5}}")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def marker_path(session_id):
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise ValueError(f"unusable session_id {session_id!r}")
    return MARKERS / f"{session_id}.json"


CURRENT = "current"

# A pass is trusted for sandboxed commands for this long, then Bash re-locks
# until the probe passes again (the WSL2 proxy bug is intermittent).
PASS_TTL_S = 30 * 60


def _read_full(path):
    """(state, epoch seconds) or (None, None)."""
    try:
        d = json.loads(path.read_text())
        ts = d.get("ts")
        return d.get("state"), (float(ts) if isinstance(ts, (int, float)) else None)
    except (OSError, ValueError, TypeError):
        return None, None


def _read(path):
    return _read_full(path)[0]


def read_marker_full(session_id):
    """This session's (state, ts); for a session with no marker of its own
    (a subagent), the project's current one."""
    own = _read_full(marker_path(session_id))
    return own if own[0] is not None else _read_full(MARKERS / f"{CURRENT}.json")


def read_marker(session_id):
    return read_marker_full(session_id)[0]


def _write(path, state, detail):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"state": state, "time": now(), "ts": time.time(),
                               "detail": detail}))
    os.replace(tmp, path)


def write_marker(session_id, state, detail=""):
    MARKERS.mkdir(parents=True, exist_ok=True)
    _write(marker_path(session_id), state, detail)
    _write(MARKERS / f"{CURRENT}.json", state, f"{detail} [{session_id}]")


def write_log(record):
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n = 1
    while True:
        path = LOGS / f"preflight-{stamp}-{n}.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            break
        except FileExistsError:
            n += 1
    with os.fdopen(fd, "w") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    return path


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


# --- modes -----------------------------------------------------------------

def start(data):
    sid = data.get("session_id")
    if data.get("source") != "compact" or _read(marker_path(sid)) != "pass":
        write_marker(sid, "pending", f"session start ({data.get('source')})")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": (
            "Sandbox preflight: Bash is locked until the preflight probe "
            f"passes. Your first Bash command must be exactly: {PROBE_CMD}  "
            f"A pass lasts {PASS_TTL_S // 60} minutes; when Bash re-locks, "
            "run the probe again. "
            "Report its result to the user. If it fails, stop and tell the "
            "user; do not try to work around it."),
    }}))


def gate(data):
    cmd = (data.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str):
        block("Preflight gate: malformed Bash call.")
    if cmd.strip() == PROBE_CMD:
        sys.exit(0)
    state, ts = read_marker_full(data.get("session_id"))
    if state == "fail":
        block("Sandbox preflight FAILED this session, so Bash is disabled. "
              "Tell the user; see the newest workspace/logs/preflight-*.json.")
    if state != "pass":
        block(f"Bash is locked until the sandbox preflight passes. "
              f"Run exactly: {PROBE_CMD}")
    if "run_analysis" in cmd and not RUN_ANALYSIS_RE.fullmatch(cmd):
        block(f"run_analysis.py must be run as exactly: python3 "
              f"{ROOT}/scripts/run_analysis.py <run-id> "
              "workspace/analysis/<run-id>/<script>.py (nothing before or "
              "after it; the script path stays relative to the project).")
    if "corpus_search" in cmd and not CORPUS_SEARCH_RE.fullmatch(cmd):
        block("corpus_search.py must be run as exactly: "
              f"{Path.home()}/.venvs/ai-welfare-search/bin/python "
              f'{ROOT}/scripts/corpus_search.py "<query>" --k <n>  (nothing before or '
              "after it; the query may use only letters, digits, spaces and "
              ", . ? ' ( ) : ; -)")
    if "find_text" in cmd and not FIND_TEXT_RE.fullmatch(cmd):
        block(f"find_text.py must be run as exactly: {FIND_TEXT_USAGE}  (options before the "
              f"pattern, nothing before or after; {FIND_TEXT_QUOTING}; paths use only "
              "letters, digits and _ . / -)")
    if "fetch_pdf" in cmd and not FETCH_PDF_RE.fullmatch(cmd):
        block(f'fetch_pdf.py must be run as exactly: python3 {ROOT}/scripts/'
              'fetch_pdf.py "<https-url>" <name>  (nothing before or after it; '
              "name = letters, digits, - or _).")
    if "fetch_repo" in cmd and not FETCH_REPO_RE.fullmatch(cmd):
        block(f'fetch_repo.py must be run as exactly: python3 {ROOT}/scripts/'
              'fetch_repo.py "<https-url>" <name>  (nothing before or after it; '
              "name = letters, digits, - or _).")
    if re.search(r"docs_sync\.py\s+(apply|reject)", cmd) and not DOCS_DECIDE_RE.fullmatch(cmd):
        block(f"docs_sync.py apply/reject must be run as exactly: python3 {ROOT}/scripts/docs_sync.py "
              "apply <id>  (or reject <id>; nothing before or after it).")
    # The excluded commands don't run in the Bash sandbox (their boundary
    # is Docker / a frozen script), so a stale Layer A pass doesn't affect
    # them. Everything else runs sandboxed and needs a fresh pass.
    excluded = (RUN_ANALYSIS_RE.fullmatch(cmd) or CORPUS_SEARCH_RE.fullmatch(cmd)
                or FETCH_PDF_RE.fullmatch(cmd) or FETCH_REPO_RE.fullmatch(cmd) or DOCS_DECIDE_RE.fullmatch(cmd))
    if not excluded:
        age = None if ts is None else time.time() - ts
        if age is None or age < -60 or age > PASS_TTL_S:
            block(f"The sandbox preflight pass is more than {PASS_TTL_S // 60} "
                  f"minutes old, so Bash is locked again. Re-run exactly: "
                  f"{PROBE_CMD}")
    sys.exit(0)


def record(data):
    cmd = (data.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str) or cmd.strip() != PROBE_CMD:
        sys.exit(0)
    sid = data.get("session_id")
    text = json.dumps(data.get("tool_response"), ensure_ascii=False)
    checks = re.findall(r"PROBE (\w+) (PASS|FAIL)", text)
    passed = ("PROBE RESULT PASS" in text and "PROBE RESULT FAIL" not in text
              and checks and all(r == "PASS" for _, r in checks))
    state = "pass" if passed else "fail"
    write_marker(sid, state)
    log = write_log({"event": "sandbox_preflight", "result": state.upper(),
                     "session_id": sid, "time": now(),
                     "checks": dict(checks)})
    if not passed:
        failed = [n for n, r in checks if r != "PASS"] or ["probe output unreadable"]
        msg = ("SANDBOX PREFLIGHT FAILED: " + ", ".join(failed) +
               f". Bash is now disabled for this session. Log: {log}")
        print(json.dumps({"systemMessage": msg, "decision": "block",
                          "reason": msg + " Stop and tell the user."}))
    sys.exit(0)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            raise ValueError("hook input is not an object")
        {"start": start, "gate": gate, "record": record}[mode](data)
    except SystemExit:
        raise
    except BaseException as e:
        if mode == "gate":
            block(f"Preflight gate error (failing closed): {e!r}")
        print(f"preflight {mode} error: {e!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
