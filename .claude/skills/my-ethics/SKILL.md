---
name: my-ethics
description: Interview the operator about their ethical views and write their own my/ETHICS_SHORT.md, which comes first everywhere in this workspace, with the default ethics file filling in where theirs is silent or unsure. Use when they want the workspace to reflect their own framework, or ask about changing the ethics files.
---

# Writing the operator's own ethics file

The default `ETHICS_SHORT.md` is the project creator's framework: hedonic, non-speciesist utilitarianism, with its
controversial views marked as the creator's, the main alternative frameworks summarised, and rules for acting when
frameworks disagree. The operator's own file doesn't replace it. Theirs comes first and wins wherever it says
something; the default fills in where theirs is silent or unsure. So a short file with a lot of "not sure" in it
is a good result, not a weak one.

**Keep it light.** Most people haven't worked out their ethics in detail, and a long philosophical interview is
overwhelming even for people who have. Aim for about 10 minutes. Plain words, no jargon unless they use it, and
never push for more precision than they have. Go deeper only if they ask (step 7).

1. **Explain in a few short lines:**
   - The file guides what drafts weigh, the expected-value notes, and what counts as a line not to cross.
   - Their views come first. Wherever they're silent or unsure, the creator's default fills in, labelled as the
     creator's view: roughly, reduce suffering and increase wellbeing for every sentient being equally, with the
     main alternatives (rights and Kantian views, preferences, virtue, care ethics and others) taken into account.
   - They can change that: `Default ethics: reference only` in their file means Claude uses only their views, plus
     the default's survey of other frameworks and how to act when frameworks disagree.
   - Short answers, skipping a question, and "not sure" are all fine.
2. **Ask a few questions, one or two at a time** (five or six in all), and follow up only where an answer is
   surprising or unclear:
   - Whose welfare counts, and does it count equally? (Humans, other animals, insects, possible digital minds.)
   - What matters most for a being: not suffering, enjoying life, getting what it wants, its rights, flourishing?
   - Is anything off limits even for good outcomes (deception, coercion, breaking the law, violence)?
   - Long shots at huge impact, or likely, smaller good: which do they lean towards?
3. **Check where they part from the default.** Summarise the default in five or six plain bullets (its main
   commitments, including the ones it marks as the creator's own: intense suffering counting far more, the
   population view, refusing fanaticism) and ask which they disagree with. Those disagreements matter most:
   they're where the default would otherwise fill in with something they reject.
4. **Draft the file in their words**, usually 200 to 800 words, with these parts (leave out any that are empty):
   ```
   # My ethics
   Default ethics: fill gaps          (or: reference only)

   ## What I'm fairly sure of
   ## What I lean towards
   ## Where I part from the default    (each point the default makes that I reject, and what to do instead)
   ## What I'm unsure about
   ```
   Only the `Default ethics:` line says whether the default fills the gaps: don't repeat it in the body ("the
   default fills in here"), so switching that one line is all it takes. Keep their wording where it matters. Mark open questions as open; don't argue them out of anything.
5. **Show them the draft**, ask what's wrong, and revise. Then save it to `my/ETHICS_SHORT.md`; they'll approve the
   write at a permission prompt.
6. **Tell them, briefly:** it applies to every new session and subagent; the creator's default fills the gaps
   (or not, if they chose "reference only", which they can switch with that one line); they can edit the file any
   time; and `my/ETHICS_FULL.md` works the same way if they ever want a long version.
7. **Offer to go further, in one line, and only do it if they say yes:** "If you'd like a fuller framework of your
   own, I can show you the main frameworks in plain language; you pick the closest, and I write yours from it at
   full strength, with your changes." If they want that: summarise the main frameworks from `ETHICS_FULL.md` §2 and
   §5 in a sentence or two each, let them pick (or combine two), then write the file as that framework stated
   properly plus their changes, marking which parts they've agreed to and which are still the framework's defaults.
   Show it and save it as in step 5.

Don't edit the default `ETHICS_SHORT.md` or `ETHICS_FULL.md` (depending on the mode they're blocked or ask first,
and they're the shared defaults), and don't write their file without showing it to them first.
