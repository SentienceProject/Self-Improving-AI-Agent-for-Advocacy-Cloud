# Tech watch: which changes may be automatic

<!-- How tech watch runs is set in my/techwatch.md ("When a change is ready": ask me / test it, then ask me / test
it, apply if it passes / apply it). This file decides WHICH changes those automatic choices apply to. Any change
that doesn't qualify here waits for you, exactly as with "ask me". Answer yes or no on each line. -->

**Major changes:** yes
<!-- Breakthrough-level changes, such as a new model generation or a new Claude Code capability that changes how the
workspace works. The scout's "major" label isn't enough on its own: every reviewer (below) must agree it's major. -->

**Changes that meet all four criteria:** yes
<!-- A change of any size qualifies if every reviewer agrees it is all four of:
  1. specific: it does one clear thing;
  2. narrow: it touches little, so it can't do much harm elsewhere;
  3. approved: the reviewer would adopt it;
  4. good under virtually every ethical framework, not just one (for example, checking citations).
The citation checker this workspace added is the model case. -->

**Other changes:** no
<!-- "yes" lets every change through (what "test it, apply if it passes" and "apply it" meant before this file). -->

**Trials:** yes
<!-- A test of a few tasks can't tell small gains from noise, so a change whose test showed no clear difference may
still help. With "yes", such a change can go on trial automatically, if it qualifies above, can be undone, doesn't
close off options, and showed no harm: it's applied, checked again at the eval harness's next checkup, then kept,
or rolled back if the workspace did worse. One trial at a time. "no": you're offered trials, but none start
by themselves. -->

**Trials last at most:** 60 days
<!-- A trial ends at the first checkup at least two weeks after it starts, or after this many days if none comes. -->

**At most one automatic change every:** 7 days
<!-- Spacing them out means that if something gets worse, you know which change did it. Changes you apply yourself
don't count. -->

<!-- Who the reviewers are: a fresh Claude session always, plus ChatGPT and Gemini when the eval harness is set up
with them. They read the proposal and the exact change once, which costs a few cents. Their answers are kept in
techwatch/state/criteria/.

Some changes are never automatic, whatever this file says: hooks, MCP servers, the sandbox and safety layers, the
guardrails and ethics files, CLAUDE.md's hard constraints, tech watch's own rules and code, setup.py, the scripts
that enforce safety, your my/ files, anything that removes an ask or deny rule or edits an existing test, and
anything with steps to do by hand.
Other scripts, new tests, agents' tools and new permission rules can be automatic, but only as a major change or one
meeting all four criteria (never through "Other changes: yes"), and only when reviewers from at least two companies
agree. -->
