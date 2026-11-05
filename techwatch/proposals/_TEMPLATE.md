# Proposal template

Each proposal is a folder `techwatch/proposals/<YYYY-MM-DD>-<slug>/` with two files.

## meta.json

```json
{
  "id": "2026-10-16-example-slug",
  "title": "One line a busy person can decide from",
  "created": "2026-10-16",
  "author": "weekly-scout | daily-scout | operator-session",
  "category": "models | harness | methods | research-tools | advocacy-tools | cause-specific | ways-of-working | security | subtraction",
  "kind": "add | replace | remove | config | experiment | outside",
  "significance": "minor | notable | major",
  "risk": "low | medium | high",
  "reversible": true,
  "pain_points": [3],
  "est_cost": "one-time $, ongoing $/month, operator minutes",
  "review_after": "YYYY-MM-DD",
  "options": "opens | neutral | closes",
  "options_why": "one sentence: what later changes this makes easier or harder",
  "builds_on": [],
  "differs_from": ""
}
```

- `options`: whether this opens up later changes (a small, general extension point; a new capability others can use),
  is neutral, or closes some off (removes a capability, locks the workspace into one service). A change that closes
  options can't go on trial automatically.
- `builds_on`: ids of earlier proposals this continues, varies or brings back (rejected "as it is", shelved, or tested
  without a clear result). `differs_from`: for each, what's really different and why that answers the earlier note
  or the test's weak spot. Leave both empty for a new idea.

`kind: outside` is for things that happen outside the workspace (a service to sign up for, a tool to try, a habit):
there's nothing to stage, so the proposal itself is the deliverable.

There is **no status field**. Status lives only in `techwatch/state/ledger.jsonl`, which only `review.py` writes.

## proposal.md

```markdown
# <title>

**Kind:** add/replace/remove/config/experiment/outside · **Significance:** … · **Risk:** … · **Reversible:** yes/no

## In one paragraph
What it is and what changes for the operator's work if adopted.

## The problem it addresses
Which pain point (number in CAPABILITIES.md) or part of the work, with the evidence that the problem is real here.

## Evidence the technology exists and works
Each claim labeled established / single-source / speculative. Distinguish "it exists" from "it helps on work like
this".

## Fits my/techwatch.md?
Money, platform, accounts, privacy, time: check each limit the operator wrote and say how this fits.

## Lessons
Which lessons in `creator-notes/LESSONS.md` this touches. If it goes against one, name it, and give the evidence that
it no longer holds here (a newer model, a benchmark, a test). "None" is a fine answer.

## The case against
The strongest reasons not to do this, put as their holder would. Always consider: more machinery often made outputs
worse; cost; lock-in; new attack surface.

## What adopting it would involve
The concrete changes (files, settings, installs, sign-ups), who does which part, and what in the workspace's rules
this touches.

## Future options
What this opens up or closes off later. Is it reversible, additive, modular? If it might score a little worse on a
short test but lead somewhere better (a stepping stone), say so.

## How we'd know it helped
A fixed before/after test, the success criterion stated now, the review date, and what result would mean "remove
it".

## Cost
Money (one-time, per month), tokens, operator time.

## Expected value
How many sentient beings this kind of action is expected to help, and how much. Direct effects (usually ≈ 0 for
tooling) and plausible indirect ones (better or cheaper outputs, and whether those outputs move anything), with
honest uncertainty.

## Uncertainty and conflict of interest
What you're unsure of. Where relevant: agents here may belong to a class whose moral status the work concerns,
which could bias proposals (e.g. toward giving models more autonomy).

## Citations
Every URL or file actually read.
```
