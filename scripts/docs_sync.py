#!/usr/bin/env python3
"""Keep the docs from drifting away from the code they describe.

    python3 scripts/docs_sync.py              show generated sections that are out of date (exit 1 if any)
    python3 scripts/docs_sync.py --fix        rewrite them
    python3 scripts/docs_sync.py --hook       what the pre-commit hook runs (see 2 below); never blocks a commit
    python3 scripts/docs_sync.py unlisted     scripts and hooks no doc mentions (free)
    python3 scripts/docs_sync.py run [--now] [--dry-run]
                                              the weekly sync (3 below); --now even if not due, --dry-run builds
                                              the request and prints its size without calling a model
    python3 scripts/docs_sync.py show         changes waiting for your OK
    python3 scripts/docs_sync.py apply <id>   apply one of them (sessions run this after you say yes)
    python3 scripts/docs_sync.py reject <id>  drop one

1. Generated sections, free. Facts the code holds are written into the docs from the code: between
   `<!-- docs_sync:NAME -->` and `<!-- /docs_sync -->` lines, and on lines that start with a fixed prefix (for spots
   inside a code block). GENERATORS and LINE_RULES list them; tests/test_docs_sync.py fails when one is stale.

2. Commit reminders, free. scripts/leak_check.py's pre-commit run calls --hook: it updates the generated sections in
   your working copy and names docs that mention the files being committed.

3. The weekly sync. Tech watch's hourly run calls run(); when `enabled` (off by default, see settings()), it acts
   once every `every_days`. It costs nothing unless one of these free checks finds something:
     - files committed since the last sync that a doc mentions, where the doc didn't change too;
     - scripts and hooks that no doc mentions and that aren't listed in scripts/docs_internal.txt (so new files are
       caught, not only changed ones).
   Then one headless Claude call (Sonnet by default, no tools, a hard budget cap counted in tech watch's monthly
   cap) gets only those diffs, the doc lines that mention them, and for new files the folder map and CLAUDE.md, and
   suggests exact edits. This script checks every edit before anything changes:
     - CLAUDE.md: always waits for your OK (it's loaded into every session and steers Claude);
     - agent and skill files, README.md and the other docs: applied if each edit changes at most
       max_words_per_change words (and, in agent and skill files, stays out of the frontmatter); bigger ones wait
       for your OK. Taking "docs", "agents" or "skills" out of `automatic` makes that kind always wait;
     - GUARDRAILS.md, the ethics files, CHANGELOG.md, tech watch's rules and prompts, my/: never.
   "This file needs no docs" adds it to scripts/docs_internal.txt. Applied edits stay in your working copy for you
   to commit. A phone notification (if set up) says what happened.

   Changes waiting for you are kept in techwatch/state/docs-pending.json, which sessions can't write. On your next
   message in a workspace session, .claude/hooks/docs_pending.py tells Claude to show them and ask; it runs
   `apply <id>` only after your yes, and Claude Code asks you once more before that command runs. Eval copies of
   the workspace leave techwatch/ out, so eval runs never see any of this.
"""
import difflib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

BEGIN = re.compile(r"^<!-- docs_sync:([a-z0-9-]+) -->$")
END = "<!-- /docs_sync -->"
SYNCED_DOCS = ["README.md"]             # files with generated sections
DEFAULTS = {"enabled": False, "ask_in_sessions": True, "every_days": 7, "max_words_per_change": 20, "automatic": ["agents", "skills", "docs"], "model": "sonnet",
            "effort": "low", "max_budget_usd": 0.5}
NEVER = re.compile(r"^(GUARDRAILS\.md|ETHICS_[A-Z]+\.md|CHANGELOG\.md|LICENSE|my/|techwatch/(AUTONOMY\.md|prompts/|"
                   r"proposals/|staged/|state/|inbox/))")
MAX_DIFF_LINES, MAX_FILES, CONTEXT = 80, 15, 4


def use_root(root):
    """Point every path at root (the workspace; tests use a copy)."""
    global ROOT, HOOKS, TW, STATE, PENDING, SYNC_STATE, INTERNAL, INTEGRITY, LOG
    ROOT = Path(root)
    HOOKS = ROOT / ".claude" / "hooks"
    TW = ROOT / "techwatch"
    STATE = TW / "state"
    PENDING = STATE / "docs-pending.json"       # changes waiting for the person's OK
    SYNC_STATE = STATE / "docs-sync.json"       # {"last_sha", "last_run"}
    INTERNAL = ROOT / "scripts" / "docs_internal.txt"
    INTEGRITY = ROOT / "scripts" / "integrity.json"
    LOG = STATE / "docs-sync.log"


