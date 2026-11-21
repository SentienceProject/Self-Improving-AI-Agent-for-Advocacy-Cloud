"""Tests for the agent browser: the guard hook (readable prompts, check-ins) and what browser_setup.py writes.

Nothing here starts Firefox, downloads anything or touches ~/.local/share/agent-firefox: the hook gets made-up
transcripts, and the setup functions write to temporary folders.

    python3 -m unittest tests.test_browser -v
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = load("browser_guard", REPO / ".claude" / "hooks" / "browser_guard.py")
setup = load("browser_setup", REPO / "scripts" / "browser_setup.py")

SNAPSHOT = """Snapshot [DOM truncated]

uid=e1 body
  uid=e30 nav "Personal tools"
    uid=e29 div
      uid=e24 a "Log in" href="https://en.wikipedia.org/w/..."
  uid=e16 form
    uid=e11 input "Search Wikipedia"
    uid=e15 button "Search" text="Search"
"""


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def turn(content):
    return json.dumps({"type": "user", "timestamp": stamp(), "message": {"role": "user", "content": content}}) + "\n"


def result(text):
    return turn([{"type": "tool_result", "tool_use_id": "t", "content": [{"type": "text", "text": text}]}])


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.main = self.tmp / "session.jsonl"
        self.agent = self.tmp / "session" / "subagents" / "agent-a1.jsonl"
        self.agent.parent.mkdir(parents=True)
        page = result("[0] → https://en.wikipedia.org/wiki/Octopus") + result(SNAPSHOT)
        self.main.write_text(turn("hello") + page)
        self.agent.write_text(turn("Please find the article") + page)
        patcher = mock.patch.object(guard, "STATE", self.tmp / "state")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_hook(self, tool, args, agent_type=None):
        d = {"transcript_path": str(self.main), "tool_name": "mcp__firefox__" + tool, "tool_input": args}
        if agent_type:
            d.update(agent_id="a1", agent_type=agent_type)
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(d))), contextlib.redirect_stdout(out):
            try:
                guard.main()
            except SystemExit:
                pass
        return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue().strip() else {}

    def test_click_prompt_says_what_and_where(self):
        o = self.run_hook("click_by_uid", {"uid": "e24"})
        self.assertEqual(o["permissionDecision"], "ask")
        action = o["updatedInput"]["action"]
        self.assertIn('"Log in"', action)
        self.assertIn('nav "Personal tools"', action)
        self.assertIn("https://en.wikipedia.org/wiki/Octopus", action)
        self.assertEqual(o["updatedInput"]["uid"], "e24")   # the real argument is kept

    def test_typing_and_unknown_elements(self):
        o = self.run_hook("type_text", {"text": "animal sentience", "uid": "e11", "submitKey": "Enter"})
        self.assertIn('Type "animal sentience" into input "Search Wikipedia", then press Enter',
                      o["updatedInput"]["action"])
        o = self.run_hook("click_by_uid", {"uid": "e999"})
        self.assertIn("not in the last snapshot", o["updatedInput"]["action"])

    def test_reading_needs_no_prompt(self):
        self.assertEqual(self.run_hook("get_page_text", {}), {})

    def test_page_visits_check_in_after_the_limit(self):
        _, visits, _ = guard.CADENCE["browser-haiku"]
        for _ in range(visits):
            self.assertEqual(self.run_hook("navigate_page", {"url": "https://x"}, "browser-haiku"), {})
        o = self.run_hook("navigate_page", {"url": "https://x"}, "browser-haiku")
        self.assertEqual(o["permissionDecision"], "deny")
        self.assertIn("Checkpoint 1", o["permissionDecisionReason"])
        # Still blocked, for reading too, until the main session replies.
        self.assertEqual(self.run_hook("get_page_text", {}, "browser-haiku")["permissionDecision"], "deny")
        time.sleep(1.1)
        with self.agent.open("a") as f:
            f.write(turn("Thanks, please continue."))
        self.assertEqual(self.run_hook("navigate_page", {"url": "https://x"}, "browser-haiku"), {})

    def test_interactions_check_in_sooner_and_page_check_comes_due(self):
        acts, _, check = guard.CADENCE["browser-haiku"]
        for n in range(1, check + 1):
            for _ in range(acts):
                self.assertEqual(self.run_hook("click_by_uid", {"uid": "e15"}, "browser-haiku")["permissionDecision"],
                                 "ask")
            o = self.run_hook("click_by_uid", {"uid": "e15"}, "browser-haiku")
            self.assertEqual(o["permissionDecision"], "deny")
            self.assertEqual("PAGE CHECK DUE" in o["permissionDecisionReason"], n == check)
            time.sleep(1.1)
            with self.agent.open("a") as f:
                f.write(turn("continue"))

    def test_other_agents_and_the_main_session_never_check_in(self):
        for _ in range(60):
            self.assertEqual(self.run_hook("navigate_page", {"url": "https://x"}, "general-purpose"), {})
            self.assertEqual(self.run_hook("navigate_page", {"url": "https://x"}), {})


class SetupTest(unittest.TestCase):
    def test_sandbox_hides_your_files_and_its_own_programs(self):
        w = setup.WRAPPER
        for hidden in ("--tmpfs /home", "--tmpfs /tmp", "--tmpfs /run", "--tmpfs \"$d\""):
            self.assertIn(hidden, w)
        self.assertIn('--ro-bind "$A" "$A"', w)   # its own folder read-only (mcp-server.sh, geckodriver)
        self.assertIn("for d in profile downloads home sandbox-tmp", w)
        self.assertIn("SSH_AUTH_SOCK", w)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", w)

    def test_default_policies_and_your_overlay(self):
        p = setup.policies()["policies"]
        self.assertTrue(p["DisableTelemetry"])
        self.assertIn("file:///*", p["WebsiteFilter"]["Block"])
        self.assertTrue(p["Permissions"]["Camera"]["BlockNewRequests"])
        with tempfile.TemporaryDirectory() as tmp:
            mine = Path(tmp) / "browser-policies.json"
            mine.write_text(json.dumps({"policies": {"HttpsOnlyMode": None, "FirefoxHome": {"Stories": True},
                                                     "SearchEngines": {"Default": "DuckDuckGo"}}}))
            with mock.patch.object(setup, "MY_POLICIES", mine), contextlib.redirect_stdout(io.StringIO()):
                q = setup.policies()["policies"]
        self.assertNotIn("HttpsOnlyMode", q)
        self.assertTrue(q["FirefoxHome"]["Stories"])
        self.assertFalse(q["FirefoxHome"]["SponsoredStories"])   # merged, not replaced
        self.assertEqual(q["SearchEngines"]["Default"], "DuckDuckGo")

    def test_agents_and_hook_are_written(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            setup.write_agents(tmp)
            setup.add_hook_and_rules(tmp)
            setup.add_hook_and_rules(tmp)   # running setup again doesn't duplicate anything
            for model in ("haiku", "sonnet", "opus"):
                text = (Path(tmp) / ".claude" / "agents" / f"browser-{model}.md").read_text()
                self.assertIn(f"model: {model}", text)
                self.assertNotIn("upload_file_by_uid", text)   # uploads stay with the main session
            local = json.loads((Path(tmp) / ".claude" / "settings.local.json").read_text())
        hooks = [h for h in local["hooks"]["PreToolUse"] if h["matcher"] == "mcp__firefox__.*"]
        self.assertEqual(len(hooks), 1)
        self.assertIn("browser_guard.py", hooks[0]["hooks"][0]["command"])
        self.assertEqual(len(local["permissions"]["ask"]), len(set(local["permissions"]["ask"])))
        self.assertIn("mcp__firefox__click_by_uid", local["permissions"]["ask"])


if __name__ == "__main__":
    unittest.main()


PW_PAGE = """### Page state
- Page URL: https://example.org/search
- Page Title: Search
- Page Snapshot:
```yaml
- generic [ref=e1]:
  - form "Search" [ref=e2]:
    - textbox "Query" [ref=e3]
    - button "Go" [ref=e4] [cursor=pointer]
```"""


class CloudBrowserGuardTest(unittest.TestCase):
    """The same guard for the cloud sessions' Playwright browser (mcp__cloud-browser__*)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.main = self.tmp / "session.jsonl"
        self.agent = self.tmp / "session" / "subagents" / "agent-a1.jsonl"
        self.agent.parent.mkdir(parents=True)
        page = result(PW_PAGE)
        self.main.write_text(turn("hello") + page)
        self.agent.write_text(turn("Please search") + page)
        patcher = mock.patch.object(guard, "STATE", self.tmp / "state")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_pw(self, tool, args, agent_type=None):
        d = {"transcript_path": str(self.main), "tool_name": "mcp__cloud-browser__" + tool, "tool_input": args}
        if agent_type:
            d.update(agent_id="a1", agent_type=agent_type)
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(d))), contextlib.redirect_stdout(out):
            try:
                guard.main()
            except SystemExit:
                pass
        return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue().strip() else {}

    def test_prompts_use_the_page_s_own_labels(self):
        o = self.run_pw("browser_type", {"target": "e3", "element": "the box", "text": "fish", "submit": True})
        self.assertEqual(o["permissionDecision"], "ask")
        self.assertEqual(o["updatedInput"]["action"], 'Type "fish" into textbox "Query" (in form "Search"), then '
                                                      "press Enter on https://example.org/search")
        o = self.run_pw("browser_click", {"target": "e99", "element": "Next page"})
        self.assertIn("Claude's description", o["updatedInput"]["action"])
        self.assertEqual(self.run_pw("browser_snapshot", {}), {})        # reading: no prompt
        self.assertEqual(self.run_pw("browser_navigate", {"url": "https://x"}), {})

    def test_cloud_agents_check_in(self):
        acts, visits, _ = guard.CADENCE["cloud-browser-haiku"]
        for _ in range(acts):
            self.assertEqual(self.run_pw("browser_click", {"target": "e4"}, "cloud-browser-haiku")["permissionDecision"],
                             "ask")
        o = self.run_pw("browser_click", {"target": "e4"}, "cloud-browser-haiku")
        self.assertEqual(o["permissionDecision"], "deny")
        self.assertIn("Checkpoint 1", o["permissionDecisionReason"])


class CloudAgentFiles(unittest.TestCase):
    def test_agent_files_match_the_template(self):
        cb = load("cloud_browser", REPO / "scripts" / "cloud_browser.py")
        for name, text in cb.agent_files().items():
            self.assertEqual((REPO / ".claude" / "agents" / name).read_text(encoding="utf-8"), text,
                             f"{name} is out of date: python3 scripts/cloud_browser.py --write-agents")
            self.assertNotIn("browser_run_code_unsafe", text.split("---")[1])   # main session only
