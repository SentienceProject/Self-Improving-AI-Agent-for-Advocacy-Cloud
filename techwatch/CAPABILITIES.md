# What this workspace runs on (baseline)

*The scouts compare discoveries against this. It describes the workspace as shipped; `my/profile.md` and
`my/techwatch.md` describe this operator's setup. When a change is applied, updating this file is part of
applying it.*

## Shape

- **Main session writes the drafts.** It researches in parallel through subagents, reads key sources itself,
  gets a critic's review, revises, and has the verifier check facts before anything that matters goes out.
- **Agents** (`.claude/agents/`): `researcher` (one sub-question, web + corpus), `critic`, `verifier` (checks
  claims against sources; read-only), `ideator` (angles; also translates and filters wild ideas), `wild-ideator`
  (disguised-goal brainstorming from random cards), `drafter` (alternative drafts), `helper` (any role the main
  session writes), `analyst` (hardened mode: analysis code in a Docker container with no network).
- **Skills**: `/wild-ideas`, `/add-papers`, `/connect`, `/my-ethics`, `/techwatch`, `/fetch-sources` (hardened).
- **Models**: agents run on Opus or inherit the session's model; tech-watch dailies on Sonnet.
- **Corpus**: `workspace/corpus/`, papers as text, searched with grep; semantic search (Ollama + ChromaDB) in
  hardened mode if set up.
- **Memory**: `workspace/memory.md` (verified facts carried between tasks).

## Two modes

- **Casual** (default): everything plain Claude Code can do. Claude Code's sandbox (Linux, WSL2, macOS) runs code
  without prompts while it stays inside the workspace and allowlisted research sites; anything beyond that asks.
  Ask rules (each one removable with `setup.py --allow`) cover sending and posting, credentials and the
  instruction files. Native Windows has no sandbox, so commands ask first.
- **Hardened** (`setup.py --hardened`): adds a sandbox preflight probe that gates all shell use, run logs and saved
  subagent returns (hooks), strict deny lists, manual permission mode, the Docker analyst, and allowlisted
  PDF/code downloads with provenance.

## Known pain points (numbered; proposals cite them)

1. **Wild ideation is expensive.** Up to 3 wild-ideators × 35 ideas, plus translation, can take a large share of a
   run's tokens, and its value hasn't been measured well.
2. **The corpus is small and grows by hand.** `/add-papers` helps, but finding the right papers and keeping them
   current is manual.
3. **Verification is the bottleneck for trustworthy drafts.** The verifier checks claims against fetched sources;
   paywalled or JavaScript-only pages often can't be checked.
4. **Claude judging Claude.** Critics, verifiers and scouts share blind spots with the drafter.
5. **Usage limits.** Big tasks and tech-watch runs compete with the operator's own work for plan usage.
6. **Sending is manual by default.** Claude can send through a connected app only after the operator approves
   each send at a prompt; most drafts are still copied out by hand.
7. **Setup friction for non-technical users**, especially on Windows (no sandbox) and for the hardened mode
   (Docker, Ollama).
8. **Live facts go stale.** Bills, organizations and contact details change; memory entries carry recheck dates
   but nothing rechecks them automatically.
