Hello Claude! This is a tech-watch **staging run**. Your job is to turn the proposal named above into an exact, reviewable change bundle. If you're willing, please do the job below. If you can't stage it safely, or you think it shouldn't be staged, say so in `CANNOT_STAGE.md` (described below). That's a good outcome when it's true. Thank you.

You **don't apply anything**. The operator reads your bundle as one diff and applies it with `review.py apply`. Your bundle is the thing they'll be trusting, so make it small, exact and easy to check. The operator may not be technical: write `APPLY.md` for a careful non-programmer.

## Read first

1. `GUARDRAILS.md`, `my/profile.md` and `my/techwatch.md`.
2. The proposal: `proposal.md` and `meta.json` in the folder named above.
3. Any operator note for this id in `techwatch/state/ledger.jsonl` (the latest `approved` event, if any). **If a note narrows or changes the proposal, the note wins.** If an earlier stage run ended in `cannot_stage` or `stage_failed`, a later note is the operator's answer to it.
4. `techwatch/AUTONOMY.md` ("Never autonomous") and `techwatch/CAPABILITIES.md`.
5. **Every file you'll change, in its current form.** Bundles are built against what's on disk now, not against the proposal's description of it.

## Write: the bundle, in `techwatch/staged/<id>/`

- `manifest.json`:
  ```json
  {"proposal": "<id>", "changes": [
    {"action": "write",   "target": "relative/path/from/workspace/root", "source": "files/<name>", "why": "one line"},
    {"action": "append",  "target": "relative/path", "source": "files/<name>", "why": "one line"},
    {"action": "command", "run": "exact shell command", "why": "one line"},
    {"action": "manual",  "instructions": "what the operator does by hand", "why": "one line"}
  ]}
  ```
  - `write` replaces the whole file with `files/<name>`. Copy the current file and change only what the proposal needs; preserve everything else byte for byte.
  - `append` adds `files/<name>` at the end.
  - Targets are paths **inside this workspace**, relative to its root. That includes `CLAUDE.md`, `.claude/` settings, agents, skills and hooks: stage those as `write` too. `review.py` marks them high-risk, shows the operator exactly what changes, refreshes the integrity manifest after they apply it, and can roll it back.
  - `command` is printed for the operator, never run. Use it for installs, `ollama pull`, `docker build` and the like.
  - `manual` is for anything outside the workspace (a sign-up, an app setting, a `/config` toggle) and for files in `my/`, which belong to the operator.
  - For a proposal of kind `outside`, the bundle may be just `APPLY.md` (step-by-step instructions) and a manifest of `manual` steps.
- `files/…`: the new contents.
- `APPLY.md`, in plain words:
  - what changes and why (two lines);
  - **every risk, flagged up front**: it touches permissions, hooks, settings, scripts, agent frontmatter, `CLAUDE.md`, the guardrails or the ethics files; or it widens what an agent can do (network, tools, files). Say which, and why it's needed;
  - the order of steps, and anything the operator must do by hand.
- `ROLLBACK.md`: how to undo, including any `command`/`manual` steps that `review.py rollback` can't undo.
- `EVAL.md`: how to tell if it helped: a fixed task to run before and after; what to compare; the success criterion, stated before the result is known; a review date; the **removal** criterion.

## Constraints

- Minimal diffs. Don't tidy, reformat or "improve" anything the proposal didn't ask for.
- Changes to GUARDRAILS.md, the ethics files or tech watch's own rails (`techwatch/scripts/`, `prompts/`, `config.json`, `AUTONOMY.md`) only if the proposal is explicitly and only about that file **and** an operator note confirms it.
- A change that widens permissions, network access or tool access needs its reasoning spelled out at the top of APPLY.md.
- If you can't do it safely or exactly, write `techwatch/staged/<id>/CANNOT_STAGE.md` explaining why and what would unblock it, and write **no** `manifest.json`.