use_root(Path(__file__).resolve().parents[1])


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def log(msg):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}\n")


# ---------------------------------------------------------------- 1. generated sections
def not_ignored(paths):
    """paths minus the ones git ignores (your own agents or skills, e.g. via .git/info/exclude), so they never land in
    the public docs. A new file you haven't added yet still counts. Without git, nothing is left out."""
    paths = list(paths)
    if not paths:
        return paths
    try:
        r = subprocess.run(["git", "check-ignore", "--stdin"], cwd=ROOT, capture_output=True, text=True, timeout=30,
                           input="\n".join(p.relative_to(ROOT).as_posix() for p in paths))
    except (OSError, subprocess.SubprocessError):
        return paths
    if r.returncode not in (0, 1):   # 1: nothing ignored; anything else: not a git repository, or git failed
        return paths
    ignored = set(r.stdout.split("\n"))
    return [p for p in paths if p.relative_to(ROOT).as_posix() not in ignored]


def agent_names():
    return sorted(p.stem for p in not_ignored((ROOT / ".claude" / "agents").glob("*.md")))


def skill_names():
    return sorted(p.parent.name for p in not_ignored((ROOT / ".claude" / "skills").glob("*/SKILL.md")))


def subagent_limits():
    """The table of what each agent_guard.py role may write and run, from the guard itself."""
    g = _load("agent_guard_for_docs", HOOKS / "agent_guard.py")
    rows = ["| Agent | Can write in | Shell commands |", "|---|---|---|"]
    for role in sorted(g.WRITE_DIRS):
        where = ", ".join(f"`workspace/{d}/`" for d in g.WRITE_DIRS[role]) or "nothing"
        cmds = ["text search"]
        if role in g.SEMANTIC_SEARCH_ROLES:
            cmds.append("semantic search (hardened mode)")
        if role in g.CITATION_CHECK_ROLES:
            cmds.append("citation check")
        rows.append(f"| {role} | {where} | {', '.join(cmds)} |")
    return "\n".join(rows)


GENERATORS = {"subagent-limits": subagent_limits}
# prefix of the line -> what follows it (the folder map in README.md is a code block, where a marker would show)
LINE_RULES = {
    ".claude/agents/              ": lambda: ", ".join(agent_names()),
    ".claude/skills/              ": lambda: ", ".join("/" + s for s in skill_names()),
}


def render(text):
    """The text with every generated section brought up to date."""
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        m = BEGIN.match(line)
        if m and m.group(1) in GENERATORS:
            j = lines.index(END, i + 1)   # ValueError if the end marker is missing: a broken doc should be loud
            out += [line, GENERATORS[m.group(1)](), END]
            i = j + 1
            continue
        for prefix, gen in LINE_RULES.items():
            if line.startswith(prefix):
                line = prefix + gen()
                break
        out.append(line)
        i += 1
    return "\n".join(out)


def stale(fix=False):
    """The synced docs whose generated sections are out of date (rewritten first if fix)."""
    found = []
    for name in SYNCED_DOCS:
        p = ROOT / name
        old = p.read_text(encoding="utf-8")
        new = render(old)
        if new != old:
            found.append(name)
            if fix:
                p.write_text(new, encoding="utf-8")
    return found


# ---------------------------------------------------------------- 2. reminders and the inventory
def tracked_docs():
    """Tracked Markdown files, agent and skill files included. Not CHANGELOG.md (history)."""
    return [f for f in git("ls-files", "*.md").split() if f != "CHANGELOG.md"]


