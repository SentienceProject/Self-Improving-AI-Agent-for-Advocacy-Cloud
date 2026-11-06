#!/usr/bin/env python3
"""Set up this workspace on your computer. Safe to run again at any time (after an update, too).

    python3 setup.py               casual mode (the default): Claude Code's sandbox, light rules
    python3 setup.py --hardened    hardened mode: strict rules, preflight check, run logs, Docker analyst
    python3 setup.py --casual      switch back from hardened to casual
    python3 setup.py --search      also set up semantic corpus search (hardened mode; needs Ollama)
    python3 setup.py --status      show the current mode and what's set up; change nothing
    python3 setup.py --more        the optional extras: safety mode, your paper corpus, using sessions from your phone
    python3 setup.py --ask-outside-models     ask before each call Claude makes to ChatGPT or Gemini (off by default;
                                              --no-ask-outside-models turns it off again)

Fewer (or more) permission prompts. The workspace's rules are asks, not blocks: Claude can do each thing after you
say yes. To stop being asked about one, or to start being asked:
    python3 setup.py --rules                  list the workspace's rules and your changes to them
    python3 setup.py --allow "<rule>"         drop the workspace's ask, e.g. --allow "Bash(git push *)" (plain
                                              Claude Code's own prompts, which depend on your mode, still apply)
    python3 setup.py --preapprove "<rule>"    never ask about it, in any mode (more than plain Claude Code does)
    python3 setup.py --ask "<rule>"           ask first, e.g. --ask "WebFetch"
    python3 setup.py --default "<rule>"       undo your change to that rule
    python3 setup.py --allow-all              stop asking for everything except sending, posting and sharing
Your changes are kept in my/permissions.json and survive updates and mode switches. Removing one of the
workspace's rules in Claude Code's /permissions also sticks: setup.py notices and remembers it.

What it does:
  1. creates your own files in my/ (profile.md, techwatch.md) if they don't exist yet, asking a few questions:
     your work, how tech watch runs and whether ready changes apply automatically, phone notifications for
     follow-ups, and bringing in what other AI tools know about you (my/imports/, then /import-context). It never
     schedules anything itself: the commands are listed at the end. Then it offers the optional extras (--more);
  2. merges the chosen layer (layers/casual.json or layers/hardened.json) into .claude/settings.local.json,
     your machine's own settings file, with this folder's path filled in. Anything else in that file (such as
     "always allow" choices you've made in Claude Code) is kept. Switching modes removes only what the previous
     layer added;
  3. records the mode and this folder's path in my/profile.md, under "Machine";
  4. builds the integrity manifest for the self-edit proposal system (scripts/validate_proposal.py --init).

It never touches the tracked files (CLAUDE.md, .claude/settings.json, ...), so updates (`python3 scripts/update.py`) stay clean.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import venv
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "scripts"))
from style import S   # noqa: E402  (bold headings, coloured questions and warnings; plain when not a terminal)
TICK = S.ok("✓" if S._utf8() else "+")
MY = HERE / "my"
LOCAL = HERE / ".claude" / "settings.local.json"
OVERRIDES = MY / "permissions.json"   # your changes: {"allow": [...], "ask": [...], "off_<mode>": [...]}
STATE = MY / ".setup-state.json"   # which layer was merged in, exactly as merged, so it can be taken out again
SEARCH_VENV = Path.home() / ".venvs" / "ai-welfare-search"
SENDING = ("send", "reply", "forward", "post", "publish", "share", "comment", "invite", "create_event",
           "update_event", "notify", "Send-MailMessage", "git push")
MACHINE = "## Machine (written by setup.py; don't edit by hand)"


def fill(obj):
    """The layer with {{WORKSPACE}}, {{HOME}} and {{HARNESS}} (the eval harness beside this workspace in the
    repository) replaced by real paths, and {{HARNESS_RULE}} by the harness's path in permission-rule form."""
    harness = find_harness() or HERE.parent / "ai-welfare-eval-harness"
    sys.path.insert(0, str(HERE / "scripts"))
    from rule_paths import rule_path   # permission rules want "//c/Users/..." on Windows, "//home/..." elsewhere
    text = (json.dumps(obj).replace("{{WORKSPACE}}", HERE.as_posix()).replace("{{HOME}}", Path.home().as_posix())
            .replace("{{HARNESS_RULE}}", rule_path(harness)).replace("{{HARNESS}}", harness.as_posix()))
    return json.loads(text)


def merge(dst, src, replaced=None, path=()):
    """Merge the layer src into dst. replaced, if a list, gets [path, value] for every value of yours the layer
    overwrote (e.g. your own defaultMode), so switching modes later can put it back."""
    for k, v in src.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            merge(dst.setdefault(k, {}), v, replaced, path + (k,))
        elif isinstance(v, list):
            cur = dst.setdefault(k, [])
            cur.extend(x for x in v if x not in cur)
        else:
            if replaced is not None and k in dst and dst[k] != v:
                replaced.append([list(path + (k,)), dst[k]])
            dst[k] = v


