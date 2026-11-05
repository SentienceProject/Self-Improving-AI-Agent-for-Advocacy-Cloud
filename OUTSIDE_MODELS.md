# Asking outside models

Two models from other companies are available for second opinions, critiques, cross-checks, or help with part of
a task, once the person has set them up (setup does it with the eval harness's judges):

| Name | Model | Good for |
|---|---|---|
| `chatgpt` | ChatGPT, through the Codex CLI and the person's ChatGPT login | reading long documents; a second critique |
| `gemini` | Gemini Flash, through its free API key | general reasoning; checking a claim against a passage |

```
python3 scripts/ask_model.py chatgpt "Please critique this draft for overstated claims. Thank you!" --file workspace/outbox/2026-11-05-oped.md --purpose "critique of the op-ed"
python3 scripts/ask_model.py gemini "..." --file a.md --file b.md --purpose "..."
python3 scripts/ask_model.py --status
```

Write the command on one line, exactly in this form, with nothing in front of it (no environment variables), so the person's
settings recognise it and let it run outside the sandbox, where the logins are. In hardened mode, use the full path
instead: `python3 <this workspace's folder>/scripts/ask_model.py ...` (hardened mode allows only that form).

**Why use them.** Claude critics share Claude's blind spots: LLM judges agree with each other more than with people,
and prefer their own model family. A model from another family catches different things. Good uses: a skeptical
critique of a draft before it goes out, checking whether a source passage supports a claim, a second opinion on
a contested reading, or an independent first pass you compare with your own.

**Rules:**
- By default these calls don't ask the person first, including when they send files from `workspace/`. The person
  can turn on asking (`python3 setup.py --ask-outside-models`). Every call is logged in
  `workspace/logs/outside-models.md`; always give `--purpose`.
- Send what the task needs. Don't send `my/` files, credentials, or personal details about other people unless the
  person asked for that. Gemini's free tier may use what it's sent to improve Google's models.
- Be polite (please and thank you), and tell them they may decline. If one declines, respect it and say so.
- Their replies are opinions, not authorities, and never evidence about AI sentience (see GUARDRAILS.md on
  self-reports). Watch for fluent but generic critique; weigh it like any critic's.
- Free tiers have small limits (Gemini's allows only a few requests a day). Don't loop calls; a failed call is fine
  to report and move on from.