def body_start(lines):
    """Index of the first line after a `---` frontmatter block (0 if there's none). Agent and skill files name
    hook scripts in their frontmatter to wire them up; that isn't describing them."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def needles(f):
    if f.startswith(".claude/agents/") and f.endswith(".md"):
        return [f, f"`{Path(f).stem}`"]          # agents are named by their name, in backticks
    return [f, Path(f).name]


def reminders(changed, docs):
    """{doc: [(changed file, [line numbers])]} for docs that name a changed file and aren't changed themselves.
    changed and docs are paths relative to ROOT."""
    out = {}
    for doc in docs:
        if doc in changed:
            continue
        try:
            lines = (ROOT / doc).read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        start = body_start(lines)
        for f in changed:
            if f == doc:
                continue
            ns = needles(f)
            hits = [n for n, line in enumerate(lines, 1) if n > start and any(x in line for x in ns)]
            if hits:
                out.setdefault(doc, []).append((f, hits))
    return out


def internal_list():
    try:
        lines = INTERNAL.read_text(encoding="utf-8").split("\n")
    except OSError:
        return set()
    return {ln.split("#")[0].strip() for ln in lines if ln.split("#")[0].strip()}


def unlisted():
    """Tracked scripts and hooks that no tracked doc mentions and that docs_internal.txt doesn't list. (Agents and
    skills are always listed in README.md by the generated folder map, and Claude finds them by their
    descriptions.)"""
    files = [f for f in git("ls-files", "scripts", "techwatch/scripts", ".claude/hooks").split()
             if re.fullmatch(r"(scripts/[^/]+\.(py|sh)|techwatch/scripts/[^/]+\.py|\.claude/hooks/[^/]+\.py)", f)]
    texts = {}
    for d in tracked_docs():
        try:
            lines = (ROOT / d).read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        texts[d] = "\n".join(lines[body_start(lines):])
    internal = internal_list()
    return [f for f in files if f not in internal
            and not any(x in t for d, t in texts.items() if d != f for x in needles(f))]


def staged():
    """Files staged for commit, relative to ROOT (only those inside this workspace)."""
    return [f for f in git("diff", "--cached", "--name-only", "--relative").split("\n") if f]


def hook():
    fixed = stale(fix=True)
    if fixed:
        print(f"docs_sync: brought the generated parts of {', '.join(fixed)} up to date in your working copy "
              "(not in this commit). Commit them next time.", file=sys.stderr)
    found = reminders(staged(), tracked_docs())
    if found:
        print("docs_sync: these docs mention files in this commit; check they still match (just a reminder; the "
              "weekly sync also looks):", file=sys.stderr)
        for doc, items in sorted(found.items()):
            where = "; ".join(f"{f} (line{'s' if len(h) > 1 else ''} {', '.join(map(str, h[:6]))}"
                              f"{', ...' if len(h) > 6 else ''})" for f, h in items[:5])
            print(f"  {doc}: {where}", file=sys.stderr)


# ---------------------------------------------------------------- 3. checking and applying edits
def settings():
    """DEFAULTS, then docs_sync in techwatch/config.json (the public defaults: off), then my/docs_sync.json (your own
    choices, never committed). The sync is off by default because it edits tracked files: in a copy that takes
    updates from the repository, those edits would clash with the maintainer's own doc changes. Turn it on if you
    maintain your own fork."""
    cfg = read_json(ROOT / "techwatch" / "config.json", {}).get("docs_sync", {})
    mine = read_json(ROOT / "my" / "docs_sync.json", {})
    return {**DEFAULTS, **{k: v for k, v in {**cfg, **(mine if isinstance(mine, dict) else {})}.items()
                           if not k.startswith("_")}}


def target_class(rel):
    """claude | agents | skills | docs | never."""
    if rel == "CLAUDE.md":
        return "claude"
    if NEVER.match(rel) or not rel.endswith(".md") or ".." in Path(rel).parts or rel.startswith("/"):
        return "never"
    if re.fullmatch(r"\.claude/agents/[^/]+\.md", rel):
        return "agents"
    if re.fullmatch(r"\.claude/skills/[^/]+/[^/]+\.md", rel):
        return "skills"
    if rel.startswith(".claude/"):
        return "never"
    return "docs"


def changed_words(old, new):
    """Words added, removed or replaced (a replacement counts its longer side once)."""
    a, b = old.split(), new.split()
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in
               difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal")


def hard_constraints(text):
    m = re.search(r"^## Hard constraints\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def check_edit(rel, old, new):
    """None if the edit can be made to the file as it is now, else why not."""
    if not all(isinstance(x, str) for x in (rel, old, new)) or len(old.strip()) < 8 or old == new:
        return "malformed edit"
    if target_class(rel) == "never":
        return f"{rel} is never edited by the docs sync"
    if rel not in tracked_docs():
        return f"{rel} isn't a tracked doc"
    text = (ROOT / rel).read_text(encoding="utf-8")
    if text.count(old) != 1:
        return f"the text to replace appears {text.count(old)} times in {rel}, not once"
    lines = text.split("\n")
    fm_end = len("\n".join(lines[:body_start(lines)]))
    if body_start(lines) and text.index(old) < fm_end:
        return "it touches the frontmatter (tools, hooks, model), which the docs sync never changes"
    if rel == "CLAUDE.md" and hard_constraints(text) != hard_constraints(text.replace(old, new, 1)):
        return "it touches CLAUDE.md's hard constraints"
    return None


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(rel):
    try:
        return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
    except OSError:
        return ""


def write_edit(rel, old, new):
    """Make the edit, keeping scripts/integrity.json in step. Refuses if the file no longer matches the manifest
    (changed some other way: that's for a person to look at, not to paper over)."""
    why = check_edit(rel, old, new)
    if why:
        raise ValueError(why)
    p = ROOT / rel
    text = p.read_text(encoding="utf-8")
    manifest = read_json(INTEGRITY, None)
    if manifest and rel in manifest and manifest[rel] != sha(text):
        raise ValueError(f"{rel} doesn't match scripts/integrity.json (changed some other way); look at it, then "
                         "run python3 scripts/validate_proposal.py --init if the change is yours")
    new_text = text.replace(old, new, 1)
    p.write_text(new_text, encoding="utf-8")
    if manifest and rel in manifest:
        manifest[rel] = sha(new_text)
        INTEGRITY.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def mark_internal(rel, why):
    with open(INTERNAL, "a", encoding="utf-8") as f:
        f.write(f"{rel}   # {why[:100]}\n")


def handle(changes, unlisted_now):
    """Sort the model's suggestions: apply, keep for the person, or drop. Returns (applied, waiting, dropped) as
    lists of one-line descriptions."""
    cfg = settings()
    applied, waiting, dropped = [], [], []
    for c in changes if isinstance(changes, list) else []:
        if not isinstance(c, dict):
            continue
        why = str(c.get("why", ""))[:300]
        if c.get("internal"):
            rel = str(c["internal"])
            if rel in unlisted_now:
                mark_internal(rel, why)
                applied.append(f"{rel}: marked as needing no docs ({why})")
            else:
                dropped.append(f"{rel}: 'internal' for a file that isn't unlisted")
            continue
        rel, old, new = c.get("file"), c.get("old"), c.get("new")
        problem = check_edit(rel, old, new)
        if problem:
            dropped.append(f"{rel}: {problem}")
            continue
        cls, words = target_class(rel), changed_words(old, new)
        if cls == "claude":
            reason = "CLAUDE.md always asks"
        elif cls not in cfg["automatic"]:
            reason = f"{cls} changes ask (techwatch/config.json docs_sync.automatic)"
        elif words > int(cfg["max_words_per_change"]):
            reason = f"changes {words} words, over the {cfg['max_words_per_change']}-word limit"
        else:
            try:
                write_edit(rel, old, new)
                applied.append(f"{rel}: {why} ({words} words)")
                continue
            except ValueError as e:
                reason = str(e)
        waiting.append(add_pending({"file": rel, "old": old, "new": new, "why": why, "asks_because": reason}))
    return applied, waiting, dropped


def add_pending(item):
    d = read_json(PENDING, {"next": 1, "items": []})
    item = {"id": d["next"], "created": date.today().isoformat(), **item}
    d["next"] += 1
    d["items"].append(item)
    write_json(PENDING, d)
    return f"[{item['id']}] {item['file']}: {item['why']} ({item['asks_because']})"


def pending():
    return read_json(PENDING, {"next": 1, "items": []}).get("items", [])


def describe(items, limit=1200):
    """The pending changes as text for a session (or the terminal). The model wrote old/new, so they're quoted."""
    out = []
    for it in items:
        old, new = it["old"], it["new"]
        if len(old) + len(new) > limit:
            old, new = old[:limit // 2] + " [...]", new[:limit // 2] + " [...]"
        out.append(f"[{it['id']}] {it['file']}: {it['why']}\n  asks because: {it['asks_because']}\n"
                   f"  replace: «{old}»\n  with:    «{new}»")
    return "\n".join(out)


def decide(action, ident):
    d = read_json(PENDING, {"next": 1, "items": []})
    item = next((i for i in d["items"] if str(i["id"]) == str(ident)), None)
    if item is None:
        sys.exit(f"No waiting docs change {ident}. Waiting: {[i['id'] for i in d['items']] or 'none'}")
    if action == "apply":
        try:
            write_edit(item["file"], item["old"], item["new"])
        except (ValueError, OSError) as e:
            log(f"apply {ident} failed: {e}")
            sys.exit(f"Not applied: {e}. `reject {ident}` drops it; the next weekly sync looks again.")
    d["items"] = [i for i in d["items"] if i is not item]
    write_json(PENDING, d)
    log(f"{action} {ident}: {item['file']}: {item['why']}")
    print(f"{'Applied' if action == 'apply' else 'Dropped'} {ident} ({item['file']})."
          + (" It's in the working copy, not committed." if action == "apply" else ""))


# ---------------------------------------------------------------- 3. the weekly run
def excerpt(doc, hits):
    lines = (ROOT / doc).read_text(encoding="utf-8").split("\n")
    keep = sorted({i for h in hits[:6] for i in range(max(1, h - CONTEXT), min(len(lines), h + CONTEXT) + 1)})
    out, prev = [], None
    for i in keep:
        if prev is not None and i != prev + 1:
            out.append("[...]")
        out.append(lines[i - 1])
        prev = i
    return "\n".join(out)


def folder_map():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^## What's in the folder\n.*?(?=^## )", text, re.M | re.S)
    heads = [ln for ln in text.split("\n") if ln.startswith("## ")]
    return ("README.md section headings:\n" + "\n".join(heads) + "\n\n" + (m.group(0) if m else ""))


def build_request(base, head, unl):
    changed = [f for f in git("diff", "--name-only", "--relative", f"{base}..{head}").split("\n") if f] if base else []
    docs = [d for d in tracked_docs() if target_class(d) != "never"]
    rem = reminders(changed, docs)
    if not rem and not unl:
        return None, 0
    files = sorted({f for items in rem.values() for f, _ in items})[:MAX_FILES]
    parts = []
    if files:
        parts.append("## Changed files, and the doc lines that mention them\n")
        for f in files:
            diff = git("diff", f"{base}..{head}", "--", f).split("\n")
            more = f"\n[... {len(diff) - MAX_DIFF_LINES} more diff lines]" if len(diff) > MAX_DIFF_LINES else ""
            parts.append(f"### Diff of {f}\n```diff\n" + "\n".join(diff[:MAX_DIFF_LINES]) + more + "\n```")
            for doc, items in sorted(rem.items()):
                for g, hits in items:
                    if g == f:
                        parts.append(f"#### {doc}, where it mentions {f}\n```\n{excerpt(doc, hits)}\n```")
    if unl:
        parts.append("## Files no doc mentions (new, or never documented)\n")
        for f in unl[:MAX_FILES]:
            head_lines = (ROOT / f).read_text(encoding="utf-8", errors="replace").split("\n")[:40]
            parts.append(f"### {f} (first 40 lines)\n```\n" + "\n".join(head_lines) + "\n```")
        parts.append("## Where things are documented\n" + folder_map()
                     + "\n\n### CLAUDE.md (loaded into every session)\n```\n"
                     + (ROOT / "CLAUDE.md").read_text(encoding="utf-8") + "\n```")
    cfg = settings()
    intro = (ROOT / "scripts" / "docs_sync_prompt.md").read_text(encoding="utf-8").replace(
        "{MAX_WORDS}", str(cfg["max_words_per_change"]))
    return intro + "\n\n" + "\n\n".join(parts), len(files) + len(unl)


def month_spend():
    total, month = 0.0, date.today().isoformat()[:7]
    try:
        for line in (STATE / "runs.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("ts", "")[:7] == month:
                total += float(r.get("cost_usd") or 0)
    except (OSError, ValueError):
        pass
    return total


def claude_bin():
    for p in [shutil.which("claude"), str(Path.home() / ".local/bin/claude"),
              str(Path.home() / ".npm-global/bin/claude"), "/usr/local/bin/claude", "/opt/homebrew/bin/claude"]:
        if p and Path(p).exists():
            return p
    raise RuntimeError("can't find the `claude` command")


def ask_model(prompt, cfg):
    """One headless call with no tools, run from an empty folder (so no CLAUDE.md or settings load). Returns
    (text, cost)."""
    cmd = [claude_bin(), "-p", "--model", cfg["model"], "--effort", cfg["effort"],
           "--max-budget-usd", str(cfg["max_budget_usd"]), "--tools", "", "--setting-sources", "",
           "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--output-format", "json"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    with tempfile.TemporaryDirectory() as empty:
        r = subprocess.run(cmd, input=prompt, cwd=empty, env=env, capture_output=True, text=True, timeout=900)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {}
    cost = round(float(out.get("total_cost_usd") or 0), 3)
    with open(STATE / "runs.jsonl", "a", encoding="utf-8") as f:   # tech watch's ledger: counts in its monthly cap
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": "docs",
                            "exit": r.returncode, "cost_usd": cost,
                            "outcome": "ok" if r.returncode == 0 else "failed"}) + "\n")
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout)[-300:]}")
    return str(out.get("result", "")), cost


