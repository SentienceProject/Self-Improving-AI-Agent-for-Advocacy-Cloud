#!/usr/bin/env python3
"""Using the workspace in a cloud session (claude.ai/code): keep your work by saving it to your private GitHub copy.

    python3 scripts/cloud.py save ["what changed"]   save my/ and workspace/: commit them and push to main
    python3 scripts/cloud.py status                  is this a cloud session, which repository, anything unsaved?
    python3 scripts/cloud.py update [--check]        bring in the maintainer's updates, then save
    python3 scripts/cloud.py start                   the session-start hook runs this; it re-applies the
                                                     workspace's ask-first rules and prints notes for the session

A cloud session runs on a fresh computer that is thrown away later: only what is pushed to your GitHub repository
survives. On a computer of your own, my/ and workspace/ stay off GitHub (.gitignore). In the cloud, `save` adds them
to your private copy instead (git add -f), commits, and pushes to main, so the next session starts with them.

Safety: `save` and `update` only run in a cloud session (CLAUDE_CODE_REMOTE=true), and `save` refuses to push to the
maintainer's public repositories or to any repository GitHub reports as public.

Updates come from the public cloud copy (UPSTREAM below; set AI_WELFARE_CLOUD_UPSTREAM to use another). Your own
commits only touch my/ and workspace/, which the maintainer never ships, so updates merge without conflicts.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = os.environ.get("AI_WELFARE_CLOUD_UPSTREAM",
                          "https://github.com/SentienceProject/Self-Improving-AI-Agent-for-Advocacy-Cloud.git")
PUBLIC = {"sentienceproject/self-improving-ai-agent-for-advocacy",
          "sentienceproject/self-improving-ai-agent-for-advocacy-cloud"}
SAVED = ("my", "workspace")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}
SKIP_NAMES = (".env",)
TOO_BIG = 50 * 2**20       # GitHub refuses files over 100 MB and warns above 50 MB
LOCAL = ROOT / ".claude" / "settings.local.json"
CHECK = ROOT / "my" / ".cloud-update-check.json"
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}


def in_cloud():
    return os.environ.get("CLAUDE_CODE_REMOTE") == "true"


def git(*args, timeout=60):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout, env=ENV,
                          stdin=subprocess.DEVNULL)


def origin_slug():
    """owner/repo of origin, lower-case, or "" if there's no GitHub origin."""
    url = git("remote", "get-url", "origin").stdout.strip()
    if "github.com" not in url:
        return ""
    tail = url.split("github.com", 1)[1].lstrip(":/")
    parts = [p for p in tail.split("/") if p]
    return "/".join(parts[-2:]).removesuffix(".git").lower() if len(parts) >= 2 else ""


def is_public(slug):
    """True or False when GitHub says (an anonymous request sees only public repositories), None when unknown."""
    if not slug:
        return None
    if slug in PUBLIC:
        return True
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{slug}",
                                                           headers={"User-Agent": "workspace-cloud-save"}),
                                    timeout=10) as r:
            return not json.load(r).get("private", False)
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except (OSError, ValueError):
        return None


def new_files():
    """Files under my/ and workspace/ that git doesn't track yet and that should be saved, plus the big ones left
    out. Skips caches, nested repositories (workspace/repos/...), key files and symlinks."""
    tracked = set(git("ls-files", "-z", "--", *SAVED).stdout.split("\0"))
    files, big = [], []
    for top in SAVED:
        for d, dirs, names in os.walk(ROOT / top):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not (Path(d) / x / ".git").exists()
                       and not (Path(d) / x).is_symlink()]
            for n in names:
                p = Path(d) / n
                if n.endswith(".pyc") or n in SKIP_NAMES or n.startswith(".env.") or p.is_symlink():
                    continue
                rel = p.relative_to(ROOT).as_posix()
                if rel in tracked:
                    continue
                (big if p.stat().st_size > TOO_BIG else files).append(rel)
    return files, big


