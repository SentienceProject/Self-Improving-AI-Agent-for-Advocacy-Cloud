# Tech watch: my settings and limits

<!-- Tech watch proposes any kind of new technology that could help your work: AI models and features, apps,
services, data sources, hardware, ways of working. First, how it runs; then your limits, which are the only ones
it applies beyond the workspace's guardrails, so write down whatever matters to you. Leave a line blank if you
have no limit there. -->

**Tech watch runs:** off
<!-- Off by default, and that's what we recommend for most people. The maintainer already runs tech watch, tests
what it finds on advocacy tasks, and ships the changes that suit everyone: `python3 scripts/update.py` gets them, and
Claude tells you at the start of a session when there are some. Running it yourself mostly repeats that work with
your own usage: the scans alone are roughly $10-25 a month (3 days a week and weekly), $5-15 (weekly only) or $25-45 (daily and
weekly), and each test
of a change in the eval harness is roughly $10-40 more. On a subscription these come out of the same usage limits as
your own work.
"automatically": scans run on a schedule, while your computer is on. Start the schedule once with
`python3 techwatch/scripts/techwatch.py schedule on` (setup.py shows this command; it doesn't run it for you). "when I
start it": Claude reminds you when a scan is due and you run `! python3 techwatch/scripts/techwatch.py run`. "off": no
scans. -->

**Proposals:** only ones for me
<!-- Used when tech watch runs. "only ones for me" (recommended): tech watch proposes only changes that your own
settings in my/ call for (your budget, platform, privacy, interests, ethics), which the maintainer can't find for you,
plus anything urgent that can't wait for the maintainer's next update. "all": every change it finds, including the
ones the maintainer will ship anyway; not recommended unless you maintain your own version of the workspace. -->

**Scans:** 3 days a week and weekly
<!-- "3 days a week and weekly": one deeper weekly scan (Opus, up to $4) and a quick scan (Opus, up to $1.50) on
three other days; roughly $10-25 of usage a month. "daily and weekly": the quick scan on all the other days; roughly
$25-45 a month, and a month with several prepared changes can reach the cap. "weekly only": roughly $5-15 a month.
Preparing a change costs up to $3 more. On a subscription these are estimates against your usage limits, not
charges. -->

**Monthly cap:**
<!-- Scans and preparation stop for the month once they would pass this. Blank: $45 (monthly_cap_usd in
techwatch/config.json). Write an amount, such as "$60", to use your own. -->

**When a change is ready:** ask me
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
