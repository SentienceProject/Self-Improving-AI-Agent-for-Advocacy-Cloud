"""setup.py: switching layers keeps the user's own local settings and leaves nothing of the old layer behind."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_mod", ROOT / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def layer(name):
    return setup.fill(json.loads((ROOT / "layers" / f"{name}.json").read_text(encoding="utf-8")))


class TestLayers(unittest.TestCase):
    def test_round_trip_keeps_user_settings(self):
        local = {"permissions": {"allow": ["Bash(ls *)"]}, "model": "opus"}
        start = copy.deepcopy(local)
        for a, b in (("casual", "hardened"), ("hardened", "casual")):
            setup.merge(local, layer(a))
            setup.unmerge(local, layer(a))
            setup.merge(local, layer(b))
            setup.unmerge(local, layer(b))
        self.assertEqual(local, start)

    def test_your_own_default_mode_survives_a_mode_switch(self):
        local = {"permissions": {"defaultMode": "auto"}}
        replaced = []
        setup.merge(local, layer("hardened"), replaced)
        self.assertEqual(local["permissions"]["defaultMode"], "default")
        setup.unmerge(local, layer("hardened"))
        setup.put_back(local, replaced)
        setup.merge(local, layer("casual"))
        self.assertEqual(local["permissions"]["defaultMode"], "auto")

    def test_allow_all_keeps_every_sending_rule(self):
        for rule in layer("casual")["permissions"]["ask"]:
            if rule.startswith("mcp__") or "push" in rule or "Mail" in rule:
                self.assertTrue(setup.is_sending(rule), rule)

    def test_layers_fill_every_placeholder(self):
        for name in ("casual", "hardened"):
            self.assertNotIn("{{", json.dumps(layer(name)), name)

    def test_tracked_settings_have_no_machine_paths(self):
        text = (ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("{{", text)
        self.assertNotIn("/home/", text)

    def test_tracked_settings_ask_and_block_nothing(self):
        """Casual users get at least what plain Claude Code allows: every ask/deny lives in a layer they can change."""
        s = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertFalse(s["permissions"].get("ask") or s["permissions"].get("deny"))
        self.assertTrue(s["sandbox"]["allowUnsandboxedCommands"])
        self.assertNotIn("deny", layer("casual")["permissions"])

    def test_casual_asks_before_sending(self):
        asks = layer("casual")["permissions"]["ask"]
        for rule in ("Bash(git push *)", "mcp__claude_ai_Gmail__send_message", "mcp__*send*", "mcp__*post*"):
            self.assertIn(rule, asks)

    def test_overrides(self):
        ov = {"allow": ["Bash(git push *)"], "ask": ["WebFetch"], "off_casual": ["Bash(sudo *)"],
              "off_hardened": ["Bash(ssh *)"]}
        c = setup.adjust(layer("casual"), ov, "casual")["permissions"]
        self.assertIn("Bash(git push *)", c["allow"], "--preapprove adds an allow rule")
        self.assertNotIn("Bash(git push *)", c["ask"])
        self.assertIn("WebFetch", c["ask"])
        self.assertNotIn("Bash(sudo *)", c["ask"], "--allow drops the workspace's ask in this mode")
        h = setup.adjust(layer("hardened"), ov, "hardened")["permissions"]
        self.assertIn("Bash(git push *)", h["deny"], "pre-approving never lifts a hardened block")
        self.assertIn("Bash(sudo *)", h["deny"], "a change in casual mode doesn't loosen hardened mode")
        self.assertNotIn("Bash(ssh *)", h["deny"])

    def test_a_deleted_settings_file_doesnt_drop_the_rules(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            old = setup.OVERRIDES
            setup.OVERRIDES = Path(d) / "permissions.json"
            try:
                setup.notice_removals({}, layer("casual"), "casual")
                self.assertFalse(setup.OVERRIDES.exists())
            finally:
                setup.OVERRIDES = old

    def test_allow_all_keeps_sending_asks(self):
        asks = layer("casual")["permissions"]["ask"]
        kept = [r for r in asks if setup.is_sending(r)]
        self.assertIn("mcp__claude_ai_Gmail__reply", kept)
        self.assertNotIn("Edit(./CLAUDE.md)", kept)
        self.assertNotIn("Bash(sudo *)", kept)

    def test_hardened_keeps_its_rails(self):
        h = layer("hardened")
        self.assertTrue(h["sandbox"]["failIfUnavailable"])
        self.assertFalse(h["sandbox"]["allowUnsandboxedCommands"])
        self.assertIn("PreToolUse", h["hooks"])
        self.assertTrue(all(str(ROOT.as_posix()) in c for c in h["sandbox"]["excludedCommands"]))


if __name__ == "__main__":
    unittest.main()
