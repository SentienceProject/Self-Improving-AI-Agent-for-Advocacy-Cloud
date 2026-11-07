"""Tests for techwatch/scripts/outcome.py: which finished runs count as ok.

    python3 -m unittest discover -s techwatch/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import outcome  # noqa: E402


def run(*denials):
    return {"is_error": False, "subtype": "success", "total_cost_usd": 0.6, "permission_denials": list(denials)}


def write(path, tool="Write"):
    return {"tool_name": tool, "tool_input": {"file_path": path, "content": "x"}}


class ClassifyTest(unittest.TestCase):
    def test_clean_run_is_ok(self):
        self.assertEqual(outcome.classify(run()), ("ok",))

    def test_refused_save_of_real_work_is_reported(self):
        res = outcome.classify(run(write("/home/u/ws/techwatch/proposals/2026-01-01-x.md")))
        self.assertEqual(res[0], "done")
        self.assertIn("1 file save(s) refused", res[1])

    def test_refused_auto_memory_note_is_not_a_failure(self):
        # A stage run (2026-11-07) did its job, then tried to save a Claude Code memory note; the guard refused it,
        # rightly, and the run was wrongly reported as failed.
        mem = "/home/u/.claude/projects/-home-u-repo/memory/"
        res = outcome.classify(run(write(mem + "carried-over-proposals.md"), write(mem + "MEMORY.md", "Edit"),
                                   {"tool_name": "Glob", "tool_input": {"pattern": "**", "path": "/home/u"}}))
        self.assertEqual(res, ("ok",))
        win = "C:\\Users\\u\\.claude\\projects\\C--repo\\memory\\MEMORY.md"
        self.assertEqual(outcome.classify(run(write(win))), ("ok",))

    def test_memory_refusal_does_not_hide_a_real_one(self):
        res = outcome.classify(run(write("/home/u/.claude/projects/p/memory/a.md"),
                                   write("/home/u/ws/techwatch/inbox/note.md")))
        self.assertEqual(res[0], "done")
        self.assertIn("techwatch/inbox/note.md", res[1])

    def test_files_merely_named_memory_still_count(self):
        res = outcome.classify(run(write("/home/u/ws/workspace/memory.md")))
        self.assertEqual(res[0], "done")


if __name__ == "__main__":
    unittest.main()
