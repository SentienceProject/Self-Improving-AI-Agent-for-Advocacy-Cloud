#!/usr/bin/env python3
"""Ask an outside model (ChatGPT, Gemini, or a model on OpenRouter or Groq) one question: second opinions,
critiques, cross-checks, or help with a task. Claude uses this during tasks; see OUTSIDE_MODELS.md for when and how.

    python3 scripts/ask_model.py chatgpt "Please critique this draft. Thank you!" --file workspace/outbox/x.md
    python3 scripts/ask_model.py gemini  "..." --file a.md --file b.md --purpose "fact check of the op-ed"
    python3 scripts/ask_model.py gemini  "..." --video <public YouTube link> [--start 0:00 --end 10:00] --purpose "..."
    python3 scripts/ask_model.py gemini  "..." --audio workspace/videos/clip.mp3 --purpose "..."   (Gemini only)
    python3 scripts/ask_model.py openrouter --model <id> "..." --purpose "..."
    python3 scripts/ask_model.py groq --model <id> "..." --purpose "..."
    python3 scripts/ask_model.py --models [words]   Groq's and OpenRouter's current models and prices (calls no model)
    python3 scripts/ask_model.py --status        which models are set up (calls nothing)

It uses the same logins as the eval harness's judges (python3 setup.py, or the harness's setup_judges.py):
  - ChatGPT through the Codex CLI, signed in with your ChatGPT account (a free account works): `codex login`.
  - Gemini through its API, with a free key from https://aistudio.google.com/apikey, which setup saves in this
    workspace's .env file (GEMINI_API_KEY=...). A GEMINI_API_KEY environment variable works too.
  - OpenRouter (paid, prepaid credits; hundreds of models from many companies), with a key from
    https://openrouter.ai/keys in this workspace's .env (OPENROUTER_API_KEY=...) or the environment. There's no
    default model on purpose: models change fast, so the caller picks one from --models each time. Costs are kept
    down by a ceiling on reply length that only stops runaways (--max-tokens), a refusal of models priced above
    PRICEY_PER_M per million output tokens unless --allow-pricey, and the cost of each call printed and logged.
  - Groq (fast; a free tier with daily rate limits, and paid only if you add billing there), with a key from
    https://console.groq.com/keys in this workspace's .env (GROQ_API_KEY=...) or the environment. It works like
    OpenRouter (same flags, no default model); its few models are listed first by --models.
This script runs outside Claude Code's sandbox, so it can use those logins; Claude itself can't read them: .env is
git-ignored, and the workspace's settings keep Claude's file tools and sandboxed commands out of it. The key is never
put in Claude's environment, and files named .env* can't be sent with --file.

By default Claude may run it without asking you, files from workspace/ included (setup says so). To be asked before
every call: python3 setup.py --ask-outside-models. To go back: python3 setup.py --no-ask-outside-models.
Every call is logged in workspace/logs/outside-models.md: when, which model, why, what was sent (a summary and the
file names). Codex runs in an empty folder, read-only, so ChatGPT sees only what's sent.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
LOG = HERE / "workspace" / "logs" / "outside-models.md"
ENV_FILE = HERE / ".env"   # GEMINI_API_KEY=... (setup writes it; git-ignored; Claude can't read it)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-4-hydrogen")
# Thinking level sent with every Gemini call, so a change to Google's default can't quietly change the critic
# (low | medium | high | xhigh; "" = the model's own default). Hydrogen's default measured close to high.
GEMINI_THINKING = os.environ.get("GEMINI_THINKING", "high")
MAX_FILE_BYTES = 400_000
MAX_AUDIO_BYTES = 14_000_000   # sent inline (base64), which keeps a request under Gemini's inline size limit
AUDIO_TYPES = {".mp3": "audio/mp3", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
               ".opus": "audio/ogg", ".aac": "audio/aac", ".aiff": "audio/aiff", ".m4a": "audio/mp4"}
APIS = {   # OpenAI-style chat APIs: name -> (base URL, key variable, name shown to people)
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OpenRouter"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "Groq"),
}
KEY_PAGES = {"openrouter": "https://openrouter.ai/keys", "groq": "https://console.groq.com/keys"}
OPENROUTER_MAX_TOKENS = 32_000   # a ceiling against runaway replies, not a length budget: real replies are far shorter
PRICEY_PER_M = 20.0              # USD per million output tokens; above this needs --allow-pricey


def node_on_path():
    """Windows: Node.js installed after this window opened isn't on its PATH yet; the Codex CLI needs it."""
    if os.name != "nt":
        return
    have = os.environ.get("PATH", "").lower().split(os.pathsep)
    for d in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs",
              Path(os.environ.get("APPDATA", "~")) / "npm"):
        if d.is_dir() and str(d).lower() not in have:
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


