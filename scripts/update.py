#!/usr/bin/env python3
"""Get the maintainer's updates without losing your changes.

    python3 scripts/update.py           update now (asks nothing; tells you what it did)
    python3 scripts/update.py --check   just say whether updates are available

A plain `git pull` can stop with a merge conflict when tech watch (or you) changed a file the update also changes.
This avoids that:
  1. saves a copy of every file you or tech watch changed, in techwatch/state/update-backups/<time>/;
  2. takes tech watch's applied changes out (as `review.py rollback` would);
  3. sets your own edits aside (git stash), and fast-forwards to the maintainer's version;
  4. puts your edits back; where one clashes with the update, the update's version wins, and yours is kept in
     the backup folder (the summary says which files);
  5. puts tech watch's changes back where the update didn't touch their files. Where it did, the change is
     re-prepared against the new version by the next tech-watch run, and you (or your my/techwatch.md settings)
     decide again;
  6. updates the integrity manifest for the files the update changed, since you chose to take it. If a protected
     file had already changed some other way before the update, that one isn't re-blessed: you're told instead.
If the maintainer published a change that's still on trial (checked again later, then kept or rolled back), the
summary says so, and a later update tells you the result.
If anything goes wrong midway, tech watch's changes and your edits are put back as they were, and the backup folder
has a copy of everything.

In a cloud session (claude.ai/code) this hands over to scripts/cloud.py update, which merges the maintainer's cloud
copy instead.

Your my/ and workspace/ folders aren't tracked by git, so updates never touch them. If you've made git commits of
your own on this copy, this stops and explains instead of guessing.

The workspace may be the repository itself or a folder inside one (the published repository holds the workspace and
the eval harness side by side): every step here looks only at this workspace's folder, though the update itself
brings the whole repository up to date.
"""
import hashlib
import json
import re
import shutil
import os
import subprocess
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
try:
    from style import S  # noqa: E402  (readable colours in a terminal; plain text otherwise)
except ImportError:      # an older copy without scripts/style.py
    class S:
        ok = warn = ask = b = staticmethod(str)
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TW = ROOT / "techwatch"
sys.path.insert(0, str(TW / "scripts"))
import review   # noqa: E402


def git(*args, check=False, timeout=120):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r


# A line in the maintainer's commit message: "Trial: <what> (checked by <date>) [<id>]" when a change on trial is
# published, "Trial result: <what>: kept|rolled back [<id>]" when its trial ends.
TRIAL = re.compile(r"^Trial( result)?: (.+?)\s*(?:\[([^\]\s]+)\])?\s*$")
OUTSIDE = "outside-workspace"   # in the backup folder: your edited files from elsewhere in the repository


