#!/usr/bin/env python3
"""Changes tech watch made only for you, kept out of anything you publish.

Only matters if you publish your copy of the workspace (a fork, or the maintainer's repository). Each proposal says
who it's for (`audience` in its meta.json): "everyone", or "me" when it was proposed only because of your own
settings in my/ (your budget, platform, privacy, interests). A "me" change is applied to your working copy like any
other, and stays there: committing takes it out of each file, and keeps whatever else changed in that file.

    python3 techwatch/scripts/personal.py list            your personal changes in effect, and the files they touch
    python3 techwatch/scripts/personal.py add <path>...   git add, but without your personal changes (your working
                                                          copy keeps them; `git status` will go on showing them)
    python3 techwatch/scripts/personal.py check [--push]  what scripts/leak_check.py runs: refuses a commit (or push)
                                                          whose files still hold a personal change

How: tech watch keeps each applied change's file from before and after (techwatch/staged/<id>/backup/). Taking a
change out of today's file is a three-way merge (git merge-file), so later edits to the same file, yours or public
ones, stay. If a later edit touched the same lines as the personal change, the two can't be told apart: that file
has to be committed by hand (git add -p), and the check says so.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TW = Path(__file__).resolve().parent.parent
WS = TW.parent

def _review():
    """This workspace's review.py (by path: another workspace's copy may be loaded already under that name)."""
    mod = sys.modules.get("review")
    if mod is not None and getattr(mod, "TW", None) == TW:
        return mod
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"review_{abs(hash(str(TW)))}", TW / "scripts" / "review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


review = _review()

PERSONAL_STATUSES = ("applied", "trial", "kept")


class Tangled(Exception):
    """A personal change and a later edit touch the same lines: the public version can't be worked out."""


def git(*args, input=None, check=True):
    r = subprocess.run(["git", *args], cwd=WS, capture_output=True, input=input,
                       text=not isinstance(input, bytes))
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or '').strip()[:300]}")
    return r


def personal(evs=None):
    """Ids of the changes for you only ("audience": "me") whose files are in the workspace now, in the order they
    were applied."""
    evs = review.events() if evs is None else evs
    order = {}
    for i, e in enumerate(evs):
        if e.get("event") in ("applied", "auto_applied", "trial_started"):
            order[e["id"]] = i
    return [pid for pid in sorted(order, key=order.get)
            if review.meta(review.PROPOSALS / pid).get("audience") == "me"
            and review.status_of(pid, evs) in PERSONAL_STATUSES]


def entries(rel, pids=None):
    """[(id, text before or None if the change created the file, text it wrote)] for rel, oldest first."""
    out = []
    for pid in personal() if pids is None else pids:
        for a in review.applied_entries(pid):
            if a.get("target") != rel:
                continue
            bk, written = Path(a["backup"]), Path(a.get("written", ""))
            if not written.is_file():
                raise Tangled(f"{rel}: {pid}'s record of what it wrote is missing")
            absent = bk.with_suffix(bk.suffix + ".absent")
            before = None if absent.exists() else bk.read_text(encoding="utf-8") if bk.is_file() else False
            if before is False:
                raise Tangled(f"{rel}: {pid}'s backup of the file is missing")
            out.append((pid, before, written.read_text(encoding="utf-8")))
    return out


def merge(current, base, other):
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, text in (("current", current), ("base", base), ("other", other)):
            (Path(d) / name).write_text(text, encoding="utf-8")
            paths.append(str(Path(d) / name))
        r = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8")


def public_version(rel, text, pids=None):
    """rel's text (None: no such file) with every personal change taken out; None if the file shouldn't exist
    publicly. Raises Tangled if a change can't be separated from later edits."""
    for pid, before, written in reversed(entries(rel, pids)):
        if text is None:
            continue   # deleted since: nothing of the change is left to publish
        if before is None:   # the personal change created the file
            if text != written:
                raise Tangled(f"{rel}: {pid} created this file for you, and it has been edited since")
            text = None
            continue
        out = merge(text, written, before)
        if out is None:
            raise Tangled(f"{rel}: {pid} (for you only) and a later edit change the same lines")
        text = out
    return text