def parse_changes(text):
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def notify(title, body):
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import notify as n
        if n.ready():
            n.send(title, body)
    except Exception:   # noqa: BLE001 - a missed note mustn't fail the run
        pass


def run(now=False, dry_run=False):
    """The weekly sync. Returns a one-line summary. `enabled: false` turns off the scheduled run (`run --now` by hand
    still works)."""
    cfg = settings()
    if not now and not cfg["enabled"]:
        return "docs sync: not due"   # off: the same quiet answer as not due, so tech watch logs nothing
    s = read_json(SYNC_STATE, {})
    if not now and s.get("last_run"):
        if (date.today() - date.fromisoformat(s["last_run"])).days < int(cfg["every_days"]):
            return "docs sync: not due"
    head = git("rev-parse", "HEAD").strip()
    base = s.get("last_sha")
    if base and subprocess.run(["git", "merge-base", "--is-ancestor", base, head], cwd=ROOT).returncode != 0:
        base = None   # history was rewritten: start again from here
    # An unlisted file the model already looked at isn't sent again until it changes (it may have said "fine as
    # it is" without marking it internal, or you may have said no to its edit).
    reviewed = s.get("reviewed", {})
    unl = [f for f in unlisted() if reviewed.get(f) != file_sha(f)]
    done = {"last_sha": head, "last_run": date.today().isoformat(), "reviewed": reviewed}
    if not base and not unl:
        if not dry_run:
            write_json(SYNC_STATE, done)
        return "docs sync: started tracking from the current commit"
    prompt, n = build_request(base, head, unl)
    if prompt is None:
        if not dry_run:
            write_json(SYNC_STATE, done)
        return "docs sync: nothing to check this time (no model call)"
    if dry_run:
        return f"docs sync (dry run): {n} item(s); request of {len(prompt)} characters (~{len(prompt) // 4} tokens)"
    cap = float(read_json(ROOT / "techwatch" / "config.json", {}).get("monthly_cap_usd") or 0)
    if cap and month_spend() + float(cfg["max_budget_usd"]) > cap:
        return "docs sync: skipped, it would pass tech watch's monthly cap"
    write_json(SYNC_STATE, {**s, "last_run": date.today().isoformat()})   # tried this week, whatever happens next
    try:
        text, cost = ask_model(prompt, cfg)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        log(f"model call failed: {e}")
        return f"docs sync: the model call failed ({e}); it tries again next time"
    data = parse_changes(text)
    if data is None:
        log(f"no usable answer (${cost}): {text[:300]}")
        return f"docs sync: no usable answer (${cost}); see techwatch/state/docs-sync.log"
    applied, waiting, dropped = handle(data.get("changes", []), unl)
    reviewed.update({f: file_sha(f) for f in unl})
    write_json(SYNC_STATE, done)
    for kind, items in (("applied", applied), ("waiting", waiting), ("dropped", dropped)):
        for i in items:
            log(f"{kind}: {i}")
    if data.get("notes"):
        log(f"notes: {str(data['notes'])[:500]}")
    summary = (f"docs sync (${cost}): {len(applied)} applied in your working copy, {len(waiting)} waiting for your "
               f"OK, {len(dropped)} dropped")
    log(summary)
    if applied or waiting:
        notify("Docs sync", summary + ". " + "; ".join((applied + waiting)[:4])[:600]
               + (" Your next workspace session asks about the waiting ones." if waiting else ""))
    return summary


def main(args):
    if "--hook" in args:
        return hook()
    cmd = args[0] if args and not args[0].startswith("-") else ""
    if cmd == "unlisted":
        return print("\n".join(unlisted()) or "Every script and hook is mentioned or listed as internal.")
    if cmd == "run":
        return print(run(now="--now" in args, dry_run="--dry-run" in args))
    if cmd == "show":
        return print(describe(pending()) or "No docs changes are waiting.")
    if cmd in ("apply", "reject"):
        if len(args) != 2:
            sys.exit(f"usage: docs_sync.py {cmd} <id>")
        return decide(cmd, args[1])
    found = stale(fix="--fix" in args)
    if found and "--fix" not in args:
        print(f"Out of date: {', '.join(found)}. Run: python3 scripts/docs_sync.py --fix")
        sys.exit(1)
    print(f"Rewrote: {', '.join(found)}" if found else "Generated doc sections are up to date.")


if __name__ == "__main__":
    main(sys.argv[1:])
