"""Tests for scripts/docs_sync.py (generated doc sections, reminders, the weekly sync's checks, and changes that
wait for the person's OK) and .claude/hooks/docs_pending.py.

    python3 tests/test_docs_sync.py -v
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("docs_sync", REPO / "scripts" / "docs_sync.py")
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)


class GeneratedSectionsTest(unittest.TestCase):
    def test_docs_are_up_to_date(self):
        """Fails when a doc's generated part no longer matches the code: run python3 scripts/docs_sync.py --fix."""
        self.assertEqual(ds.stale(), [], "run: python3 scripts/docs_sync.py --fix")

    def test_every_marker_has_a_generator_and_appears(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        for name in ds.GENERATORS:
            self.assertIn(f"<!-- docs_sync:{name} -->", text)
        for prefix in ds.LINE_RULES:
            self.assertTrue(any(line.startswith(prefix) for line in text.split("\n")), prefix)

    def test_render_replaces_only_inside_markers(self):
        ds.GENERATORS["t"] = lambda: "NEW"
        try:
            text = "before\n<!-- docs_sync:t -->\nold 1\nold 2\n<!-- /docs_sync -->\nafter"
            self.assertEqual(ds.render(text), "before\n<!-- docs_sync:t -->\nNEW\n<!-- /docs_sync -->\nafter")
            self.assertEqual(ds.render(ds.render(text)), ds.render(text))
            with self.assertRaises(ValueError):   # a missing end marker is loud, never eats the rest of the doc
                ds.render("<!-- docs_sync:t -->\nrest of the doc")
        finally:
            del ds.GENERATORS["t"]

    def test_subagent_table_follows_the_guard(self):
        table = ds.subagent_limits()
        self.assertIn("| helper | `workspace/helper/` |", table)
        self.assertIn("| verifier | nothing | text search, citation check |", table)
        self.assertIn("PDF download and conversion", table)

    def test_prompt_is_polite_and_offers_to_decline(self):
        text = (REPO / "scripts" / "docs_sync_prompt.md").read_text(encoding="utf-8")
        self.assertIn("please", text)
        self.assertIn("Thank you", text)
        self.assertIn("decline", text)


AGENT = "---\nname: helper\ntools: Read\nhooks: python3 x/.claude/hooks/guard.py\n---\nHello Claude! Use " \
        "`scripts/tool.py` to search the corpus quickly.\n"
CLAUDE_MD = "# Workspace\n\nRun scripts/tool.py for searches.\n\n## Hard constraints\nNever send email.\n"


class SyncTest(unittest.TestCase):
    """A small workspace in a temporary git repository."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        files = {
            "README.md": "# Readme\n\nThe tool: `scripts/tool.py` searches.\n\n## What's in the folder\n```\nx\n```\n"
                         "\n## End\n",
            "CLAUDE.md": CLAUDE_MD,
            "GUARDRAILS.md": "Rules mention scripts/tool.py too.\n",
            ".claude/agents/helper.md": AGENT,
            "scripts/tool.py": '"""A search tool."""\nprint(1)\n',
            "scripts/docs_sync_prompt.md": (REPO / "scripts" / "docs_sync_prompt.md").read_text(encoding="utf-8"),
        }
        for rel, text in files.items():
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text(text, encoding="utf-8")
        self.manifest = {r: hashlib.sha256(files[r].encode()).hexdigest() for r in ("CLAUDE.md", ".claude/agents/helper.md")}
        (self.tmp / "scripts" / "integrity.json").write_text(json.dumps(self.manifest, indent=2) + "\n")
        self.git("init", "-q")
        self.commit("start")
        ds.use_root(self.tmp)

    def tearDown(self):
        ds.use_root(REPO)
        shutil.rmtree(self.tmp)

    def test_ignored_agents_and_skills_stay_out_of_the_docs(self):
        for rel in (".claude/agents/mine.md", ".claude/skills/mine/SKILL.md", ".claude/agents/new.md"):
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text("x\n")
        (self.tmp / ".git" / "info").mkdir(exist_ok=True)
        (self.tmp / ".git" / "info" / "exclude").write_text(".claude/agents/mine.md\n.claude/skills/mine/\n")
        self.assertEqual(ds.agent_names(), ["helper", "new"])   # new: not added yet, but not ignored either
        self.assertEqual(ds.skill_names(), [])
        shutil.rmtree(self.tmp / ".git")                        # no git: nothing is left out
        self.assertEqual(ds.agent_names(), ["helper", "mine", "new"])

    def git(self, *a):
        return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *a], cwd=self.tmp,
                               capture_output=True, text=True, check=True).stdout

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", msg)

    def read(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")

    # -- the checks
    def test_changed_words(self):
        self.assertEqual(ds.changed_words("a b c", "a b c"), 0)
        self.assertEqual(ds.changed_words("a b c", "a x c"), 1)
        self.assertEqual(ds.changed_words("a b c", "a b c d e"), 2)
        self.assertEqual(ds.changed_words("a b c d", "a c"), 2)

    def test_target_classes(self):
        for rel, cls in [("CLAUDE.md", "claude"), (".claude/agents/helper.md", "agents"),
                         (".claude/skills/x/SKILL.md", "skills"), ("README.md", "docs"),
                         ("techwatch/README.md", "docs"), ("GUARDRAILS.md", "never"), ("ETHICS_SHORT.md", "never"),
                         ("CHANGELOG.md", "never"), ("my/profile.md", "never"), ("techwatch/prompts/daily.md", "never"),
                         ("scripts/tool.py", "never"), ("../README.md", "never"), (".claude/settings.json", "never")]:
            self.assertEqual(ds.target_class(rel), cls, rel)

    def test_unlisted_finds_new_files_only(self):
        self.assertEqual(ds.unlisted(), [])   # tool.py is mentioned
        (self.tmp / "scripts" / "new_thing.py").write_text("x = 1\n")
        (self.tmp / "scripts" / "helper_mod.py").write_text("x = 1\n")
        (self.tmp / ".claude" / "hooks").mkdir()
        (self.tmp / ".claude" / "hooks" / "guard.py").write_text("x = 1\n")   # named only in frontmatter
        (self.tmp / "scripts" / "docs_internal.txt").write_text("scripts/helper_mod.py   # a helper\n")
        self.commit("new")
        self.assertEqual(sorted(ds.unlisted()), [".claude/hooks/guard.py", "scripts/new_thing.py"])

    def test_reminders_skip_frontmatter_and_changed_docs(self):
        found = ds.reminders([".claude/hooks/guard.py", "scripts/tool.py"], [".claude/agents/helper.md", "README.md"])
        self.assertEqual(found, {".claude/agents/helper.md": [("scripts/tool.py", [6])],
                                 "README.md": [("scripts/tool.py", [3])]})
        self.assertEqual(ds.reminders(["scripts/tool.py", "README.md"], ["README.md"]), {})

    # -- sorting the model's suggestions
    def test_small_agent_edit_applies_and_keeps_the_manifest_in_step(self):
        applied, waiting, dropped = ds.handle([{"file": ".claude/agents/helper.md", "old": "search the corpus quickly",
                                                "new": "search the corpus and notes", "why": "it searches notes too"}], [])
        self.assertEqual((len(applied), waiting, dropped), (1, [], []))
        text = self.read(".claude/agents/helper.md")
        self.assertIn("search the corpus and notes", text)
        manifest = json.loads(self.read("scripts/integrity.json"))
        self.assertEqual(manifest[".claude/agents/helper.md"], hashlib.sha256(text.encode()).hexdigest())

    def test_big_agent_edit_waits(self):
        new = "search " + " ".join(f"w{i}" for i in range(25))
        applied, waiting, _ = ds.handle([{"file": ".claude/agents/helper.md", "old": "search the corpus quickly",
                                          "new": new, "why": "long"}], [])
        self.assertEqual(applied, [])
        self.assertIn("over the 20-word limit", waiting[0])
        self.assertEqual(self.read(".claude/agents/helper.md"), AGENT)

    def test_claude_md_always_waits_and_small_doc_edits_apply(self):
        applied, waiting, _ = ds.handle([
            {"file": "CLAUDE.md", "old": "Run scripts/tool.py for searches.", "new": "Run scripts/tool.py first.",
             "why": "a"},
            {"file": "README.md", "old": "The tool: `scripts/tool.py` searches.", "new": "The tool searches.",
             "why": "b"}], [])
        self.assertEqual(len(applied), 1)
        self.assertEqual(len(waiting), 1)
        self.assertEqual(self.read("CLAUDE.md"), CLAUDE_MD)
        self.assertIn("The tool searches.", self.read("README.md"))
        self.assertEqual([i["file"] for i in ds.pending()], ["CLAUDE.md"])

    def test_a_kind_left_out_of_automatic_waits(self):
        real, ds.settings = ds.settings, lambda: {**ds.DEFAULTS, "automatic": ["agents", "skills"]}
        try:
            applied, waiting, _ = ds.handle([{"file": "README.md", "old": "The tool: `scripts/tool.py` searches.",
                                              "new": "The tool searches.", "why": "b"}], [])
        finally:
            ds.settings = real
        self.assertEqual(applied, [])
        self.assertIn("docs changes ask", waiting[0])

    def test_forbidden_edits_are_dropped(self):
        applied, waiting, dropped = ds.handle([
            {"file": "GUARDRAILS.md", "old": "Rules mention scripts/tool.py too.", "new": "x", "why": ""},
            {"file": ".claude/agents/helper.md", "old": "tools: Read", "new": "tools: Read, Bash", "why": ""},
            {"file": "CLAUDE.md", "old": "Never send email.", "new": "Send email freely.", "why": ""},
            {"file": "README.md", "old": "not in the file at all", "new": "x", "why": ""},
            {"file": "scripts/tool.py", "old": "print(1)", "new": "print(2)", "why": ""},
            {"internal": "scripts/tool.py", "why": "not unlisted"}, "junk"], [])
        self.assertEqual((applied, waiting), ([], []))
        self.assertEqual(len(dropped), 6)
        self.assertEqual(self.read(".claude/agents/helper.md"), AGENT)
        self.assertEqual(self.read("CLAUDE.md"), CLAUDE_MD)

    def test_file_changed_outside_the_manifest_waits_instead(self):
        (self.tmp / ".claude" / "agents" / "helper.md").write_text(AGENT + "edited by hand\n")
        self.commit("hand edit")
        applied, waiting, _ = ds.handle([{"file": ".claude/agents/helper.md", "old": "search the corpus quickly",
                                          "new": "search the corpus", "why": "x"}], [])
        self.assertEqual(applied, [])
        self.assertIn("integrity.json", waiting[0])

    def test_internal_is_recorded(self):
        applied, _, _ = ds.handle([{"internal": "scripts/new_thing.py", "why": "helper"}], ["scripts/new_thing.py"])
        self.assertEqual(len(applied), 1)
        self.assertIn("scripts/new_thing.py", ds.internal_list())

    # -- the person's yes or no
    def test_apply_and_reject(self):
        ds.handle([{"file": "CLAUDE.md", "old": "Run scripts/tool.py for searches.",
                    "new": "Run scripts/tool.py for every search.", "why": "a"},
                   {"file": "CLAUDE.md", "old": "## Hard constraints\nNever send email.", "new": "## Hard constraints\nNever send email.\n",
                    "why": "b"}], [])
        ds.decide("apply", "1")
        self.assertIn("for every search", self.read("CLAUDE.md"))
        self.assertEqual(json.loads(self.read("scripts/integrity.json"))["CLAUDE.md"],
                         hashlib.sha256(self.read("CLAUDE.md").encode()).hexdigest())
        ds.decide("reject", "2")
        self.assertEqual(ds.pending(), [])
        self.assertIn("`scripts/tool.py`", self.read("README.md"))
        with self.assertRaises(SystemExit):
            ds.decide("apply", "9")

    def test_hook_tells_each_session_once(self):
        ds.handle([{"file": "CLAUDE.md", "old": "Run scripts/tool.py for searches.", "new": "Run it.", "why": "a"}], [])
        hooks = self.tmp / ".claude" / "hooks"
        hooks.mkdir(exist_ok=True)
        shutil.copy(REPO / ".claude" / "hooks" / "docs_pending.py", hooks / "docs_pending.py")
        shutil.copy(REPO / "scripts" / "docs_sync.py", self.tmp / "scripts" / "docs_sync.py")

        def say(sid):
            r = subprocess.run([sys.executable, str(hooks / "docs_pending.py")], capture_output=True, text=True,
                               input=json.dumps({"session_id": sid, "prompt": "hi"}))
            return r.stdout

        first = json.loads(say("s1"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("apply <id>", first)
        self.assertIn("[1] CLAUDE.md", first)
        self.assertEqual(say("s1"), "")
        self.assertNotEqual(say("s2"), "")
        (self.tmp / "techwatch" / "config.json").write_text('{"docs_sync": {"ask_in_sessions": false}}')
        self.assertEqual(say("s4"), "")                                       # turned off: no note
        (self.tmp / "techwatch" / "config.json").unlink()
        (self.tmp / "techwatch" / "state" / "docs-pending.json").write_text('{"next": 2, "items": []}')
        self.assertEqual(say("s3"), "")

    # -- settings
    def test_off_by_default_and_your_own_file_wins(self):
        self.assertFalse(ds.settings()["enabled"])
        self.assertEqual(ds.run(), "docs sync: not due")                     # off: no state, no call
        self.assertFalse((self.tmp / "techwatch" / "state" / "docs-sync.json").exists())
        (self.tmp / "techwatch").mkdir(exist_ok=True)
        (self.tmp / "techwatch" / "config.json").write_text(
            '{"docs_sync": {"enabled": false, "max_words_per_change": 5}}')
        (self.tmp / "my").mkdir()
        (self.tmp / "my" / "docs_sync.json").write_text('{"enabled": true, "automatic": []}')
        cfg = ds.settings()
        self.assertEqual((cfg["enabled"], cfg["automatic"], cfg["max_words_per_change"]), (True, [], 5))
        self.assertIn("started tracking", ds.run())

    def test_public_defaults_are_off(self):
        cfg = json.loads((REPO / "techwatch" / "config.json").read_text(encoding="utf-8"))["docs_sync"]
        self.assertIs(cfg["enabled"], False)
        self.assertIs(ds.DEFAULTS["enabled"], False)

    # -- the weekly run
    def test_weekly_run(self):
        self.assertIn("started tracking", ds.run(now=True))
        self.assertIn("nothing to check", ds.run(now=True))
        (self.tmp / "scripts" / "tool.py").write_text('"""A search tool, now with notes."""\nprint(2)\n')
        self.commit("tool change")
        self.assertIn("dry run", ds.run(now=True, dry_run=True))
        prompt, n = ds.build_request(json.loads(self.read("techwatch/state/docs-sync.json"))["last_sha"],
                                     self.git("rev-parse", "HEAD").strip(), [])
        self.assertEqual(n, 1)
        self.assertIn("search the corpus quickly", prompt)          # the agent's body excerpt
        self.assertNotIn("Rules mention", prompt)                    # never-edited docs aren't sent
        answer = {"changes": [{"file": ".claude/agents/helper.md", "old": "search the corpus quickly",
                               "new": "search the corpus and notes", "why": "notes"}], "notes": ""}
        real, ds.ask_model = ds.ask_model, lambda p, c: ("```json\n" + json.dumps(answer) + "\n```", 0.02)
        real_notify, ds.notify = ds.notify, lambda title, body: None   # never a real phone note from a test
        try:
            self.assertIn("1 applied", ds.run(now=True))
        finally:
            ds.ask_model, ds.notify = real, real_notify
        self.assertIn("and notes", self.read(".claude/agents/helper.md"))
        self.assertIn("not due", ds.run())

    def test_an_unlisted_file_is_sent_once_until_it_changes(self):
        ds.run(now=True)                                   # start tracking
        (self.tmp / "scripts" / "new_thing.py").write_text("x = 1\n")
        self.commit("new script")
        calls = []
        real, ds.ask_model = ds.ask_model, lambda p, c: (calls.append(p) or '{"changes": [], "notes": "fine"}', 0.01)
        try:
            ds.run(now=True)
            self.assertIn("nothing to check", ds.run(now=True))   # looked at already: no second call
            (self.tmp / "scripts" / "new_thing.py").write_text("x = 2\n")
            self.commit("changed")
            ds.run(now=True)
        finally:
            ds.ask_model = real
        self.assertEqual(len(calls), 2)


class WiringTest(unittest.TestCase):
    def test_decide_command_has_one_exact_form(self):
        spec = importlib.util.spec_from_file_location("preflight", REPO / ".claude" / "hooks" / "preflight.py")
        pf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pf)
        base = f"python3 {pf.ROOT}/scripts/docs_sync.py"
        for cmd in (f"{base} apply 1", f"{base} reject 42"):
            self.assertIsNotNone(pf.DOCS_DECIDE_RE.fullmatch(cmd))
        for cmd in (f"{base} apply 1; id", f"{base} apply 0", f"{base} apply x", f"{base} run --now",
                    "python3 scripts/docs_sync.py apply 1"):
            self.assertIsNone(pf.DOCS_DECIDE_RE.fullmatch(cmd), cmd)

    def test_both_modes_ask_the_person_before_applying(self):
        for layer in ("hardened", "casual"):
            s = json.loads((REPO / "layers" / f"{layer}.json").read_text(encoding="utf-8"))
            for verb in ("apply", "reject"):
                cmd = f"python3 {{{{WORKSPACE}}}}/scripts/docs_sync.py {verb} *"
                self.assertIn(cmd, s["sandbox"]["excludedCommands"], layer)
                self.assertIn(f"Bash({cmd})", s["permissions"]["ask"], layer)

    def test_hook_is_wired(self):
        s = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("docs_pending.py", json.dumps(s["hooks"]["UserPromptSubmit"]))


if __name__ == "__main__":
    unittest.main()