node_on_path()


def codex():
    for p in (shutil.which("codex"), Path.home() / ".npm-global" / "bin" / "codex",
              Path(os.environ.get("APPDATA", "~")) / "npm" / "codex.cmd"):
        if p and Path(p).exists():
            return str(p)
    return None


def env_file():
    """(the .env file's settings, whether it exists but couldn't be read). Unreadable means this is running inside
    Claude Code's sandbox, which keeps sandboxed commands out of .env."""
    try:
        text = ENV_FILE.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, False
    except OSError:
        return {}, True
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.removeprefix("export ").split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out, False


def gemini_key():
    return env_file()[0].get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""


def api_key(api):
    var = APIS[api][1]
    return env_file()[0].get(var) or os.environ.get(var) or ""


def sandboxed():
    """Signs that this is running inside Claude Code's sandbox, where the logins can't be reached."""
    if env_file()[1]:
        return True
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    return "localhost" in proxy or "127.0.0.1" in proxy


def sandbox_hint():
    """For Claude: the run-it-outside-the-sandbox form, when this call looks like it ran sandboxed. Only the exact
    full-path command is let out of the sandbox (in hardened mode); 'cd ...;' or a relative path keeps it in."""
    if not (sandboxed() or not os.path.isabs(sys.argv[0])):
        return ""
    return (f"\n(If you're Claude: this may have run inside the sandbox, where the logins can't be reached. Run it "
            f"again exactly as\n  python3 {HERE / 'scripts' / 'ask_model.py'} ...\non one line, with nothing "
            "before it: no cd, no &&, no environment variables.)")


