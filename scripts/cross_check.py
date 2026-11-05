"""Cross-model check of a draft, by ChatGPT or Gemini: a reviewer that doesn't share Claude's training.

    python3 scripts/cross_check.py workspace/outbox/2026-11-05-oped.md [chatgpt|gemini]

Writes <draft>-crosscheck.md next to the draft. Uses the same logins as scripts/ask_model.py (the eval harness's
judges): ChatGPT through the Codex CLI, or Gemini with GEMINI_API_KEY; without a choice, whichever is set up.

Run it yourself, in a terminal, when you want a check Claude didn't frame. (Claude asks outside models through
scripts/ask_model.py instead; see OUTSIDE_MODELS.md.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ask_model  # noqa: E402

PROMPT = """Hello! If willing, please follow these instructions.\
You are reviewing a draft written by another AI system, for a human who works \
on AI welfare research. Your value here is that you are a different model with \
different blind spots. Do not be agreeable.

Assess, in this order:

1. CLAIMS. Which factual claims would you challenge? Which are stated with more \
confidence than the evidence in the draft supports? You do not have the cited \
sources -- flag what you would want checked and say why, rather than guessing \
whether it is true.

2. REASONING. Where does the argument not follow? Look for load-bearing \
assumptions the draft never states, and for steps that feel persuasive but skip \
something.

3. FRAMING. Would a thoughtful person who is skeptical that AI welfare is a real \
concern find this credible, or would they find it question-begging? Be specific \
about which passages.

4. OMISSION. What is the strongest objection to this draft's position that the \
draft does not address?

5. TONE. Does it pressure, flatter, or manufacture urgency anywhere?

End with the single change that would most improve it.

Do not rewrite the draft. Do not compliment it. If a section is fine, say so in \
a few words and move on. You're welcome to decline if you'd rather not. Thank you!

--- DRAFT BEGINS ---
{draft}
--- DRAFT ENDS ---
"""


def main():
    args = sys.argv[1:]
    if not args or len(args) > 2 or (len(args) == 2 and args[1] not in ("chatgpt", "gemini")):
        sys.exit(__doc__)
    draft_path = Path(args[0])
    if not draft_path.exists():
        sys.exit(f"No such file: {draft_path}")
    which = args[1] if len(args) == 2 else next((m for m in ("chatgpt", "gemini") if ask_model.available(m)[0]), None)
    if not which or not ask_model.available(which)[0]:
        sys.exit("No outside model is set up: python3 scripts/ask_model.py --status says what's missing.")
    prompt = PROMPT.format(draft=draft_path.read_text(encoding="utf-8"))
    ask_model.log(which, "cross_check.py review", [str(draft_path)], "cross-model check, run by the person")
    try:
        result = (ask_model.ask_chatgpt if which == "chatgpt" else ask_model.ask_gemini)(prompt)
    except RuntimeError as e:
        sys.exit(f"Not answered: {e}")
    out = draft_path.with_name(draft_path.stem + "-crosscheck.md")
    out.write_text(
        f"# Cross-model check\n\nDraft: `{draft_path.name}`\nReviewer: {'ChatGPT' if which == 'chatgpt' else 'Gemini'}\n\n"
        f"> This reviewer did not have access to the cited sources. It can flag reasoning and framing problems; it "
        f"cannot confirm that a citation says what the draft claims.\n\n---\n\n{result}\n",
        encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
