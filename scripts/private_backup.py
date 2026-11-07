#!/usr/bin/env python3
"""Back up your own files (the ones git ignores, so they never reach the public repository) to a PRIVATE git
repository of your own, e.g. a private GitHub repository.

    python3 scripts/private_backup.py init <remote url>   once: e.g. https://github.com/you/advocacy-private.git
    python3 scripts/private_backup.py                      back up now (commit what changed, push it)
    python3 scripts/private_backup.py status               what would be backed up, and the last backup
    python3 scripts/private_backup.py restore <folder>     copy the latest backup into an empty folder

What's backed up: my/, workspace/ (except the search index and downloaded code repositories, which can be
rebuilt), tech watch's findings and state, this machine's .claude/settings.local.json, and, if the eval harness is
beside this workspace, its private settings, unlocked tasks, results, reviews, test reports and runs (without the
runs' raw transcripts). Files over 90 MB are skipped (GitHub refuses files over 100 MB) and named in the output.

It's a second git repository over the same folders: its history lives in
ai-welfare-eval-harness/.private-backup.git (agents in the workspace can't read the harness folder, and the
backup holds eval results), or in .private-backup.git in this workspace when there's no harness beside it. The
public repository's own history never sees these files, and this one never sees the rest.

Make sure the remote is private: this pushes your drafts, memory and settings there.
"""
import os
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
        inc += [f"{h}/evals/legacy-rubric-eval/data"]
    inc = [p for p in inc if (REPO / p).exists()]
    exc_glob = ["**/raw/**", "**/__pycache__/**", "**/*.pyc", "**/.DS_Store", "**/*.locked"]
    return inc + [f":(exclude){p}" for p in exc if (REPO / p).exists()] + [f":(exclude,glob){g}" for g in exc_glob]


def git(*args, check=True, quiet=False):
    env = {**os.environ, "GIT_DIR": str(GITDIR), "GIT_WORK_TREE": str(REPO)}
    r = subprocess.run(["git", *args], cwd=REPO, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args[:3])}... failed: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout


def init(url):
    if not GITDIR.exists():
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(GITDIR)], check=True)
        git("config", "core.bare", "false")
        git("config", "user.name", "private backup")
        git("config", "user.email", "backup@localhost")
        # This repository holds only the listed folders: don't let the public .gitignore files hide them.
        git("config", "core.excludesFile", os.devnull)
    if git("remote", check=False).strip():
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)
    print(f"Backup repository ready ({GITDIR}), remote {url}.\nRun python3 scripts/private_backup.py to back up.")


def too_big():
    out = []
    for spec in pathspecs():
        if spec.startswith(":"):
            continue
        p = REPO / spec
        for f in ([p] if p.is_file() else p.rglob("*") if p.is_dir() else []):
            try:
                if f.is_file() and f.stat().st_size > MAX_BYTES:
                    out.append(rel(f))
            except OSError:
                pass
    return out


def backup():
    if not GITDIR.exists():
        sys.exit("Not set up yet: python3 scripts/private_backup.py init <url of a PRIVATE repository>")
    big = too_big()
    specs = pathspecs() + [f":(exclude){b}" for b in big]
    git("add", "-A", "-f", "--", *specs)
    has_commit = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=REPO, capture_output=True,
                                env={**os.environ, "GIT_DIR": str(GITDIR), "GIT_WORK_TREE": str(REPO)}).returncode == 0
    if has_commit and subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO,
                                     env={**os.environ, "GIT_DIR": str(GITDIR),
                                          "GIT_WORK_TREE": str(REPO)}).returncode == 0:
        print("private backup: nothing changed since the last backup")
    else:
        git("commit", "-q", "-m", f"Backup {datetime.now():%Y-%m-%d %H:%M}")
        print("private backup: committed")
    for b in big:
        print(f"  skipped (over 90 MB): {b}")
    r = subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "GIT_DIR": str(GITDIR), "GIT_WORK_TREE": str(REPO)})
    if r.returncode != 0:
        sys.exit(f"private backup: push failed (the commit is kept and goes with the next push): "
                 f"{(r.stderr or r.stdout).strip()[:300]}")
    print("private backup: pushed")


def status():
    print(f"Backup repository: {GITDIR}{'' if GITDIR.exists() else ' (not set up yet)'}")
    for s in pathspecs():
        print(f"  {s}")
    if GITDIR.exists():
        print("Last backup: " + (git("log", "-1", "--format=%cd", "--date=local", check=False).strip() or "none yet"))


def restore(dest):
    dest = Path(dest).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"{dest} isn't empty; give an empty or new folder")
    dest.mkdir(parents=True, exist_ok=True)
    url = git("remote", "get-url", "origin").strip()
    subprocess.run(["git", "clone", "-q", url, str(dest)], check=True)
    print(f"Restored into {dest}. Copy the folders you need back beside your workspace.")


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