def put_back(dst, replaced):
    """Restore values a layer overwrote, where nothing else has set them since."""
    for path, value in replaced or []:
        d = dst
        for k in path[:-1]:
            d = d.setdefault(k, {})
        d.setdefault(path[-1], value)


def unmerge(dst, src):
    """Take out exactly what merge(dst, src) put in (list items and scalar values that are still as set)."""
    for k, v in src.items():
        if k.startswith("_") or k not in dst:
            continue
        if isinstance(v, dict) and isinstance(dst[k], dict):
            unmerge(dst[k], v)
            if not dst[k]:
                del dst[k]
        elif isinstance(v, list) and isinstance(dst[k], list):
            dst[k] = [x for x in dst[k] if x not in v]
            if not dst[k]:
                del dst[k]
        elif dst[k] == v:
            del dst[k]


def ask(q, default=""):
    if not sys.stdin.isatty():
        return default
    try:
        lead = q[:len(q) - len(q.lstrip())]
        a = input(lead + S.ask(q.strip()) + " ").strip()
    except EOFError:
        return default
    return a or default


def make_profile():
    profile = MY / "profile.md"
    if profile.exists():
        return
    text = (HERE / "templates" / "profile.md").read_text(encoding="utf-8")
    print(S.head("Your work"))
    print("A few questions for my/profile.md (Enter to skip any; you can edit the file later):")
    causes = {"1": "animals", "2": "digital minds (the welfare of AI systems that may be sentient)",
              "3": "animals and digital minds"}
    c = ask("  Your cause: 1 = animals, 2 = digital minds, 3 = both, or type your own (e.g. \"fish welfare in "
            "the EU\")?")
    answers = {
        "**Cause area:**": causes.get(c, c),
        "**Who I am:**": ask("  Who you are, in a line (e.g. independent volunteer, nonprofit staff)?"),
        "**What I mostly want help with:**": ask("  What you mostly want help with (e.g. research briefs, letters)?"),
    }
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for key, value in answers.items():
            if line.startswith(key) and value:
                lines[i] = f"{key} {value}"
    profile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("  " + TICK + f" wrote {profile.relative_to(HERE)}")


def write_machine(mode):
    profile = MY / "profile.md"
    text = profile.read_text(encoding="utf-8")
    text = text.split(MACHINE)[0].rstrip() + "\n\n"
    lines = [MACHINE, "", f"- Mode: **{mode}**", f"- Workspace path: `{HERE.as_posix()}`"]
    if mode == "hardened":
        lines.append(f"- Analysis runner (analyst): `python3 {HERE.as_posix()}/scripts/run_analysis.py <run-id> "
                     "workspace/analysis/<run-id>/<script>.py`")
        if venv_python().exists():
            lines.append(f"- Semantic corpus search: `{venv_python().as_posix()} "
                         f"{HERE.as_posix()}/scripts/corpus_search.py \"<query>\" --k <n>` (query: letters, digits, "
                         "spaces and `, . ? ' ( ) : ; -` only)")
    profile.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except json.JSONDecodeError:
        return default


def save_overrides(ov):
    ov = {k: sorted(set(v)) for k, v in ov.items() if v}
    OVERRIDES.write_text(json.dumps(ov, indent=1) + "\n", encoding="utf-8")


def set_override(rule, kind):
    """kind: allow (pre-approve), ask, off_<mode>, or None (back to the workspace's default)."""
    ov = load(OVERRIDES, {})
    for k in {"allow", "ask", "off_casual", "off_hardened", *ov}:
        ov[k] = [r for r in ov.get(k, []) if r != rule]
    if kind:
        ov.setdefault(kind, []).append(rule)
    save_overrides(ov)


def adjust(layer, ov, mode):
    """The layer with the user's changes. off_<mode> (from --allow, or a removal in /permissions) takes a rule out
    of that mode's ask/deny lists only; allow (--preapprove) adds an allow rule and leaves the ask list, but never
    lifts a hardened block; ask (--ask) adds an ask rule in both modes."""
    perms = layer.setdefault("permissions", {})
    off = set(ov.get(f"off_{mode}", [])) | set(ov.get("ask", []))
    perms["ask"] = [r for r in perms.get("ask", []) if r not in off and r not in ov.get("allow", [])]
    perms["deny"] = [r for r in perms.get("deny", []) if r not in set(ov.get(f"off_{mode}", []))]
    for k in ("allow", "ask"):
        perms[k] = perms.get(k, []) + [r for r in ov.get(k, []) if r not in perms.get(k, [])]
    for k in [k for k in ("allow", "ask", "deny") if k in perms and not perms[k]]:
        del perms[k]
    return layer


