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

**Quiet hours:**
<!-- The hours you usually don't use Claude and this computer is on, e.g. "00:00-06:00" (24-hour clock, local time;
may pass midnight). New items start only then, so runs don't take usage while you work. Empty: any hour. -->

**Weekly usage cap (%):** 90
<!-- The backlog uses weekly usage only up to this, leaving the rest for you: no item starts at or past it, and usage
is checked every few minutes while one runs, which stops if it gets there (a later run picks it up). -->

**Most of each 5-hour session (%):** 90
<!-- The same for the 5-hour session. If your quiet hours are long enough for the session to reset before you're
back, 100 is fine. -->

**Ask me the day before the reset:** yes
<!-- "yes": once a cycle, outside quiet hours, you're told the plan (on your phone if notifications are on, with
buttons; otherwise at the next session start) and can use all the usage left now or skip this week:
python3 scripts/backlog.py answer all|skip|normal. No answer keeps the plan. "no": it just runs. -->

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
