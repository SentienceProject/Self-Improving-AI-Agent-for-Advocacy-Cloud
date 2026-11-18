#!/usr/bin/env python3
"""Back up your own files (the ones git ignores, so they never reach the public repository) to a PRIVATE git
repository of your own, e.g. a private GitHub repository.

    python3 scripts/private_backup.py init <remote url>   once: e.g. https://github.com/you/advocacy-private.git
    python3 scripts/private_backup.py                      back up now (commit what changed, push it)
    python3 scripts/private_backup.py status               what would be backed up, and the last backup
    python3 scripts/private_backup.py restore <folder>     copy the latest backup into an empty folder

What's backed up: my/, workspace/ (except the search index and downloaded code repositories, which can be
rebuilt), tech watch's findings and state, this machine's .claude/settings.local.json, and, if the eval harness is
beside this workspace, its private settings, unlocked tasks, results, reviews, test reports, runs (without the
runs' raw transcripts) and archived workspaces (without installed packages). Files over 90 MB are skipped (GitHub
refuses files over 100 MB) and named in the output. Not backed up: .env (API keys don't belong in git; you can make
new ones), and anything that can be rebuilt.

Folders outside the repository (your own notes, a Claude memory folder): list them in my/backup-folders.txt, one
path per line (# comments and ~ are fine). Each is backed up whole to its own branch, extra/<path>, of the same
backup repository; restore puts each in its own folder.

It's a second git repository over the same folders: its history lives in
ai-welfare-eval-harness/.private-backup.git (agents in the workspace can't read the harness folder, and the
backup holds eval results), or in .private-backup.git in this workspace when there's no harness beside it. The
public repository's own history never sees these files, and this one never sees the rest.

Make sure the remote is private: this pushes your drafts, memory and settings there.
"""
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
REPO = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=WS, capture_output=True,
                           text=True).stdout.strip() or WS)
HARNESS = next((p for p in (REPO / "ai-welfare-eval-harness", WS.parent / "ai-welfare-eval-harness")
                if (p / "evals").is_dir()), None)
GITDIR = (HARNESS or WS) / ".private-backup.git"
MAX_BYTES = 90_000_000
FOLDERS_FILE = WS / "my" / "backup-folders.txt"
SKIP_GLOBS = ["**/__pycache__/**", "**/*.pyc", "**/.DS_Store", "**/*.locked", "**/.venv/**", "**/node_modules/**"]


def rel(p):
    return Path(os.path.relpath(p, REPO)).as_posix()


def pathspecs():
    """What to back up, as git pathspecs relative to REPO."""
    w = rel(WS)
    inc = [f"{w}/my", f"{w}/workspace", f"{w}/.claude/settings.local.json"] + \
          [f"{w}/techwatch/{d}" for d in ("inbox", "proposals", "staged", "state", "logs", "evaluations",
                                          "evaluations-full")]
    exc = [f"{w}/workspace/index", f"{w}/workspace/repos"]
    if HARNESS:
        h = rel(HARNESS)
        ci = f"{h}/evals/ClaudeImproverHarness"
        inc += [f"{ci}/{d}" for d in ("runs", "results", "review", "techtest", "config.local.toml",
                                      "external-queries-log.md")]
        inc += [rel(p) for p in sorted((HARNESS / "evals").glob("tasks*")) if p.is_dir()]
        inc += [f"{h}/evals/legacy-rubric-eval/data", f"{h}/evals/legacy-rubric-eval/improver_tasks",
                f"{h}/evals/.unlocked-from", f"{h}/archived-workspaces"]
        exc += [rel(p) for p in sorted((HARNESS / "archived-workspaces").glob("*/tools/node"))]  # a Node install
    inc = [p for p in inc if (REPO / p).exists()]
    exc_glob = ["**/raw/**"] + SKIP_GLOBS
    return inc + [f":(exclude){p}" for p in exc if (REPO / p).exists()] + [f":(exclude,glob){g}" for g in exc_glob]


def folders():
    """Folders outside the repository listed in my/backup-folders.txt: [(path, branch)]."""
    out = []
    lines = FOLDERS_FILE.read_text(encoding="utf-8").splitlines() if FOLDERS_FILE.is_file() else []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        p = Path(line).expanduser().resolve()
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.relpath(p, Path.home())).strip("-")
        out.append((p, f"extra/{name}"))
    return out


def git(*args, check=True, tree=None, index=None):
    """git on the backup repository; tree/index: another work tree and its own index (an extra folder)."""
    env = {**os.environ, "GIT_DIR": str(GITDIR), "GIT_WORK_TREE": str(tree or REPO)}
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    r = subprocess.run(["git", *args], cwd=tree or REPO, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args[:3])}... failed: {(r.stderr or r.stdout).strip()[:500]}")
    return r if not check else r.stdout


