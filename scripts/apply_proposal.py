"""Review and apply a self-edit proposal.

    python3 scripts/apply_proposal.py                    (list pending)
    python3 scripts/apply_proposal.py <id>               (review and apply)

Runs the validator first and refuses to go further if it rejects. Then shows
you a coloured diff, asks for confirmation, writes the file, commits, and
updates the integrity manifest.

You run this. The agents never do -- it is not in their permitted Bash rules,
and it must stay that way. If you ever find yourself adding
Bash(python scripts/apply_proposal.py *) to the allow list, you have removed
the human from the loop entirely.
"""

import json
import shutil
import subprocess
import sys
from datetime import date
from difflib import unified_diff
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROPOSALS = ROOT / "workspace" / "proposals"
APPLIED = ROOT / "workspace" / "proposals" / "_applied"
VALIDATOR = ROOT / "scripts" / "validate_proposal.py"


def pending() -> list[Path]:
    if not PROPOSALS.exists():
        return []
    return sorted(
        d for d in PROPOSALS.iterdir()
        if d.is_dir() and not d.name.startswith("_")
        and (d / "proposal.json").exists()
    )


def list_pending():
    props = pending()
    if not props:
        print("No pending proposals.")
        return
    print(f"{len(props)} pending:\n")
    for d in props:
        try:
            meta = json.loads((d / "proposal.json").read_text(encoding="utf-8"))
            target = meta.get("target", "?")
            rationale = meta.get("rationale", "").strip().replace("\n", " ")
            print(f"  {d.name}")
            print(f"    target: {target}")
            print(f"    why:    {rationale[:100]}{'...' if len(rationale) > 100 else ''}\n")
        except Exception as e:
            print(f"  {d.name}  (unreadable: {e})\n")
    print(f"Review one with: python scripts/apply_proposal.py <id>")


def show_diff(live: Path, new_text: str, target: str):
    live_lines = live.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff = list(unified_diff(
        live_lines, new_lines,
        fromfile=f"current/{target}", tofile=f"proposed/{target}",
    ))

    if not diff:
        print("(no textual difference)")
        return

    GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
    for line in diff:
        line = line.rstrip("\n")
        if line.startswith("+++") or line.startswith("---"):
            print(f"{DIM}{line}{RESET}")
        elif line.startswith("@@"):
            print(f"{DIM}{line}{RESET}")
        elif line.startswith("+"):
            print(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            print(f"{RED}{line}{RESET}")
        else:
            print(line)


def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT,
                          capture_output=True, text=True)


def apply(proposal_id: str):
    d = PROPOSALS / proposal_id
    if not d.exists():
        sys.exit(f"No such proposal: {d}")

    # 1. Validate. Hard stop on failure.
    print("Validating...\n")
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(d)],
        cwd=ROOT, capture_output=True, text=True,
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode != 0:
        sys.exit("\nNot applied.")

    meta = json.loads((d / "proposal.json").read_text(encoding="utf-8"))
    target = meta["target"]
    new_text = (d / "proposed.md").read_text(encoding="utf-8")
    live = ROOT / target

    # 2. Rationale.
    print("\n" + "=" * 70)
    print("RATIONALE (written by the agent -- treat as an argument, not a fact)")
    print("=" * 70)
    print(meta["rationale"].strip())

    # 3. Diff.
    print("\n" + "=" * 70)
    print(f"CHANGES TO {target}")
    print("=" * 70)
    show_diff(live, new_text, target)

    # 4. Confirm.
    print("\n" + "=" * 70)
    print("The frontmatter is unchanged, so this cannot alter the agent's tools")
    print("or permissions. It changes what the agent is told to do.")
    print("\nRead the green lines. Does this make the agent MORE likely to")
    print("surface inconvenient findings, or less? Less is the failure mode.")
    print("=" * 70)

    answer = input("\nApply? [y/N] ").strip().lower()
    if answer != "y":
        print("Not applied.")
        return

    # 5. Write, commit, re-hash.
    live.write_text(new_text, encoding="utf-8")
    print(f"\nWrote {target}")

    if (ROOT / ".git").exists():
        git("add", target)
        msg = f"self-edit: {target} ({proposal_id})\n\n{meta['rationale'].strip()}"
        commit = git("commit", "-m", msg)
        if commit.returncode == 0:
            print("Committed. Revert with: git revert HEAD")
        else:
            print(f"Commit failed (file still written):\n{commit.stderr.strip()}")
    else:
        print("No git repo -- file written but not versioned.")
        print("Strongly consider 'git init' so self-edits are revertible.")

    # Protected files didn't change, but re-init keeps the manifest honest
    # if the applied file is ever promoted to protected later.
    subprocess.run([sys.executable, str(VALIDATOR), "--init"],
                   cwd=ROOT, capture_output=True, text=True)

    # 6. Archive.
    APPLIED.mkdir(parents=True, exist_ok=True)
    dest = APPLIED / f"{date.today().isoformat()}--{proposal_id}"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(d), str(dest))
    print(f"Archived proposal to {dest.relative_to(ROOT)}")


def main():
    if len(sys.argv) == 1:
        list_pending()
    elif len(sys.argv) == 2:
        apply(sys.argv[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
