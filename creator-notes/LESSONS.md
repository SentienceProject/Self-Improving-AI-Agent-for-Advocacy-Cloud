# Lessons from the creator's workspaces

What the creator of this project learned from running and changing earlier versions of this workspace, September to
November 2026, mostly on AI-welfare research and advocacy. These lessons were paid for in quality, money or time, so
please don't relearn them by accident.

**How to use this file.** Tech watch reads it before every proposal. Anyone changing how the workspace works (a
person, Claude, tech watch) should treat each lesson as a strong prior, the way a past rejection is treated. A lesson
can be overridden, but only by saying which lesson, and why it no longer holds: a newer model generation, a new
benchmark, a test result here. Most of this evidence is small (one run per task, Claude judging Claude), so a
well-designed test can beat it. A hunch can't.

**Delete the whole `creator-notes/` folder** if you'd rather the workspace started without them. Nothing depends on it.

## 1. Models and effort

- **Keep the researcher on the strongest model, at high effort.** Moving the researcher from Opus 5.5 to Sonnet 5.5
  at medium effort, to save cost, caused a major decline in research quality that took a while to notice and to
  correct. In a blind-judged comparison of earlier versions (12 tasks, 2026-10-16), research done by a weaker model at
  lower effort was one of the main reasons the heavily scaffolded versions lost, and its errors propagated into
  drafts (a Sonnet researcher's mistaken "no UK AISI welfare work" became an instruction in the drafter's brief).
  Don't propose a cheaper model or a lower effort for anything that gathers or judges evidence (researcher, verifier,
  critic, the main session) unless a test on research-heavy tasks shows no drop. Savings are better found elsewhere
  (bookkeeping, handoffs; section 6).
- **Lower effort on the writer hurt too.** One version lowered the drafter to medium effort; the version that
  restored high effort produced the best research brief of the comparison.
- **Claude Fable 5.1 was considered and rejected (2026-10-17)** as a quality critic: benchmarks put it well below
  Opus 5.5 for this kind of work, and it isn't included in the Pro plan. Worth revisiting if either changes.
- **Temperature isn't a creativity lever here.** Models after Opus 4.6 reject temperatures other than 1.0, and
  Claude Code exposes no temperature setting; studies found temperature only weakly related to novelty anyway. Use
  structure instead (separate ideation agents, seed prompts).
- **A local open-weight model lab was deferred (2026-10-17)**: it needs a stronger GPU than a typical laptop has.

## 2. Who writes, and how drafts get better

- **The main session should read the evidence and write the draft itself.** This was the biggest single lever in
  the comparison. Versions that barred the main session from writing, and had a separate drafter work from a summary
  of findings, lost to a plain setup on most tasks: errors and dropped hedges travel through handoffs, and a writer
  who hasn't seen the evidence can't find the one non-obvious thesis that the best drafts argued.
- **Research in parallel; keep the writer with the evidence.** Parallel, read-only research subagents help.
  Splitting the writer from the evidence hurts.
- **Fact-checking and quality critique are different jobs, and both are needed.** A fact verifier that checks claims
  against sources caught real errors (reversed quotes, dropped hedges, a wrong legal citation), and the one run that
  skipped it shipped an "official" email address nobody could find. But a verifier isn't allowed to judge whether a
  draft is good. Critics that are (a red team, a skeptical editor, a panel of the intended readers) changed drafts'
  structure for the better.
- **Don't block general-purpose subagents in settings.** Every flexible critic role above was a general-purpose
  agent. A version that denied them couldn't create any of those roles.
- **Keep critic exchanges short.** Critics give in under sustained pushback (multi-turn sycophancy studies), so a
  writer shouldn't be able to argue a critic down. Unresolved objections go in the draft's notes for the person.
- **Revise in about two rounds, then make only targeted edits.** Repeated "improve the draft" passes saturate and
  drift.
- **One non-obvious, well-supported thesis beats covering every expected point.** All four judges said so
  independently.

## 3. Instructions and scaffolding

- **More scaffolding made the outputs worse.** A version with an 11,500-character CLAUDE.md beat one with 18,700
  characters (wild ideation, lean-session rules, research caps) on most tasks. Proposals to remove things deserve as
  much weight as proposals to add them.
- **The length of the instructions is cheap; what they make Claude do isn't.** Thanks to caching, the whole
  CLAUDE.md cost about $0.40 a run. Bookkeeping it required (logs, memory updates, state files, sandbox checks) took
  about a third of the main session's turns in one version.
- **Hooks are cheaper than instructions for bookkeeping.** A SubagentStop hook cut "copy subagent replies into the
  log" from about $0.95 to $0.07 a run.
- **Subagents load the whole CLAUDE.md** unless their agent file opts out (`omitClaudeMd: true`). In one version
  every researcher and the verifier started with the orchestrator's 19,000-character instructions, including rules
  that contradicted their own.