def stage():
    """Stage everything to save; return (staged paths, big files left out)."""
    tops = [t for t in SAVED if (ROOT / t).exists() or git("ls-files", "--", t).stdout.strip()]
    if tops:   # one missing folder would make git refuse the whole command
        git("add", "-u", "--", *tops)
    files, big = new_files()
    if files:
        subprocess.run(["git", "-C", str(ROOT), "add", "-f", "--pathspec-from-file=-", "--pathspec-file-nul"],
                       input="\0".join(files), text=True, capture_output=True, env=ENV, timeout=120)
    staged = [p for p in git("diff", "--cached", "--name-only", "-z").stdout.split("\0") if p]
    grown = [p for p in staged if (ROOT / p).is_file() and (ROOT / p).stat().st_size > TOO_BIG]
    if grown:
        git("reset", "-q", "--", *grown)
        big += grown
    return [p for p in staged if p not in grown], big


def push_main():
    """Push HEAD to main; if main moved on (another session saved), rebase onto it and try again. Returns (ok,
    message)."""
    r = git("push", "origin", "HEAD:main", timeout=120)
    if r.returncode == 0:
        return True, "pushed to main"
    git("fetch", "origin", "main", timeout=120)
    rb = git("rebase", "--autostash", "origin/main", timeout=120)
    if rb.returncode:
        git("rebase", "--abort")
        branch = "saved/" + time.strftime("%Y-%m-%d-%H%M%S")
        b = git("push", "origin", f"HEAD:refs/heads/{branch}", timeout=120)
        if b.returncode == 0:
            return False, (f"main changed in a way that clashes with this session's changes (another session saved "
                           f"the same file?), so this was saved to the branch {branch} instead. Ask Claude to merge "
                           "it into main.")
        return False, "the push failed: " + (b.stderr or r.stderr).strip()[-400:]
    r = git("push", "origin", "HEAD:main", timeout=120)
    if r.returncode == 0:
        return True, "pushed to main (after bringing in another session's saved work)"
    return False, "the push failed: " + r.stderr.strip()[-400:]


def refuse_unless_cloud(what):
    if not in_cloud():
        sys.exit(f"cloud.py {what} only runs in a cloud session (claude.ai/code). On your own computer, my/ and "
                 "workspace/ stay off GitHub on purpose.")


def save(message):
    refuse_unless_cloud("save")
    slug = origin_slug()
    if not slug:
        sys.exit("Not saved: this copy has no GitHub repository to save to. Start the session from your private "
                 "copy on GitHub (README, 'Using it from claude.ai').")
    if is_public(slug):
        sys.exit(f"Not saved: {slug} is a public repository, and saving would publish your profile, drafts and "
                 "memory. Make your own private copy (README, 'Using it from claude.ai') and work there.")
    staged, big = stage()
    note = (f" Left out (over 50 MB, too big for GitHub): {', '.join(big)}." if big else "")
    if staged:
        c = git("commit", "-q", "-m", "Save: " + (message or "work from a cloud session").strip()[:200],
                "-m", f"{len(staged)} file(s) in my/ and workspace/, saved by scripts/cloud.py.")
        if c.returncode:
            sys.exit("Not saved: the commit failed: " + (c.stderr or c.stdout).strip()[-400:])
    ahead = git("rev-list", "--count", "origin/main..HEAD").stdout.strip()
    if not staged and ahead == "0":
        print("Nothing new to save." + note)
        return
    ok, how = push_main()
    if ok:   # keep the session's own branch in step too, so the session has nothing left to push
        branch = git("branch", "--show-current").stdout.strip()
        if branch and branch != "main":
            git("push", "-q", "-u", "origin", f"HEAD:refs/heads/{branch}", timeout=120)
    print((f"Saved {len(staged)} changed file(s) to {slug}: {how}." if ok else f"Not fully saved: {how}") + note)
    if not ok:
        sys.exit(2)


def status():
    print(f"Cloud session: {'yes' if in_cloud() else 'no'}")
    slug = origin_slug()
    pub = is_public(slug)
    print(f"Repository: {slug or '(none)'}" + {True: " (PUBLIC: don't save personal files here)",
                                                False: " (private)", None: ""}[pub])
    files, big = new_files()
    changed = [l for l in git("diff", "--name-only", "HEAD", "--", *SAVED).stdout.splitlines() if l]
    print(f"Unsaved: {len(files) + len(changed)} file(s) in my/ and workspace/"
          + (f"; {len(big)} too big to save" if big else ""))


