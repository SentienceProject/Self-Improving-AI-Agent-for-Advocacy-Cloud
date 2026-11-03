#!/usr/bin/env python3
"""Set up this workspace on your computer. Safe to run again at any time (after an update, too).

    python3 setup.py               casual mode (the default): Claude Code's sandbox, light rules
    python3 setup.py --hardened    hardened mode: strict rules, preflight check, run logs, Docker analyst
    python3 setup.py --casual      switch back from hardened to casual
    python3 setup.py --search      also set up semantic corpus search (hardened mode; needs Ollama)
    python3 setup.py --status      show the current mode and what's set up; change nothing

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
  1. creates your own files in my/ (profile.md, techwatch.md) if they don't exist yet, asking a few questions,
     and schedules tech watch if it's set to run automatically (the default; you're asked first). It also asks,
     once, whether you'd like phone notifications for follow-ups (scripts/followups.py) and how many days apart;
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
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
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
    repository) replaced by real paths."""
    text = (json.dumps(obj).replace("{{WORKSPACE}}", HERE.as_posix()).replace("{{HOME}}", Path.home().as_posix())
            .replace("{{HARNESS}}", (HERE.parent / "ai-welfare-eval-harness").as_posix()))
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
        a = input(f"{q} ").strip()
    except EOFError:
        return default
    return a or default


def make_profile():
    profile = MY / "profile.md"
    if profile.exists():
        return
    text = (HERE / "templates" / "profile.md").read_text(encoding="utf-8")
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
    print(f"  wrote {profile.relative_to(HERE)}")


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


def setup_techwatch():
    """my/techwatch.md (asking how tech watch should run, the first time), the eval harness's folder, and the
    schedule."""
    f = MY / "techwatch.md"
    chose_now = False
    if not f.exists():
        text = (HERE / "templates" / "techwatch.md").read_text(encoding="utf-8")
        print("Tech watch looks for new tools and AI features that could help your work and prepares each change for "
              "you.\n  By default it runs automatically (a daily and a weekly scan; roughly $15-35 of usage a month, "
              "capped at $30),\n  and asks you before changing anything.")
        a = ask("  Run it automatically (Enter), only when you start it (m), or not at all (o)? [A/m/o]", "a").lower()
        runs = "when I start it" if a.startswith("m") else "off" if a.startswith("o") else "automatically"
        if not sys.stdin.isatty():   # nobody answered: don't schedule anything; the session note offers it
            runs = "automatically"
        chose_now = sys.stdin.isatty() and runs == "automatically"
        text = text.replace("**Tech watch runs:** automatically", f"**Tech watch runs:** {runs}")
        f.write_text(text, encoding="utf-8")
        print(f"  wrote my/techwatch.md (tech watch: {runs}; change it, and your limits, in that file any time)")
    auto = MY / "techwatch-auto.md"
    if not auto.exists():
        auto.write_text((HERE / "templates" / "techwatch-auto.md").read_text(encoding="utf-8"), encoding="utf-8")
        print("  wrote my/techwatch-auto.md (which changes tech watch may handle automatically)")
    text = f.read_text(encoding="utf-8")
    h = find_harness()
    if h and re.search(r"^\*\*Eval harness folder:\*\*[ \t]*$", text, re.M):
        text = re.sub(r"^(\*\*Eval harness folder:\*\*)[ \t]*$", lambda m: f"{m.group(1)} {h.as_posix()}", text,
                      count=1, flags=re.M)
        f.write_text(text, encoding="utf-8")
        print(f"  found the eval harness at {h}: tech watch can test changes before applying them")
    automatic = re.search(r"^\*\*Tech watch runs:\*\*[ \t]*automatic", text, re.M | re.I)
    tw = [sys.executable, str(HERE / "techwatch" / "scripts" / "techwatch.py"), "schedule"]
    if not automatic or "scheduled" in subprocess.run(tw + ["status"], capture_output=True, text=True).stdout.replace(
            "not scheduled", ""):
        return
    # schedule only on a yes given in this run: never because an older file says "automatically"
    if chose_now or (sys.stdin.isatty() and not ask("  Tech watch is set to run automatically but isn't scheduled on "
                                                    "this computer. Schedule it now? [Y/n]", "y").lower().startswith("n")):
        r = subprocess.run(tw + ["on"], check=False)
        if r.returncode:
            print("  scheduling didn't work (see above); you can retry with  python3 techwatch/scripts/techwatch.py "
                  "schedule on")
    else:
        print("  tech watch: to run it automatically, run  python3 techwatch/scripts/techwatch.py schedule on")


def setup_notifications():
    """The first time only: phone notifications for follow-up briefs (scripts/followups.py), every N days."""
    if (MY / "notify.json").exists() or not sys.stdin.isatty():
        return
    sys.path.insert(0, str(HERE / "scripts"))
    import notify
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
    r = subprocess.run([sys.executable, str(HERE / "scripts" / "followups.py"), "schedule", "on"], check=False)
    if r.returncode:
        print("  scheduling didn't work (see above); retry with  python3 scripts/followups.py schedule on")


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


def rule_command(args):
    """Handle --allow/--ask/--default/--allow-all/--rules. Returns True if one was given."""
    MY.mkdir(exist_ok=True)
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
    rule_flags = ("--allow", "--preapprove", "--ask", "--default", "--allow-all")
    if not STATE.exists() and any(f in args for f in rule_flags):
        print("This workspace isn't set up yet, so setting it up first.")
        main([a for a in args if a in ("--casual", "--hardened")])
    if rule_command(args):
        return
    if "--status" in args:
        print(f"Mode: {current_mode() or 'not set up yet (run: python3 setup.py)'}")
        for f in ("my/profile.md", "my/techwatch.md", "my/notify.json", "my/ETHICS_SHORT.md",
                  ".claude/settings.local.json"):
            print(f"  {f}: {'yes' if (HERE / f).exists() else 'no'}")
        return
    mode = "hardened" if "--hardened" in args else "casual" if "--casual" in args else current_mode() or "casual"
    print(f"Setting up {HERE} in {mode} mode.")
    MY.mkdir(exist_ok=True)
    make_profile()
    setup_techwatch()
    setup_notifications()
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
    print("\nDone. Next:")
    print("  - Edit my/profile.md if you skipped any questions.")
    if mode == "hardened":
        print("  - Optional: build the analyst's Docker image once with sandbox/build.sh (see README, 'Hardened mode').")
    print("  - Start Claude Code in this folder:  claude   (trust the folder when asked)")


if __name__ == "__main__":
    main(sys.argv[1:])
