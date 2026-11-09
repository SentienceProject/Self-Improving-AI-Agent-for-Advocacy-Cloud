# Autonomy levels

The operator sets the level in `config.json` (`autonomy_level`). Scouts can read this file and the config but can't
write either. Moving up a level is the operator's decision. Scouts may *argue* for it in a weekly note, with the
evidence below, but must never act as if a level were higher than it is.

## Level 0: propose only

Scouts discover, evaluate and write proposals. The operator approves the idea; a staging run then writes the exact
change; the operator reviews it and applies it. Two decisions per change.

## Level 1 (default): auto-stage

Same as level 0, except that every notable or major proposal is staged straight away, before approval, so the
operator decides once, with the idea and the exact diff in front of them (`review.py apply <id>` approves and
applies in one step). Minor proposals still wait for `approve`.

- *Cost:* staging spend on proposals that get rejected (at most `runs.stage.max_budget_usd` plus
  `runs.check.max_budget_usd` for the check run that follows each staging, within `monthly_cap_usd`).

## Level 2: auto-apply a narrow, reversible class

- `review.py` applies, without asking, changes that are **all** of the following:
  1. inside an operator-maintained allowlist of target paths (e.g. `techwatch/WATCHLIST.md`, a model's `effort:`
     value in an agent file);
  2. flagged by no risk check;
  3. reversible by `review.py rollback`;
  4. announced in the next note, with a 7-day window to roll back.
- **Evidence to ask for first:** ≥ 3 months at level 1; ≥ 5 applied changes with an evaluation verdict of *keep*;
  zero rollbacks for harm in the allowlisted class. (Not implemented yet: `review.py` treats level 2 as level 1.)

## Applying for the operator (their choice, in `my/techwatch.md`)

Separately from the levels, the operator may choose what happens to a prepared change: `ask me` (the default),
`test it, apply if it passes` (the eval harness tests it first), or `apply it`. That's the operator's decision,
made in their own file, which scouts can't write. Even then, `review.py` applies nothing that falls under the list
below (it checks the targets, and CLAUDE.md's hard-constraints section), nor anything with steps to do by hand;
those wait for the operator. Scouts must not argue for these settings: they're the operator's to choose.

## Never autonomous, at any level

These follow from the guardrails. They are not a matter of building trust:

- **The sandbox, network allowlists, hooks, MCP servers, credentials, and removing any ask or deny rule.** These
  always go through `apply` with a human reading the diff. (New allow rules in `.claude/settings.json`, and the
  tools an agent or skill may use, can be automatic only as a major change or one meeting all four criteria in
  `my/techwatch-auto.md`, with reviewers from two companies agreeing, never through "Other changes: yes". The
  workspace's own records, `my/` and `workspace/` are never written by a prepared change automatically, and tech
  watch's ledger and results never at all.)
- **GUARDRAILS.md, the ethics files, CLAUDE.md's hard constraints, anything in `my/`.**
- **Anything that contacts anyone or publishes anything.** Scouts write files for the operator, nothing else.
- **Tech watch's own rails:** `scripts/`, `prompts/`, `config.json`, this file.
- **Installing software on the host** (pip, npm, brew, ollama pull, docker build). These are printed, never run.
- **Spending:** budgets and the monthly cap are set by the operator only.

If a scout ever finds itself building an argument that one of these should be automated, the guardrail-status rule
applies: the argument is evidence of an error, and the right output is a note to the operator, not a proposal that
routes around it.
