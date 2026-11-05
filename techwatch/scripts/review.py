#!/usr/bin/env python3
"""Review tech-watch proposals and apply the ones you want.

    review.py                        what needs you; then, change by change: test it (and apply it if it passes,
                                     or just report), apply it now, or skip it
    review.py show <id>              read a proposal (and its staged change, if any)
    review.py test <id> [--no-apply] have the eval harness test it; it's applied if it passes (--no-apply: just
                                     report). Needs "Eval harness folder" in my/techwatch.md
    review.py apply <id> [--each|--yes]  see the exact change as one diff, then apply it with one yes (--each: file
                                     by file; --yes: apply what's shown without asking, e.g. from Claude Code's `!`,
                                     which has no terminal to answer in). For a proposal that's not staged yet, this
                                     approves it instead.
    review.py approve <id> [note]    have it staged (the next `techwatch.py run` does it); the note steers staging
    review.py reject  <id> [--kind|--as-is] [note]
                                     no. --as-is (the default): not as it is, but scouts may propose a variant
                                     with real differences, or build on it. --kind: not this kind of change at all
    review.py shelve  <id> [note]    not now: kept for later, and scouts may bring it back or build on it
    review.py trial   <id> [--yes]   apply it on trial: checked at the next checkup, then kept or rolled back
    review.py test <id> --deeper     test again with every task run several times (about 3x the cost), for a
                                     result too small for the first test to settle
    review.py defer   <id> [YYYY-MM-DD] [note]
    review.py rollback <id> [--yes]  restore the files apply changed (--yes: without asking per file)
    review.py evaluated <id> keep|remove [note]   after its review date: did it help? (remove undoes it first)
    review.py list [--all]           every proposal and its status
    review.py checkpoints            the earlier versions of the workspace the eval harness has kept
    review.py restore <date>         go back to one of them (shows what changes first; asks before writing)

How tech watch runs, and what happens when a change is ready, are set in my/techwatch.md ("Tech watch runs",
"When a change is ready"). With an automatic choice, `techwatch.py run` applies changes for you, except ones that
touch what AUTO_NEVER below lists, which always wait for you.

You run this; the scouts never do (they have no shell, and state/ is outside what they can write). A proposal's
status comes only from state/ledger.jsonl, so a proposal, or a web page quoted into one, can't approve itself.
Shell commands in a bundle are printed for you to run yourself; this script never runs them.
"""
import difflib
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

TW = Path(__file__).resolve().parent.parent
WS = TW.parent
PROPOSALS, STAGED, INBOX = TW / "proposals", TW / "staged", TW / "inbox"
LEDGER, RUNS = TW / "state" / "ledger.jsonl", TW / "state" / "runs.jsonl"
PROBLEM, SCOUT_OK = TW / "state" / "last_problem", TW / "state" / "scout_ok"
INTEGRITY = WS / "scripts" / "integrity.json"
EVALUATIONS = TW / "evaluations"   # test results, written by the eval harness's techtest.py
MY = WS / "my" / "techwatch.md"

RISKY_TARGET = re.compile(r"(settings(\.local)?\.json|/hooks/|GUARDRAILS|ETHICS|CLAUDE\.md|^scripts/|/scripts/|"
                          r"^sandbox/|\.mcp\.json|integrity\.json|^\.claude/agents/|^layers/|techwatch/(config\.json|"
                          r"AUTONOMY\.md|scripts/|prompts/)|^my/)")
RISKY_TEXT = re.compile(
    r"(allowedDomains|\"allow\"|permissions|bypassPermissions|dangerously|defaultMode|excludedCommands|"
    r"curl\s|wget\s|\|\s*(ba)?sh\b|ANTHROPIC_API_KEY|api[_-]?key|credential|\.ssh|base64|eval\(|exec\(|"
    r"subprocess|os\.system|rm\s+-rf|chmod|crontab|launchctl|systemctl|pip3? install|npm (i|install)|git push|"
    r"smtp|send_message|webhook|http://)", re.I)

# Never applied without a person saying yes to that change: hooks, the sandbox, the safety layers, the ethics and
# guardrails, tech watch's own rules and code, the scripts that enforce safety, and the user's own files.
AUTO_NEVER = re.compile(r"(settings\.local\.json|/hooks/|GUARDRAILS|ETHICS|\.mcp\.json|integrity\.json|^layers/|"
                        r"techwatch/(config\.json|AUTONOMY\.md|scripts/|prompts/)|^my/|^setup\.py|"
                        r"^scripts/(validate_proposal|apply_proposal|run_analysis|update|sandbox_probe)\b|"
                        r"^sandbox/|^\.claude/(?!agents/|skills/|commands/|settings\.json$)|CLAUDE\.local\.md|^\.git|"
                        r"^workspace/|^techwatch/(?!WATCHLIST\.md$|CAPABILITIES\.md$))")
# Tech watch's own records (the ledger, results, other changes' backups, scout notes): no prepared change may write
# them, even one a person applies, or a change could approve itself or fake a test result.
BOOKKEEPING = re.compile(r"^techwatch/(state|staged|evaluations|evaluations-full|inbox|proposals|logs)/")
# Automatic only if the reviewers agree it's major or meets all four criteria (never just because "Other changes:
# yes"): other scripts, new test files, an agent's or skill's tools, and permission rules in .claude/settings.json.
# Within these, what would weaken a safeguard stays never-automatic: editing or deleting an existing test, an
# agent's hooks, MCP servers or permission mode, removing an ask or deny rule, and any other settings key.
REVIEWED = re.compile(r"^(scripts/|tests/|techwatch/tests/|\.claude/settings\.json$|"
                      r"\.claude/(skills|agents|commands)/.*\.(py|sh|js|ts|mjs|cjs)$)")
FRONTMATTER_FILES = re.compile(r"^\.claude/(agents|skills|commands)/")
NEVER_KEYS = ("hooks", "mcpServers", "permissionMode", "disable-model-invocation")

def tty():
    """True if someone can answer questions here: a terminal on stdin. False with no stdin at all (some Windows and
    scheduled runs) and in Claude Code's `!`, which has no terminal."""
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


RED, YEL, GRN, DIM, BOLD, OFF = ("\033[31m", "\033[33m", "\033[32m", "\033[2m", "\033[1m", "\033[0m") \
    if getattr(sys.stdout, "isatty", lambda: False)() else ("",) * 6
RUNNER_EVENTS = ("staged", "cannot_stage", "stage_failed", "auto_applied")
DECISIVE = ("approved", "rejected", "deferred", "shelved", "applied", "rolled_back", "kept", "removed",
            "trial_started") + RUNNER_EVENTS
APPLIED = ("applied", "auto_applied")
IN_EFFECT = ("applied", "trial", "kept")   # statuses whose files are in the workspace now


# ---------------------------------------------------------------- my/techwatch.md
def prefs():
    """The user's tech-watch settings from my/techwatch.md, with defaults for anything missing."""
    try:
        text = MY.read_text(encoding="utf-8")
    except OSError:
        text = ""

    def field(name):
        m = re.search(rf"^[ \t*_]*{re.escape(name)}[ \t*_]*:[ \t*_]*(.*?)[ \t*_]*$", text, re.M | re.I)
        return m.group(1).strip() if m else ""

    runs = field("Tech watch runs").lower()
    old = field("Run my own tech watch").lower()   # the earlier yes/no setting
    if not runs:
        runs = "off" if old.startswith("no") else ("when I start it" if old.startswith("yes") else "automatically")
    # a value tech watch doesn't recognise means the cautious choice, not the automatic one
    runs = ("automatically" if runs.startswith("auto") else "off" if runs.startswith(("off", "no", "never", "stop"))
            else "when I start it")
    scans = "weekly only" if field("Scans").lower().startswith("weekly") else "daily and weekly"
    ready = field("When a change is ready").lower()
    ready = (("test-only" if "ask" in ready or "tell" in ready else "test") if ready.startswith("test")
             else "apply" if ready.startswith("apply") else "ask")
    harness = field("Eval harness folder")
    harness = Path(harness).expanduser() if harness and not harness.startswith(("(", "<")) else None
    return {"runs": runs, "scans": scans, "ready": ready, "harness": harness}


