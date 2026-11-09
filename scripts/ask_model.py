#!/usr/bin/env python3
"""Ask an outside model (ChatGPT or Gemini) one question: second opinions, critiques, cross-checks, or help with a
task. Claude uses this during tasks; see OUTSIDE_MODELS.md for when and how.

    python3 scripts/ask_model.py chatgpt "Please critique this draft. Thank you!" --file workspace/outbox/x.md
    python3 scripts/ask_model.py gemini  "..." --file a.md --file b.md --purpose "fact check of the op-ed"
    python3 scripts/ask_model.py --status        which models are set up (calls nothing)

It uses the same logins as the eval harness's judges (python3 setup.py, or the harness's setup_judges.py):
  - ChatGPT through the Codex CLI, signed in with your ChatGPT account (a free account works): `codex login`.
  - Gemini through its API, with a free key from https://aistudio.google.com/apikey, which setup saves in this
    workspace's .env file (GEMINI_API_KEY=...). A GEMINI_API_KEY environment variable works too.
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
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
MAX_FILE_BYTES = 400_000


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
    return False, f"unknown model {which!r} (chatgpt or gemini)"


def log(model, prompt, files, purpose):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists():
        LOG.write_text("# Outside models log\n\nEvery question the workspace sent to ChatGPT or Gemini.\n\n"
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


def ask_gemini(prompt, timeout=300):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode(),
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


def build_prompt(prompt, files):
    parts = [prompt]
    for f in files:
        p = (HERE / f) if not Path(f).is_absolute() else Path(f)
        data = p.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", "replace")
        parts.append(f"\n\n=== FILE: {Path(f).name} ===\n{data}")
    return "".join(parts)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("model", nargs="?", choices=["chatgpt", "gemini"])
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--file", action="append", default=[], help="a file to include (repeat for several)")
    ap.add_argument("--purpose", default="", help="why, for the log")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    if a.status or not a.model:
        for m in ("chatgpt", "gemini"):
            ok, why = available(m)
            print(f"{m}: {'ready' if ok else 'not set up: ' + why}")
        return 0
    if not a.prompt:
        ap.error("give a prompt")
    ok, why = available(a.model)
    if not ok:
        print(f"{a.model} isn't available: {why}", file=sys.stderr)
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
    log(a.model, a.prompt, a.file, a.purpose)
    try:
        print((ask_chatgpt if a.model == "chatgpt" else ask_gemini)(full))
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"Not answered: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
