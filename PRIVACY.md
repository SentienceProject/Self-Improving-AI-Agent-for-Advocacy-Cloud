# Where your data goes

This workspace runs on your computer, but the AI doesn't: everything Claude reads or writes passes through
Anthropic's servers, and a few features send things to other services. Here is each flow, what's in it, and how to
turn it off.

## What stays on your computer

- Your files in `my/` (profile, ethics, tech-watch settings, permission changes) and everything in `workspace/`
  (drafts, corpus, notes, memory, logs). Both are in `.gitignore`, so `git push` doesn't publish them even if you
  make your own copy of this repository public. (Remove a line from `.gitignore` if you *want* to share a folder.)
- Tech watch's state, logs and backups (`techwatch/state/`, `techwatch/logs/`, `techwatch/staged/`).
- **Session transcripts.** Claude Code keeps every session's full transcript in `~/.claude/projects/`, and this
  workspace sets `cleanupPeriodDays` to keep them indefinitely (hardened mode's run logs rely on them). They hold
  everything Claude read, including drafts. Delete that folder's entries for this workspace to remove them, or set
  `"cleanupPeriodDays": 30` in `.claude/settings.local.json`. The eval harness's peer judges resume eval runs' saved
  sessions (the folders named after `ai-welfare-copies`), so keep those while you might still want peer judging.
  If you said yes to keeping them in setup, `cleanupPeriodDays` in `~/.claude/settings.json` applies to every
  Claude Code project on this computer; set it back (or delete the line) to return to Claude Code's 30 days.
- **Settings setup may add to `~/.claude/settings.json`**, each only on your yes: `cleanupPeriodDays` (above),
  `remoteControlAtStartup` (every session can be followed from the Claude app or claude.ai/code, signed in as you),
  and, in hardened mode, `strictAllowlist`. The file as it was before setup first changed it is kept as
  `settings.json.before-ai-welfare-setup`.

"Stays on your computer" means it isn't uploaded *as a file*. Anything Claude reads in a session is sent to
Anthropic as part of that session (next section).

## Flows, one by one

| What | Goes to | What's sent | Turn it off |
|---|---|---|---|
| **Every Claude session** (you, subagents, tech-watch scouts, eval runs) | Anthropic | Your messages, and the contents of every file Claude reads, including drafts, `my/` files and corpus papers | Can't, it's how Claude works. Whether Anthropic may train on it depends on your account's privacy settings ([claude.ai → Settings → Privacy](https://claude.ai/settings/data-privacy-controls)); API and commercial accounts aren't used for training by default. |
| **Web search** | Anthropic's search provider | The search terms Claude writes, which can reveal what you're working on | Deny `WebSearch` in `/permissions` |
| **Fetching web pages** (WebFetch, `add_paper.py`, commands in the sandbox) | The website | A normal request for that page, from your internet connection. WebFetch runs without asking by default, so a manipulated session could put text into a web address it opens | `python3 setup.py --ask "WebFetch"` (every fetch asks), or deny `WebFetch` in `/permissions` |
| **Citation check** (`scripts/check_citations.py`) | arXiv and Crossref | The arXiv IDs and DOIs in a draft, nothing else. For an unpublished draft, that shows what you're reading | Run it with `--offline`, or tell Claude not to |
| **Connected apps** (`/connect`: Zotero, Drive, Gmail, ...) | That service, and Anthropic | Whatever Claude reads from or writes to the app | Don't connect it, or remove it (`claude mcp remove <name>`) |
| **Follow-up briefs** (if you turned them on) | Anthropic; the ntfy server (ntfy.sh unless you set your own) | A read-only Claude run reads what changed in `workspace/`. Its short phone version (a few lines about what's waiting on you) goes to your ntfy topic, where ntfy.sh keeps it about 12 hours | `"followups_details": false` in `my/notify.json` sends only "your brief is ready"; `python3 scripts/followups.py schedule off` stops them |
| **Your own notifications** (`scripts/notify.py`) | The ntfy server | Whatever title and message you, or a hook you added, send | `python3 scripts/notify.py --off` |
| **Tech watch** | Anthropic; the news pages it watches | Scouts read the workspace (including `my/` and recent drafts) to understand your work; the sensor requests a fixed list of public pages | `Tech watch runs: off` in `my/techwatch.md` |
| **Updates** (`scripts/update.py`, the session-start check) | The repository you cloned from (e.g. GitHub) | A normal `git fetch`; nothing of yours is uploaded | Don't use git, or delete the remote |
| **Eval harness** (optional, separate repository) | OpenAI (ChatGPT via Codex) and Google (Gemini), plus Anthropic | Task requests, the agents' drafts, the judge notes, and your ethics and guardrails files | Use only the Claude stand-in judge (`standin_judge` in its `config.toml`, and don't set up Codex or Gemini) |
| **Claude Code's own telemetry** | Anthropic | Usage metrics and error reports, not your files | Set `DISABLE_TELEMETRY=1` and `DISABLE_ERROR_REPORTING=1` in your environment (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` turns off both, and auto-updates too) |

**Free tiers of outside judges may train on what you send.** Gemini's free API tier and ChatGPT's free plan can
use your inputs to improve their models. The eval harness only sends its own test tasks and the drafts written for
them, but if you add tasks built from private material, keep that in mind.

## Things worth knowing

- **Secrets.** The workspace asks before Claude reads common key and password files, and Claude is told never to
  read, print or store secret values. If you paste a key into the chat anyway, it goes to Anthropic like any
  message.
- **Other people's information.** Drafts about real people (legislators, journalists, researchers) are fine to
  write, and they stay on your computer, but the people's names and anything you paste about them pass through
  Anthropic in the session. Don't paste private correspondence you don't have the right to share.
- **Prompt injection.** Web pages, PDFs and connected apps can contain text written to manipulate an AI. Claude
  treats them as data, and anything that sends, posts or publishes asks you first, but read what a session proposes
  to send before you say yes.
- **Your files on disk** are as safe as your computer. The sandbox (Linux, WSL2, macOS) keeps commands from
  writing outside this folder without asking, and from reading your SSH keys and cloud credentials.

Corrections welcome: if you find a flow that isn't listed, please report it.