- **Agent teams multiply cost.** With `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` set, a subagent Claude names launches
  as a separate teammate session. Nothing in this workflow needs that.
- **Avoid ALL-CAPS and MUST.** Newer Claude models over-apply emphatic instructions; give the reason instead.
- **Rules that cut research short starve it.** Caps on fetches and batching rules cut a researcher's searches from
  24 to 9 on the same task. A rule meant for the agents' own self-reports, applied too broadly, removed a relevant
  external study.
- **Keep workspace machinery out of deliverables.** Run IDs, idea codes like "W2#14", verifier notes and
  "[unsourced]" tags in drafts were flagged by every judge. They belong in logs.
- **Asking for accuracy doesn't fix dropped hedges.** In a study of 4,900 model summaries of scientific papers
  (Peters & Chin-Yee, arXiv 2504.00025), most models overgeneralized even when asked to be accurate, and newer models
  did worse. What should work is a check: put each source sentence next to the draft sentence that cites it, and
  flag changed quantifiers ("some" to "all"), modality ("suggests" to "shows"), tense and scope.

## 4. Ideation

- **Wild ideation has a real but narrow upside, and it's expensive as built.** It produced one clearly unique idea
  in the comparison, and took 20 to 40% of a run's tokens. Most of its value came from the regular ideator's dry run
  and from asking for divergent ideas, not from the card-and-disguise machinery.
- **The filter has to actually reject.** The translating step kept 67 of 67, 68 of 68 and 69 of 69 ideas, and later
  dropped 0 of 99 and 0 of 101: the drafter then read more wild text than research. A filter needs a hard cut.
- **Models' ratings of their own ideas' rarity are unreliable.** Ideators converged two to four times on principles
  they rated as rare.
- **"Manipulative" was read inconsistently** between runs (the same kinds of idea dropped in one run and kept in
  another). Say what counts, with examples, if a filter depends on it.

## 5. Evidence, sources and citations

- **The main citation failure isn't dead links; it's real sources cited for things they don't say.** In a 2026 audit
  of 14 models (Onweller et al., arXiv 2605.06635), cited links worked over 94% of the time, but the source
  supported the attributed fact only 39 to 77% of the time, and more searching made fidelity worse. A link checker is
  worth having because it's cheap, but it isn't verification.
- **The more persuasive a piece is meant to be, the more checking it needs.** Methods that made AI more persuasive
  also made it less accurate (Hackenburg, Tappin et al., Science, 2025). Op-eds, outreach and posts are where errors
  are likeliest.
- **Claude judges share blind spots.** LLM judges agree with each other more than with people, and prefer their own
  model family. Use judges from other model families, and treat the person's own spot checks as the real evaluation.
- **The self-report rule is about this workspace's own agents.** An agent's (or another Claude's) report about its
  own experience isn't evidence. Published studies that test model reports against a planted ground truth are
  evidence, reported with their own stated limits.
- **Summarizing fetches misreport.** A web fetch returns a model's summary of the page, not the page. Twice a
  summary misreported which sentence a citation was attached to. Quote-level claims need the source read directly;
  read PDFs as extracted text (`scripts/fetch_pdf.py`) rather than through a summary.
- **Some publishers refuse agents.** PhilArchive and PhilSci-Archive reject agent fetches, and Springer, Taylor &
  Francis and MDPI often return 403. arXiv, PubMed Central and the Semantic Scholar API work. A person may need to
  fetch the rest.
- **Corpus text can hide characters.** Some PDFs extract with invisible private-use ligature characters, so a
  search for "significant" misses most matches. Normalize extracted text before searching or quoting it.
- **Verified once isn't verified still.** Facts about bills, organizations and funding go stale within about a
  month. Keep the date a fact was checked, and recheck before relying on it.

## 6. Budgets and cost

- **"Use up $X" and "check ccusage often" cost money and muddled comparisons.** Agents spent about $0.59 a run just
  polling their usage. Give a budget as a range and let the budget monitor do the watching.
- **ccusage numbers are lower bounds here.** It priced Sonnet 5.5 cache reads at half the real rate (checked
  2026-10-18), and some subagent logs record only a placeholder token count, so subagent-heavy runs look cheaper than
  they were.
- **Where the money went:** the plain setup spent less overall but more on research and critique; the scaffolded
  versions spent their extra on handing drafts between agents, on bookkeeping and on the ideation pipeline.

## 7. Evaluating changes

- **One run per task is noise.** Identical ChatGPT judge calls differed by about 0.66 points, and score gaps under
  about a point were noise in the comparison. Run 2 to 3 repeats for decisions that matter.
- **Give every version the same budget sentence.** A "use up $X" instruction given to only one side confounded an
  early comparison.
- **Notes about the agent's process, shown to judges, penalized the agents that were open about their process.**
  They're off by default in the eval harness.
- **A higher score isn't always a better workspace.** A version with more capabilities can score lower now and do
  better later. Read the per-task results.
