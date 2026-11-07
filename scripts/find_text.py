#!/usr/bin/env python3
"""Read-only text search and file listing inside this workspace, for subagents that have no Grep or Glob tool.

    python3 <workspace>/scripts/find_text.py [-i] [-w] [-l] [-C <n>] [--glob "<glob>"] "<regex>" [<path> ...]
    python3 <workspace>/scripts/find_text.py --list "<glob>"

Search mode prints `file:line: text` for every line matching the Python regex (whole files or folders; default:
workspace/). -i ignores case, -w matches whole words, -l prints only the file names, -C shows n lines of context
(0-9), and --glob keeps only files whose path matches the glob (e.g. "workspace/corpus/normalized/*.txt").
List mode prints the files whose workspace-relative path matches the glob ("**" crosses folders).

Why it exists: some Claude Code builds give subagents no Grep or Glob tool even when their definition lists them,
and the agent guards (agent_guard.py, analyst_guard.py) allow no general shell. This script is the one shell
command they allow for searching, in an exact form (FIND_TEXT_RE in .claude/hooks/preflight.py).

It never runs anything, never writes, and reads only inside the workspace: every path is resolved (symlinks
included) and must stay in the workspace; .git folders and .env files are skipped. Paths are relative to the
workspace, not the current folder. Output is capped, so a broad search can't flood the caller.
"""
import argparse
import fnmatch
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_LINES = 300          # output lines before stopping
MAX_LINE_CHARS = 400     # corpus files have very long lines
MAX_FILE_BYTES = 20_000_000
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}


def skipped(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name == ".env" or name.startswith(".env.")


def inside(p: Path) -> bool:
    try:
        p.relative_to(ROOT)
        return True
    except ValueError:
        return False


def resolve(raw: str) -> Path:
    p = Path(raw)
    real = Path(os.path.realpath(p if p.is_absolute() else ROOT / p))
    if not inside(real):
        sys.exit(f"find_text: {raw} is outside the workspace; only {ROOT} can be searched.")
    if not real.exists():
        sys.exit(f"find_text: {raw} doesn't exist.")
    return real


def glob_match(rel: str, pattern: str) -> bool:
    """fnmatch where * stays inside one folder and ** crosses folders."""
    rx, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            rx, i = rx + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            rx, i = rx + ".*", i + 2
        elif pattern[i] == "*":
            rx, i = rx + "[^/]*", i + 1
        elif pattern[i] == "?":
            rx, i = rx + "[^/]", i + 1
        else:
            rx, i = rx + re.escape(pattern[i]), i + 1
    return re.fullmatch(rx, rel) is not None or (
        "/" not in pattern and fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], pattern))


def files_under(paths):
    seen = set()
    for top in paths:
        if top.is_file():
            walk = [(str(top.parent), [], [top.name])]
        else:
            walk = os.walk(top, followlinks=False)
        for d, dirs, names in walk:
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for n in sorted(names):
                real = Path(os.path.realpath(Path(d) / n))
                if real in seen or not inside(real) or not real.is_file():
                    continue
                rel = real.relative_to(ROOT).as_posix()
                if any(part in SKIP_DIRS for part in rel.split("/")) or skipped(rel):
                    continue
                seen.add(real)
                yield real, rel


class Out:
    def __init__(self):
        self.n = 0

    def line(self, text):
        if self.n >= MAX_LINES:
            print(f"[find_text] stopped after {MAX_LINES} lines; narrow the pattern, path or --glob.")
            sys.exit(0)
        print(text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS] + " […]")
        self.n += 1


def main():
    ap = argparse.ArgumentParser(description="Read-only search inside the workspace.")
    ap.add_argument("-i", action="store_true", help="ignore case")
    ap.add_argument("-w", action="store_true", help="whole words only")
    ap.add_argument("-l", action="store_true", help="print only file names")
    ap.add_argument("-C", type=int, default=0, choices=range(10), metavar="N", help="context lines (0-9)")
    ap.add_argument("--glob", help="only files whose workspace-relative path matches this glob")
    ap.add_argument("--list", metavar="GLOB", help="list files matching GLOB instead of searching")
    ap.add_argument("pattern", nargs="?")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args()
    out = Out()

    if a.list is not None:
        if a.pattern or a.paths:
            sys.exit("find_text: --list takes only a glob.")
        hits = [rel for _, rel in files_under([ROOT]) if glob_match(rel, a.list)]
        for rel in hits:
            out.line(rel)
        print(f"[find_text] {len(hits)} file(s)")
        return

    if not a.pattern:
        sys.exit("find_text: give a pattern (or --list \"<glob>\").")
    try:
        rx = re.compile(rf"\b(?:{a.pattern})\b" if a.w else a.pattern, re.I if a.i else 0)
    except re.error as e:
        sys.exit(f"find_text: bad regex: {e}")
    paths = [resolve(p) for p in a.paths] or [ROOT / "workspace"]
    files = hits = 0
    for real, rel in files_under(paths):
        if a.glob and not glob_match(rel, a.glob):
            continue
        try:
            if real.stat().st_size > MAX_FILE_BYTES:
                continue
            data = real.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue   # binary
        lines = data.decode("utf-8", errors="replace").splitlines()
        matched = [i for i, s in enumerate(lines) if rx.search(s)]
        if not matched:
            continue
        files += 1
        hits += len(matched)
        if a.l:
            out.line(rel)
            continue
        shown, mset = -1, set(matched)
        for i in matched:
            lo, hi = max(0, i - a.C), min(len(lines), i + a.C + 1)
            if a.C and shown >= 0 and lo > shown + 1:
                out.line("--")
            for j in range(max(lo, shown + 1), hi):
                out.line(f"{rel}:{j + 1}{':' if j in mset else '-'} {lines[j]}")
            shown = hi - 1
    print(f"[find_text] {hits} matching line(s) in {files} file(s)")


if __name__ == "__main__":
    main()