def rel_of(path):
    """Workspace-relative path for a path given relative to the repository root, the current folder, or absolute."""
    top = Path(git("rev-parse", "--show-toplevel").stdout.strip())
    for base in (Path.cwd(), top):
        p = (base / path).resolve()
        if p.is_relative_to(WS.resolve()) and (p.exists() or base == top):
            return p.relative_to(WS.resolve()).as_posix(), p.relative_to(top.resolve()).as_posix()
    raise SystemExit(f"{path}: not inside the workspace")


def touched():
    """{workspace-relative path} of every file a personal change wrote."""
    return {a["target"] for pid in personal() for a in review.applied_entries(pid)}


def add(paths):
    """Stage each path as its public version: your personal changes out, everything else in."""
    mine = touched()
    for path in paths:
        rel, repo_rel = rel_of(path)
        f = WS / rel
        if rel not in mine:
            git("add", "-A", "--", repo_rel)
            continue
        pub = public_version(rel, f.read_text(encoding="utf-8") if f.is_file() else None)
        if pub is None:
            git("rm", "-q", "--cached", "--ignore-unmatch", "--", repo_rel)
            print(f"{rel}: left out (it exists only for you)")
            continue
        blob = git("hash-object", "-w", "--stdin", input=pub.encode("utf-8")).stdout.decode().strip()
        mode = "100755" if f.is_file() and os.access(f, os.X_OK) else "100644"
        git("update-index", "--add", "--cacheinfo", f"{mode},{blob},{repo_rel}")
        print(f"{rel}: staged without your personal changes")


def problems(mode="staged"):
    """Files in the commit (or push) that still hold a personal change, as leak_check-style lines."""
    mine = touched()
    if not mine:
        return []
    top = Path(git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    prefix = WS.resolve().relative_to(top).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    if mode == "push":
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        base = f"origin/{branch}"
        if git("rev-parse", "--verify", "-q", base, check=False).returncode != 0:
            base = git("rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
        files, ref = git("diff", "--name-only", f"{base}..HEAD").stdout.splitlines(), "HEAD:"
    else:
        files, ref = git("diff", "--cached", "--name-only").stdout.splitlines(), ":"
    out = []
    for repo_rel in files:
        if not repo_rel.startswith(prefix) or repo_rel[len(prefix):] not in mine:
            continue
        rel = repo_rel[len(prefix):]
        r = git("show", f"{ref}{repo_rel}", check=False)
        text = r.stdout if r.returncode == 0 else None
        try:
            if public_version(rel, text) != text:
                out.append(f"{repo_rel}: holds a change tech watch made for you only. Stage it with "
                           f"`python3 {prefix}techwatch/scripts/personal.py add {repo_rel}` instead")
        except Tangled as e:
            out.append(f"{repo_rel}: {e}, so it can't be checked; stage it by hand (git add -p) and leave the "
                       "personal lines out, then commit with --no-verify")
    return out


def main(a):
    if not a or a[0] == "list":
        evs = review.events()
        pids = personal(evs)
        if not pids:
            return print("No changes for you only are in effect.")
        for pid in pids:
            print(f"{pid}  ({review.status_of(pid, evs)}): "
                  + ", ".join(x["target"] for x in review.applied_entries(pid)))
        return
    if a[0] == "add" and a[1:]:
        try:
            return add(a[1:])
        except Tangled as e:
            sys.exit(f"{e}. Stage that file by hand (git add -p) and leave the personal lines out.")
    if a[0] == "check":
        found = problems("push" if "--push" in a else "staged")
        for p in found:
            print(p)
        sys.exit(1 if found else 0)
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
