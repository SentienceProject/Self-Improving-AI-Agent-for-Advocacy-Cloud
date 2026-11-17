# Asking outside models

Models from other companies are available for second opinions, critiques, cross-checks, or help with part of a
task, once the person has set them up (setup does ChatGPT and Gemini with the eval harness's judges; OpenRouter and
Groq need `OPENROUTER_API_KEY=...` and `GROQ_API_KEY=...` in this workspace's `.env`):

| Name | Model | Good for |
|---|---|---|
| `chatgpt` | ChatGPT, through the Codex CLI and the person's ChatGPT login | reading long documents; a second critique |
| `gemini` | Gemini 4 Hydrogen, through its free API key | general reasoning; checking a claim against a passage |
| `openrouter` | any of hundreds of models, paid from the person's prepaid OpenRouter credits | picking the model that suits the job |
| `groq` | a few very fast open models, free within Groq's rate limits (paid only if billing is added there) | quick, short checks at no cost |

```
python3 <this workspace's folder>/scripts/ask_model.py chatgpt "Please critique this draft for overstated claims. Thank you!" --file workspace/outbox/2026-11-05-oped.md --purpose "critique of the op-ed"
python3 <this workspace's folder>/scripts/ask_model.py gemini "..." --file a.md --file b.md --purpose "..."
python3 <this workspace's folder>/scripts/ask_model.py gemini "..." --video <public YouTube link> [--start 1:00 --end 5:00] --purpose "..."
python3 <this workspace's folder>/scripts/ask_model.py --models deepseek
python3 <this workspace's folder>/scripts/ask_model.py openrouter --model <id from --models> "..." --file a.md --purpose "..."
python3 <this workspace's folder>/scripts/ask_model.py groq --model <id from --models> "..." --purpose "..."
python3 <this workspace's folder>/scripts/ask_model.py groq --model <speech-to-text id> --audio workspace/videos/clip.mp3 --purpose "..."
python3 <this workspace's folder>/scripts/ask_model.py --status
```

**Choosing an OpenRouter or Groq model.** Models change fast, so nothing here names one: look at what's current with
`--models <words>` (e.g. `--models openai`, `--models google`, `--models deepseek`; free, calls no model). It lists
Groq's few models first, then OpenRouter's, newest first, with prices per million tokens. OpenRouter ids ending in
`:free` cost nothing (they draw on a free daily allowance); every other OpenRouter id is paid from the person's
credits. Groq is free within its rate limits unless the person added billing there (its listed prices are what a
paid plan would charge). Pick for the job:
- *Groq for short, quick checks:* very fast and free, but its free tier allows only about 8,000 tokens a minute, so
  a request (prompt plus files plus reply) must stay well under that, roughly 5,000 words of input at most. A larger
  request is refused with a "Request too large" error: use an OpenRouter model instead.
- *Free first, when it's enough:* for quick, low-stakes questions about material that isn't private (public sources,
  general reasoning, a first rough check), try a capable `:free` model (`--models free`; prefer the larger ones over
  small or "preview" ones). If it's busy, fails, or its answer is thin, move to a cheap paid model rather than retry.
  Their providers may log or train on what's sent, so don't send drafts or other private material to `:free` models
  unless the person's profile (`my/profile.md`) says that's fine. For short material, Groq (above) is another free
  option.
- *A different perspective:* a strong model from a family not yet asked in this task (OpenAI, Google, xAI,
  DeepSeek, Qwen, Moonshot/Kimi, Mistral, Z.ai/GLM, Meta). Two different families beat two calls to one.
- *Quick or mechanical checks* (does this passage say X?, list the claims in this draft): a cheap "flash"/"mini"
  model, usually well under $1 per million output tokens.
- *Hard reasoning or a critique that matters:* a provider's current top model. A few dollars per million tokens is
  normal; a long critique still costs only cents.
- On OpenRouter, avoid `:batch` models (made for bulk jobs, not single questions).
- `--models` lists chat models. Groq also has speech-to-text (Whisper), text-to-speech and safety-classifier models:
  add `--all-models` to see them (a search that matches no chat model shows them anyway).
- Paid calls are cheap but not free: a long critique from a top model is a few cents, a cheap model's answer a
  tenth of a cent. Spend where it improves the work.

**Transcribing audio.** Groq's speech-to-text models turn a recording into text for free within the free tier's
limits: `groq --model <id> --audio <file>` (flac, mp3, mp4, m4a, ogg, wav or webm, up to 25 MB; cut a longer
recording into pieces). No prompt is needed. One may be given, but it isn't read as an instruction: the model treats it
as text that came before the audio, so use it only for names and spellings it should get right. The plain
`whisper-large-v3` is more accurate than the `-turbo` one; prefer it unless speed matters. Add `--timestamps` to
start each line with its time in the recording (`[1:05-1:12] ...`), e.g. to find where to cut a clip. Give the
language spoken with `--language en` (or `fr`, `de`...): left to guess, Whisper sometimes picks the wrong one and
returns a translation. The transcript is the recording's words only. For a whole video, the `/video` skill does
the cutting and joining. To ask questions about audio instead, send it to `gemini` with `--audio` (up to
14 MB); for a public YouTube video, `gemini --video <link>`, optionally with `--start`/`--end` for a clip.

**Cost.** OpenRouter is paid from the person's credits, so spend where it helps the task and not otherwise; Groq's
free tier costs nothing. Replies
aren't cut short in practice: the 32,000-token ceiling (`--max-tokens`) only stops runaways, and if a reply does hit
it the script says so. Models above $20 per million output tokens are refused unless you add `--allow-pricey`; use
it only when the task needs that model. Each OpenRouter or Groq call prints its token counts and cost. Ask once with
everything the model needs rather than several thin calls.

Use the full path (e.g. `python3 /home/you/ai-welfare-workspace/scripts/ask_model.py ...`) and write the command on
one line, with nothing in front of it: no `cd ...;`, no `&&`, no environment variables. Only that exact form is let
out of the sandbox, where the logins are; any other form runs inside it, where the models look "not set up" even
when they are. If a call says it may have run in the sandbox, run it again in that form.

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
- Free tiers have small limits (Gemini's allows only a few requests a day; Groq's, small requests), and OpenRouter
  calls cost the person money. Don't loop calls; a failed call is fine to report and move on from.
- If OpenRouter or Groq says a model doesn't exist, it was probably renamed or retired: check `--models` and pick
  again.
