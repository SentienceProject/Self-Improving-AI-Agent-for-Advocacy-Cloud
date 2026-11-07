#!/usr/bin/env python3
"""Stop private material from going into a commit or a push of this repository.

For people who publish a copy of this repository (a fork, or the maintainer's own). Install once:

    python3 scripts/leak_check.py --install     adds git hooks: checked before every commit and every push

Or run it by hand:

    python3 scripts/leak_check.py               check what's staged for the next commit
    python3 scripts/leak_check.py --push        check the commits not yet on the remote (origin/<branch>)
    python3 scripts/leak_check.py --all         check every tracked file as it is now

What it refuses:
  - your files: anything staged under my/ (except my/README.md), workspace/ (except the .gitkeep files and other
    files already tracked), tech watch's findings and state, the eval harness's runs, results and settings, unlocked
    eval tasks, and *.private.md files;
  - secrets: API keys and tokens in the usual formats, private keys;
  - your home folder's path, and email addresses (other than no-reply and example addresses);
  - text from the eval harness's held-out tasks (any line of 60 characters or more copied from one);
  - anything in your own list, .git/info/leak-patterns: one string per line (or "re:<regex>"), e.g. your name, your
    email address, a notification topic. Lines in .git/info/leak-allow are allowed even if they match.
Both lists live inside .git/, so they're never committed themselves.

A refusal names the file and line. If something is a false alarm, add it to .git/info/leak-allow, or commit with
--no-verify after checking it yourself.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def git(*args, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           cwd=HERE).stdout.strip() or HERE.parent)
GITDIR = Path(git("rev-parse", "--absolute-git-dir").strip())

# Paths (relative to the repository root, with "/" separators) that must never be committed. "*/" at the start
# matches any folder that holds the workspace or the harness.
PRIVATE_PATHS = [
    r"(^|/)my/(?!README\.md$)",
    r"(^|/)workspace/(?!.*\.gitkeep$)(?!corpus/SOURCES\.md$)(?!proposals/README\.md$)(?!assets/seed-decks/)",
    r"(^|/)techwatch/(inbox|staged|state|logs|evaluations|evaluations-full)/(?!\.gitkeep$)",
    r"(^|/)techwatch/proposals/(?!_TEMPLATE\.md$|\.gitkeep$)",
    r"(^|/)ClaudeImproverHarness/(runs|results|review|techtest)/",
    r"(^|/)ClaudeImproverHarness/(config\.local\.toml|external-queries-log\.md)$",
    r"(^|/)evals/tasks[^/]*/(dev|heldout|heldout-drafts|retired)/",
    r"(^|/)archived-workspaces/",
    r"(^|/)\.private-backup\.git/",
    r"\.private\.md$",
    r"(^|/)\.claude/settings\.local\.json$",
    r"(^|/)improver_packets/",
]

SECRETS = [
    (r"sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}", "an API key (sk-...)"),
    (r"AIza[0-9A-Za-z_-]{30,}", "a Google API key"),
    (r"\bAQ\.[A-Za-z0-9_-]{30,}", "a Google API key"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}", "a GitHub token"),
    (r"xox[abposr]-[A-Za-z0-9-]{10,}", "a Slack token"),
    (r"AKIA[0-9A-Z]{16}", "an AWS key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(noreply|no-reply|@example\.(com|org|net)|@users\.noreply\.github\.com$|@anthropic\.com$)",
                      re.I)


def own_list(name):
    p = GITDIR / "info" / name
    if not p.is_file():
        return []
    return [ln.rstrip("\n") for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and
            not ln.startswith("#")]


def heldout_lines():
    """Lines of 60+ characters from unlocked held-out task files anywhere in the repository."""
    out = set()
    for f in ROOT.glob("**/evals/tasks*/heldout*/*.md"):
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if len(ln) >= 60:
                out.add(ln)
    return out


def checks():
    home = str(Path.home())
    user = Path.home().name
    patterns = [(re.compile(rx), what) for rx, what in SECRETS]
    if len(user) >= 3:
        patterns.append((re.compile(re.escape(home) + r"\b|(/home/|/Users/|\\Users\\)" + re.escape(user) + r"\b"),
                         "your home folder's path"))
    for p in own_list("leak-patterns"):
        rx = p[3:] if p.startswith("re:") else re.escape(p)
        patterns.append((re.compile(rx, re.I), f"an entry in .git/info/leak-patterns ({p[:20]}...)"))
    return patterns


def scan_text(path, lines, patterns, allow, held):
    """lines: [(line number or None, text)]. Returns problems as strings."""
    found = []
    for n, text in lines:
        if any(a in text for a in allow):
            continue
        where = f"{path}:{n}" if n else path
        for rx, what in patterns:
            if rx.search(text):
                found.append(f"{where}: {what}")
        for m in EMAIL.finditer(text):
            if not EMAIL_OK.search(m.group(0)) and m.group(0) not in allow:
                found.append(f"{where}: an email address ({m.group(0)})")
        if text.strip() in held:
            found.append(f"{where}: a line from a held-out eval task")
    return found


def added_lines(diff):
    """{path: [(line number, text)]} for the lines a unified diff adds."""
    out, path, n = {}, None, 0
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            path = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("@@"):
            m = re.search(r"\+(\d+)", ln)
            n = int(m.group(1)) if m else 0
        elif path and ln.startswith("+"):
            out.setdefault(path, []).append((n, ln[1:]))
            n += 1
        elif path and not ln.startswith("-"):
            n += 1
    return out


def run(mode):
    patterns, allow, held = checks(), own_list("leak-allow"), heldout_lines()
    private = [re.compile(p) for p in PRIVATE_PATHS]
    problems = []
    if mode == "all":
        files = git("ls-files").splitlines()
        new = files
        for f in files:
            p = ROOT / f
            if p.is_file() and p.stat().st_size < 2_000_000 and not f.endswith(".locked"):
                text = p.read_text(encoding="utf-8", errors="replace").splitlines()
                problems += scan_text(f, list(enumerate(text, 1)), patterns, allow, held)
    else:
        if mode == "push":
            branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
            base = f"origin/{branch}"
            if subprocess.run(["git", "rev-parse", "--verify", "-q", base], cwd=ROOT,
                              capture_output=True).returncode != 0:
                base = git("rev-list", "--max-parents=0", "HEAD").split()[0]
            rng = [f"{base}..HEAD"]
            new = git("diff", "--name-only", "--diff-filter=A", *rng).splitlines()
            diff = git("diff", "-U0", "--no-color", "--no-ext-diff", *rng)
        else:
            new = git("diff", "--cached", "--name-only", "--diff-filter=A").splitlines()
            diff = git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff")
        for path, lines in added_lines(diff).items():
            if path.endswith(".locked"):
                continue
            problems += scan_text(path, lines, patterns, allow, held)
    for f in new:
        if any(rx.search(f) for rx in private):
            problems.append(f"{f}: a private file (your own files, results or unlocked tasks)")
    return problems


def install():
    hooks = GITDIR / "hooks"
    hooks.mkdir(exist_ok=True)
    me = Path(__file__).resolve()
    for name, arg in (("pre-commit", ""), ("pre-push", " --push")):
        h = hooks / name
        if h.exists() and "leak_check.py" not in h.read_text(errors="replace"):
            print(f"{h} exists already and isn't this check's; add this line to it yourself:\n"
                  f'  python3 "{me}"{arg} || exit 1')
            continue
        h.write_text(f'#!/bin/sh\n# Added by scripts/leak_check.py --install\nexec python3 "{me}"{arg}\n')
        h.chmod(0o755)
        print(f"installed {h}")
    info = GITDIR / "info"
    info.mkdir(exist_ok=True)
    for name, what in (("leak-patterns", "strings that must never be committed, one per line (or re:<regex>): "
                                         "your name, email address, notification topic..."),
                       ("leak-allow", "lines that may be committed even though they match a check")):
        p = info / name
        if not p.exists():
            p.write_text(f"# {what}\n", encoding="utf-8")
            print(f"created {p}: {what}")


def main(args):
    if "--install" in args:
        return install()
    mode = "push" if "--push" in args else "all" if "--all" in args else "staged"
    problems = run(mode)
    if problems:
        print("leak check: this would publish private material. Nothing was committed or pushed.", file=sys.stderr)
        for p in sorted(set(problems))[:60]:
            print(f"  {p}", file=sys.stderr)
        if len(set(problems)) > 60:
            print(f"  ... and {len(set(problems)) - 60} more", file=sys.stderr)
        print("False alarm? Add the line to .git/info/leak-allow, or use --no-verify after checking.", file=sys.stderr)
        sys.exit(1)
    if os.isatty(1):
        print("leak check: nothing private found")


if __name__ == "__main__":
    main(sys.argv[1:])