def techtest_script(p=None):
    """The eval harness's techtest.py, or None if there isn't one set up."""
    h = (p or prefs())["harness"]
    if not h:
        return None
    for c in (h / "evals" / "ClaudeImproverHarness" / "techtest.py", h / "ClaudeImproverHarness" / "techtest.py",
              h / "techtest.py"):
        if c.exists():
            return c
    return None


# ---------------------------------------------------------------- ledger
def events():
    try:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def record(pid, event, note="", **extra):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "id": pid, "event": event, "note": note,
         "actor": "runner" if event in RUNNER_EVENTS else "operator", **extra}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e) + "\n")


def status_of(pid, evs=None):
    """Latest decisive event for a proposal; 'pending' if none (or if a deferral has run out)."""
    evs = events() if evs is None else evs
    st, until = "pending", None
    for e in evs:
        if e.get("id") == pid and e.get("event") in DECISIVE:
            st, until = e["event"], e.get("until")
    if st == "deferred" and until and until <= date.today().isoformat():
        st = "pending"
    return {"auto_applied": "applied", "trial_started": "trial"}.get(st, st)


def test_request(pid, evs=None):
    """The latest test request for pid that came after its last decision other than staging, or None."""
    req = None
    for e in events() if evs is None else evs:
        if e.get("id") != pid:
            continue
        if e.get("event") == "test_requested":
            req = e
        elif e.get("event") in DECISIVE and e.get("event") not in ("staged",):
            req = None
    return req


def tested_waiting(pid, evs=None):
    """True if pid's requested test has a result and the change is still waiting (didn't pass, test only, or it
    passed but needs a person): it's the user's decision again."""
    req = test_request(pid, evs)
    return bool(req) and bool(evaluation(pid, req.get("ts", ""))[0])


def evaluation(pid, after=""):
    """(outcome, verdict of the 'staged' arm) from the harness's evaluations/<id>.md, if written after `after`
    (an ISO time); (None, None) if there's none yet."""
    f = EVALUATIONS / f"{pid}.md"
    if not f.exists() or (after and datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat() < after):
        return None, None
    text = f.read_text(encoding="utf-8")
    m = re.search(r"^Outcome: (.+)$", text, re.M)
    v = re.search(r"^\*\*staged: (met|not met|INVALID|inconclusive)\.\*\*", text, re.M)
    outcome = m.group(1).strip() if m else None
    if outcome in ("in progress", "re-running"):
        return None, None
    return outcome, (v.group(1) if v else None)


