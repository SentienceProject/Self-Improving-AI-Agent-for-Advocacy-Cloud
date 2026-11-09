#!/usr/bin/env python3
"""Check (and fix) the YAML frontmatter of the workspace's agents, skills and commands.

    python3 scripts/check_frontmatter.py           list problems (exit 1 if any)
    python3 scripts/check_frontmatter.py --fix     also fix the ones it can: quote a value that YAML misreads

Claude Code reads the block between the first two `---` lines of each `.claude/agents/*.md`,
`.claude/skills/*/SKILL.md` and `.claude/commands/*.md` as YAML. An unquoted value containing ": " (a colon then a
space), or " #", is misread ("mapping values are not allowed in this context"), and the agent or skill fails to
load. --fix wraps such a value in double quotes, which YAML reads as the same text.

Uses PyYAML when it's installed; without it, a stricter-than-needed check of plain `key: value` lines (enough for
the single-line values these files use).
"""
import json
import re
import sys
from pathlib import Path

WS = Path(__file__).resolve().parents[1]
GLOBS = (".claude/agents/*.md", ".claude/skills/*/SKILL.md", ".claude/commands/*.md")
REQUIRED = {".claude/agents": ("name", "description"), ".claude/skills": ("name", "description")}
LINE = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$")

try:
    import yaml
except ImportError:
    yaml = None


def split(text):
    """(frontmatter lines, start index of the body) or (None, 0) if the file has no frontmatter block."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None, 0
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return lines[1:i], i
    return None, 0


def plain_value_problem(value):
    """Why an unquoted single-line value would be misread, or ''."""
    v = value.strip()
    if not v or v[0] in "\"'[{|>":
        return ""   # quoted, a flow collection or a block scalar: YAML's own parser judges those
    if ": " in v or v.endswith(":"):
        return "contains ': ' (a colon then a space)"
    if " #" in v:
        return "contains ' #' (YAML reads the rest as a comment)"
    if v[0] in "&*!%@`":
        return f"starts with {v[0]!r}"
    return ""


def problems(path):
    """[(line number or 0, message)] for one file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [(0, f"unreadable: {e}")]
    fm, _ = split(text)
    if fm is None:
        return [(0, "no frontmatter block (--- lines at the top)")]
    out = []
    for n, line in enumerate(fm, 2):
        m = LINE.match(line)
        if m and (why := plain_value_problem(m.group(2))):
            out.append((n, f"{m.group(1)}: {why}"))
    data = None
    if yaml is not None:
        try:
            data = yaml.safe_load("\n".join(fm))
        except yaml.YAMLError as e:
            if not out:
                out.append((0, f"YAML error: {' '.join(str(e).split())}"))
    else:
        data = {m.group(1): m.group(2) for m in map(LINE.match, fm) if m}
    if not out:
        if not isinstance(data, dict):
            out.append((0, "frontmatter isn't a set of key: value lines"))
        else:
            rel = path.relative_to(WS).as_posix()
            for prefix, keys in REQUIRED.items():
                if rel.startswith(prefix + "/"):
                    out += [(0, f"missing {k}") for k in keys if not data.get(k)]
    return out


def fix(path):
    """Quote every plain value plain_value_problem flags. Returns how many it quoted."""
    text = path.read_text(encoding="utf-8")
    fm, end = split(text)
    if fm is None:
        return 0
    lines, n = text.split("\n"), 0
    for i in range(1, end):
        m = LINE.match(lines[i])
        if m and plain_value_problem(m.group(2)):
            lines[i] = f"{m.group(1)}: {json.dumps(m.group(2).strip(), ensure_ascii=False)}"
            n += 1
    if n:
        path.write_text("\n".join(lines), encoding="utf-8")
    return n


def files(root=WS):
    return sorted(p for g in GLOBS for p in root.glob(g) if p.is_file())


def main(argv):
    do_fix, bad = "--fix" in argv, 0
    for p in files():
        if do_fix and (n := fix(p)):
            print(f"fixed {p.relative_to(WS)}: quoted {n} value(s)")
        for line, msg in problems(p):
            bad += 1
            print(f"{p.relative_to(WS)}{f':{line}' if line else ''}: {msg}")
    if bad and not do_fix:
        print("\nFix: put the value in double quotes, or run python3 scripts/check_frontmatter.py --fix")
    print(f"{len(files())} files checked, {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
