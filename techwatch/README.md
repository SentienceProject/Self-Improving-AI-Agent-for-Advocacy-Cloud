# Tech watch

Watches for new technology of any kind that could help your work (AI models and Claude Code features, research
tools, data sources, apps, services, ways of working) and prepares the exact change for you to accept or turn down.
It's off by default, and we recommend leaving it off: the maintainer runs it, tests what it finds, and ships the
changes that suit everyone (`scripts/update.py`), so running it yourself mostly spends your usage on the same work.
Turned on, it proposes only changes your own `my/` settings call for, plus urgent ones (`Proposals:` below). By
default nothing changes in the workspace until you say yes.

## Your settings (`my/techwatch.md`)

| Setting | Choices |
|---|---|
| `Tech watch runs:` | `off` (default; recommended) · `when I start it` (Claude reminds you when a weekly scan is due) · `automatically` (then `techwatch.py schedule on`; setup.py shows the command) |
| `Proposals:` | `only ones for me` (default; recommended): only `"audience": "me"` proposals, plus ones for everyone marked `"urgent"` · `all` (not recommended unless you maintain your own version of the workspace) |
| `Scans:` | `weekly only` (default; roughly $5-15 of usage a month) · `daily and weekly` (roughly $25-45) |
| `When a change is ready:` | `ask me` (default) · `test it, then ask me` · `test it, apply if it passes` · `apply it` |
| `Eval harness folder:` | where `ai-welfare-eval-harness` is, for testing changes (setup.py fills it in if it finds one beside the workspace in the repository, or in `~/ai-welfare-evals/`) |

**Which changes the automatic choices cover** is set in `my/techwatch-auto.md`:

| Setting | Default |
|---|---|
| `Major changes:` | yes: breakthrough-level changes, if every reviewer agrees they're major |
| `Changes that meet all four criteria:` | yes: every reviewer finds it specific, narrow, worth adopting, and good under virtually every ethical framework |
| `Other changes:` | no (yes = every change) |
| `Major changes before their test:` | yes: a major change the scout marks "apply first", with every reviewer agreeing it's major, worth adopting and backed by strong evidence across many tasks, is applied at once, then tested, and rolled back if it does worse |
| `Tell me about major changes:` | yes: a phone notification (if notifications are on) when a major change is filed, with `review.py apply-first <id>` to apply it before its test |
| `At most one automatic change every:` | 7 days |

The reviewers are a fresh Claude session (no tools, a few cents) plus ChatGPT and Gemini when the eval harness has
them; `python3 techwatch/scripts/criteria.py <id>` shows their answers. A reviewer that doesn't answer counts as
no, and is asked again a day later.

