# Changelog

Each release has a version number (in `VERSION`) and the Claude Code version it was tested with. `python3
scripts/update.py` shows you the new entries when you update. Numbers follow `major.minor.patch`: a new major
number means something you set up may need redoing, and the entry says what.

## Unreleased

- Ideator: about 20 angles by default, scaled by the profile's Creativity setting (low about 12, high 25+), written
  in any order with a closing `Strongest:` line rather than best-first. Ideas that work by deception or by clearly
  breaking major laws may be explored but are marked `[deceitful/illegal]`, with why they'd work and an honest
  version; `CLAUDE.md` keeps marked angles out of plans, drafts and memory. New examples for "the complex one".

- `scripts/docs_sync.py`: keeps the README from drifting. Facts the code holds (which subagent may write and run
  what, the agent and skill lists) are generated into it, and `tests/test_docs_sync.py` fails when they're out of
  date (`--fix` rewrites them). Before each commit, the leak-check hook also brings those parts up to date in your
  working copy and names docs that mention the files you're committing. Mechanical, no model: it never uses
  Claude, and never blocks a commit.

- Subagent guard (`agent_guard.py`): the `helper` agent can now write files in `workspace/helper/` and, like the
  verifier, run `scripts/check_citations.py` on a draft. The read-only search (`find_text.py`) now takes regex
  escapes such as `\b`, `\d` and `\.`, and `$` as an end anchor (`"^---$"`), which bash leaves untouched inside
  double quotes. The guard matches commands exactly as given, like the preflight gate. Hardened mode: the allow
  rule for the semantic search now matches the command the hooks require, so it no longer asks every time
  (re-run `python3 setup.py --hardened` to pick it up).

- `scripts/check_frontmatter.py` (and `tests/test_frontmatter.py`): finds agent, skill and command files whose
  frontmatter Claude Code can't load, such as an unquoted description containing ": " ("mapping values are not
  allowed in this context"); `--fix` quotes the value. Fixed: the `helper` agent's description.

- Tech watch: a **check run** after every staging. A fresh Opus run (up to $2, `runs.check` in
  `techwatch/config.json`) reviews the prepared change before it's tested, fixes or widens it, and writes
  `CHECK.md` with a verdict. Nothing automatic happens to a change until its check finishes; the reviewers, the four
  criteria and the never-automatic rules judge the checked change. `techwatch.py check <id>` runs one by hand.

- `scripts/leak_check.py`: git hooks (`--install`) that refuse a commit or push carrying your own files, keys, your
  home folder's path, email addresses, held-out task text, or strings you list in `.git/info/leak-patterns`.
- `scripts/private_backup.py`: back up `my/`, `workspace/`, tech watch's findings and the harness's results to a
  private repository of your own (without the runs' raw transcripts).
- Everything an agent creates in `workspace/` is now ignored by git, not only the listed folders (a new folder there
  could otherwise end up in a commit).
- `Default ethics: none` in your `my/ETHICS_SHORT.md`: once your own ethics files are complete, the default isn't
  used at all.
- Fixes for Claude Code 2.1.319, which gives subagents no Grep or Glob tool and has them deliver their report
  with a `SubagentHandback` tool:
  - `scripts/find_text.py`: a read-only text search and file listing, kept inside the workspace. The researcher,
    helper, critic, verifier, ideator, drafter and analyst may run it (in one exact form, enforced by their guards),
    so they can search the corpus again.
  - The analyst's guard no longer blocks `SubagentHandback`, so its reports reach the main session.
  - `save_returns.py` saves reports delivered with `SubagentHandback`; the findings, critique, review and ideation
    logs were being saved empty.
- The analyst now reads the image's package list from `sandbox/requirements.in` and `requirements.txt` instead
  of a list in its instructions that had fallen behind (it was missing pypdf and pyarrow), so approving a package
  request no longer needs a matching edit to `analyst.md`. Its description of the container's folders is also
  tidied up.
- Tech watch: scouts no longer keep Claude Code auto-memory, and a refused save into that memory no longer marks a
  run as failed (a stage run that did its job was reported as a problem).

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
- Backlog (optional, `setup.py --more`): ideas and unfinished work run with the usage left near the end of each
  weekly cycle (`scripts/backlog.py`). Starts when the reset is within 24 hours and weekly usage is below 85%,
  stops at 90%, lets tech watch and the eval harness go first; every session sees the short list and records
  finished items or progress; unattended runs can't send or post. All settings in `my/backlog.md`.
- `/plain-prose`: a final editing pass for outward-facing writing that removes AI writing patterns without
  adding facts or dropping hedges (adapted from humanizer and avoid-ai-writing, both MIT). In a small blind test
  (three advocacy pieces, seven blind judgments by ChatGPT and Gemini), a draft plus this revision pass did best
  overall; drafting with the guide in mind was weaker for its audience and added unsupported claims, so it's a
  pass on finished drafts only.
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
