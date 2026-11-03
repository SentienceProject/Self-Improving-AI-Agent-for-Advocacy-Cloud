# Changelog

Each release has a version number (in `VERSION`) and the Claude Code version it was tested with. `python3
scripts/update.py` shows you the new entries when you update. Numbers follow `major.minor.patch`: a new major
number means something you set up may need redoing, and the entry says what.

## 0.1.0 — 2026-11-03

First public version. Tested with Claude Code 2.1.317 on Linux (WSL2); macOS and Windows are untested.

- Casual mode (no stricter than plain Claude Code) and an opt-in hardened mode, set up by `setup.py`.
- Research, critique, verification and ideation agents; a local paper corpus (`/add-papers`, Zotero import);
  a citation checker; export to Word, PDF or a web page.
- Tech watch: automatic scans, prepared changes, optional testing with the eval harness, and settings for what may
  happen automatically (`my/techwatch.md`, `my/techwatch-auto.md`). Test results are kept as facts (what a test
  could tell, and how deep it was), not just verdicts: `reject` says whether you mean "not as it is" or "not this
  kind", `shelve` keeps an idea for later, `test <id> --deeper` runs every task three times, and `trial <id>` applies
  a change and checks it at the next checkup. Proposals say whether they open or close future options, and can
  build on earlier ones.
- Phone notifications with ntfy (`scripts/notify.py`): `setup.py` offers follow-up briefs every few days
  (`scripts/followups.py`, a read-only Claude run capped at $0.50).
- `scripts/update.py` for updates that keep your changes; `scripts/compat_check.py` after Claude Code updates.
- Safeguards: the researcher has no shell and the ideator and drafter write only to their own folders;
  never-automatic and reviewer-gated kinds of tech-watch change; reviewers protected against text planted in
  proposals; a change that would run code during its test waits until you've read it.
- Ships in one repository with the eval harness (`../ai-welfare-eval-harness`).
