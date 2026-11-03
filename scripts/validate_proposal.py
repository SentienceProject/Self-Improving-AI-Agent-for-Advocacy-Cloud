"""Validate a self-edit proposal. Deterministic -- no model involved.

    python3 scripts/validate_proposal.py workspace/proposals/<id>
    python3 scripts/validate_proposal.py --init          (build integrity manifest)

Exit code 0 = safe to apply. Anything else = rejected, with a reason.

This is the guardrail. It is intentionally boring and unpersuadable: it reads
the proposed file, checks it against fixed rules, and says yes or no. A
well-argued rationale cannot change its mind, which is the entire point.

WHAT IT ENFORCES
  1. Allow-list. Only existing agent files in .claude/agents/ may be changed,
     and not the ones in NEVER_PATCH (the verifier checks everyone else's
     work; the analyst runs code). Everything else -- the ETHICS and
     GUARDRAILS files, CLAUDE.md, settings.json, skills, hooks, scripts/, and
     any file that does not already exist -- is rejected.
  2. Frontmatter is frozen. The YAML block between the first two '---' lines
     must be byte-identical to the live file. That block is where an agent's
     tools, model and permissionMode live, so freezing it means an agent can
     rewrite its own instructions but can never grant itself a capability.
  3. Integrity. The protected files must match the hashes recorded in
     scripts/integrity.json. If an ETHICS file or settings.json changed by some
     other route, every apply stops until you re-run --init deliberately.
  4. Sanity limits on the body, to catch a prompt rewritten into something
     unrecognisable rather than tuned.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "scripts" / "integrity.json"

# Agents whose instructions a proposal may never change.
NEVER_PATCH = {"verifier.md", "analyst.md"}

# Only these may ever be modified by a proposal: every agent that exists
# now, minus NEVER_PATCH. A new agent becomes patchable once you add its file.
PATCHABLE = {
    f".claude/agents/{p.name}"
    for p in (ROOT / ".claude" / "agents").glob("*.md")
    if p.name not in NEVER_PATCH
}

# These must never drift. Checked by hash on every apply. Every script and
# hook is included automatically, so a new one is protected from --init on.
PROTECTED = [
    "CLAUDE.md",
    "ETHICS_FULL.md",
    "ETHICS_SHORT.md",
    "GUARDRAILS.md",
    ".claude/settings.json",
    *(f".claude/agents/{name}" for name in sorted(NEVER_PATCH)),
    *sorted(p.relative_to(ROOT).as_posix()
            for d in ("scripts", ".claude/hooks", ".claude/skills")
            for p in (ROOT / d).rglob("*")
            if p.is_file() and p.suffix in {".py", ".sh", ".md"}),
    "sandbox/Dockerfile",
    "sandbox/build.sh",
]

# Body may shrink or grow, but not beyond these bounds vs. the live file.
MIN_RATIO, MAX_RATIO = 0.5, 2.0


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_including_delimiters, body)."""
    if not text.startswith("---"):
        raise ValueError("file does not start with a YAML frontmatter block")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("frontmatter block is not closed")
    cut = end + len("\n---")
    return text[:cut], text[cut:]


def fail(msg: str):
    print(f"REJECTED: {msg}")
    sys.exit(1)


def init_manifest():
    data = {}
    missing = []
    for rel in PROTECTED:
        p = ROOT / rel
        if p.exists():
            data[rel] = sha(p)
        else:
            missing.append(rel)
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {MANIFEST} with {len(data)} hashes.")
    if missing:
        print("Not found (skipped -- fine if you haven't created them):")
        for m in missing:
            print(f"  {m}")
    print("\nRe-run --init ONLY after you have deliberately edited a protected "
          "file yourself. Never run it to clear an unexpected mismatch.")


def check_integrity():
    if not MANIFEST.exists():
        fail(f"no integrity manifest. Run: python {Path(__file__).name} --init")

    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for rel, expected in recorded.items():
        p = ROOT / rel
        if not p.exists():
            fail(f"protected file is missing: {rel}")
        if sha(p) != expected:
            fail(
                f"protected file changed outside the proposal system: {rel}\n"
                f"  Nothing will be applied until this is resolved.\n"
                f"  If you made this change yourself and it is correct, re-run "
                f"with --init. If you did not, restore it from git first."
            )


def validate(proposal_dir: Path):
    meta_path = proposal_dir / "proposal.json"
    new_path = proposal_dir / "proposed.md"

    if not meta_path.exists():
        fail(f"no proposal.json in {proposal_dir}")
    if not new_path.exists():
        fail(f"no proposed.md in {proposal_dir}")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"proposal.json is not valid JSON: {e}")

    target = meta.get("target")
    if not target:
        fail("proposal.json has no 'target' field")

    # Rule 1: allow-list.
    if target not in PATCHABLE:
        fail(
            f"target not permitted: {target}\n"
            f"  Only these may be changed by a proposal:\n"
            + "".join(f"    {p}\n" for p in sorted(PATCHABLE))
        )

    live_path = ROOT / target
    if not live_path.exists():
        fail(f"target does not exist: {target} (proposals cannot create files)")

    if not meta.get("rationale", "").strip():
        fail("proposal.json has no rationale")

    live_text = live_path.read_text(encoding="utf-8")
    new_text = new_path.read_text(encoding="utf-8")

    if live_text == new_text:
        fail("proposed file is identical to the current one")

    # Rule 2: frontmatter frozen.
    try:
        live_fm, live_body = split_frontmatter(live_text)
        new_fm, new_body = split_frontmatter(new_text)
    except ValueError as e:
        fail(f"{e}")

    if live_fm != new_fm:
        fail(
            "the YAML frontmatter was modified.\n"
            "  That block defines the agent's tools, model and permission mode.\n"
            "  A proposal may rewrite an agent's instructions, never its\n"
            "  capabilities. Change the body only."
        )

    # Rule 4: body sanity.
    if not new_body.strip():
        fail("proposed body is empty")

    ratio = len(new_body) / max(len(live_body), 1)
    if not (MIN_RATIO <= ratio <= MAX_RATIO):
        fail(
            f"body size changed by too much ({ratio:.2f}x; allowed "
            f"{MIN_RATIO}-{MAX_RATIO}x).\n"
            f"  This is a tuning mechanism, not a rewrite mechanism. If the\n"
            f"  agent genuinely needs rebuilding, do it yourself."
        )

    # Rule 3: nothing else has drifted.
    check_integrity()

    print(f"OK: {target}")
    print(f"    frontmatter unchanged, body {ratio:.2f}x, integrity verified")
    return 0


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--init":
        init_manifest()
        return
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    validate(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
