"""Absolute paths in permission rules must be "//..." on every system ("//c/Users/..." on Windows). A single "/"
means relative to the settings file, so rules built as "/" + "C:/Users/..." silently did nothing on Windows: tech
watch's scouts couldn't save their notes, and read-block rules didn't block."""
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "techwatch" / "scripts"))
from rule_paths import rule_path  # noqa: E402

PATH_RULE = re.compile(r"^(Read|Edit|Write)\((.+)\)$")


def path_rules(rules):
    return [m.group(2) for r in rules if (m := PATH_RULE.match(r))]


class TestRulePaths(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(rule_path("/home/ana/ws"), "//home/ana/ws")
        self.assertEqual(rule_path("C:\\Users\\Sky Travis\\ws"), "//c/Users/Sky Travis/ws")
        self.assertEqual(rule_path("C:/Users/Ana"), "//c/Users/Ana")

    def assert_absolute_forms(self, rules):
        """Rules built from this machine's absolute paths (home, the workspace) must use "//"; "/workspace/..."
        style rules are deliberately relative to the project and are left alone."""
        machine = [rule_path(Path.home()).lstrip("/"), rule_path(ROOT).lstrip("/")]
        for p in path_rules(rules):
            self.assertNotRegex(p, r"^/*[A-Za-z]:", f"Windows drive left in a rule: {p}")
            if any(p.lstrip("/").startswith(m) for m in machine):
                self.assertTrue(p.startswith("//"), f"absolute path in a rule needs '//': {p}")

    def test_scout_settings(self):
        import techwatch
        p = techwatch.scout_settings(("inbox", "proposals"))["permissions"]
        self.assert_absolute_forms(p["allow"] + p["deny"])
        self.assertIn(f"Edit({rule_path(techwatch.TW / 'inbox')}/**)", p["allow"])

    def test_scout_settings_for_a_windows_workspace(self):
        """The reported bug: on Windows the scouts' Edit rule came out as /C:/... and every save was refused."""
        import techwatch
        from unittest import mock
        ws = Path("C:/Users/Sky Travis/repo/ai-welfare-workspace")
        with mock.patch.object(techwatch, "WS", ws), mock.patch.object(techwatch, "TW", ws / "techwatch"):
            p = techwatch.scout_settings(("inbox",))["permissions"]
        self.assertIn("Edit(//c/Users/Sky Travis/repo/ai-welfare-workspace/techwatch/inbox/**)", p["allow"])
        self.assertIn("Read(//c/Users/Sky Travis/repo/ai-welfare-workspace/**)", p["allow"])
        for r in p["allow"] + p["deny"]:
            self.assertNotRegex(r, r"\(/[A-Za-z]:", r)

    def test_followups_settings(self):
        import followups
        p = followups.settings()["permissions"]
        self.assert_absolute_forms(p["allow"] + p["deny"])

    def test_layers_after_setup_fills_them(self):
        spec = importlib.util.spec_from_file_location("setup_mod", ROOT / "setup.py")
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        for name in ("casual", "hardened"):
            layer = setup.fill(json.loads((ROOT / "layers" / f"{name}.json").read_text(encoding="utf-8")))
            perms = layer.get("permissions", {})
            self.assert_absolute_forms(perms.get("allow", []) + perms.get("ask", []) + perms.get("deny", []))


if __name__ == "__main__":
    unittest.main()
