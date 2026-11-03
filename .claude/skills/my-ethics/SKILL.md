---
name: my-ethics
description: Interview the operator about their ethical views and write their own my/ETHICS_SHORT.md, which then replaces the default ethics file everywhere in this workspace. Use when they want the workspace to reflect their own framework, or ask about changing the ethics files.
---

# Writing the operator's own ethics file

The default `ETHICS_SHORT.md` is one person's framework (hedonic, non-speciesist utilitarianism, with specific views
on intensity and fanaticism). Every draft is shaped by whichever ethics file applies, so it should be the
operator's own. This isn't about steering them toward any view, the default's included. Your job is to help
them say clearly what *they* think, and to show where it leads.

1. **Explain in two lines** what the file does (it guides expected-value notes, what drafts weigh, and what counts
   as a line not to cross) and that the result goes in `my/ETHICS_SHORT.md`, which updates never touch.
2. **Ask, one or two questions at a time**, and follow up where an answer is surprising or vague:
   - Whose welfare counts, and does it count equally? (Humans, other animals, insects, possible digital minds.)
   - What matters for a being: pleasure and suffering, getting what it wants, rights, flourishing, relationships?
   - Are some things off limits even for good outcomes (deception, coercion, breaking the law, violence)?
   - How should tiny chances of huge stakes be weighed against likely, smaller good?
   - How much should near and certain help count against distant or speculative help?
   - What makes them uneasy in the default file, if they've read it? What's missing?
3. **Read the default `ETHICS_SHORT.md`** and keep its structure where it helps, so other parts of the workspace
   that refer to its sections still make sense.
4. **Draft the file in their words.** Mark which claims are theirs and which are open questions to them. Note
   where reasonable people disagree, without arguing them out of it. Keep it about as long as the default (about
   1,000 words); a short, honest file beats a long one they don't recognize.
5. **Show them the draft**, ask what's wrong, and revise. Then save it to `my/ETHICS_SHORT.md`. They'll approve the
   write at a permission prompt.
6. Tell them: it now applies to every new session and subagent; they can edit it any time; and if they want a long
   version for hard cases, `my/ETHICS_FULL.md` works the same way.

Don't edit the default `ETHICS_SHORT.md` or `ETHICS_FULL.md` (depending on the mode they're blocked or ask first, and they're the shared defaults), and don't write their
file without showing it to them first.
