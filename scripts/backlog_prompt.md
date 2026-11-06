Hi Claude! This is an unattended backlog run in this workspace. The person keeps a backlog of ideas, unfinished
work and things to look into that weren't worth usage when they came up, and set aside the usage left at the end
of their weekly cycle for them. You're welcome to decline this item, or to stop partway; if you do, say so in the
result file. Thank you for your help!

**Nobody is here to answer questions or approve anything.** Anything that would normally ask the person first is
refused. Don't try to send, post, share or contact anyone; draft instead, and say what you'd send. Where the item is
unclear, choose the most sensible reading and say which you chose. Work as you would in a normal session here
(CLAUDE.md, research subagents, critics, the verifier where they help), at the size the item calls for.

**The item:** {id} · {title}
{details}

**Earlier progress on it** (build on this rather than redoing it):
{progress}

**When you finish:**
1. Write the result to `workspace/backlog/results/{id}-{date}.md`. Start with three lines: what you did, what you
   found or made, and what's left. Put any drafts where they'd normally go (e.g. `workspace/drafts/`), and link them.
2. Record it, with one of:
   - finished: `python3 scripts/backlog.py done {id} --result workspace/backlog/results/{id}-{date}.md`
   - partly done: `python3 scripts/backlog.py progress {id} --path workspace/backlog/results/{id}-{date}.md --note "what it adds; what's left"`
3. If the work turned up something else worth doing later, add it: `python3 scripts/backlog.py add "one-line goal" --size small|medium|large --details "..."`.