def notice_removals(local, old_layer, mode):
    """Rules the old layer added that are gone from settings.local.json were removed by the user (e.g. in
    /permissions): remember that, so the next merge doesn't put them back. Not when the file is missing or most
    rules vanished at once (a deleted or reset file, not a choice), and never for sending rules."""
    have = local.get("permissions", {})
    old = [r for k in ("ask", "deny") for r in old_layer.get("permissions", {}).get(k, [])]
    removed = [r for k in ("ask", "deny") for r in old_layer.get("permissions", {}).get(k, [])
               if r not in have.get(k, []) and r not in have.get("allow", []) and not is_sending(r)]
    if not have or len(removed) > max(3, len(old) // 3):
        if removed:
            print(f"  {len(removed)} of the workspace's rules are missing from settings.local.json; putting them back "
                  "(to drop one for good: python3 setup.py --allow \"<rule>\")")
        return
    if removed:
        ov = load(OVERRIDES, {})
        ov[f"off_{mode}"] = ov.get(f"off_{mode}", []) + removed
        save_overrides(ov)
        print(f"  remembered {len(removed)} rule(s) you removed: {', '.join(removed)}")


def apply_layer(mode):
    try:
        local = json.loads(LOCAL.read_text(encoding="utf-8")) if LOCAL.exists() else {}
    except json.JSONDecodeError:
        sys.exit(f"{LOCAL} isn't valid JSON; fix or delete it, then run this again.")
    try:
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except json.JSONDecodeError:
        state = {}
    if state.get("layer"):
        notice_removals(local, state["layer"], state.get("mode", "casual"))
        unmerge(local, state["layer"])
        put_back(local, state.get("replaced"))
    layer = adjust(fill(json.loads((HERE / "layers" / f"{mode}.json").read_text(encoding="utf-8"))),
                   load(OVERRIDES, {}), mode)
    replaced = []
    merge(local, layer, replaced)
    LOCAL.parent.mkdir(exist_ok=True)
    LOCAL.write_text(json.dumps(local, indent=2) + "\n", encoding="utf-8")
    STATE.write_text(json.dumps({"mode": mode, "layer": layer, "replaced": replaced}, indent=1) + "\n",
                     encoding="utf-8")
    print(f"  {mode} layer merged into {LOCAL.relative_to(HERE)}"
          + (f" (replacing the {state['mode']} layer)" if state.get("mode") not in (None, mode) else ""))


def current_mode():
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("mode")
    except (OSError, json.JSONDecodeError):
        return None


def venv_python():
    return SEARCH_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def setup_search():
    py = venv_python()
    if not py.exists():
        print(f"  creating {SEARCH_VENV} ...")
        venv.create(SEARCH_VENV, with_pip=True)
    subprocess.run([str(py), "-m", "pip", "install", "--quiet", "chromadb", "requests"], check=True)
    print("  semantic search venv ready (needs Ollama with qwen3-embedding:0.6b, and an index; see README)")


def find_harness():
    """The companion eval harness: beside this workspace in the repository, or in ~/ai-welfare-evals/."""
    sibling = HERE.parent / "ai-welfare-eval-harness"
    if (sibling / "evals" / "ClaudeImproverHarness" / "techtest.py").exists():
        return sibling
    for c in sorted((Path.home() / "ai-welfare-evals").glob("*/evals/ClaudeImproverHarness/techtest.py")):
        return c.parents[2]
    return None


NEXT = []   # commands to show at the end: setup never schedules anything itself


def setup_techwatch():
    """my/techwatch.md (asking how tech watch should run, the first time) and the eval harness's folder. Scheduling
    is left to you: setup prints the command."""
    f = MY / "techwatch.md"
    if not f.exists():
        text = (HERE / "templates" / "techwatch.md").read_text(encoding="utf-8")
        print(S.head("Tech watch"))
        print("Tech watch looks for new tools and AI features that could help your work, and prepares each change as "
              "an exact edit\n  to the workspace. A daily and a weekly scan use roughly $15-35 of usage a month, "
              "capped at $30.")
        a = ask("  Run it automatically (Enter), only when you start it (m), or not at all (o)? [A/m/o]\n"
                "  (Automatic doesn't start the schedule by itself: setup shows you the one command that does, at "
                "the end.)", "a").lower()
        runs = "when I start it" if a.startswith("m") else "off" if a.startswith("o") else "automatically"
        ready = "test it, apply if it passes" if find_harness() else "apply it"
        if runs != "off":
            b = ask("  When a change is ready: test it and apply it if it passes (Enter), or ask you first (a)? [T/a]\n"
                    "  (Automatic changes stay within my/techwatch-auto.md: by default, major changes and ones every "
                    "reviewer finds\n  narrow and safe. Hooks, safety rules, the ethics files and your my/ files "
                    "always wait for you. Claude tells you\n  what was applied, and any change can be undone.)",
                    "t").lower()
            ready = "ask me" if b.startswith("a") else ready
        text = text.replace("**Tech watch runs:** automatically", f"**Tech watch runs:** {runs}")
        text = re.sub(r"^(\*\*When a change is ready:\*\*).*$", lambda m: f"{m.group(1)} {ready}", text, count=1,
                      flags=re.M)
        f.write_text(text, encoding="utf-8")
        print("  " + TICK + f" wrote my/techwatch.md (tech watch: {runs}; when a change is ready: {ready}; change either in that "
              "file any time)")
    auto = MY / "techwatch-auto.md"
    if not auto.exists():
        auto.write_text((HERE / "templates" / "techwatch-auto.md").read_text(encoding="utf-8"), encoding="utf-8")
        print("  " + TICK + " wrote my/techwatch-auto.md (which changes tech watch may handle automatically)")
    text = f.read_text(encoding="utf-8")
    h = find_harness()
    if h and re.search(r"^\*\*Eval harness folder:\*\*[ \t]*$", text, re.M):
        text = re.sub(r"^(\*\*Eval harness folder:\*\*)[ \t]*$", lambda m: f"{m.group(1)} {h.as_posix()}", text,
                      count=1, flags=re.M)
        f.write_text(text, encoding="utf-8")
        print("  " + TICK + f" found the eval harness at {h}: tech watch can test changes before applying them")
    automatic = re.search(r"^\*\*Tech watch runs:\*\*[ \t]*automatic", text, re.M | re.I)
    tw = [sys.executable, str(HERE / "techwatch" / "scripts" / "techwatch.py"), "schedule", "status"]
    if automatic and "not scheduled" in subprocess.run(tw, capture_output=True, text=True).stdout:
        NEXT.append("Start tech watch's schedule (it then runs by itself):  python3 techwatch/scripts/techwatch.py "
                    "schedule on")


def setup_notifications():
    """The first time only: phone notifications for follow-up briefs (scripts/followups.py), every N days."""
    if (MY / "notify.json").exists() or not sys.stdin.isatty():
        return
    sys.path.insert(0, str(HERE / "scripts"))
    import notify
    print(S.head("Phone notifications"))
    a = ask("Would you like phone notifications for follow-ups (every few days, a short list of what's waiting on "
            "you, via the free ntfy app)? [y/N]", "n").lower()
    if not a.startswith("y"):
        notify.save({"on": False})
        print("  no notifications (to turn them on later: python3 scripts/notify.py --setup, then "
              "python3 scripts/followups.py schedule on)")
        return
    days = None
    while days is None:
        d = ask("  How many days in between follow-up notifications? [3]", "3")
        days = int(d) if d.isdigit() and int(d) >= 1 else None
        if days is None:
            print("  Please type a whole number of days, like 1 or 7.")
    cfg = notify.setup(days)
    print(f"  Follow-ups every {days} day(s). Each one is a short read-only Claude run (capped at $0.50).")
    print(notify.instructions(cfg))
    ask("  Press Enter once you've subscribed, and I'll send a test.")
    print("  test sent: check your phone" if notify.send("Welfare workspace", "Notifications work. Follow-ups "
                                                         "will arrive here.", cfg=cfg)
          else "  the test didn't go through (see above); try later with  python3 scripts/notify.py --test")
    NEXT.append(f"Start the follow-up briefs' schedule:  python3 scripts/followups.py schedule on --days {days}")


OUTSIDE_NOTE = ("  ChatGPT and Gemini (once set up) also help Claude during your tasks: second opinions, critiques and "
                "cross-checks\n  (scripts/ask_model.py). " + S.b("Claude doesn't ask you before these calls") +
                ", even when it sends files from workspace/;\n  each one is logged in workspace/logs/outside-models.md. "
                "To be asked first:\n" + S.cmd("python3 setup.py --ask-outside-models"))


def setup_harness():
    """The eval harness beside this workspace: it tests tech watch's changes before they're applied. Unlocks its
    tasks (which tests need) and offers to set up its outside judges."""
    h = find_harness()
    print(S.head("Eval harness"))
    if not h:
        print("  " + S.warn("The eval harness isn't beside this folder (ai-welfare-eval-harness/). Tech watch can't test changes "
              "without it:\n    download the whole repository again, and keep both folders side by side."))
        return
    if sys.version_info < (3, 12):
        print("  " + S.warn(f"The eval harness needs Python 3.12 or newer (this is {sys.version.split()[0]}); see README, "
              "'If something goes wrong'."))
        return
    evals = h / "evals"
    if not (evals / "tasks" / "dev").is_dir():
        r = subprocess.run([sys.executable, str(evals / "task_lock.py"), "unlock"], capture_output=True, text=True)
        print("  " + (S.ok("eval harness: tasks unlocked for testing") if r.returncode == 0 else
                      S.warn(f"couldn't unlock the eval tasks: {(r.stderr or r.stdout).strip()[:200]}")))
    judges = h / "evals" / "ClaudeImproverHarness" / "setup_judges.py"
    check = subprocess.run([sys.executable, str(judges), "--check"], cwd=judges.parent, capture_output=True,
                           text=True, timeout=120)
    keep_sessions()
    if check.returncode == 0 and "not set up" not in check.stdout.lower():
        return print(OUTSIDE_NOTE)
    cmd = f"cd {judges.parent.as_posix()} && python3 setup_judges.py"
    if sys.stdin.isatty() and not ask("The eval harness judges changes with ChatGPT and Gemini as well as Claude (free "
                                      "tiers are enough; a few minutes; any part can be skipped).\n  Set them up now? [Y/n]",
                                      "y").lower().startswith("n"):
        try:   # every step there can be skipped; Ctrl+C skips the rest, and setup carries on
            subprocess.run([sys.executable, str(judges)], cwd=judges.parent, check=False)
        except KeyboardInterrupt:
            print(f"\n  skipped (any time later:  {cmd})")
        # setup_judges.py itself explains the outside-models default once a model is ready; don't say it twice
    else:
        NEXT.append(f"Set up the eval harness's outside judges (ChatGPT, Gemini):  {cmd}")


USER_SETTINGS = Path.home() / ".claude" / "settings.json"
KEEP_DAYS = 99999   # the same as this workspace's own .claude/settings.json


def user_settings():
    """~/.claude/settings.json as a dict, {} if it doesn't exist, or None if it isn't valid JSON (left alone then)."""
    try:
        cfg = json.loads(USER_SETTINGS.read_text(encoding="utf-8")) if USER_SETTINGS.exists() else {}
    except (OSError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) else None


def set_user_setting(keys, value, what):
    """Set one value in ~/.claude/settings.json (keys: the path to it, e.g. ("sandbox", "network",
    "strictAllowlist")), keeping everything else and a backup of the file as it was before setup first changed it.
    Only call this after the person said yes."""
    cfg = user_settings()
    if cfg is None:
        print("  " + S.warn(f"{USER_SETTINGS} isn't valid JSON, so setup left it alone. To {what}, add "
              f"{json.dumps({keys[-1]: value})} under {' > '.join(keys[:-1]) or 'the top level'} yourself."))
        return False
    backup = USER_SETTINGS.with_name("settings.json.before-ai-welfare-setup")
    if USER_SETTINGS.exists() and not backup.exists():
        shutil.copy2(USER_SETTINGS, backup)
    d = cfg
    for k in keys[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value
    USER_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    USER_SETTINGS.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print("  " + TICK + f" set {' > '.join(keys)} to {json.dumps(value)} in {USER_SETTINGS}"
          + (f" (a copy from before setup changed it: {backup.name})" if backup.exists() else ""))
    return True


def get_user_setting(*keys):
    d = user_settings() or {}
    for k in keys:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def keep_sessions():
    """The eval harness's peer judges resume eval runs' saved Claude Code sessions, and Claude Code deletes sessions
    after 30 days unless your user settings say otherwise. Asks, then sets cleanupPeriodDays in
    ~/.claude/settings.json."""
    try:
        days = int(get_user_setting("cleanupPeriodDays") or 30)
    except (TypeError, ValueError):
        days = 30
    if days >= KEEP_DAYS:
        return
    if not sys.stdin.isatty():
        NEXT.append(f"To keep eval runs' sessions for peer judging, add \"cleanupPeriodDays\": {KEEP_DAYS} to "
                    f"{USER_SETTINGS} (or run setup again in a terminal)")
        return
    a = ask("The eval harness can use eval runs' own saved sessions as extra judges (\"peers\"), but Claude Code "
            "deletes sessions after\n  30 days. Keep them? This sets cleanupPeriodDays in ~/.claude/settings.json, "
            "so all your Claude Code sessions,\n  from every project, are kept until you delete them (they hold what "
            "Claude read; see PRIVACY.md). [Y/n]", "y").lower()
    if a.startswith("n"):
        return print("  left as it is (peer judging then works only for evals from the last 30 days)")
    set_user_setting(("cleanupPeriodDays",), KEEP_DAYS, "keep sessions for the eval harness's peer judges")


def strict_network():
    """Hardened mode: commands in the sandbox reach only the listed research sites. Claude Code takes that from your
    user settings (sandbox > network > strictAllowlist), so setup asks to set it there."""
    if get_user_setting("sandbox", "network", "strictAllowlist") is True:
        return
    if not sys.stdin.isatty():
        NEXT.append(f"For hardened mode's strict network limit, add \"strictAllowlist\": true under \"sandbox\" > "
                    f"\"network\" in {USER_SETTINGS}")
        return
    a = ask("  Hardened mode holds sandboxed commands to the listed research sites, but only if your user settings say "
            "so\n  (sandbox > network > strictAllowlist in ~/.claude/settings.json). That applies to every Claude Code "
            "project\n  on this computer: a sandboxed command can then only reach sites a project allows, instead of "
            "asking you. Set it? [Y/n]", "y").lower()
    if a.startswith("n"):
        return print("  left as it is: unlisted sites ask you first instead of being blocked")
    set_user_setting(("sandbox", "network", "strictAllowlist"), True, "turn on hardened mode's strict network limit")


def remote_control():
    """Opt-in: every Claude Code session starts with Remote Control on, so you can follow it from your phone."""
    if get_user_setting("remoteControlAtStartup") is True or not sys.stdin.isatty():
        return
    a = ask("   Start every Claude Code session with Remote Control on, so you can always pick it up on your phone? "
            "This sets\n   remoteControlAtStartup in ~/.claude/settings.json, for all your projects. [y/N]", "n").lower()
    if a.startswith("y"):
        set_user_setting(("remoteControlAtStartup",), True, "start sessions with Remote Control on")


IMPORTS = MY / "imports"   # what you bring in from other AI tools, waiting for /import-context


def import_context():
    """The first time only: memory and context from Claude chats or other AI assistants, so the workspace starts out
    knowing your work. Saved in my/imports/; Claude folds it into your profile and memory (with your OK) when you run
    /import-context."""
    flag = MY / ".import-asked"
    if flag.exists() or not sys.stdin.isatty():
        return
    flag.write_text("asked\n", encoding="utf-8")
    print(S.head("What other AI tools know about you"))
    a = ask("Would you like to bring in what other AI tools already know about you and your work (your chat history\n"
            "or memory from Claude, ChatGPT, Gemini, or another agent)? It gets this workspace up to speed faster. [y/N]",
            "n").lower()
    if not a.startswith("y"):
        print("  skipped (any time later: ask Claude for /import-context)")
        return
    print("  Two kinds help. " + S.b("Your chat history") + " is best: it works even if those tools remember little "
          "about you.\n  Download it (each export is emailed or ready within minutes to a day):\n"
          "    - ChatGPT: Settings > Data controls > Export data > Export.\n"
          "    - Claude (claude.ai or the app): Settings > Privacy > Export data.\n"
          "    - Gemini: takeout.google.com > Deselect all > tick \"My Activity\" > \"All activity data included\" > "
          "only\n      \"Gemini Apps\" > Next step > Create export.\n"
          "  " + S.b("Memory and notes") + " help too: ask Claude, ChatGPT or Gemini: \"Please write out everything "
          "you remember about\n  me, my work and my preferences, as a plain list I can copy\", and save the answers "
          "as text files in one folder.\n  Another agent's notes (CLAUDE.md, memory.md) work the same way.")
    projects = Path.home() / ".claude" / "projects"
    if projects.is_dir() and any(projects.glob("*/*.jsonl")) and ask(
            "  Bring in your past Claude Code sessions from this computer too (the ones you typed in, not automated "
            "runs)? [y/N]", "n").lower().startswith("y"):
        subprocess.run([sys.executable, str(HERE / "scripts" / "import_chats.py"), "--claude-code"], check=False)
    src = ask("  A downloaded export (the .zip, as it is), or a file or folder of notes, to bring in now\n  (Enter: "
              "later; drag the file into this window to paste its path):", "").strip().strip('"').strip("'")
    if not src:
        print("  Later, then: when an export arrives, run\n" + S.cmd("python3 scripts/import_chats.py <the .zip>") +
              "\n  and for notes, type /import-context in a Claude session and paste them.")
        return
    srcp = Path(src).expanduser()
    if not srcp.exists():
        print("  " + S.warn(f"Couldn't find {srcp}. Later: python3 scripts/import_chats.py <the export>, or "
                            "/import-context for notes."))
        return
    if subprocess.run([sys.executable, str(HERE / "scripts" / "import_chats.py"), str(srcp)],
                      stderr=subprocess.DEVNULL).returncode == 0:   # it was a chat export
        return
    files = [srcp] if srcp.is_file() else sorted(q for q in srcp.rglob("*") if q.is_file()) if srcp.is_dir() else []
    files = [q for q in files if q.suffix.lower() in (".txt", ".md", ".json", ".csv", "") and q.stat().st_size < 2_000_000]
    if not files:
        print(f"  Nothing to bring in at {src}: no chat export, and no text, Markdown, JSON or CSV files under 2 MB.\n"
              "  Later: python3 scripts/import_chats.py <the export>, or /import-context for notes.")
        return
    IMPORTS.mkdir(parents=True, exist_ok=True)
    for q in files[:50]:
        shutil.copy2(q, IMPORTS / q.name)
    print("  " + TICK + f" copied {min(len(files), 50)} file(s) to my/imports/. In your first Claude session, type "
          "/import-context:\n  Claude reads them and suggests what to add to your profile and memory, and asks "
          "before saving.")


MORE = MY / ".more-setup.json"   # {"status": "done" | "declined" | "later", "remind": "YYYY-MM-DD"}


def more_setup(asked_by_user=False):
    """Optional extras after the main setup: the safety mode, your paper corpus, and using sessions from your phone.
    "Not now" brings it up again the next day (at the start of a session)."""
    if not sys.stdin.isatty():
        return
    state = load(MORE, {})
    if not asked_by_user:
        if state.get("status") in ("done", "declined"):
            return
        print(S.head("More setup options"))
        a = ask("More setup options: the safety mode (casual or hardened), your paper corpus, and using sessions from "
                "your phone.\n  Go through them now (y), not now (Enter: you'll be reminded tomorrow), or no (n)? "
                "[y/L/n]", "l").lower()
        if a.startswith("n"):
            MORE.write_text(json.dumps({"status": "declined"}), encoding="utf-8")
            return print("  OK. If you change your mind: python3 setup.py --more")
        if not a.startswith("y"):
            remind = date.fromordinal(date.today().toordinal() + 1).isoformat()
            MORE.write_text(json.dumps({"status": "later", "remind": remind}), encoding="utf-8")
            return print("  OK, I'll remind you tomorrow (or any time: python3 setup.py --more).")
    mode = current_mode() or "casual"
    print(f"\n" + S.b("1. Safety mode") + f" (now: {mode}).\n"
          "   Casual: Claude Code's sandbox and a few ask-first rules, no stricter than plain Claude Code. Fine for most "
          "people.\n   Hardened: strict rules, a preflight check, run logs, and a Docker container for analysis code. "
          "For sensitive work.")
    if mode == "casual" and os.name == "nt":
        print("   Hardened mode needs Linux, macOS or WSL2, so it isn't available on native Windows.")
        want = "n"
    else:
        want = ask(f"   Switch to {'hardened' if mode == 'casual' else 'casual'} mode? [y/N]", "n").lower()
    if want.startswith("y"):
        mode = "hardened" if mode == "casual" else "casual"
        apply_layer(mode)
        write_machine(mode)
        if mode == "hardened":
            strict_network()
        print(f"   now in {mode} mode" + (" (optional: build the analyst's container with sandbox/build.sh)"
                                          if mode == "hardened" else ""))
    print("\n" + S.b("2. Your paper corpus") + ": papers Claude can read and quote, kept in workspace/corpus/.")
    z = ask("   If you use Zotero, export a collection (BibTeX, with files) and give its folder here (Enter to skip; "
            "later, ask Claude\n   for /add-papers to find open-access papers):", "").strip().strip('"').strip("'")
    if z:
        subprocess.run([sys.executable, str(HERE / "scripts" / "import_zotero.py"), z], check=False)
    if mode == "hardened" and ask("   Set up semantic search over the corpus (needs Ollama; see README)? [y/N]",
                                  "n").lower().startswith("y"):
        setup_search()
    print("\n" + S.b("3. Using a session from your phone") + ": in any Claude Code session here, type /remote-control (or /rc). You "
          "can then\n   follow and steer it from the Claude app or claude.ai/code, handy for long research tasks.")
    remote_control()
    MORE.write_text(json.dumps({"status": "done"}), encoding="utf-8")
    print("\nThat's all the extra setup. Run python3 setup.py --more any time to go through it again.")


def checks():
    """Common setup problems, said plainly with the fix (README, "If something goes wrong", has more)."""
    out = []
    if sys.version_info < (3, 10):
        out.append(f"Python {sys.version.split()[0]} is old; some parts need 3.10+, and the eval harness 3.12+. "
                   "Install a newer Python (python.org, or your system's package manager).")
    if not shutil.which("git"):
        out.append("git isn't installed, so updates won't work (python3 scripts/update.py). Install it from "
                   "git-scm.com, or with your package manager.")
    claude = shutil.which("claude")
    if not claude:
        out.append("The `claude` command isn't on this computer's PATH. Install Claude Code "
                   "(https://code.claude.com/docs/en/setup); if it is installed, open a new terminal.")
    else:
        try:
            v = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20).stdout
            m = re.search(r"(\d+)\.(\d+)\.(\d+)", v)
            if m and tuple(map(int, m.groups())) < (2, 1, 300):
                out.append(f"Claude Code {m.group(0)} is older than this workspace was tested with (2.1.317). "
                           "Update it: claude update")
        except (OSError, subprocess.TimeoutExpired):
            pass
    if os.name == "nt":
        out.append("This is native Windows, where Claude Code's sandbox doesn't run: shell commands aren't held to this "
                   "folder or to the research sites, and only Claude Code's own permission prompts stand in for that. "
                   "For the full protection, use WSL2 (README, 'If something goes wrong').")
    if os.name == "nt" and not shutil.which("python3"):
        out.append("There's no `python3` command, which the workspace's hooks use. In the Windows installer, tick "
                   "'Add python.exe to PATH', or use WSL2 (see README, 'If something goes wrong').")
    for line in out:
        print("  " + S.warn(line))
    return out


def is_sending(rule):
    return any(w.lower() in rule.lower() for w in SENDING)


def show_rules():
    mode = current_mode() or "casual"
    layer = json.loads((HERE / "layers" / f"{mode}.json").read_text(encoding="utf-8")).get("permissions", {})
    ov = load(OVERRIDES, {})
    print(f"Workspace rules in {mode} mode (Claude Code's own prompts come on top of these):")
    for k, word in (("deny", "blocked"), ("ask", "asks first")):
        for r in layer.get(k, []):
            mine = next((x for x in ("allow", "ask", f"off_{mode}") if r in ov.get(x, [])), None)
            mine = "removed" if mine and mine.startswith("off") else mine
            print(f"  {word:11} {r}" + (f"   -> you changed it: {mine}" if mine else ""))
    for k in ("allow", "ask"):
        extra = [r for r in ov.get(k, []) if r not in layer.get("ask", []) + layer.get("deny", [])]
        for r in extra:
            print(f"  {'you: ' + k:11} {r}")


OUTSIDE_RULES = ("Bash(python3 scripts/ask_model.py *)", "Bash(python3 {ws}/scripts/ask_model.py *)")


def outside_rules():
    return [r.format(ws=HERE.as_posix()) for r in OUTSIDE_RULES]   # an ask rule for a form a mode doesn't allow is harmless


def rule_command(args):
    """Handle --allow/--ask/--default/--allow-all/--rules and --(no-)ask-outside-models. True if one was given."""
    MY.mkdir(exist_ok=True)
    if "--ask-outside-models" in args or "--no-ask-outside-models" in args:
        ask_first = "--ask-outside-models" in args
        for r in outside_rules():
            set_override(r, "ask" if ask_first else None)
        apply_layer(current_mode() or "casual")
        print("Claude now asks you before each call to ChatGPT or Gemini (scripts/ask_model.py)." if ask_first else
              "Claude may call ChatGPT and Gemini without asking again (the default). Every call is still logged "
              "in workspace/logs/outside-models.md.")
        return True
    if "--rules" in args:
        show_rules()
        return True
    if "--allow-all" in args:
        mode = current_mode() or "casual"
        asks = json.loads((HERE / "layers" / f"{mode}.json").read_text(encoding="utf-8"))
        asks = asks.get("permissions", {}).get("ask", [])
        for r in asks:
            if not is_sending(r):
                set_override(r, f"off_{mode}")
        print("Dropped the workspace's extra prompts, except for sending, posting and sharing. You're back to plain "
              "Claude Code for the rest: whether it asks depends on your permission mode.")
    mode = current_mode() or "casual"
    for flag, kind in (("--allow", f"off_{mode}"), ("--preapprove", "allow"), ("--ask", "ask"), ("--default", None)):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                sys.exit(f'{flag} needs a rule, e.g. {flag} "Bash(git push *)"  (see: python3 setup.py --rules)')
            set_override(args[i + 1], kind)
            print(f"  {args[i + 1]}: " + {"allow": "pre-approved (never asks, in any permission mode)", "ask": "asks first",
                                          None: "back to the workspace default"}.get(kind, f"workspace's ask dropped "
                                                                                           f"in {mode} mode"))
    if not any(f in args for f in ("--allow", "--preapprove", "--ask", "--default", "--allow-all")):
        return False
    apply_layer(current_mode() or "casual")
    print("Done. If Claude Code is open, the change applies from its next session.")
    return True


def main(args):
    if sys.version_info < (3, 9):
        sys.exit("Python 3.9 or newer is needed.")
    rule_flags = ("--allow", "--preapprove", "--ask", "--default", "--allow-all", "--ask-outside-models",
                  "--no-ask-outside-models")
    if not STATE.exists() and any(f in args for f in rule_flags):
        print("This workspace isn't set up yet, so setting it up first.")
        main([a for a in args if a in ("--casual", "--hardened")])
    if rule_command(args):
        return
    if "--more" in args:
        if not STATE.exists():
            sys.exit("Run python3 setup.py first; --more is for after the main setup.")
        return more_setup(asked_by_user=True)
    if "--status" in args:
        print(f"Mode: {current_mode() or 'not set up yet (run: python3 setup.py)'}")
        for f in ("my/profile.md", "my/techwatch.md", "my/ETHICS_SHORT.md", ".claude/settings.local.json"):
            print(f"  {f}: {'yes' if (HERE / f).exists() else 'no'}")
        sys.path.insert(0, str(HERE / "scripts"))
        import notify
        print(f"  phone notifications: {'on' if notify.ready() else 'off'}")
        return
    mode = "hardened" if "--hardened" in args else "casual" if "--casual" in args else current_mode() or "casual"
    if mode == "hardened" and os.name == "nt":
        sys.exit("Hardened mode needs Linux, macOS or WSL2: its scripts and sandbox don't run on native Windows. "
                 "Install WSL2 and set the workspace up there, or use casual mode.")
    print(S.b(f"Setting up {HERE} in {mode} mode."))
    if mode == "hardened":
        strict_network()
    checks()
    MY.mkdir(exist_ok=True)
    make_profile()
    if not (MY / "ETHICS_SHORT.md").exists():
        print("  Ethics: the workspace currently uses the personal ethics file made by the creator of this project "
              "(ETHICS_SHORT.md).\n  Its views likely differ from yours. To add your own, ask Claude for /my-ethics in "
              "a session (about 10 minutes;\n  \"not sure\" is a fine answer). Your views then come first, and the "
              "creator's fill in where yours are\n  silent or unsure (you can turn that off).")
    setup_techwatch()
    setup_harness()
    setup_notifications()
    import_context()
    if not (HERE / "workspace" / "memory.md").exists():
        (HERE / "workspace" / "memory.md").write_text((HERE / "templates" / "memory.md").read_text(encoding="utf-8"),
                                                      encoding="utf-8")
    if "--search" in args:
        setup_search()
    apply_layer(mode)
    write_machine(mode)
    subprocess.run([sys.executable, str(HERE / "scripts" / "validate_proposal.py"), "--init"],
                   check=False, stdout=subprocess.DEVNULL)
    print("  integrity manifest built (scripts/integrity.json)")
    print(S.head("Done") + "\nNext:")
    print("  - Edit my/profile.md if you skipped any questions.")
    if mode == "hardened":
        print("  - Optional: build the analyst's Docker image once with sandbox/build.sh (see README, 'Hardened mode').")
    for n in NEXT:   # "what it does:  the command" puts the command on its own, bold line
        what, _, command = n.partition(":  ")
        print(f"  - {what}:" + "\n" + S.cmd(command) if command else f"  - {n}")
    print("  - Start Claude Code in this folder (trust the folder when asked):\n" + S.cmd("claude"))
    more_setup()


if __name__ == "__main__":
    main(sys.argv[1:])
