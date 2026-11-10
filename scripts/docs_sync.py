#!/usr/bin/env python3
"""Keep the docs from drifting away from the code they describe. Mechanical: no model, no network.

    python3 scripts/docs_sync.py           show generated sections that are out of date (exit 1 if any)
    python3 scripts/docs_sync.py --fix     rewrite them
    python3 scripts/docs_sync.py --hook    what the pre-commit hook runs: fix generated sections in your working
                                           copy, and remind you about docs that mention the files you're committing.
                                           Never blocks a commit.

Two kinds of help:

1. Generated sections. Facts the code already holds are written into the docs from the code:
   - between `<!-- docs_sync:NAME -->` and `<!-- /docs_sync -->` lines (Markdown hides these lines), and
   - on lines that start with a fixed prefix, for spots where a marker would show, such as inside a code block.
   GENERATORS and LINE_RULES below list them. tests/test_docs_sync.py fails when one is out of date.

2. Reminders. When a commit changes a file that a tracked doc names (by path or file name), and doesn't change
   that doc, it prints the doc and line numbers to look at. Nothing to keep up to date: it reads the docs
   themselves. Most changes need no doc change, so it's only a reminder.

The hook part is called by scripts/leak_check.py's pre-commit run, so a hook installed with
`leak_check.py --install` runs it too. A failure here never stops the commit or the leak check.
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"
SYNCED_DOCS = ["README.md"]             # files with generated sections
BEGIN = re.compile(r"^<!-- docs_sync:([a-z0-9-]+) -->$")
END = "<!-- /docs_sync -->"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def agent_names():
    return sorted(p.stem for p in (ROOT / ".claude" / "agents").glob("*.md"))


def skill_names():
    return sorted(p.parent.name for p in (ROOT / ".claude" / "skills").glob("*/SKILL.md"))


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


def tracked_docs():
    """Tracked Markdown docs. Not CHANGELOG.md (history), and not .claude/ (agent and skill files name scripts to
    wire them up, not to describe them)."""
    r = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [f for f in r.stdout.split() if f != "CHANGELOG.md" and not f.startswith(".claude/")]


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
        for f in changed:
            if f.endswith(".md") and f.startswith(".claude/agents/"):
                needles = [f, f"`{Path(f).stem}`"]          # agents are named by their name, in backticks
            else:
                needles = [f, Path(f).name]
            hits = [n for n, line in enumerate(lines, 1) if any(x in line for x in needles)]
            if hits:
                out.setdefault(doc, []).append((f, hits))
    return out


def staged():
    """Files staged for commit, relative to ROOT (only those inside this workspace)."""
    r = subprocess.run(["git", "diff", "--cached", "--name-only", "--relative"], cwd=ROOT,
                       capture_output=True, text=True, check=True)
    return [f for f in r.stdout.split("\n") if f]


def hook():
    fixed = stale(fix=True)
    if fixed:
        print(f"docs_sync: brought the generated parts of {', '.join(fixed)} up to date in your working copy "
              "(not in this commit). Commit them next time.", file=sys.stderr)
    changed = staged()
    found = reminders(changed, tracked_docs())
    if found:
        print("docs_sync: these docs mention files in this commit; check they still match (just a reminder):",
              file=sys.stderr)
        for doc, items in sorted(found.items()):
            where = "; ".join(f"{f} (line{'s' if len(h) > 1 else ''} {', '.join(map(str, h[:6]))}"
                              f"{', ...' if len(h) > 6 else ''})" for f, h in items[:5])
            print(f"  {doc}: {where}", file=sys.stderr)


def main(args):
    if "--hook" in args:
        return hook()
    found = stale(fix="--fix" in args)
    if found and "--fix" not in args:
        print(f"Out of date: {', '.join(found)}. Run: python3 scripts/docs_sync.py --fix")
        sys.exit(1)
    print(f"Rewrote: {', '.join(found)}" if found else "Generated doc sections are up to date.")


if __name__ == "__main__":
    main(sys.argv[1:])