def init(url):
    if not GITDIR.exists():
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(GITDIR)], check=True)
        git("config", "core.bare", "false")
        git("config", "user.name", "private backup")
        git("config", "user.email", "backup@localhost")
        # This repository holds only the listed folders: don't let the public .gitignore files hide them.
        git("config", "core.excludesFile", os.devnull)
    if git("remote", check=False).stdout.strip():
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)
    print(f"Backup repository ready ({GITDIR}), remote {url}.\nRun python3 scripts/private_backup.py to back up.")


def too_big(root, specs):
    out = []
    for spec in specs:
        if spec.startswith(":"):
            continue
        p = root / spec
        for f in ([p] if p.is_file() else p.rglob("*") if p.is_dir() else []):
            try:
                if f.is_file() and f.stat().st_size > MAX_BYTES and ".venv" not in f.parts:
                    out.append(Path(os.path.relpath(f, root)).as_posix())
            except OSError:
                pass
    return out


def backup_folder(path, branch):
    """Commit the folder to its own branch (its own index, so the main backup's index is untouched)."""
    big = too_big(path, ["."])
    specs = ["."] + [f":(exclude,glob){g}" for g in SKIP_GLOBS] + [f":(exclude){b}" for b in big]
    index = GITDIR / f"index-{branch.replace('/', '-')}"
    git("add", "-A", "-f", "--", *specs, tree=path, index=index)
    tree = git("write-tree", tree=path, index=index).strip()
    parent = git("rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False).stdout.strip()
    if parent and git("rev-parse", f"{parent}^{{tree}}").strip() == tree:
        print(f"private backup: {path}: nothing changed")
    else:
        commit = git("commit-tree", tree, *(["-p", parent] if parent else []),
                     "-m", f"Backup {datetime.now():%Y-%m-%d %H:%M}").strip()
        git("update-ref", f"refs/heads/{branch}", commit)
        print(f"private backup: {path}: committed")
    for b in big:
        print(f"  skipped (over 90 MB): {path / b}")


def backup():
    if not GITDIR.exists():
        sys.exit("Not set up yet: python3 scripts/private_backup.py init <url of a PRIVATE repository>")
    big = too_big(REPO, pathspecs())
    specs = pathspecs() + [f":(exclude){b}" for b in big]
    git("add", "-A", "-f", "--", *specs)
    has_commit = git("rev-parse", "-q", "--verify", "HEAD", check=False).returncode == 0
    if has_commit and git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("private backup: nothing changed since the last backup")
    else:
        git("commit", "-q", "-m", f"Backup {datetime.now():%Y-%m-%d %H:%M}")
        print("private backup: committed")
    for b in big:
        print(f"  skipped (over 90 MB): {b}")
    refs = ["HEAD:main"]
    for path, branch in folders():
        if path.is_dir():
            backup_folder(path, branch)
            refs.append(f"refs/heads/{branch}:refs/heads/{branch}")
        else:
            print(f"private backup: {path} (in {FOLDERS_FILE.name}) isn't a folder; skipped")
    r = git("push", "-q", "origin", *refs, check=False)
    if r.returncode != 0:
        sys.exit(f"private backup: push failed (the commits are kept and go with the next push): "
                 f"{(r.stderr or r.stdout).strip()[:300]}")
    print("private backup: pushed")


def status():
    print(f"Backup repository: {GITDIR}{'' if GITDIR.exists() else ' (not set up yet)'}")
    for s in pathspecs():
        print(f"  {s}")
    for path, branch in folders():
        print(f"  {path} -> branch {branch}{'' if path.is_dir() else ' (not found)'}")
    if GITDIR.exists():
        print("Last backup: " + (git("log", "-1", "--format=%cd", "--date=local", check=False).stdout.strip()
                                 or "none yet"))


def restore(dest):
    dest = Path(dest).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"{dest} isn't empty; give an empty or new folder")
    dest.mkdir(parents=True, exist_ok=True)
    url = git("remote", "get-url", "origin").strip()
    subprocess.run(["git", "clone", "-q", url, str(dest / "repository")], check=True)
    extras = subprocess.run(["git", "ls-remote", "--heads", url, "extra/*"], capture_output=True, text=True,
                            check=True).stdout.split()[1::2]
    for ref in extras:
        branch = ref.removeprefix("refs/heads/")
        subprocess.run(["git", "clone", "-q", "-b", branch, url, str(dest / branch.replace("/", "-"))], check=True)
    print(f"Restored into {dest}: repository/ (the files that go beside your workspace)"
          + "".join(f", {r.removeprefix('refs/heads/').replace('/', '-')}/" for r in extras)
          + ". Copy what you need back into place.")


def main(args):
    if args[:1] == ["init"] and len(args) == 2:
        return init(args[1])
    if args[:1] == ["status"]:
        return status()
    if args[:1] == ["restore"] and len(args) == 2:
        return restore(args[1])
    if args:
        sys.exit(__doc__)
    backup()


if __name__ == "__main__":
    main(sys.argv[1:])