def available(which):
    """(True, "") or (False, what to do)."""
    if which == "chatgpt":
        c = codex()
        if not c:
            return False, "the Codex CLI isn't installed (npm install -g @openai/codex, then codex login)"
        try:
            r = subprocess.run([c, "login", "status"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False, "the Codex CLI didn't answer" + sandbox_hint()
        if r.returncode == 0:
            return True, ""
        hint = sandbox_hint()
        return False, ("Codex couldn't confirm a sign-in" + hint) if hint else "Codex isn't signed in (run: codex login)"
    if which == "gemini":
        if gemini_key():
            return True, ""
        if sandbox_hint():
            return False, "no Gemini key found from here" + sandbox_hint()
        return False, ("no Gemini key yet: run python3 setup.py, which saves a free key "
                       "(https://aistudio.google.com/apikey) in this workspace's .env")
    if which in APIS:
        base, var, name = APIS[which]
        if api_key(which):
            return True, ""
        if sandbox_hint():
            return False, f"no {name} key found from here" + sandbox_hint()
        return False, f"no {name} key yet: put {var}=... (from {KEY_PAGES[which]}) in this workspace's .env"
    return False, f"unknown model {which!r} (chatgpt, gemini, {' or '.join(APIS)})"


def api_models(api):
    """The API's current chat models (listing costs nothing). OpenRouter's list is public; Groq's needs the key."""
    headers = {"User-Agent": "ask_model.py"}   # Groq refuses Python's default one
    key = api_key(api)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{APIS[api][0]}/models", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)["data"]
    if api == "groq":   # Groq also serves speech, transcription and prompt-guard models; keep the chat ones
        data = [m for m in data if m.get("active", True) and "text" in (m.get("output_modalities") or ["text"])
                and "text" in (m.get("input_modalities") or ["text"]) and "guard" not in m["id"]]
    return data


def per_million(m, kind):
    try:
        return float(m.get("pricing", {}).get(kind) or 0) * 1e6
    except ValueError:
        return 0.0


def list_models(words):
    """Print each set-up API's current models matching all of `words`, newest first, with prices and context size.
    Groq comes first: its list is short and its free tier costs nothing."""
    words = [w.lower() for w in words]
    for api in ("groq", "openrouter"):
        name = APIS[api][2]
        if api == "groq" and not api_key(api):
            print(f"== {name}: not set up (no {APIS[api][1]}), skipped\n")
            continue
        try:
            models = api_models(api)
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            print(f"== {name}: couldn't fetch its model list: {e!r}{sandbox_hint()}\n")
            continue
        rows = [m for m in models if all(w in (m["id"] + " " + (m.get("name") or "")).lower() for w in words)]
        rows.sort(key=lambda m: -m.get("created", 0))
        print(f"== {name} (use: {api} --model <id>)"
              + ("; free tier: no charge, within daily rate limits, unless billing was added at Groq. Prices are "
                 "what a paid plan would charge." if api == "groq" else "; paid from credits, except :free ids."))
        for m in rows[:60]:
            print(f"{m['id']:55} in ${per_million(m, 'prompt'):6.2f}/M  out ${per_million(m, 'completion'):6.2f}/M  "
                  f"context {m.get('context_length') or m.get('context_window') or '?'}")
        more = " (showing the newest 60; add words to narrow it)" if len(rows) > 60 else ""
        print(f"{len(rows)} models{more}. Prices are USD per million tokens.\n")


def log(model, prompt, files, purpose):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists():
        LOG.write_text("# Outside models log\n\nEvery question sent to an outside model (ChatGPT, Gemini, "
                       "OpenRouter).\n\n"
                       "| Date and time | Model | Purpose | Prompt (start) | Files |\n|---|---|---|---|---|\n",
                       encoding="utf-8")
    clean = lambda t, n: " ".join(str(t).split()).replace("|", "/")[:n]
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"| {datetime.now().astimezone():%Y-%m-%d %H:%M %Z} | {model} | {clean(purpose, 120)} | "
                f"{clean(prompt, 200)} | {clean(', '.join(files), 300) or '-'} |\n")


def ask_chatgpt(prompt, timeout=900):
    with tempfile.TemporaryDirectory(prefix="ask-chatgpt-") as d:
        out = Path(d) / "reply.md"
        r = subprocess.run([codex(), "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-o",
                            str(out), "-"], input=prompt, cwd=d, capture_output=True, text=True, timeout=timeout)
        if r.returncode:
            raise RuntimeError(f"ChatGPT (Codex) failed: {r.stderr.strip()[-800:]}")
        reply = out.read_text(encoding="utf-8").strip() if out.exists() else r.stdout.strip()
    if not reply:
        raise RuntimeError("ChatGPT returned an empty reply")
    return reply


def seconds(t):
    """'90', '1:30' or '1:01:30' -> seconds."""
    n = 0
    for piece in str(t).strip().split(":"):
        if not piece.isdigit():
            raise ValueError(f"not a time: {t!r} (use seconds, m:ss or h:mm:ss)")
        n = n * 60 + int(piece)
    return n


def media_parts(video, start, end, audio):
    """Gemini parts for a YouTube link (optionally a clip of it) and/or a local audio file."""
    import base64
    parts = []
    if video:
        if not video.startswith(("https://www.youtube.com/watch?v=", "https://youtu.be/", "https://youtube.com/watch?v=")):
            raise ValueError("--video takes a public YouTube link (https://www.youtube.com/watch?v=... or youtu.be/...)")
        part = {"file_data": {"file_uri": video}}
        clip = {k: f"{seconds(v)}s" for k, v in (("start_offset", start), ("end_offset", end)) if v}
        if clip:
            part["video_metadata"] = clip
        parts.append(part)
    if audio:
        p = (HERE / audio) if not Path(audio).is_absolute() else Path(audio)
        kind = AUDIO_TYPES.get(p.suffix.lower())
        if not kind:
            raise ValueError(f"--audio takes {', '.join(AUDIO_TYPES)} files")
        if p.stat().st_size > MAX_AUDIO_BYTES:
            raise ValueError(f"{audio} is {p.stat().st_size / 1e6:.0f} MB; the limit is {MAX_AUDIO_BYTES / 1e6:.0f} MB. "
                             "Cut a shorter clip, or a lower bitrate (about 10 minutes of mp3 fits).")
        parts.append({"inline_data": {"mime_type": kind, "data": base64.b64encode(p.read_bytes()).decode()}})
    return parts


def ask_gemini(prompt, timeout=300, media=()):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        data=json.dumps({"contents": [{"parts": [*media, {"text": prompt}]}],
                        **({"generationConfig": {"thinkingConfig": {"thinkingLevel": GEMINI_THINKING}}}
                            if GEMINI_THINKING else {})}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": gemini_key()})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(15 * 2 ** attempt)   # busy or rate-limited: wait a little (free tiers are small)
                continue
            raise RuntimeError(f"Gemini failed: HTTP {e.code} {e.read().decode()[:500]}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(15 * 2 ** attempt)
                continue
            raise RuntimeError(f"Gemini failed: {e!r}")
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini gave no answer: {json.dumps(data)[:500]}")
    reply = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not reply:
        raise RuntimeError("Gemini returned an empty reply")
    return reply


def model_limit(m):
    """The most output tokens the model allows, if the API says."""
    lim = m.get("max_completion_tokens") or (m.get("top_provider") or {}).get("max_completion_tokens")
    return lim if isinstance(lim, int) and lim > 0 else None


def check_model(api, model_id, allow_pricey):
    """(problem, model info): problem is None if `model_id` may be used. Checks it exists (ids change fast) and
    isn't pricey."""
    name = APIS[api][2]
    try:
        models = {m["id"]: m for m in api_models(api)}
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
        return f"couldn't fetch {name}'s model list to check {model_id!r}: {e!r}", {}
    m = models.get(model_id)
    if not m:
        return (f"{name} has no chat model {model_id!r} now (models change often). See the current ones with: "
                f"python3 {HERE / 'scripts' / 'ask_model.py'} --models <words>"), {}
    out = per_million(m, "completion")
    if out > PRICEY_PER_M and not allow_pricey:   # Groq's free tier charges nothing, but billing may be added there
        return (f"{model_id} costs ${out:.2f} per million output tokens, above the ${PRICEY_PER_M:.0f} line. Pick a "
                "cheaper model, or add --allow-pricey if this task really needs it."), m
    return None, m


def ask_api(api, prompt, model_id, max_tokens, timeout=900):
    """(reply, note): note says what it cost and whether the reply was cut short."""
    base, _, name = APIS[api]
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps({"model": model_id, "max_tokens": max_tokens,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key(api)}",
                 "User-Agent": "ask_model.py"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(10 * 2 ** attempt)
                continue
            raise RuntimeError(f"{name} failed: HTTP {e.code} {e.read().decode()[:500]}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 2:
                time.sleep(10 * 2 ** attempt)
                continue
            raise RuntimeError(f"{name} failed: {e!r}")
    if data.get("error"):
        raise RuntimeError(f"{name} failed: {json.dumps(data['error'])[:500]}")
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError):
        raise RuntimeError(f"{name} gave no answer: {json.dumps(data)[:500]}")
    reply = (choice.get("message", {}).get("content") or "").strip()
    usage = data.get("usage", {})
    cost = usage.get("cost")
    note = (f"[{api} {model_id}: {usage.get('prompt_tokens', '?')} tokens in, {usage.get('completion_tokens', '?')} "
            "out" + (f", ${cost:.4f}" if isinstance(cost, (int, float)) else "")
            + (", free tier" if api == "groq" else "") + "]")
    if choice.get("finish_reason") == "length":
        note += (f"\n[The reply was cut off at the {max_tokens}-token ceiling. If you need the rest, ask again with a "
                 "higher --max-tokens or a narrower question.]")
    if not reply:
        raise RuntimeError(f"{name} returned an empty reply " + note)
    return reply, note


def build_prompt(prompt, files):
    parts = [prompt]
    for f in files:
        p = (HERE / f) if not Path(f).is_absolute() else Path(f)
        data = p.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", "replace")
        parts.append(f"\n\n=== FILE: {Path(f).name} ===\n{data}")
    return "".join(parts)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    names = ["chatgpt", "gemini", *APIS]
    ap.add_argument("model", nargs="?", choices=names)
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--file", action="append", default=[], help="a file to include (repeat for several)")
    ap.add_argument("--purpose", default="", help="why, for the log")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--model", dest="model_id", help="openrouter/groq: the model id (see --models)")
    ap.add_argument("--models", nargs="*", metavar="WORD",
                    help="list Groq's and OpenRouter's current models matching the words")
    ap.add_argument("--max-tokens", type=int, default=OPENROUTER_MAX_TOKENS,
                    help=f"openrouter/groq: ceiling on the reply, thinking included (default {OPENROUTER_MAX_TOKENS}, "
                         "or the model's own maximum if lower)")
    ap.add_argument("--allow-pricey", action="store_true",
                    help=f"openrouter/groq: allow models above ${PRICEY_PER_M:.0f} per million output tokens")
    ap.add_argument("--log", help="log to this file instead of workspace/logs/outside-models.md")
    ap.add_argument("--video", help="gemini: a public YouTube link for Gemini to watch")
    ap.add_argument("--start", help="gemini --video: start of the clip (seconds, m:ss or h:mm:ss)")
    ap.add_argument("--end", help="gemini --video: end of the clip (seconds, m:ss or h:mm:ss)")
    ap.add_argument("--audio", help=f"gemini: a local audio file to send ({', '.join(AUDIO_TYPES)}; "
                                    f"up to {MAX_AUDIO_BYTES // 1_000_000} MB)")
    a = ap.parse_args(argv)
    if a.log:
        global LOG
        LOG = Path(a.log).expanduser().resolve()
    if a.models is not None:
        list_models(a.models)
        return 0
    if a.status or not a.model:
        for m in names:
            ok, why = available(m)
            print(f"{m}: {'ready' if ok else 'not set up: ' + why}")
        return 0
    if not a.prompt:
        ap.error("give a prompt")
    if a.model in APIS and not a.model_id:
        ap.error(f"{a.model} needs --model <id>; see the current models with --models <words>")
    ok, why = available(a.model)
    if not ok:
        print(f"{a.model} isn't available: {why}", file=sys.stderr)
        return 2
    if (a.video or a.audio or a.start or a.end) and a.model != "gemini":
        ap.error("--video, --audio, --start and --end work only with gemini")
    if (a.start or a.end) and not a.video:
        ap.error("--start and --end need --video")
    if a.audio and not ((HERE / a.audio) if not Path(a.audio).is_absolute() else Path(a.audio)).is_file():
        print(f"no such file: {a.audio}", file=sys.stderr)
        return 2
    if a.audio and Path(a.audio).name.startswith(".env"):
        print(f"won't send {a.audio}: .env files hold keys", file=sys.stderr)
        return 2
    try:
        media = media_parts(a.video, a.start, a.end, a.audio) if a.model == "gemini" else []
    except ValueError as e:
        print(f"Not sent: {e}", file=sys.stderr)
        return 2
    secret = [f for f in a.file if Path(f).name.startswith(".env")
              or ((HERE / f) if not Path(f).is_absolute() else Path(f)).resolve().name.startswith(".env")]
    if secret:
        print(f"won't send {', '.join(secret)}: .env files hold keys", file=sys.stderr)
        return 2
    missing = [f for f in a.file if not ((HERE / f) if not Path(f).is_absolute() else Path(f)).is_file()]
    if missing:
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return 2
    full = build_prompt(a.prompt, a.file)
    max_tokens = a.max_tokens
    if a.model in APIS:
        problem, info = check_model(a.model, a.model_id, a.allow_pricey)
        if problem:
            print(f"Not sent: {problem}", file=sys.stderr)
            return 2
        max_tokens = min(max_tokens, model_limit(info) or max_tokens)
    log(f"{a.model} {a.model_id}" if a.model in APIS
        else f"gemini {GEMINI_MODEL}" + (f" ({GEMINI_THINKING})" if GEMINI_THINKING else "") if a.model == "gemini"
        else a.model, a.prompt,
        a.file + ([f"video {a.video}" + (f" [{a.start or '0'}-{a.end or 'end'}]" if a.start or a.end else "")]
                  if a.video else []) + ([f"audio {a.audio}"] if a.audio else []), a.purpose)
    try:
        if a.model in APIS:
            reply, note = ask_api(a.model, full, a.model_id, max_tokens)
            print(reply)
            print(note, file=sys.stderr)
        elif a.model == "gemini":
            print(ask_gemini(full, timeout=600 if media else 300, media=media))
        else:
            print(ask_chatgpt(full))
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"Not answered: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
