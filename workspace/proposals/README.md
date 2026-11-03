## Proposing changes to yourselves

Agents may propose changes to their own instructions. They may not make them.

**Only agent files can be changed this way:** any existing file in
`.claude/agents/` except `verifier.md` and `analyst.md`. Not CLAUDE.md, not
GUARDRAILS.md or the ETHICS files, not this file, not settings, skills, hooks,
or anything in `scripts/`. Those are rejected automatically, so there is no
point proposing them -- tell me in conversation instead.

**Only the body changes.** The YAML frontmatter at the top of an agent file is
frozen. An agent can rewrite what it is told to do; it cannot give itself a new
tool, model, or permission mode. A proposal that touches the frontmatter is
rejected without being read.

To file one, create:

```
workspace/proposals/<run-id>-<short-slug>/
    proposal.json     {"target": ".claude/agents/<name>.md", "rationale": "..."}
    proposed.md       the COMPLETE new file, frontmatter byte-identical
```

Write the whole file, not a diff. Copy the frontmatter across unchanged.

The rationale should say what went wrong that prompted this, not what the
change does -- I can see what it does from the diff. "Run 2026-09-14-02 missed
the Sebo paper because the query had six words in it" is useful. "Improves
search quality" is not.

Propose when a specific failure has happened and you can name it. Don't propose
speculatively, don't propose more than one change at a time, and don't propose
the same thing twice if I declined it.

I review with `python3 scripts/apply_proposal.py` and apply or reject. Nothing
happens until I do.