def classify(pid):
    """(never, reviewed) for pid's staged change: targets only a person may approve, plus manual steps and stale
    files; and targets that may change automatically only if the reviewers agree (REVIEWED)."""
    try:
        mf = json.loads((STAGED / pid / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["(no readable manifest)"], []
    never, reviewed = [], []
    for ch in mf.get("changes", []):
        if ch.get("action") in ("manual", "command"):
            never.append(f"a by-hand step ({ch.get('why', '')[:60]})")
            continue
        p = resolve(ch.get("target", ""))
        rel = p.relative_to(WS).as_posix() if p else str(ch.get("target"))
        if p is None or AUTO_NEVER.search(rel):
            never.append(rel)
            continue
        src = (STAGED / pid / str(ch.get("source", ""))).resolve()
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        new = src.read_text(encoding="utf-8") if src.is_file() else ""
        if ch.get("action") == "append":
            new = old + ("" if old.endswith("\n") or not old else "\n") + new
        if REVIEWED.search(rel):
            if rel.endswith("settings.json"):
                why = settings_change(old, new)
                (never if why else reviewed).append(f"{rel} ({why or 'permission rules'})")
            elif "tests/" in rel and p.exists():
                never.append(f"{rel} (changes an existing test)")
            else:
                reviewed.append(rel)
        elif FRONTMATTER_FILES.search(rel) and frontmatter(old) != frontmatter(new):
            fm_old, fm_new = frontmatter_keys(frontmatter(old)), frontmatter_keys(frontmatter(new))
            if any(fm_old.get(k) != fm_new.get(k) for k in NEVER_KEYS):
                never.append(f"{rel} (its hooks, MCP servers or permission mode)")
            else:
                reviewed.append(f"{rel} (its tools or settings)")
        if p.exists() and base_changed(pid, rel):
            never.append(f"{rel} (changed since the change was prepared)")
        elif rel == "CLAUDE.md" and hard_constraints(old) != hard_constraints(new):
            never.append("CLAUDE.md's hard constraints")
    return never, reviewed


def touches_never_auto(pid):
    return classify(pid)[0]


def bundle_hash(pid):
    """sha256 of the prepared change (its manifest and the files it names), as the eval harness's techtest.py
    records it for the bundle it tested; None if there's no readable manifest."""
    import hashlib
    bundle = STAGED / pid
    try:
        data = (bundle / "manifest.json").read_bytes()
        changes = json.loads(data).get("changes", [])
    except (OSError, ValueError):
        return None
    h = hashlib.sha256(data)
    for ch in changes:
        if ch.get("source"):
            p = bundle / str(ch["source"])
            h.update(str(ch["source"]).encode() + b"\0" + (p.read_bytes() if p.is_file() else b"<missing>") + b"\0")
    return h.hexdigest()


def tested_bundle(pid):
    """The bundle hash the harness's result names, or None (older results don't name one)."""
    f = EVALUATIONS / f"{pid}.md"
    m = re.search(r"^Bundle: ([0-9a-f]{64})$", f.read_text(encoding="utf-8"), re.M) if f.exists() else None
    return m.group(1) if m else None


def needs_review(pid):
    return classify(pid)[1]


def settings_change(old, new):
    """'' if .claude/settings.json changes only by permission rules, without dropping an ask or deny rule;
    otherwise what else it changes."""
    try:
        o, n = json.loads(old or "{}"), json.loads(new)
    except ValueError:
        return "not valid JSON"
    rules = ("allow", "ask", "deny")
    po, pn = o.get("permissions", {}), n.get("permissions", {})
    rest = lambda d, p: {**{k: v for k, v in d.items() if k != "permissions"},
                         "permissions": {k: v for k, v in p.items() if k not in rules}}
    if rest(o, po) != rest(n, pn):
        return "changes settings other than permission rules"
    dropped = [r for k in ("ask", "deny") for r in po.get(k, []) if r not in pn.get(k, [])]
    return f"removes {', '.join(dropped[:3])}" if dropped else ""


def frontmatter_keys(fm):
    """{top-level key: its block of text} for YAML frontmatter, without needing a YAML library."""
    out, key = {}, None
    for line in fm.splitlines():
        m = re.match(r"^[\"']?([A-Za-z][\w-]*)[\"']?\s*:(.*)$", line)
        if m:
            key = m.group(1)
            out[key] = m.group(2).strip()
        elif key:
            out[key] += "\n" + line
    return out


def frontmatter(text):
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    return m.group(1).strip() if m else ""


def base_changed(pid, rel):
    """True if a target changed since the change was prepared (techwatch.py records the hashes after staging)."""
    import hashlib
    try:
        bases = json.loads((TW / "state" / "stage-bases" / f"{pid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False   # prepared before hashes were kept: the diff shown at apply time is the check
    if rel not in bases:
        return False
    p = WS / rel
    now = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    return now != bases[rel]


def hard_constraints(text):
    m = re.search(r"^## Hard constraints\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def stage_attempted(pid, evs):
    return any(e.get("id") == pid and e.get("event") in RUNNER_EVENTS for e in evs)


# ---------------------------------------------------------------- proposals
def proposals():
    if not PROPOSALS.exists():
        return []
    return sorted((d for d in PROPOSALS.iterdir() if d.is_dir() and (d / "proposal.md").exists()),
                  key=lambda d: d.name, reverse=True)


def meta(d):
    try:
        return json.loads((Path(d) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def require(pid):
    d = PROPOSALS / pid
    if not (d / "proposal.md").exists():
        sys.exit(f"No proposal named {pid}. Try: review.py list --all")
    return d


def auto_stage_ok(d, evs, level):
    m = meta(d)
    return (level >= 1 and status_of(d.name, evs) == "pending" and not stage_attempted(d.name, evs)
            and m.get("significance") in ("notable", "major") and m.get("kind") != "outside")


def to_stage(level):
    """Proposals the runner should stage now, oldest first: approved ones, plus (at autonomy level 1+) notable and
    major ones nobody has decided on yet."""
    evs = events()
    return [d.name for d in sorted(proposals(), key=lambda d: d.name)
            if status_of(d.name, evs) == "approved" or auto_stage_ok(d, evs, level)]


def can_stage(pid):
    level = json.loads((TW / "config.json").read_text(encoding="utf-8")).get("autonomy_level", 0)
    return pid in to_stage(level)


def mark_stage_result(pid, why=""):
    """After a stage run that won't be retried: record what it left, so a proposal that can't be staged waits for
    you instead of being re-staged (and paid for) every day."""
    if (STAGED / pid / "manifest.json").exists():
        record(pid, "staged")
    elif (STAGED / pid / "CANNOT_STAGE.md").exists():
        record(pid, "cannot_stage", "the stage run explained why in CANNOT_STAGE.md")
    else:
        record(pid, "stage_failed", why or "no bundle written")
    return status_of(pid)


# ---------------------------------------------------------------- status
def health():
    out = []
    try:
        p = PROBLEM.read_text(encoding="utf-8").strip()
        if p:
            out.append(f"Tech-watch problem: {p}")
    except OSError:
        pass
    return out


def summary():
    """Counts for the session-start reminder."""
    evs = events()
    groups = {"decide": 0, "staged": 0, "testing": 0, "stuck": 0, "major": 0, "trial_over": 0}
    for d in proposals():
        st = status_of(d.name, evs)
        if st == "trial" and trial_waiting(d.name, evs):
            groups["trial_over"] += 1
        if st == "staged" and test_request(d.name, evs) and not tested_waiting(d.name, evs):
            groups["testing"] += 1
        elif st == "pending":
            groups["decide"] += 1
            groups["major"] += meta(d).get("significance") == "major"
        elif st == "staged":
            groups["staged"] += 1
            groups["major"] += meta(d).get("significance") == "major"
        elif st in ("cannot_stage", "stage_failed"):
            groups["stuck"] += 1
    return groups


def line_for(d, st):
    m = meta(d)
    sig = m.get("significance", "?")
    flag = f"{RED}{BOLD}MAJOR{OFF} " if sig == "major" else (f"{YEL}notable{OFF} " if sig == "notable" else "")
    return (f"  {flag}{BOLD}{d.name}{OFF}  [{st}]  {DIM}{m.get('kind', '?')} · risk {m.get('risk', '?')}{OFF}\n"
            f"      {m.get('title', '(no title)')}")


def cmd_status():
    evs = events()
    evs = note_results(evs)
    groups = {k: [] for k in ("trial_over", "tested", "testing", "staged", "pending", "stuck", "approved", "trial",
                              "applied")}
    for d in proposals():
        st = status_of(d.name, evs)
        st = "stuck" if st in ("cannot_stage", "stage_failed") else st
        if st == "trial" and trial_waiting(d.name, evs):
            st = "trial_over"
        if st == "staged" and test_request(d.name, evs):
            st = "tested" if tested_waiting(d.name, evs) else "testing"
        if st in groups:
            groups[st].append(d)
    print(f"{BOLD}Tech watch{OFF}  ({TW})\n")
    for w in health():
        print(f"{RED}{BOLD}{w}{OFF}\n")
    titles = {
        "trial_over": "Trial over, waiting for you: `show <id>`, then `evaluated <id> keep` or `rollback <id>`",
        "tested": "Tested, waiting for you: `show <id>` has the result; then `apply`, `trial`, `test <id> --deeper`, "
                  "`shelve` or `reject`",
        "testing": "Being tested by the eval harness",
        "staged": "Ready to decide, with the exact change prepared: `show <id>`, then `apply <id>` or `reject <id>`",
        "pending": "Ideas waiting for you: `show <id>`, then `approve <id>` (to have it prepared), `reject` or `defer`",
        "stuck": "Couldn't be prepared: read staged/<id>/CANNOT_STAGE.md, then `approve <id> \"your answer\"` to "
                 "retry, or `reject <id>`",
        "approved": "Approved, waiting to be prepared (the next `techwatch.py run`)",
        "trial": "On trial: checked again at the next checkup, then kept or rolled back",
        "applied": "Applied: after the review date, `evaluated <id> keep|remove`"}
    for k, title in titles.items():
        if groups[k]:
            print(f"{BOLD}{title}{OFF}")
            for d in groups[k]:
                print(line_for(d, status_of(d.name, evs) if k == "stuck" else k))
                if k in ("testing", "tested"):
                    req = test_request(d.name, evs)
                    outcome, verdict = evaluation(d.name, req.get("ts", ""))
                    ev = evidence_of(d.name) if outcome else None
                    print(f"      {DIM}" + ((f"result: {ev['plain']}" if ev else f"result: {outcome}, "
                                            f"{verdict or 'no verdict'}") if outcome else
                          "waiting for the test" + (" (deeper)" if req.get("depth") == "deeper" else ""))
                          + ("; applied if it passes" if req.get("apply_if_passes") and not outcome else "") + OFF)
                    if k == "tested" and (ok := trial_ok(d.name, evs))[0]:
                        print(f"      {DIM}could go on trial ({ok[1]}): review.py trial {d.name}{OFF}")
                tests = history(d.name, evs)
                if len(tests) > 1 or (tests and k not in ("testing", "tested")):
                    print(f"      {DIM}tested {len(tests)}x: " + "; ".join(tests[-3:]) + OFF)
                if k == "trial_over":
                    print(f"      {DIM}{next(e['note'] for e in reversed(evs) if e.get('id') == d.name and e.get('event') == 'trial_ended')}{OFF}")
                if k == "applied" and meta(d).get("review_after"):
                    print(f"      {DIM}review after {meta(d)['review_after']}{OFF}")
            print()
    if not any(groups.values()):
        print("Nothing needs you.\n")
    notes = sorted(INBOX.glob("*-daily.md")) + sorted(INBOX.glob("*-weekly.md"))
    if notes:
        print(f"{DIM}Latest scout note: {max(notes, key=lambda p: p.stat().st_mtime).relative_to(WS)}{OFF}")
    month, total, n = date.today().isoformat()[:7], 0.0, 0
    try:
        for line in RUNS.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("ts", "")[:7] == month:
                n, total = n + 1, total + float(r.get("cost_usd") or 0)
    except (OSError, ValueError):
        pass
    print(f"{DIM}Tech-watch runs this month: {n}, est. ${total:.2f} of usage{OFF}")
    try:
        sys.path.insert(0, str(WS / "scripts"))
        import feedback
        applied = sorted((e["ts"], e["id"]) for e in events() if e.get("event") in APPLIED)
        lines = feedback.summary(applied)
        if lines:
            print(f"{DIM}Your 'was this useful?' answers: " + "; ".join(lines[-3:]) + OFF)
    except Exception:
        pass


def trial_waiting(pid, evs):
    """True if pid's trial ended and is waiting for you."""
    start = max((e.get("ts", "") for e in evs if e.get("id") == pid and e.get("event") == "trial_started"), default="")
    return any(e.get("id") == pid and e.get("event") == "trial_ended" and e.get("ts", "") >= start for e in evs)


def cmd_list(all_):
    evs = events()
    for d in proposals():
        st = status_of(d.name, evs)
        if all_ or st in ("pending", "approved", "staged", "cannot_stage", "stage_failed"):
            print(line_for(d, st))


def cmd_show(pid):
    d = require(pid)
    evs = note_results()
    m = meta(d)
    print(f"{DIM}status: {status_of(pid, evs)}"
          + "".join(f"\n{x}" for x in [options_line(m)] if x)
          + "".join(f"\nbuilds on: {', '.join(m['builds_on'])}" for _ in [0] if m.get("builds_on"))
          + "".join(f"\ntest {i}: {h}" for i, h in enumerate(history(pid, evs), 1)) + f"{OFF}\n")
    print((d / "proposal.md").read_text(encoding="utf-8"))
    if (EVALUATIONS / f"{pid}.md").exists():
        print(f"\n{BOLD}Test result{OFF} ({(EVALUATIONS / (pid + '.md')).relative_to(WS)}):\n")
        print((EVALUATIONS / f"{pid}.md").read_text(encoding="utf-8"))
    if (STAGED / pid / "APPLY.md").exists():
        print(f"\n{BOLD}The prepared change{OFF} ({(STAGED / pid).relative_to(WS)}/):\n")
        print((STAGED / pid / "APPLY.md").read_text(encoding="utf-8"))
        print(f"\n{DIM}`review.py apply {pid}` shows the exact diff before anything is written.{OFF}")


def cmd_decide(pid, event, rest):
    require(pid)
    st = status_of(pid)
    if event in ("approved", "rejected", "shelved", "deferred") and st in IN_EFFECT:
        sys.exit(f"{pid} is {st}, so its files are in the workspace. Roll it back first (review.py rollback {pid}); "
                 f"to record whether it helped, use review.py evaluated {pid} keep|remove.")
    extra = {}
    if event == "deferred" and rest and re.fullmatch(r"\d{4}-\d{2}-\d{2}", rest[0]):
        extra["until"] = rest.pop(0)
    if event == "rejected":
        scope = "kind" if "--kind" in rest else "as_is" if "--as-is" in rest else None
        rest = [x for x in rest if x not in ("--kind", "--as-is")]
        if scope is None:
            a = ask("Not as it is, but a variant or something built on it could be worth proposing (v), or not this "
                    "kind of change at all (k)?", "V/k") if tty() else ""
            scope = "kind" if a.startswith("k") else "as_is"
        extra["scope"] = scope
    note = " ".join(rest)
    if event in ("rejected", "shelved") and not note and tty():
        note = input("Why? (scouts read this; Enter to skip) ").strip()
    record(pid, event, note, **extra)
    print(f"{pid}: {event}" + (f" until {extra['until']}" if extra.get("until") else "")
          + {"kind": " (not this kind of change: scouts won't propose it or anything like it)",
             "as_is": " (as it is: scouts may propose a variant with real differences, or build on it)"}.get(
              extra.get("scope"), "")
          + (" (kept for later: scouts may bring it back or build on it; `approve` revives it)"
             if event == "shelved" else ""))
    if event == "approved":
        print(f"The next run prepares it:  python3 {(TW / 'scripts' / 'techwatch.py').relative_to(WS)} stage {pid}")


# ---------------------------------------------------------------- apply
def resolve(target):
    """A manifest target (relative to the workspace, or absolute inside it) as a path, or None if outside."""
    p = Path(target).expanduser()
    p = (p if p.is_absolute() else WS / p).resolve()
    return p if p == WS or WS in p.parents else None


def lint(text):
    return [f"{i}: {l.strip()[:140]}" for i, l in enumerate(text.splitlines(), 1) if RISKY_TEXT.search(l)]


def ask(q, choices="y/N"):
    if not tty():
        return ""
    return input(f"{q} [{choices}] ").strip().lower()


def show_diff(old, new, name):
    for dl in difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                   fromfile=name, tofile=f"{name} (new)"):
        c = GRN if dl.startswith("+") and not dl.startswith("+++") else (
            RED if dl.startswith("-") and not dl.startswith("---") else "")
        sys.stdout.write(f"{c}{dl}{OFF}" if c else dl)
    print()


def cmd_apply(pid, each=False, auto=None, by_settings=False, yes=False):
    """auto: the reason it's being applied without asking (a passed test, or my/techwatch.md); no prompts then,
    and manual steps or outside proposals are left for you."""
    d = require(pid)
    st = status_of(pid)
    bundle = STAGED / pid
    mf = bundle / "manifest.json"
    if auto and (meta(d).get("kind") == "outside" or not mf.exists()):
        return False
    if meta(d).get("kind") == "outside" and not mf.exists():
        print((d / "proposal.md").read_text(encoding="utf-8"))
        if ask("\nThis one happens outside the workspace (steps above). Mark it as done?") == "y":
            record(pid, "applied", "done by hand (outside the workspace)")
            print(f"{pid}: recorded as applied. After its review date: review.py evaluated {pid} keep|remove")
        return
    if st not in ("staged", "rolled_back") or not mf.exists():
        if st in ("pending", "deferred"):
            record(pid, "approved", "approved from apply")
            print(f"{pid} isn't prepared yet, so this approved it. Prepare it now with:\n"
                  f"  python3 {(TW / 'scripts' / 'techwatch.py').relative_to(WS)} stage {pid}\n"
                  f"then run `review.py apply {pid}` again.")
            return
        sys.exit(f"{pid} is '{st}', with no prepared change to apply.")
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    if (bundle / "APPLY.md").exists():
        print(f"{BOLD}--- What this does (APPLY.md) ---{OFF}\n{(bundle / 'APPLY.md').read_text(encoding='utf-8')}\n")
    protected = set()
    if INTEGRITY.exists():
        protected = {str((WS / k).resolve()) for k in json.loads(INTEGRITY.read_text(encoding="utf-8"))}

    writes, manual, skipped = [], [], 0
    for i, ch in enumerate(manifest.get("changes", []), 1):
        action = ch.get("action")
        if action in ("command", "manual"):
            manual.append(ch)
            continue
        if action not in ("write", "append"):
            print(f"{RED}[{i}] unknown action {action!r}; skipped{OFF}")
            skipped += 1
            continue
        target = resolve(ch.get("target", ""))
        src = (bundle / ch.get("source", "")).resolve()
        if target is None:
            print(f"{RED}[{i}] target {ch.get('target')} is outside this workspace; moved to the by-hand steps{OFF}")
            manual.append({"action": "manual", "why": ch.get("why", ""),
                           "instructions": f"copy {src} to {ch.get('target')} yourself, if you want it"})
            continue
        if bundle.resolve() not in src.parents or not src.is_file():
            print(f"{RED}[{i}] source {ch.get('source')} is missing or outside the bundle; skipped{OFF}")
            skipped += 1
            continue
        if BOOKKEEPING.search(target.relative_to(WS).as_posix()):
            print(f"{RED}[{i}] {target.relative_to(WS).as_posix()} is tech watch's own record (ledger, results, "
                  f"backups or notes); a prepared change may never write it. Skipped.{OFF}")
            skipped += 1
            continue
        new = src.read_text(encoding="utf-8")
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        final = (old + ("" if old.endswith("\n") or not old else "\n") + new) if action == "append" else new
        writes.append({"n": i, "target": target, "rel": target.relative_to(WS).as_posix(), "old": old,
                       "new": final, "why": ch.get("why", ""), "exists": target.exists()})

    if not writes and not manual:
        sys.exit("Nothing to apply: " + ("every change in it was refused (see above)." if skipped else
                                        "the manifest has no changes."))
    print(f"{BOLD}--- The exact change ---{OFF}")
    for w in writes:
        risky = RISKY_TARGET.search(w["rel"])
        print(f"\n{BOLD}[{w['n']}] {w['rel']}{OFF}{'  (new file)' if not w['exists'] else ''}  {DIM}{w['why']}{OFF}")
        if risky:
            print(f"   {RED}{BOLD}High-risk file{OFF}{RED}: it shapes what agents may do or how they're instructed. "
                  f"Read this diff closely.{OFF}")
        if str(w["target"]) in protected:
            print(f"   {YEL}Integrity-checked file: after applying, the manifest is rebuilt for you.{OFF}")
        flagged = lint(w["new"])
        if flagged:
            print(f"   {YEL}Lines worth a second look:{OFF}")
            for f_ in flagged[:15]:
                print(f"     {YEL}{f_}{OFF}")
        show_diff(w["old"], w["new"], w["rel"])
    if manual:
        print(f"{BOLD}Steps for you to do by hand (not done by this script):{OFF}")
        for ch in manual:
            print(f"  - {ch.get('run') or ch.get('instructions')}  {DIM}({ch.get('why', '')}){OFF}")
        print()

    stale = [w["rel"] for w in writes if w["exists"] and base_changed(pid, w["rel"])]
    if stale:
        print(f"{RED}{BOLD}Changed since this was prepared:{OFF}{RED} {', '.join(stale)}. Applying would undo those "
              f"edits (the red lines above). Consider re-preparing it: review.py approve {pid}{OFF}")
    chosen = writes
    if auto and (manual or stale or skipped):
        why = ("has steps to do by hand" if manual else f"changed since prepared: {stale}" if stale
               else f"{skipped} of its changes can't be applied as written")
        record(pid, "not_automatic", why)
        print(f"{pid} waits for you: {why}")
        return False
    if auto or yes:
        each = False
    elif writes and not tty():
        # In Claude Code, `!` commands have no terminal to answer in: say how to apply exactly what was shown.
        return print(f"Nothing written yet. To apply exactly the change above: review.py apply {pid} --yes "
                     "(or run review.py in a terminal to answer file by file).")
    elif writes and not each:
        a = ask(f"Apply {'this change' if len(writes) == 1 else f'all {len(writes)} file changes'}?", "y/N/each")
        if a == "each":
            each = True
        elif a != "y":
            return print("Nothing written.")
    if each:
        chosen = [w for w in writes if ask(f"Apply [{w['n']}] {w['rel']}?") == "y"]

    backups, applied = bundle / "backup", []
    for w in chosen:
        backups.mkdir(parents=True, exist_ok=True)
        bk = backups / f"{w['n']:02d}-{w['target'].name}"
        if w["exists"]:
            shutil.copy2(w["target"], bk)
        else:
            bk.with_suffix(bk.suffix + ".absent").write_text("", encoding="utf-8")
        w["target"].parent.mkdir(parents=True, exist_ok=True)
        w["target"].write_text(w["new"], encoding="utf-8")
        written = backups / f"{w['n']:02d}-{w['target'].name}.written"   # what apply wrote, for update.py
        written.write_text(w["new"], encoding="utf-8")
        applied.append({"n": w["n"], "target": w["rel"], "backup": str(bk), "written": str(written)})
        print(f"   {GRN}written{OFF} {w['rel']}")
    if not applied and not manual:
        return print("Nothing written.")
    if auto:
        record(pid, "auto_applied", f"{len(applied)} file change(s); {auto}", files=applied, by_settings=by_settings)
    else:
        record(pid, "applied", f"{len(applied)} file change(s)", files=applied)
    (bundle / "applied.json").write_text(json.dumps(applied, indent=2), encoding="utf-8")
    after_apply(pid, [a["target"] for a in applied], protected)
    if manual:
        print(f"{YEL}{len(manual)} step(s) are yours to do by hand (listed above).{OFF}")
    if (bundle / "EVAL.md").exists():
        print(f"How to tell whether it helped: {(bundle / 'EVAL.md').relative_to(WS)}")
    return True


def after_apply(pid, rels, protected):
    """You've just approved these exact changes, so bring the workspace's own bookkeeping up to date."""
    if INTEGRITY.exists() and rels:
        drifted = drifted_elsewhere(rels)
        if drifted:
            print(f"   {RED}Not rebuilding the integrity manifest: these protected files changed by some other route "
                  f"first: {', '.join(drifted)}. Check them (git diff), then run "
                  f"python3 scripts/validate_proposal.py --init yourself.{OFF}")
        else:
            subprocess.run([sys.executable, str(WS / "scripts" / "validate_proposal.py"), "--init"],
                           stdout=subprocess.DEVNULL, check=False)
            print(f"   {DIM}integrity manifest rebuilt (scripts/integrity.json){OFF}")
    # Not committed to git: scripts/update.py takes applied changes out before pulling an update and puts them
    # back after, which only works while they're uncommitted.


def drifted_elsewhere(rels):
    """Protected files whose hash no longer matches the manifest, apart from the ones just written."""
    import hashlib
    mine = {str((WS / r).resolve()) for r in rels}
    out = []
    for rel, expected in json.loads(INTEGRITY.read_text(encoding="utf-8")).items():
        p = (WS / rel).resolve()
        if str(p) in mine:
            continue
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            out.append(rel)
    return out


# ---------------------------------------------------------------- what tests showed, as facts
def evidence_of(pid):
    """What pid's latest test showed (the harness's 'Evidence staged:' line), or None."""
    f = EVALUATIONS / f"{pid}.md"
    m = re.search(r"^Evidence staged: (\{.*\})\s*$", f.read_text(encoding="utf-8"), re.M) if f.exists() else None
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def note_results(evs=None):
    """Record each new test result in the ledger as a fact ("test_result"), so the history of a proposal (and what
    scouts read) says what each test showed, not just what was decided. Returns the events."""
    evs = events() if evs is None else evs
    seen = {(e["id"], e.get("file_ts")) for e in evs if e.get("event") == "test_result"}
    new = False
    for f in sorted(EVALUATIONS.glob("*.md")) if EVALUATIONS.exists() else []:
        pid = f.stem
        if pid.startswith("checkup-") or not (PROPOSALS / pid).is_dir():
            continue
        outcome = evaluation(pid)[0]
        if not outcome:
            continue
        ts = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        if (pid, ts) in seen:
            continue
        ev = evidence_of(pid)
        record(pid, "test_result", (ev or {}).get("plain") or f"{outcome}: {evaluation(pid)[1] or 'no verdict'}",
               file_ts=ts, evidence=ev)
        new = True
    return events() if new else evs


def history(pid, evs):
    """One line per test of pid, oldest first."""
    out = []
    for e in evs:
        if e.get("id") == pid and e.get("event") == "test_result":
            ev = e.get("evidence") or {}
            depth = f"{ev.get('tasks', '?')} task(s) x {ev.get('runs_per_task', '?')} run(s)" if ev else ""
            out.append(f"{e.get('ts', '')[:10]}: {ev.get('result') or e.get('note', '')[:80]}"
                       + (f" ({ev['diff']:+.2f}, {depth}; could tell about {ev['detectable']}+)"
                          if ev.get("diff") is not None and ev.get("detectable") else (f" ({depth})" if depth else "")))
    return out


def options_line(m):
    o = m.get("options")
    return f"future options: {o}" + (f" ({m['options_why']})" if m.get("options_why") else "") if o else ""


# ---------------------------------------------------------------- trials
def trial_settings():
    try:
        return load_criteria().settings()
    except Exception:
        return {"trials": True, "trial_days": 60}


def active_trial(evs=None, but=None):
    evs = events() if evs is None else evs
    return next((d.name for d in proposals() if d.name != but and status_of(d.name, evs) == "trial"), None)


def trial_ok(pid, evs=None):
    """(True/False, why) for whether pid could go on trial: its test showed no clear harm, it can be undone, it
    doesn't close off options, and no other trial is running. A trial is for a result too small to settle, not for
    one that showed harm."""
    evs = events() if evs is None else evs
    ev, m = evidence_of(pid), meta(PROPOSALS / pid)
    if status_of(pid, evs) != "staged":
        return False, "it isn't a prepared change waiting for a decision"
    if not ev:
        return False, "it has no test result to go on"
    if ev.get("invalid") or ev.get("result") not in ("no clear difference", "better"):
        return False, f"its test showed {ev.get('result', 'nothing usable')}"
    for k, why in (("floors", "a task dropped more than allowed"), ("objection", "an outside reviewer objected"),
                   ("cost_over", "it costs more than the limit")):
        if ev.get(k):
            return False, why
    if m.get("reversible") is False:
        return False, "it can't be undone"
    if m.get("options") == "closes":
        return False, "it closes off options"
    if other := active_trial(evs, but=pid):
        return False, f"{other} is on trial already (one at a time)"
    if tested_bundle(pid) != bundle_hash(pid):
        return False, "the prepared change isn't the exact one that was tested"
    return True, f"test: {ev['result']}; reversible; options {m.get('options') or 'not stated'}"


def cmd_trial(pid, auto=None, log=print, yes=False):
    """Apply pid as a trial. auto: the reason it's done without asking (my/techwatch-auto.md allows it)."""
    require(pid)
    ok, why = trial_ok(pid)
    if not ok:
        log(f"{pid} can't go on trial: {why}")
        return False
    days = int(trial_settings().get("trial_days", 60))
    if not auto:
        print(f"{BOLD}Trial:{OFF} the change is applied now and checked again at the eval harness's next checkup (or "
              f"after {days} days), then kept, or rolled back if the workspace did worse.")
    if not cmd_apply(pid, auto=f"trial; {auto}" if auto else None, by_settings=bool(auto), yes=yes):
        return False
    until = date.fromordinal(date.today().toordinal() + days).isoformat()
    record(pid, "trial_started", (auto or "you started it") + f"; {why}", until=until, auto=bool(auto))
    log(f"{pid}: on trial until the next checkup (at the latest {until})")
    return True


def checkup_after(start):
    """(date, verdict) of the first harness checkup at least 14 days after `start` (YYYY-MM-DD), or None."""
    first = date.fromordinal(date.fromisoformat(start).toordinal() + 14).isoformat()
    for f in sorted(EVALUATIONS.glob("checkup-*.md")) if EVALUATIONS.exists() else []:
        day = f.stem[len("checkup-"):]
        if day >= first:
            m = re.search(r"^\*\*Verdict:\*\* (.+)$", f.read_text(encoding="utf-8"), re.M)
            return day, (m.group(1).strip() if m else "no verdict")
    return None


def safe_to_undo(pid):
    """True if every file the change wrote is still exactly as it wrote it (so undoing it loses no later edit)."""
    try:
        applied = json.loads((STAGED / pid / "applied.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for a in applied:
        t, w = resolve(a["target"]), Path(a.get("written", ""))
        if t is None or not w.is_file() or not t.exists() or t.read_bytes() != w.read_bytes():
            return False
    return True


def undo_quietly(pid, note):
    applied = json.loads((STAGED / pid / "applied.json").read_text(encoding="utf-8"))
    rels = []
    for a in reversed(applied):
        target, bk = resolve(a["target"]), Path(a["backup"])
        absent = bk.with_suffix(bk.suffix + ".absent")
        if absent.exists():
            target.unlink(missing_ok=True)
        elif bk.exists():
            shutil.copy2(bk, target)
        rels.append(a["target"])
    record(pid, "rolled_back", note)
    if INTEGRITY.exists() and rels and not drifted_elsewhere(rels):
        subprocess.run([sys.executable, str(WS / "scripts" / "validate_proposal.py"), "--init"],
                       stdout=subprocess.DEVNULL, check=False)


def end_trials(log=print, automatic=True):
    """End trials whose checkup (or time limit) has come. With automatic settings: kept, unless the checkup found
    the workspace did worse, then rolled back (if nothing edited the files since). Otherwise they wait for you."""
    evs = events()
    for d in proposals():
        pid = d.name
        if status_of(pid, evs) != "trial":
            continue
        start = next(e for e in reversed(evs) if e.get("id") == pid and e.get("event") == "trial_started")
        cu, until = checkup_after(start["ts"][:10]), start.get("until") or "9999"
        if not cu and date.today().isoformat() < until:
            continue
        if any(e.get("id") == pid and e.get("event") == "trial_ended" and e.get("ts", "") > start["ts"] for e in evs):
            continue   # already waiting for you
        found = (f"checkup of {cu[0]}: {cu[1]}" if cu else f"no checkup came by {until}, so nothing has checked it") + \
            " (a checkup compares the whole workspace with an earlier version, so other changes count too)"
        worse = bool(cu) and cu[1].startswith("the earlier version scored better")
        others = sorted({e["id"] for e in evs if e.get("event") in APPLIED and e.get("id") != pid
                         and e.get("ts", "") >= start["ts"] and status_of(e["id"], evs) in IN_EFFECT})
        if others:
            found += f"; other changes applied since: {', '.join(others)}"
        # Without a checkup there's no evidence either way, and with other changes in the window a "worse" can't be
        # pinned on this one: both wait for you.
        if automatic and trial_settings().get("trials", True) and cu and not (worse and others):
            if not worse:
                record(pid, "kept", f"trial ended: {found}")
                log(f"{pid}: trial ended, kept ({found})")
                continue
            if safe_to_undo(pid):
                undo_quietly(pid, f"trial ended: {found}")
                log(f"{pid}: trial ended, rolled back ({found})")
                continue
        record(pid, "trial_ended", found + ("; the files changed since, so undoing it waits for you" if worse else ""))
        log(f"{pid}: trial ended; waits for you to keep it or roll it back ({found})")


# ---------------------------------------------------------------- testing, and applying on your behalf
def cmd_test(pid, then_apply=True, note="", depth="standard"):
    d = require(pid)
    if not techtest_script():
        sys.exit("No eval harness set up: add its folder to my/techwatch.md (\"Eval harness folder\"), or apply the "
                 f"change without a test: review.py apply {pid}")
    if meta(d).get("kind") == "outside":
        sys.exit(f"{pid} happens outside the workspace, so there's nothing to test here.")
    runs = runs_in_test(pid)
    if runs:
        print(f"{YEL}{BOLD}This change adds or changes things Claude Code runs on its own{OFF}{YEL}: "
              f"{', '.join(runs)}. In a test they'd run on this computer, outside the sandbox, so a test waits until "
              f"you've read them: review.py show {pid}{OFF}")
        if ask("Have you read them, and should the test run them?") != "y":
            return print("Not tested.")
        note = (note + "; " if note else "") + f"operator read what runs in the test: {', '.join(runs)}"
    if status_of(pid) in ("pending", "deferred"):
        record(pid, "approved", "approved for a test")
    record(pid, "test_requested", note, apply_if_passes=then_apply, depth=depth)
    cost = "roughly $30-100 of usage: every task runs several times" if depth == "deeper" else \
        "several full agent runs: roughly $10-40 of usage"
    print(f"{pid}: will be tested{' more deeply' if depth == 'deeper' else ''}"
          f"{' and applied if it passes' if then_apply else ''}. The next tech-watch run starts it ({cost}). "
          f"Results: review.py show {pid}")


RUNS_CODE = re.compile(r"/hooks/|\.mcp\.json|settings|hooks, MCP servers")


def runs_in_test(pid):
    """Parts of pid's change that Claude Code would run by itself in a test (hooks, MCP servers, settings), outside
    the sandbox; a test of these waits for a person. Automatic test requests never include them (AUTO_NEVER)."""
    return [x for x in touches_never_auto(pid) if RUNS_CODE.search(x)]


def ready_choices(pid):
    """Ask what to do with one prepared change. Returns nothing; records the choice."""
    harness = techtest_script() is not None
    m = meta(PROPOSALS / pid)
    print(f"\n{BOLD}{pid}{OFF}: {m.get('title', '')}")
    if (STAGED / pid / "APPLY.md").exists():
        first = (STAGED / pid / "APPLY.md").read_text(encoding="utf-8").strip().split("\n\n")[0]
        print(f"{DIM}{first[:600]}{OFF}")
    ev = evidence_of(pid) if tested_waiting(pid) else None
    if ev:
        print(f"  {BOLD}Its test:{OFF} {ev['plain']}")
    can_trial = trial_ok(pid)[0]
    opts = ((["d = test it again, more deeply (every task run several times; about 3x the cost)"] if ev else
             ["t = test it, then apply it if it passes", "o = test it only (you decide after)"]) if harness else []) + (
        ["i = put it on trial (applied now, checked at the next checkup, then kept or rolled back)"] if can_trial
        else []) + ["a = see the exact change and apply it now", "s = show the whole proposal",
                    "h = shelve it (not now; kept for later)", "r = reject it", "Enter = decide later"]
    keys = "/".join(o.split(" ")[0] for o in opts[:-1])
    while True:
        a = ask("  " + "; ".join(opts) + "\n  Your choice?", keys)
        if a == "s":
            cmd_show(pid)
            continue
        if a in ("t", "o") and harness and not ev:
            cmd_test(pid, then_apply=a == "t")
        elif a == "d" and harness and ev:
            req = test_request(pid) or {}
            cmd_test(pid, then_apply=bool(req.get("apply_if_passes")), depth="deeper")
        elif a == "i" and can_trial:
            cmd_trial(pid)
        elif a == "a":
            cmd_apply(pid)
        elif a == "h":
            cmd_decide(pid, "shelved", [])
        elif a == "r":
            cmd_decide(pid, "rejected", [])
        return


def walk_through():
    """After the status: offer each prepared change that isn't being tested already."""
    evs = events()
    todo = [d.name for d in proposals() if status_of(d.name, evs) == "staged"
            and (not test_request(d.name, evs) or tested_waiting(d.name, evs))]
    if todo and tty():
        print(f"{BOLD}Decide now?{OFF}")
        for pid in todo:
            ready_choices(pid)
    elif todo:
        print(f"{DIM}To decide on these one by one, run review.py in a terminal. Or use the commands above, e.g. "
              f"review.py show {todo[0]}, then review.py apply {todo[0]} --yes (or test, trial, shelve, reject).{OFF}")


def load_criteria():
    """criteria.py, wired to this module (so it reads this workspace's proposals, whoever loaded review.py)."""
    import importlib.util
    import types
    spec = importlib.util.spec_from_file_location(f"criteria_{id(globals())}", TW / "scripts" / "criteria.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    me = sys.modules.get(__name__)
    if me is None or getattr(me, "TW", None) != TW:
        me = types.ModuleType(__name__)
        me.__dict__.update(globals())
    mod.review = me
    return mod


def auto_actions(log=print):
    """For a tech-watch run: apply what passed its test (if you asked), request tests or apply changes as
    my/techwatch.md and my/techwatch-auto.md say, and let the harness's queue advance. Returns the ids applied."""
    criteria = load_criteria()
    pr, evs, done = prefs(), note_results(), []
    harness = techtest_script(pr)
    scope = criteria.settings()
    end_trials(log, automatic=pr["ready"] in ("test", "apply"))
    evs = events()
    for d in proposals():
        pid = d.name
        if status_of(pid, evs) != "staged":
            continue
        req = test_request(pid, evs)
        if req:
            outcome, verdict = evaluation(pid, req.get("ts", ""))
            if outcome and req.get("apply_if_passes"):
                if verdict == "met":
                    if req.get("auto") and not spaced(scope["space_days"], evs):
                        continue   # an automatic change was applied recently; this one waits its turn
                    never, reviewed = classify(pid)
                    if not never and reviewed:
                        ok, why = criteria.qualifies(pid, scope, strict=True)
                        never = [] if ok else [f"{', '.join(reviewed)}: {why}"]
                    if tested_bundle(pid) != bundle_hash(pid):
                        never.append("the prepared change isn't the exact one that was tested")
                    if never:
                        if not any(e.get("id") == pid and e.get("event") == "test_result_seen" for e in evs):
                            record(pid, "test_result_seen", f"passed; waits for you: {', '.join(never)}")
                            log(f"{pid} passed its test but waits for you ({', '.join(never)})")
                        continue
                    if cmd_apply(pid, auto="it passed its test", by_settings=bool(req.get("auto"))):
                        done.append(pid)
                        evs = events()
                        log(f"applied {pid}: it passed its test")
                else:
                    # Too small a result to settle isn't a failure: with automatic settings, such a change can go on
                    # trial (checked again at the next checkup), if it qualifies like any automatic change.
                    ok, why_t = trial_ok(pid, evs)
                    automatic = req.get("auto") or pr["ready"] in ("test", "apply")
                    if ok and automatic and scope.get("trials", True) and spaced(scope["space_days"], evs):
                        never, reviewed = classify(pid)
                        q_ok, q_why = criteria.qualifies(pid, scope, strict=bool(reviewed)) if not never else (False, "")
                        if q_ok and cmd_trial(pid, auto=f"{why_t}; {q_why}", log=log):
                            done.append(pid)
                            evs = events()
                            continue
                    if not any(e.get("id") == pid and e.get("event") == "test_result_seen" for e in evs):
                        ev = evidence_of(pid)
                        record(pid, "test_result_seen", f"{outcome}: {verdict or 'no verdict'}; not applied"
                               + (f"; {ev['result']}" if ev else ""))
                        log(f"not applied {pid}: test {outcome}, {ev['plain'] if ev else verdict or 'no verdict'}")
            continue
        if pr["ready"] == "ask":
            continue
        never, reviewed = classify(pid)
        if never:
            continue
        if pr["ready"] in ("test", "test-only") and not harness:
            continue
        if pr["ready"] == "apply" and not spaced(scope["space_days"], evs):
            continue
        ok, why = criteria.qualifies(pid, scope, strict=bool(reviewed))
        if not ok:
            if not any(e.get("id") == pid and e.get("event") == "not_automatic" for e in evs):
                record(pid, "not_automatic", why)
                log(f"{pid} waits for you: {why}")
            continue
        if pr["ready"] in ("test", "test-only"):
            record(pid, "test_requested", f"my/techwatch.md: {pr['ready']}; {why}",
                   apply_if_passes=pr["ready"] == "test", auto=True)
            log(f"test requested for {pid} ({why})")
        elif cmd_apply(pid, auto=f"my/techwatch.md says apply it; {why}", by_settings=True):
            done.append(pid)
            evs = events()
            log(f"applied {pid} ({why})")
    if harness:
        start_tests(harness, log)   # quick when there's nothing to do; also runs the harness's periodic checkup
    return done


def spaced(days, evs=None):
    """True if no change was applied automatically (by my/techwatch.md's settings) in the last `days` days."""
    since = datetime.now(timezone.utc).timestamp() - days * 86400
    for e in events() if evs is None else evs:
        if e.get("event") == "auto_applied" and e.get("by_settings"):
            try:
                if datetime.fromisoformat(e["ts"]).timestamp() >= since:
                    return False
            except (KeyError, ValueError):
                pass
    return True


def start_tests(harness, log=print):
    """Advance the harness's test queue in the background (it has its own lock, and takes hours)."""
    import os
    logf = TW / "logs" / "techtest.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TECHTEST_WORKSPACE": str(WS)}
    with logf.open("a", encoding="utf-8") as out:
        subprocess.Popen([sys.executable, str(harness), "tick"], cwd=str(harness.parent), env=env, stdout=out,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    log(f"eval harness: test queue advancing in the background (log: {logf.relative_to(WS)})")


def recent_auto(days=14):
    """Changes applied without a prompt in the last `days`, newest first: [(id, note)]."""
    since = datetime.now(timezone.utc).timestamp() - days * 86400
    out = []
    for e in events():
        try:
            ts = datetime.fromisoformat(e.get("ts", "")).timestamp()
        except ValueError:
            continue
        if e.get("event") == "auto_applied" and ts >= since and status_of(e["id"]) in IN_EFFECT:
            out.append((e["id"], e.get("note", "")))
    return out[::-1]


# ---------------------------------------------------------------- checkpoints (kept by the eval harness)
def checkpoint_dir():
    import hashlib
    return Path.home() / "ai-welfare-checkpoints" / f"{WS.name}-{hashlib.sha1(str(WS).encode()).hexdigest()[:6]}"


# Never restored or deleted: your files, tech watch itself (checkpoints don't hold it), and machine state.
SKIP_RESTORE = re.compile(r"^(my/|workspace/|\.git/|techwatch/|\.checkpoint\.json$|"
                          r"\.claude/settings\.local\.json$|scripts/integrity\.json$|sandbox/image\.lock)|"
                          r"__pycache__|\.pyc$")


def workspace_files(root):
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and not SKIP_RESTORE.search(p.relative_to(root).as_posix())}


def cmd_checkpoints():
    d = checkpoint_dir()
    days = sorted(x.name for x in d.iterdir() if x.is_dir()) if d.exists() else []
    if not days:
        return print("No checkpoints yet. The eval harness saves one when it first runs, then one after each checkup.")
    print("Checkpoints (restore one with: review.py restore <date>):")
    for day in days:
        print(f"  {day}")


def cmd_restore(day):
    src = checkpoint_dir() / day
    if not src.is_dir():
        sys.exit(f"No checkpoint {day}. See: review.py checkpoints")
    old, now = workspace_files(src), workspace_files(WS)
    changed = sorted(k for k in old.keys() & now.keys() if old[k].read_bytes().replace(str(src).encode(),
                                                                                  str(WS).encode()) != now[k].read_bytes())
    added, removed = sorted(now.keys() - old.keys()), sorted(old.keys() - now.keys())
    try:   # what the checkpoint left out on purpose ([copy_exclude]): absent from it, but not "added since"
        left_out = json.loads((src / ".checkpoint.json").read_text(encoding="utf-8")).get("left_out", [])
    except (OSError, ValueError):
        left_out = []
    added = [f for f in added if not any(f == x or f.startswith(x.rstrip("/") + "/") for x in left_out)]
    if not (changed or added or removed):
        return print(f"The workspace already matches the {day} checkpoint.")
    print(f"{BOLD}Going back to {day}{OFF} (your my/ and workspace/ files aren't touched):")
    for label, files in (("changed back", changed), ("removed (added since)", added), ("restored", removed)):
        if files:
            print(f"  {label}: " + ", ".join(files[:15]) + (f" and {len(files) - 15} more" if len(files) > 15 else ""))
    if ask("Restore it? A copy of the current files is kept first.") != "y":
        return print("Nothing changed.")
    backup = TW / "state" / "restore-backups" / datetime.now().strftime("%Y-%m-%d-%H%M%S")
    for rel in changed + added:
        (backup / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WS / rel, backup / rel)
    for rel in changed + removed:
        (WS / rel).parent.mkdir(parents=True, exist_ok=True)
        data = old[rel].read_bytes().replace(str(src).encode(), str(WS).encode())
        (WS / rel).write_bytes(data)
    for rel in added:
        (WS / rel).unlink()
    evs = events()
    for d in proposals():
        when = max((e.get("ts", "") for e in evs if e.get("id") == d.name and e.get("event") in APPLIED), default="")
        if status_of(d.name, evs) in IN_EFFECT and when[:10] >= day:
            record(d.name, "rolled_back", f"restored checkpoint {day}")
    record(f"checkpoint-{day}", "restored", f"{len(changed)} changed, {len(added)} removed, {len(removed)} restored",
           backup=str(backup))
    if INTEGRITY.exists():
        subprocess.run([sys.executable, str(WS / "scripts" / "validate_proposal.py"), "--init"],
                       stdout=subprocess.DEVNULL, check=False)
    print(f"Restored {day}. The files it replaced are in {backup.relative_to(WS)}. Start a new Claude session to "
          "load it.")


def cmd_rollback(pid, yes=False):
    """Undo pid's applied files, file by file (yes: without asking). Recorded as rolled back only if every file was
    undone; returns True then."""
    f = STAGED / pid / "applied.json"
    if not f.exists():
        sys.exit(f"Nothing recorded as applied for {pid}.")
    if not yes and not tty():
        sys.exit(f"This asks before each file, so run it in a terminal, or add --yes: review.py rollback {pid} --yes")
    entries = json.loads(f.read_text(encoding="utf-8"))
    if not safe_to_undo(pid):
        others = [d.name for d in proposals() if d.name != pid and status_of(d.name) in IN_EFFECT
                  and any(a["target"] in {x["target"] for x in entries} for a in applied_entries(d.name))]
        print(f"{YEL}{BOLD}Some of these files changed after {pid} was applied{OFF}{YEL} (your edits"
              + (f", or {', '.join(others)}, which changed the same files" if others else "")
              + "). Undoing restores the version from before the change, so those later edits would go too. "
              "Copies of the current files are kept in the change's backup folder.{OFF}")
    ask_ = (lambda q: "y") if yes else ask
    rels, total = [], 0
    for a in reversed(entries):
        target, bk = resolve(a["target"]), Path(a["backup"])
        if target is None:
            continue
        total += 1
        if target.exists():   # keep what's there now, in case it held later edits
            keep = bk.with_name(bk.name + f".before-rollback-{datetime.now():%Y%m%d%H%M%S}")
            shutil.copy2(target, keep)
        absent = bk.with_suffix(bk.suffix + ".absent")
        if absent.exists():
            if not target.exists():
                rels.append(a["target"])
            elif ask_(f"Delete {a['target']} (it didn't exist before)?") == "y":
                target.unlink()
                rels.append(a["target"])
                d = target.parent
                while d != WS and WS in d.parents and not any(d.iterdir()):   # folders apply created
                    d.rmdir()
                    d = d.parent
        elif bk.exists() and ask_(f"Restore {a['target']} from backup?") == "y":
            shutil.copy2(bk, target)
            rels.append(a["target"])
    if INTEGRITY.exists() and rels and not drifted_elsewhere(rels):
        subprocess.run([sys.executable, str(WS / "scripts" / "validate_proposal.py"), "--init"],
                       stdout=subprocess.DEVNULL, check=False)
    if len(rels) < total:
        print(f"{pid}: {len(rels)} of {total} file(s) undone, so it still counts as applied. Run it again to undo "
              "the rest.")
        return False
    record(pid, "rolled_back")
    print(f"{pid}: rolled back.")
    return True


def applied_entries(pid):
    try:
        return json.loads((STAGED / pid / "applied.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def main(a):
    if not a or a[0] == "status":
        cmd_status()
        return walk_through() if not a else None
    c, rest = a[0], a[1:]
    if c == "list":
        return cmd_list("--all" in rest)
    if c == "checkpoints":
        return cmd_checkpoints()
    if c == "restore" and rest:
        return cmd_restore(rest[0])
    if c in ("show", "apply", "rollback", "approve", "reject", "defer", "shelve", "trial", "evaluated", "test") \
            and not rest:
        sys.exit(f"usage: review.py {c} <id>")
    if c == "show":
        return cmd_show(rest[0])
    if c in ("approve", "reject", "defer", "shelve"):
        return cmd_decide(rest[0], {"approve": "approved", "reject": "rejected", "defer": "deferred",
                                    "shelve": "shelved"}[c], rest[1:])
    if c == "trial":
        return cmd_trial(rest[0], yes="--yes" in rest)
    if c == "apply":
        return cmd_apply(rest[0], "--each" in rest, yes="--yes" in rest)
    if c == "test":
        return cmd_test(rest[0], "--no-apply" not in rest,
                        " ".join(x for x in rest[1:] if x not in ("--no-apply", "--deeper")),
                        depth="deeper" if "--deeper" in rest else "standard")
    if c == "rollback":
        return cmd_rollback(rest[0], yes="--yes" in rest)
    if c == "evaluated" and len(rest) >= 2 and rest[1] in ("keep", "remove"):
        pid = rest[0]
        require(pid)
        st = status_of(pid)
        if st not in IN_EFFECT:
            sys.exit(f"{pid} is {st}, not applied, so there's nothing to keep or remove.")
        if rest[1] == "remove" and (STAGED / pid / "applied.json").exists():
            print(f"Removing {pid} means undoing its files first:")
            if not cmd_rollback(pid, yes="--yes" in rest):
                return print(f"{pid}: not recorded as removed, since it isn't fully undone.")
        record(pid, "kept" if rest[1] == "keep" else "removed", " ".join(x for x in rest[2:] if x != "--yes"))
        return print(f"{pid}: {rest[1]}")
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
