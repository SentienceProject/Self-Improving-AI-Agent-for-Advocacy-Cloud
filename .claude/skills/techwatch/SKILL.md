---
name: techwatch
description: Explain what tech watch has found and help the operator decide on it, or help them set up or adjust tech watch. Use when they ask about tech watch, new tools, updates, proposals or "what's new", or when the session-start note mentions tech watch.
---

# Tech watch, for the operator

Tech watch scans for new technology of any kind that could help this work, writes proposals, and prepares each
change as an exact diff. It runs on a schedule by default. What happens to a prepared change is the operator's
choice, set in `my/techwatch.md` ("When a change is ready": ask me / test it, then ask me / test it, apply if it passes / apply it), and
otherwise made by running `techwatch/scripts/review.py` in their own shell. You can't run it, and shouldn't try: its state is outside what you may write. Your part is to make the
decision easy.

## When they ask what's waiting

1. Read `techwatch/state/ledger.jsonl` (decisions so far), the proposals in `techwatch/proposals/` whose status
   isn't decided, and for each, `techwatch/staged/<id>/APPLY.md` and `CHECK.md` (a second run's review of the
   prepared change, ending in a verdict) if they exist. The newest note in `techwatch/inbox/` has context.
2. For each item, in plain words: what it is, what would change for them, cost, risk, and your honest view. If you
   disagree with a proposal, say so. Point out anything that touches permissions, hooks, the guardrails or the
   ethics files.
3. Give them the exact commands to run, each typed after `!` at the prompt:
   - `! python3 techwatch/scripts/review.py`: lists what's waiting. (Its one-by-one walk-through, and every yes/no
     question, needs a terminal: `!` has none, so the commands below take their choices as words, and `--yes`
     applies exactly what was just shown. In a terminal they ask instead);
   - `! python3 techwatch/scripts/review.py show <id>`: the full proposal, prepared change and any test result;
   - `! python3 techwatch/scripts/review.py test <id>`: test it with the eval harness, apply it if it passes
     (several full agent runs, roughly $10-40 of usage);
   - `! python3 techwatch/scripts/review.py apply <id>`: shows the exact diff and changes nothing; after they've
     read it, `! python3 techwatch/scripts/review.py apply <id> --yes` applies it. Never add `--yes` yourself
     without their go-ahead for that diff;
   - `! python3 techwatch/scripts/review.py reject <id> "why"`: no. It asks whether they mean "not as it is"
     (variants and follow-ups welcome; the default) or "not this kind of change" (`--kind`); scouts read both;
   - `! python3 techwatch/scripts/review.py shelve <id> "why"`: not now, kept for later;
   - for a test result that's too small to settle ("no clear difference"): `! python3 techwatch/scripts/review.py
     test <id> --deeper` (every task three times, roughly $30-100 of usage) or `! python3
     techwatch/scripts/review.py trial <id>` (applied now, kept or rolled back after the next checkup). Explain that
     "no clear difference" from a small test means the test couldn't tell, not that the change failed;
   - `! python3 techwatch/scripts/review.py apply-first <id>`: apply it as soon as it's prepared and checked, then
     test it, rolled back if the test shows it does worse (for a change they trust before its test, such as a
     clearly better new model);
   - `! python3 techwatch/scripts/review.py approve <id> "note"`: for an idea not prepared yet;
   - `! python3 techwatch/scripts/techwatch.py stage <id>`: prepare it now (Opus, up to $3 of usage).

## When they want to set it up or change it

- **Their settings** are in `my/techwatch.md` (offer to edit it; they'll approve the write):
  - `Tech watch runs:` `automatically` (the default; needs `! python3 techwatch/scripts/techwatch.py schedule on`,
    which setup.py runs; the session-start note says if it's missing), `when I start it`, or `off`;
  - `Scans:` `daily and weekly` (roughly $25-45 of usage a month) or `weekly only` ($5-15);
  - `When a change is ready:` `ask me`, `test it, apply if it passes`, `test it, then ask me`, or `apply it`. With the automatic choices,
    changes to settings, hooks, the guardrails and ethics files, tech watch's rules and `my/` still wait for them;
  - `Eval harness folder:` for testing changes.
  Which changes the automatic choices cover is in `my/techwatch-auto.md`: by default major changes and changes every
  reviewer finds specific, narrow, worth adopting and good under virtually every ethical framework, at most one a
  week. A major change with strong evidence across many tasks can be applied before its test and rolled back if
  the test shows it does worse (`Major changes before their test`). `! python3 techwatch/scripts/criteria.py <id>` shows the reviewers' answers for a change. Earlier versions
  are kept by the harness's checkups: `! python3 techwatch/scripts/review.py checkpoints` lists them and
  `review.py restore <date>` goes back to one.
  Updates also come from the workspace's maintainer: `! python3 scripts/update.py` brings them (it keeps their own and tech watch's changes), and the session-start note says when
  there are some.
- **Their limits** go in `my/techwatch.md`: money, computer, accounts, privacy, time, never-propose. Offer to fill it
  in with them. Scouts apply those limits and no others beyond the guardrails.
- **Running a scan now:** `! python3 techwatch/scripts/techwatch.py run` does what's due, then prepares notable and
  major proposals (up to $3 each). Scans and preparation stop at `monthly_cap_usd` in `techwatch/config.json` ($45); eval-harness tests and the reviewers' checks are counted separately.
- **Stopping the schedule:** `! python3 techwatch/scripts/techwatch.py schedule off`.
- **Settings they may want to change** in `techwatch/config.json` (they edit it themselves): models and budgets per run, `monthly_cap_usd`, `autonomy_level`
  (`techwatch/AUTONOMY.md` explains the levels).
