# Tech watch: my settings and limits

<!-- Tech watch proposes any kind of new technology that could help your work: AI models and features, apps,
services, data sources, hardware, ways of working. First, how it runs; then your limits, which are the only ones
it applies beyond the workspace's guardrails, so write down whatever matters to you. Leave a line blank if you
have no limit there. -->

**Tech watch runs:** automatically
<!-- "automatically": scans run on a schedule, while your computer is on. Start the schedule once with
`python3 techwatch/scripts/techwatch.py schedule on` (setup.py shows this command; it doesn't run it for you). "when I start it": Claude reminds you when a scan is due and you run
`! python3 techwatch/scripts/techwatch.py run`. "off": no scans; you still get the maintainer's improvements with
`python3 scripts/update.py`. -->

**Scans:** daily and weekly
<!-- "daily and weekly": a quick daily scan (Sonnet, up to $1) plus a deeper weekly one (Opus, up to $4); roughly
$15-35 of usage a month. "weekly only": roughly $5-15 a month. Preparing a change costs up to $3 more. Everything
stops at the monthly cap in techwatch/config.json ($30). On a subscription these are estimates against your usage
limits, not charges. -->

**When a change is ready:** test it, apply if it passes
<!-- "ask me": Claude tells you at the start of a session; `! python3 techwatch/scripts/review.py` asks, change by
change, whether to test it (and apply it if it passes), apply it now, or skip it.
"test it, then ask me": changes are tested with the eval harness (see "Eval harness folder"), and you decide with
the result in front of you.
"test it, apply if it passes": changes are tested and applied unless they do noticeably worse than the current
workspace on a few tasks (passing means "not worse", not "proven better").
"apply it": changes are applied without a test.
Each test is several full agent runs: roughly $10-40 of usage.
The three automatic choices apply only to the changes my/techwatch-auto.md allows: by default, major changes and
changes every reviewer finds specific, narrow, worth adopting and good under virtually every ethical framework.
Everything else waits for you. Changes to hooks, the sandbox, the guardrails and ethics files, tech watch's own
rules and your my/ files always wait; scripts, tests and permissions need reviewers from two companies to agree.
A change that would run code during its test (hooks, MCP servers) is only tested after you've read it.
Claude tells you what was applied; `review.py rollback <id>` undoes it. -->

**Eval harness folder:**
<!-- Where the companion eval harness (ai-welfare-eval-harness) is. setup.py fills this in when it finds one beside
this workspace in the repository, or in ~/ai-welfare-evals/. Without it, changes can't be tested, only applied or
skipped. -->

**Money I'm willing to spend on new tools or services:** (e.g. "nothing beyond my Claude plan", "up to $20/month")

**My computer and platforms:** (e.g. "a MacBook Air, 8 GB RAM; no GPU", "Windows laptop + WSL2", "phone only for
reading")

**Accounts I'm willing to create:** (e.g. "free accounts are fine", "nothing that needs a credit card")

**Privacy:** (e.g. "nothing that sends my drafts to companies that train on them")

**Time I can give to setting things up:** (e.g. "an hour a month", "a weekend once")

**Never propose:** (anything you don't want to hear about)

**Especially interested in:** (e.g. "tools for finding legislators' staff", "better literature search")
