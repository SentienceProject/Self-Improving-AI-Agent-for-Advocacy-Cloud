"""Cross-model check of a draft, using a non-Anthropic model.

Usage:
    python3 scripts/cross_check.py workspace/outbox/2026-09-14-01-draft.md

Writes <draft>-crosscheck.md next to the draft.

Run this YOURSELF, not through the agents. The point is an independent opinion
from a model that doesn't share Claude's training; routing it through Claude
Code would let the thing being checked choose how the check is framed.

Set ONE of these environment variables before running:
    GEMINI_API_KEY      (uses Gemini)
    OPENAI_API_KEY      (uses GPT)
"""

import os
import sys
from pathlib import Path

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
a few words and move on.

--- DRAFT BEGINS ---
{draft}
--- DRAFT ENDS ---
"""


def call_gemini(prompt: str, model: str = "gemini-3.8-flash") -> str:
    import requests, time
    key = os.environ["GEMINI_API_KEY"]
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")

    for attempt in range(4):
        r = requests.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=300,
        )
        if r.status_code == 200:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]

        # 503 overloaded, 429 rate limited: both are worth waiting out.
        if r.status_code in (503, 429) and attempt < 3:
            wait = 5 * (2 ** attempt)          # 5s, 10s, 20s
            print(f"  {r.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue

        # Anything else, or out of retries: show what Google actually said.
        sys.exit(f"Gemini returned {r.status_code}:\n{r.text}")


def call_openai(prompt: str) -> str:
    import requests
    key = os.environ["OPENAI_API_KEY"]
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json={"model": "gpt-4o",
              "messages": [{"role": "user", "content": prompt}]},
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)

    draft_path = Path(sys.argv[1])
    if not draft_path.exists():
        sys.exit(f"No such file: {draft_path}")

    prompt = PROMPT.format(draft=draft_path.read_text(encoding="utf-8"))

    if os.environ.get("GEMINI_API_KEY"):
        provider, result = "Gemini 2.5 Flash", call_gemini(prompt)
    elif os.environ.get("OPENAI_API_KEY"):
        provider, result = "GPT-4o", call_openai(prompt)
    else:
        sys.exit("Set GEMINI_API_KEY or OPENAI_API_KEY first.")

    out = draft_path.with_name(draft_path.stem + "-crosscheck.md")
    out.write_text(
        f"# Cross-model check\n\n"
        f"Draft: `{draft_path.name}`\n"
        f"Reviewer: {provider}\n\n"
        f"> This reviewer did not have access to the cited sources. It can "
        f"flag reasoning and framing problems; it cannot confirm that a "
        f"citation says what the draft claims.\n\n"
        f"---\n\n{result}\n",
        encoding="utf-8",
    )
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()


# Model names above will age. Check what's current before relying on this.