def in_git():
    """True if this workspace is a git clone, or a folder inside one."""
    return bool(shutil.which("git")) and git("rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def count(rng):
    r = git("rev-list", "--count", rng)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def applied_changes():
    """[(pid, [entry])] for tech-watch changes currently applied, newest first."""
    evs, out = review.events(), []
    for d in review.proposals():
        f = review.STAGED / d.name / "applied.json"
        if review.status_of(d.name, evs) in review.IN_EFFECT and f.exists():
            out.append((d.name, json.loads(f.read_text(encoding="utf-8"))))
    ts = {e["id"]: e.get("ts", "") for e in evs if e.get("event") in review.APPLIED}
    return sorted(out, key=lambda x: ts.get(x[0], ""), reverse=True)


def take_out(pid, entries, notes):
    """Restore the files apply changed to what they were before. Returns {target: content to put back}: what's in
    the file now (tech watch's change, plus any edit you made after it), or None if that can't be known."""
    wrote = {}
    for a in reversed(entries):
        target, bk = review.resolve(a["target"]), Path(a["backup"])
        if target is None:
            continue
        w = Path(a.get("written", ""))
        applied = w.read_text(encoding="utf-8") if a.get("written") and w.exists() else None
        wrote[a["target"]] = target.read_text(encoding="utf-8") if target.exists() else applied
        if applied is not None and wrote[a["target"]] != applied:
            notes.append(f"{a['target']}: you edited it after tech watch applied {pid}; your version is kept")
        absent = bk.with_suffix(bk.suffix + ".absent")
        if absent.exists():
            target.unlink(missing_ok=True)
        elif bk.exists():
            shutil.copy2(bk, target)
    return wrote


def put_back(pid, entries, wrote, notes):
    """Re-apply pid where the update left its files as they were before it; otherwise have it re-staged."""
    same = all(
        (Path(a["backup"]).with_suffix(Path(a["backup"]).suffix + ".absent").exists()
         and not review.resolve(a["target"]).exists())
        or sha(Path(a["backup"])) == sha(review.resolve(a["target"]))
        for a in entries if review.resolve(a["target"]))
    if same and all(wrote.get(a["target"]) is not None for a in entries if review.resolve(a["target"])):
        for a in entries:
            t = review.resolve(a["target"])
            if t is None:
                continue
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(wrote[a["target"]], encoding="utf-8")
        review.record(pid, "reapplied", "put back after an update")
        return "kept"
    (review.STAGED / pid / "applied.json").rename(review.STAGED / pid / "applied-before-update.json")
    review.record(pid, "approved", "the workspace was updated and this change's files changed (or its record was "
                                   "incomplete); re-staging it against the new version")
    notes.append(f"{pid}: the update changed the same files, so tech watch will prepare it again for the new version")
    return "restage"


def restore(tw, wrote):
    """After a failure: put every taken-out file back exactly as it was, without touching the ledger."""
    for pid, entries in tw:
        for a in entries:
            t, text = review.resolve(a["target"]), wrote.get(pid, {}).get(a["target"])
            if t is not None and text is not None:
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_text(text, encoding="utf-8")


def trials(old_head):
    """What the update's commit messages say about changes on trial, as lines to print."""
    started, results = {}, {}
    for line in git("log", "--reverse", "--format=%B", f"{old_head}..HEAD").stdout.splitlines():
        m = TRIAL.match(line.strip())
        if m:
            text = m.group(2).rstrip(".")
            (results if m.group(1) else started)[m.group(3) or text] = text
    return ([f"Update contains a change being trialed: {t}. You'll get its result in a later update."
             for k, t in started.items() if k not in results]   # one whose result came in the same update: just that
            + [f"Result of a trial: {t}." for t in results.values()])


def drifted():
    """Protected files whose hash doesn't match scripts/integrity.json (changed some other way), or None if there's
    no manifest."""
    m = ROOT / "scripts" / "integrity.json"
    if not m.exists():
        return None
    return [rel for rel, h in json.loads(m.read_text(encoding="utf-8")).items() if sha(ROOT / rel) != h]


def update_manifest(before_drift, changed_by_update):
    """Bless what the update brought; leave alone a protected file that had drifted before it."""
    m = ROOT / "scripts" / "integrity.json"
    if before_drift is None:
        return
    if not before_drift:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_proposal.py"), "--init"],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"Couldn't rebuild the integrity manifest: {(r.stderr or r.stdout).strip()[:300]}")
        return
    data = json.loads(m.read_text(encoding="utf-8"))
    for rel in changed_by_update:
        if rel in data and rel not in before_drift:
            if (ROOT / rel).exists():
                data[rel] = sha(ROOT / rel)
            else:
                data.pop(rel)
    m.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(S.warn("These protected files had changed before the update") + ", by some route other than tech watch or this "
          f"script, so the integrity manifest still flags them: {', '.join(before_drift)}. Check them (git diff); if "
          "the changes are yours, run: python3 scripts/validate_proposal.py --init")


def main(a):
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true":   # a cloud copy: its own saves are commits, so merge instead
        sys.exit(subprocess.run([sys.executable, str(ROOT / "scripts" / "cloud.py"), "update",
                                 *(["--check"] if "--check" in a else [])]).returncode)
    if not in_git():
        sys.exit("This copy isn't a git clone, so it can't update itself. Download the new version and copy your my/ "
                 "and workspace/ folders into it.")
    if git("rev-parse", "--abbrev-ref", "@{u}").returncode:
        sys.exit("This clone has no upstream branch to update from.")
    try:
        git("fetch", "--quiet", check=True, timeout=60)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        sys.exit(f"Couldn't reach the repository: {e}")
    behind, ahead = count("HEAD..@{u}"), count("@{u}..HEAD")
    if "--check" in a:
        return print(f"{behind} update(s) available." if behind else "Up to date.")
    if not behind:
        return print("Up to date.")
    if ahead:
        sys.exit(f"This copy has {ahead} commit(s) of its own, so updating needs a merge. If you know git: "
                 "`git pull`, then resolve any conflicts. Otherwise: save your changes somewhere, run "
                 "`git reset --hard @{u}` (this discards those commits), and copy your changes back.")

    old_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "?"
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = TW / "state" / "update-backups" / stamp
    changed = [f for f in git("diff", "--name-only", "--relative", "HEAD").stdout.splitlines() if f]
    for rel in changed:
        if (ROOT / rel).exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, backup / rel)
    # Your edits elsewhere in the repository (the eval harness's config.toml, say) are set aside and put back too.
    top, prefix = Path(git("rev-parse", "--show-toplevel").stdout.strip()), git("rev-parse", "--show-prefix").stdout.strip()
    for f in git("diff", "--name-only", "HEAD", "--", ":/").stdout.splitlines():
        if f and not (prefix and f.startswith(prefix)) and (top / f).exists():
            (backup / OUTSIDE / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(top / f, backup / OUTSIDE / f)
            changed.append(f"{OUTSIDE}/{f}")

    notes, outcome = [], {}
    tw = applied_changes()
    for pid, entries in tw:   # tech watch may have created untracked files: copy those too
        for e in entries:
            t = review.resolve(e["target"])
            if t is not None and t.exists() and not (backup / e["target"]).exists():
                (backup / e["target"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(t, backup / e["target"])
    before_drift = drifted()
    old_head = git("rev-parse", "HEAD").stdout.strip()
    wrote = {}
    try:
        for pid, entries in tw:
            wrote[pid] = take_out(pid, entries, notes)
        return finish(a, tw, wrote, notes, outcome, backup, changed, behind, old_version, before_drift, old_head,
                      stamp)
    except SystemExit:
        raise
    except BaseException as e:
        restore(tw, wrote)
        sys.exit(f"The update stopped partway ({type(e).__name__}: {e}). Tech watch's changes and your edits are "
                 f"back as they were; copies of everything are in {backup.relative_to(ROOT)}. If git says a stash "
                 "is left over (`git stash list`), `git stash pop` brings back your edits.")


def finish(a, tw, wrote, notes, outcome, backup, changed, behind, old_version, before_drift, old_head, stamp):
    stashed = bool(git("status", "--porcelain", "--untracked-files=no", "--", ":/").stdout.strip())
    if stashed:   # your edits anywhere in the repository (":/" is its top), so the update can fast-forward
        git("stash", "push", "--quiet", "-m", f"update.py {stamp}", "--", ":/", check=True)
    r = git("merge", "--ff-only", "--quiet", "@{u}")
    if r.returncode:
        if stashed:
            git("stash", "pop", "--quiet")
        restore(tw, wrote)
        sys.exit(f"The update couldn't be applied ({r.stderr.strip()}); nothing changed. Copies of your changed "
                 f"files are in {backup.relative_to(ROOT)}.")
    if stashed:
        r = git("stash", "pop", "--quiet")
        prefix = git("rev-parse", "--show-prefix").stdout.strip()
        clash = [f for f in git("diff", "--name-only", "--diff-filter=U", "--", ":/").stdout.splitlines() if f]
        if r.returncode and clash:
            for f in clash:   # paths from the repository's top
                git("checkout", "HEAD", "--", f":/{f}")
                saved = backup / (f[len(prefix):] if prefix and f.startswith(prefix) else f"{OUTSIDE}/{f}")
                notes.append(f"{f}: your edit clashed with the update; the update's version is in place, yours is in "
                             f"{saved.relative_to(ROOT)}")
            git("reset", "--quiet")
            git("stash", "drop", "--quiet")
        elif r.returncode:
            notes.append(f"your edits couldn't be put back automatically ({r.stderr.strip()[:200]}); they're in "
                         f"`git stash list` and in {backup.relative_to(ROOT)}")
    for pid, entries in reversed(tw):
        outcome[pid] = put_back(pid, entries, wrote[pid], notes)

    update_manifest(before_drift, [f for f in git("diff", "--name-only", "--relative", old_head, "HEAD").stdout.splitlines()
                                   if f])
    new_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "?"
    print(S.ok(f"Updated: {behind} new commit(s) from the maintainer"
               + (f", version {old_version} → {new_version}." if new_version != old_version else ".")))
    if new_version != old_version and (ROOT / "CHANGELOG.md").exists():
        log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        new = log.split(f"## {old_version}")[0].split("\n## ", 1)
        if len(new) > 1:
            print("\n" + S.b("What's new (CHANGELOG.md):") + "\n## " + new[1].strip() + "\n")
    for t in trials(old_head):
        print(S.b(t))
    kept = [p for p, o in outcome.items() if o == "kept"]
    if kept:
        print(f"Tech-watch changes kept: {', '.join(kept)}")
    for n in notes:
        print(f"- {n}")
    if changed:
        print(f"Copies of everything you or tech watch had changed: {backup.relative_to(ROOT)}")
    print(S.b("If Claude Code is open, start a new session to load the update."))


if __name__ == "__main__":
    main(sys.argv[1:])