def fetch_upstream(timeout=60):
    r = git("fetch", "--quiet", UPSTREAM, "main", timeout=timeout)
    if r.returncode:
        return None
    return git("rev-parse", "FETCH_HEAD").stdout.strip() or None


def behind(timeout=60):
    """How many of the maintainer's commits this copy doesn't have yet, or None if the check failed."""
    head = fetch_upstream(timeout)
    if not head:
        return None
    if git("merge-base", "--is-ancestor", head, "HEAD").returncode == 0:
        return 0
    n = git("rev-list", "--count", f"HEAD..{head}").stdout.strip()
    return int(n) if n.isdigit() else None


def reapply_rules():
    """settings.local.json (the ask-first rules) isn't kept between cloud sessions: put it back."""
    if LOCAL.exists() or not (ROOT / "my" / ".setup-state.json").exists():
        return    # already there, or first_run's full setup is about to write it
    subprocess.run([sys.executable, str(ROOT / "setup.py"), "--reapply"], capture_output=True, timeout=60,
                   stdin=subprocess.DEVNULL)
    if not (ROOT / "scripts" / "integrity.json").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_proposal.py"), "--init"],
                       capture_output=True, timeout=60, stdin=subprocess.DEVNULL)


def update(check_only):
    refuse_unless_cloud("update")
    n = behind()
    if n is None:
        sys.exit(f"Couldn't reach the maintainer's copy ({UPSTREAM}). Is the environment's network access set to "
                 "Full (README, 'Using it from claude.ai')? If your copy is a GitHub fork, the Sync fork button on "
                 "its GitHub page brings in the updates too; the next session starts with them.")
    if not n:
        print("Up to date.")
        return
    if check_only:
        print(f"{n} update(s) available. Get them: python3 scripts/cloud.py update")
        return
    log = git("log", "--format=  - %s", f"HEAD..FETCH_HEAD").stdout.rstrip()
    m = git("merge", "--no-edit", "--autostash", "-m", "Update from the maintainer's cloud copy", "FETCH_HEAD",
            timeout=120)
    if m.returncode:
        git("merge", "--abort")
        sys.exit("The update clashes with a change made in this copy outside my/ and workspace/, so nothing was "
                 "changed. Details:\n" + (m.stdout + m.stderr).strip()[-800:])
    LOCAL.unlink(missing_ok=True)   # rebuilt from the updated rules
    reapply_rules()
    print(f"Updated ({n} commit(s)):\n{log}")
    save("update from the maintainer")


def start():
    """For the session-start hook (cloud only): re-apply the rules, then return notes for the session."""
    if not in_cloud():
        return []
    lines = []
    try:
        reapply_rules()
    except (OSError, subprocess.SubprocessError):
        lines.append("The workspace's ask-first rules couldn't be re-applied this session (setup.py --reapply "
                     "failed): be careful before sending, posting or changing the workspace's own files.")
    slug = origin_slug()
    pub = is_public(slug)
    if not slug:
        lines.append("This cloud session has no GitHub repository, so nothing done here will be kept. Start sessions "
                     "from your private copy on GitHub (README, 'Using it from claude.ai').")
    elif pub:
        lines.append(f"This session runs on {slug}, a public repository: saving is off, so nothing personal is "
                     "published, and nothing done here will be kept. Make your own private copy (README, 'Using it "
                     "from claude.ai') and start sessions from that.")
    else:
        lines.append(f"Cloud session: work is kept by saving it to {slug} (python3 scripts/cloud.py save).")
    try:
        last = json.loads(CHECK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if time.time() - last.get("at", 0) > 20 * 3600:
        n = behind(timeout=10)
        last = {"at": time.time(), "behind": n or 0}
        try:
            CHECK.write_text(json.dumps(last), encoding="utf-8")
        except OSError:
            pass
    if last.get("behind"):
        lines.append(f"Updates to the workspace are available ({last['behind']} new commit(s)). Get them: python3 "
                     "scripts/cloud.py update   (your files in my/ and workspace/ aren't touched)")
    return lines


def main(a):
    cmd = a[0] if a else "status"
    if cmd == "save":
        save(" ".join(a[1:]))
    elif cmd == "status":
        status()
    elif cmd == "update":
        update("--check" in a)
    elif cmd == "start":
        print("\n".join(start()))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
