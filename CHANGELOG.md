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
- Setup: asks whether tech watch's ready changes are tested and applied automatically (the default) or wait for you;
  prepares the eval harness (unlocks its tasks, sets up its judges); offers to bring in what other AI tools know
  about you (`/import-context`); never schedules anything itself, but lists the commands; then offers more options
  (safety mode, paper corpus, `/remote-control`), with "not now" reminding you the next day (`setup.py --more`).
- The agents' write guards work on Windows (paths with spaces or backslashes, Git Bash and WSL path forms) and with
  casual mode's run IDs. README: "If something goes wrong".
- Windows: absolute paths in permission rules are now written the way Claude Code reads them (`//c/Users/...`).
  Before, tech watch's scouts couldn't save their notes or proposals there, and the read-block rules for scouts,
  follow-up briefs, the eval harness's runs and the layers' harness rules did nothing. A run whose saves were
  refused is now reported as a problem, not "ok", and `techwatch.py doctor` tries a real save. After updating,
  run `python3 setup.py` once to refresh your settings.
- `creator-notes/`: lessons from the creator's earlier workspaces (read by tech watch before every proposal, and by
  the reviewers of automatic changes) and verified AI-welfare research memory (read for digital-minds work only; left
  out of eval runs). Delete the folder to start without them.
- Ethics: your own `my/ETHICS_SHORT.md` no longer replaces the default. Yours comes first and wins wherever it says
  something; the default (now labelled as the project creator's view, with its controversial parts flagged and the
  main alternatives) fills in where yours is silent or unsure. `Default ethics: reference only` in your file keeps
  only the default's survey of other frameworks. `/my-ethics` is shorter and lighter (about 10 minutes; "not sure"
  is fine), asks which parts of the default you reject, and offers a fuller, framework-based version only if you
  want one.
- Chat history import: `scripts/import_chats.py` reads ChatGPT and Claude.ai data exports, Google Takeout's
  Gemini activity, and your Claude Code sessions (not automated runs) into one file per chat plus an index in
  `my/imports/`; setup explains how to download each, and `/import-context` picks the chats about your work.
- Easier to read: setup and the scripts use bold headings, coloured questions, green ticks and yellow warnings,
  chosen to read on dark, light and PowerShell's blue backgrounds (no dim grey). `NO_COLOR=1` turns colour off.
- Windows: signing in to ChatGPT right after setup installs Node.js works (it failed with '"node" is not
  recognized', because the window's PATH predated the install). Gemini's key is checked in seconds without using
  its free quota, and a slow or busy Google no longer counts as a bad key.
- Judge setup no longer hangs: each connection test gives up within a minute or two, Ctrl+C skips any step, and a
  busy server keeps the judge instead of failing. Step-by-step help for installing Node.js (offered via winget on
  Windows) and for setting the Gemini key on Windows.
- Outside models during tasks: once ChatGPT or Gemini is set up (the eval harness's logins), Claude can ask them for
  second opinions, critiques and cross-checks (`scripts/ask_model.py`, `OUTSIDE_MODELS.md`). No prompt by default,
  even with files from `workspace/`; every call is logged in `workspace/logs/outside-models.md`;
  `setup.py --ask-outside-models` makes each call ask. `scripts/cross_check.py` now uses the same logins.