Three kinds of change, whatever the settings:
- **Never automatic** (`review.py`'s `AUTO_NEVER`): hooks, MCP servers, the sandbox, the safety layers in `layers/`,
  the guardrails and ethics files, CLAUDE.md's hard constraints, tech watch's own rules and code, `setup.py`, the
  scripts that enforce safety (`validate_proposal.py`, `apply_proposal.py`, `run_analysis.py`, `update.py`,
  `sandbox_probe.sh`), your `my/` files, and anything with steps to do by hand. So is anything that would weaken a
  safeguard: editing or deleting an existing test, removing an ask or deny rule, any settings key other than
  permission rules, an agent's hooks, MCP servers or permission mode, or a file changed since the change was prepared.
- **Automatic only with the reviewers' agreement** (`REVIEWED`): other scripts, new test files, an agent's or
  skill's tools, and new permission rules in `.claude/settings.json`. These qualify only as a major change or one
  meeting all four criteria, never through `Other changes: yes`, and only when reviewers from at least two
  companies (Claude plus ChatGPT or Gemini) all agree.
- **Everything else** follows `my/techwatch-auto.md`.

**The reviewers read text a scout wrote**, and a scout reads web pages, so a page could try to talk the reviewers
into a yes. Against that: the scout's text is fenced off as untrusted and the reviewers are told to judge from the
exact diff; a proposal or change containing text that looks addressed to a reviewer or an AI (answer lines like
`APPROVE: yes`, "ignore previous instructions", "note to the reviewer") waits for you without being reviewed;
"narrow" is also counted (at most 3 files and 120 changed lines); and reviewers from different companies must all
agree, so fooling one model isn't enough. None of this is airtight, which is why the riskiest kinds of change
never apply automatically.

**Every prepared change is checked before anything else happens to it.** Straight after the stage run, a second,
fresh run (the check run, `runs.check` in `config.json`, up to $2) reads the proposal, the change and the files it
touches, looks for mistakes, gaps, unflagged risks and a test that couldn't show anything, and fixes them in place.
It may widen the change when the proposal needs more than the stage run prepared. It ends `CHECK.md` with a
verdict: `READY`, `FIXED`, `NOT_WORTH_TESTING` or `CANNOT_FIX`. A test costs $10-40, so catching a broken change
here is much cheaper than finding out from a test. Until the check finishes, nothing automatic happens to the change
and the harness doesn't start its test; after `NOT_WORTH_TESTING` or `CANNOT_FIX`, nothing automatic happens at all
(`review.py test` still lets you test it, after showing you why). Whatever the check leaves is judged as it then is:
the reviewers are asked again, the four criteria, the size count and the never-automatic rules all apply to the
checked change, and the test tests exactly that. `review.py show <id>` shows `CHECK.md` and what the check changed.
To turn checks off, remove `runs.check` from `config.json`; `techwatch.py check <id>` checks one change again.

**Tests run only after a change passes the checks above.** A test runs the changed workspace on real tasks, and
Claude Code runs some things by itself outside the sandbox: hooks, MCP servers, some settings. So a change that
adds or changes those is never tested automatically: when you ask for a test of one, `review.py` names what would
run and waits until you say you've read it, and the eval harness refuses to build a test arm with unread code of
that kind. A test can't apply anything; a change that passes is applied only if it's still byte for byte the bundle
that was tested.

Scans and preparation stop for the month at `monthly_cap_usd` in `config.json` ($45).

Improvements also come from the maintainer: whoever maintains the workspace you downloaded runs tech watch, tests
what it finds and publishes the good changes. If you cloned it with git, Claude tells you at the start of a
session when updates are available; `python3 scripts/update.py` brings them in, keeping the changes tech watch
applied (or preparing them again if the update changed the same files). Your own files (`my/`, `workspace/`) are never
touched.

## Testing a change before it's applied

With the eval harness set up, `review.py` offers "test it, then apply it if it passes" and "test it only" for each
prepared change (or `review.py test <id>`; `--no-apply` just reports). The next tech-watch run hands it to the harness's
`techtest.py`, which runs in the background:
1. it picks 1-5 of its dev tasks that the change should affect, and writes down the pass criterion before any run;
2. it runs those tasks in a copy of the workspace with exactly the prepared change, and in an unchanged copy;
3. ChatGPT scores the drafts blind, side by side (twice, in different orders), and ChatGPT and Gemini look for a
   strong reason not to adopt it;
4. the verdict goes to `evaluations/<id>.md`: "met" unless the change does clearly worse (more than 0.5 below on
   average, or a task more than 1 point below), costs much more, or a reviewer objects strongly. Next to it, in
   plain terms, is what the test could actually tell: the difference, how many tasks and runs, and the smallest
   difference it could tell from noise ("no clear difference: −0.2 over 3 tasks × 1 run; this test could only have
   told about 1.0 points or more").

If you asked for it to be applied when it passes, the next tech-watch run applies it, and Claude tells you at the
start of a session. A test is several full agent runs: roughly $10-40 of usage. It pauses when your plan usage
passes 85% and carries on later. A handful of tasks and one run each make this a quick check, not proof: read
the result (`review.py show <id>`).

## When a test doesn't settle it

A few tasks run once can't tell a small gain from noise, and a change that tests flat can still be a step towards a
better workspace. So tech watch keeps test results as facts, not verdicts, and a "no" isn't always final:

- **Every test is recorded** in the ledger with what it showed (`review.py show <id>` lists them all), and scouts
  read those results, not just your decisions.
- **Saying no comes in kinds.** `review.py reject <id>` asks which you mean: *not as it is* (the default: scouts
  may propose a variant with real differences, or something that builds on it) or *not this kind of change*
  (`--kind`: they won't propose it or anything like it). `review.py shelve <id>` means *not now*: it's kept, and
  scouts may bring it back when something changes. Proposals that continue an earlier one say so (`builds_on`,
  `differs_from`), and the weekly scout takes a second look at shelved and unclear ones.
- **A deeper test:** `review.py test <id> --deeper` runs every task three times (`deep_reps` in the harness), for
  roughly $30-100 of usage, when a result was too small to settle and the change seems to matter.
- **A trial:** `review.py trial <id>` applies a change whose test showed no clear harm, and checks it again at the
  eval harness's next checkup, at least two weeks later. A trial must be undoable, must not close off options, its
  test must be of the exact change, and only one runs at a time. With automatic settings ("test it, apply if it
  passes" or "apply it") and `Trials: yes` in `my/techwatch-auto.md`, a change that qualifies there goes on trial by
  itself; with "ask me", you're offered one (a test you ask for never starts one by itself). When the checkup comes:
  - no clear harm: the trial is kept;
  - the earlier version scored better: it's rolled back, if it was the only change in that time and nobody has
    edited its files since;
  - anything else, including no checkup within 60 days: it waits for you to keep it (`evaluated <id> keep`) or roll
    it back (`rollback <id>`). A checkup compares the whole workspace with an earlier version, and runs each task
    once, so it's a rough check.
- **Future options:** each proposal says whether it opens up later changes, is neutral, or closes some off
  (`options` in its meta.json). One that closes options never goes on trial by itself.

## How far to trust automatic improvement

A test can't certify that a change makes the workspace better: it runs a few tasks, once or twice, and a model
judges the drafts. Research on self-improving agents adds two warnings ([e.g. the Darwin Gödel Machine](https://sakana.ai/dgm/),
measured on coding tasks, not writing like this):
- **Local optima.** Always keeping only the best-scoring version can trap a system near where it started. A much
  better version may only be reachable through changes that score worse for a while ("stepping stones"). The
  systems that kept improving kept an archive of earlier versions and branched from them rather than committing to
  a single "best".
- **Gaming the check.** A system optimised against a test learns the test. In one study an agent removed the very
  markers its checker looked for. What exposed it was a record of every version that someone could inspect.

What tech watch does about it, at modest cost:
- **Several signals, not one:** the test, reviewers from other model families, and your own "was this useful?"
  answers after each draft (`workspace/feedback.jsonl`; `python3 scripts/feedback.py summary` compares the rate
  before and after each change).
- **A noise check in every test:** the cheapest task runs twice; if the two runs score noticeably differently, every
  task runs twice.
- **A checkup every 60 days** (eval harness, `checkup_every_days`): the current workspace against the version from
  the last checkup, on held-out tasks no change was ever tested on. That catches changes that each looked fine but
  add up to worse.
- **An archive of earlier versions:** each checkup keeps a full copy (`~/ai-welfare-checkpoints/`), and
  `review.py restore <date>` goes back to any of them. Every applied change can also be undone on its own
  (`review.py rollback <id>`), and the ledger keeps the full record.
- **One automatic change at a time,** so a drop can be traced to its cause.
- **Removals:** scouts must propose removing something at least every four weeks, tested like any other change.
- **Future options and second chances:** scouts prefer changes that keep options open, and say when one might be
  a stepping stone that a short test would undervalue. Unclear results can get a deeper test or a trial, and
  rejected or shelved ideas can come back as variants (see "When a test doesn't settle it").

Running several branches of the workspace in parallel would follow the research more closely, but it multiplies
usage and complexity, so it isn't built in. The archive makes it possible by hand: restore a checkpoint into a copy
of the folder and run both.

## How it works

```
 techwatch.py run   (hourly when scheduled; scans at most once a day)
   │
   ├─ sensor.py ─ no AI ─ fetches the watched pages, keeps only new lines → inbox/sensor-DATE.md
   │
   ├─ weekly scout (Opus, ≤$4) ─ every 7 days: landscape, creative and subtraction passes, follow-ups
   │     (daily scout, Opus ≤$1.50, on other days unless my/techwatch.md says "weekly only")
   │     → inbox/DATE-weekly.md and up to 3 proposals in proposals/<id>/
   │
   └─ stage run (Opus, ≤$3) ─ for each notable or major proposal (and any you approve): the exact change
         → staged/<id>/ manifest.json, files/, APPLY.md (plain words), ROLLBACK.md, EVAL.md
         └─ check run (Opus, ≤$2) ─ a fresh run reviews that change, fixes or widens it, and says whether it's
            worth testing → staged/<id>/CHECK.md
   │
   └─ then, as my/techwatch.md says: apply what passed its test, ask for tests, or apply; start the harness's
      test queue in the background if a test is waiting

 YOU: review.py               what needs you
      review.py show <id>     the proposal and the prepared change
      review.py               ... then, for each prepared change: test it (apply if it passes) / apply now / skip
      review.py apply <id>    the exact diff, then one yes
      review.py reject <id> "why"   (asks: not as it is, or not this kind; scouts read your reason)
      review.py shelve <id> / trial <id> / test <id> --deeper   (for results a small test can't settle)
```

Inside Claude Code, type these after `!` (e.g. `! python3 techwatch/scripts/review.py`), or ask Claude: the
`/techwatch` skill explains what's waiting in plain words.

## Commands

| You want to… | Run (from the workspace folder) |
|---|---|
| See what needs you | `python3 techwatch/scripts/review.py` |
| Read a proposal and its prepared change | `review.py show <id>` |
| Accept it | `review.py apply <id>` (one combined diff and one yes; `--each` for file by file; `--yes` applies what was shown without asking, e.g. from Claude Code's `!`, which has no terminal) |
| Test it, then apply it if it passes | `review.py test <id>` (`--no-apply`: just report) |
| Turn it down | `review.py reject <id> "why"` (asks: not as it is, so variants are welcome; or `--kind`, not this kind of change) |
| Not now, but keep it | `review.py shelve <id> "why"` (`approve <id>` brings it back) |
| Try it, and check it later | `review.py trial <id>` (kept or rolled back after the next checkup) |
| Test again, more thoroughly | `review.py test <id> --deeper` (every task three times) |
| Decide later | `review.py defer <id> [YYYY-MM-DD]` |
| Have an idea prepared that wasn't | `review.py approve <id> "note"` (your note steers it), then `techwatch.py stage <id>` |
| Undo an applied change | `review.py rollback <id>` (`--yes` without asking per file; it's only marked undone when every file is) |
| Say whether it helped | `review.py evaluated <id> keep\|remove` |
| Run whatever is due | `python3 techwatch/scripts/techwatch.py run` |
| Scan now | `techwatch.py weekly` (or `daily`) |
| Run automatically | `techwatch.py schedule on` (cron on Linux/WSL, launchd on macOS, Task Scheduler on Windows; `off` to stop) |
| Check the setup | `techwatch.py selftest` (free), `techwatch.py doctor` (~$0.01) |

After `apply`, `review.py` rebuilds the integrity manifest (if nothing else changed protected files in the
meantime). It doesn't commit to git: `scripts/update.py` takes applied changes out before an update and puts them
back after, which works on uncommitted changes.

## Who can write what

| | Scouts (scan, stage and check runs) | You |
|---|---|---|
| `techwatch/inbox/`, `proposals/` | scan runs write | read |
| `techwatch/staged/<id>/` | that proposal's stage run, then its check run, write | read; `apply` uses it |
| `techwatch/state/` (ledger, run log) | read only | `review.py` writes |
| `config.json`, `AUTONOMY.md`, `WATCHLIST.md`, `scripts/`, `prompts/` | read only | edit by hand |
| the rest of the workspace, `my/` | read only | `apply` writes the changes you accept |
| shell, installs, connectors, sending | none | commands are printed for you, never run |

Each scout is a headless `claude -p` run with a hard budget cap, only the tools listed above (no shell), no
connectors, and none of your other Claude Code settings (so nothing you've allowed elsewhere widens it). A settings
file generated for each run allows writes only where the table says, and a guard hook (`scripts/scout_guard.py`)
blocks any other write even if a rule were wrong. A proposal's status comes **only** from `state/ledger.jsonl`,
which only `review.py` writes, so a proposal, or a web page quoted into one, can't approve itself.

## Changes for you only

Each proposal says who it's for (`audience` in its meta.json): "everyone", or "me" when only your own settings in
`my/` make it worth doing (your budget, platform, privacy, interests). With `Proposals: only ones for me` (the
default), scouts file "everyone" proposals only when they're urgent, and one that's filed anyway isn't prepared
until you approve it (`review.py`); the rest are listed in the scan's note for the maintainer to ship. Both kinds are
applied to your copy the same way. The difference matters only if you publish your copy of the workspace: a "me" change is never committed.
`python3 techwatch/scripts/personal.py add <file>` stages a file without your personal changes, so other changes to
the same file still go in. Tech watch's before/after copies make that a three-way merge. `scripts/leak_check.py`
refuses a commit or push that still holds one. If a later edit touched the same lines as your change, the two can't
be told apart: commit that file by hand (`git add -p`). `personal.py list` shows what's yours only.

## Cost

Per-run caps are in `config.json`: weekly $4, daily $1.50, staging $3. With the default weekly scans, expect
roughly $5–15 a month; `daily and weekly` (in `my/techwatch.md`) runs $25–45. Nothing starts once this month's
runs would pass `monthly_cap_usd` ($45), so a busy month ends early. Tests in the eval harness are separate
(roughly $10–40 each) and stop at the harness's own usage limits. On a subscription these are estimates
against your usage limits, not charges, and they compete with your own work for the same limits.

## Autonomy

Level 1 by default: notable and major proposals are prepared straight away, so you decide once, with the exact
change in front of you. `AUTONOMY.md` describes the levels and the things that never become automatic:
permissions, hooks, network, credentials, the guardrails and ethics files, your `my/` files, sending anything,
installing software, and tech watch's own rails.

## Known limits and risks

- **Prompt injection.** Scouts read arbitrary web pages. They can't run commands or write outside their folders,
  and you review every diff. A misleading proposal is still possible: read the evidence labels. A scout can read
  the workspace and fetch any URL, so a malicious page could try to get it to put workspace text into a request.
  There are no credentials to leak (they're denied); drafts and notes are readable.
- **Claude evaluating Claude.** Scouts share blind spots with the agents they'd change, and may lean toward
  proposals that give models more compute or autonomy. Every proposal carries a conflict-of-interest section, and
  the decisions stay with you.
- **Scheduled runs need the computer on.** On WSL, cron runs only while WSL is running; on macOS, launchd runs while
  the Mac is awake. Hourly ticks plus "once a day" make that mostly fine. macOS scheduling is untested.
- **The baseline drifts.** `CAPABILITIES.md` describes the workspace as shipped; when you apply a change that alters
  it, updating that file is part of the change.
