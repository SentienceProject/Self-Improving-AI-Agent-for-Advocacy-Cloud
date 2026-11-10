"""Tests for scripts/docs_sync.py (generated doc sections and doc reminders).

    python3 tests/test_docs_sync.py -v
"""
import importlib.util
import shutil
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
        self.assertIn("| critic | nothing | text search |", table)


class RemindersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old_root, ds.ROOT = ds.ROOT, self.tmp
        (self.tmp / "README.md").write_text("Run `scripts/check_citations.py`.\nThe `helper` agent.\nOther.\n")

    def tearDown(self):
        ds.ROOT = self.old_root
        shutil.rmtree(self.tmp)

    def test_names_docs_that_mention_changed_files(self):
        found = ds.reminders(["scripts/check_citations.py", ".claude/agents/helper.md", "scripts/x.py"],
                             ["README.md"])
        self.assertEqual(found, {"README.md": [("scripts/check_citations.py", [1]),
                                               (".claude/agents/helper.md", [2])]})

    def test_no_reminder_when_the_doc_changes_too(self):
        self.assertEqual(ds.reminders(["scripts/check_citations.py", "README.md"], ["README.md"]), {})


if __name__ == "__main__":
    unittest.main()
