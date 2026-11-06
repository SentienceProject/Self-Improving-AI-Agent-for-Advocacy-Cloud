# Backlog: my settings

<!-- The backlog (workspace/backlog/BACKLOG.md) holds ideas, unfinished work and things to look into that aren't
worth usage now. Near the end of each weekly usage cycle, if usage is left, they run one by one in full,
unattended sessions; results go to workspace/backlog/results/. Start the hourly check once with
`python3 scripts/backlog.py schedule on` (setup shows this command; it doesn't run it for you).
`python3 scripts/backlog.py status` shows these settings, your usage, and whether a run would start now. -->

**Backlog:** on
<!-- "off" stops the runs and the session-start list; the files stay. -->

**Claude adds items:** yes
<!-- "yes": Claude adds ideas it notices during sessions (marked as Claude's). "suggest only": it suggests them, and
you add the ones you want: python3 scripts/backlog.py add "..." -->

**Start when the weekly reset is within (hours):** 24

**Start below weekly usage (%):** 85
<!-- Runs start only if weekly usage is below this when the reset is close. -->

**Stop at weekly usage (%):** 90
<!-- Leaves the rest for you. No new item starts past this; one already running finishes (up to the cap below). -->

**Tech watch and evals first:** yes
<!-- "yes": a run waits while tech watch or the eval harness is running, or tech watch hasn't done today's run.
"no": the backlog goes first. -->

**Most usage per item ($):** 15
<!-- A cap for each item, in API-price dollars (on a subscription, an estimate against your limits). -->

**Ask me about items older than (days):** 21
<!-- Older items wait until you say "keep" (python3 scripts/backlog.py keep B3) or drop them. 0: never ask. -->

**Weekly reset (if it can't be read):**
<!-- Usually read automatically. If status says it's unknown, write the day and time /usage shows, e.g.
"Thursday 09:00". -->
