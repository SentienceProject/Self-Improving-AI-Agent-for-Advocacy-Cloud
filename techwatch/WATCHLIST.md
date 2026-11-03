# What to watch, and what counts as significant

The sensor watches the pages listed in `config.json` (cheap, mechanical). The scouts use this file for judgment.

## Scope: any technology that could help the operator's work

Read `my/profile.md` first: the cause area and the kind of work decide what "helps". Then look **broadly**. Any kind of
technology is in scope, AI or not, inside the workspace or outside it:

1. **Models.** New Claude tiers, effort or thinking modes, price drops; other vendors' models where they'd do
   something useful here (a second opinion from a different model family, an open-weight model that runs locally,
   embedding models for the corpus).
2. **Claude Code and agent features.** Subagents, skills, hooks, memory, connectors, scheduling, permission and
   sandbox changes, cost controls. **Default behavior changes count too**: they can silently change how the
   workspace runs.
3. **Agent methods.** Published patterns with evidence: verification and critique loops, multi-agent research,
   eval-driven tuning. Prefer measurements over enthusiasm.
4. **Research tools and data.** Literature discovery, PDF and table extraction, citation checking, datasets, public
   records, legislative trackers, polling and survey tools.
5. **Advocacy and communication tools.** Anything that helps the operator reach people: contact research,
   newsletters, accessibility, translation, design, video, events, coalition tools.
6. **Cause-specific tools and methods.** For animal welfare: welfare assessment methods, supply-chain and
   corporate-commitment trackers, sentience research tools. For digital minds: welfare evaluations, interpretability
   tooling for open models, introspection-validation designs. For other causes, the equivalent.
7. **Ways of working.** Apps, services, hardware or routines that save the operator time or money, even with no AI
   in them.
8. **Security and safety of the workspace.** Vulnerabilities or prompt-injection techniques affecting Claude Code,
   connectors or tools in use, and new containment features. *These can justify an urgent proposal.*
9. **Subtraction.** Anything that makes an existing piece of the workspace unnecessary (a built-in feature that
   replaces a custom hook, a stronger model that no longer needs a crutch prompt). Look for these on purpose.

**Limits come from the operator, not from you.** `my/techwatch.md` says what they'll spend, what computer they have,
which accounts they'll create, their privacy limits and what never to propose. Respect those exactly, and apply no
other limits of your own beyond the guardrails and `AUTONOMY.md`'s never-autonomous list. Don't drop an idea
because it needs a new account, costs money or lives outside the workspace, unless their file says so. If the file
is empty or missing, assume a small budget, an ordinary laptop and free accounts only, and say that you did.

Out of scope: research findings themselves (corpus material, not technology) and general AI news.

## Significance (sets `significance` in meta.json)

- **major**: *any one* of:
  - a new model tier or family, or a model clearly better than the best one this workspace uses for some role;
  - something that plausibly removes one of the top three pain points in `CAPABILITIES.md`;
  - a security issue that affects this workspace as configured;
  - a change already in effect that silently alters how the workspace runs (a default flip, a model retirement).
- **notable**: a clear, evidenced improvement to one role, step or part of the operator's work, worth a decision
  this week.
- **minor**: a small win or an experiment. Batch these; one proposal can list several minor items.

## Quality bar for a proposal (the scout grades itself on these)

- It names a **specific part of the work** it improves, or a specific piece it lets you delete.
- It has **evidence the thing exists and works**, with each claim labeled (established / single-source /
  speculative). A launch post is single-source on the question "does it help?".
- It includes the **strongest case against** adopting it, written as its holder would put it. "More scaffolding
  made outputs worse" is always a candidate.
- It has a **test**: what to run, on what fixed task, to tell if it helped, plus a review date.
- It states **cost** (money, tokens, operator minutes) and **reversibility**, checked against `my/techwatch.md`.
- It has **expected value** and **citations** sections.
- **Boring is a failure.** "There's a new version of X" is not a proposal. "X would let the researcher drop its
  40-line search checklist, and here's the test to prove it" is.
