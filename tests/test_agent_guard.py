"""Tests for .claude/hooks/agent_guard.py (write and shell limits for the researcher, ideator and drafter).

Each test copies the guard (and preflight.py, whose search-command pattern it reuses) into a throwaway repo layout,
feeds it the JSON Claude Code would send, and checks the exit code: 0 = allowed through, 2 = blocked.

    python3 tests/test_agent_guard.py -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


ROOT_DIR = Path(__file__).resolve().parents[1]

class AgentGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "repo"
        (self.root / ".claude" / "hooks").mkdir(parents=True)
        for d in ("ideas", "outbox", "drafts", "corpus", "logs"):
            (self.root / "workspace" / d).mkdir(parents=True)
        for f in ("agent_guard.py", "preflight.py", "guard_paths.py"):
            shutil.copy(REPO / ".claude" / "hooks" / f, self.root / ".claude" / "hooks" / f)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_guard(self, role, tool, tool_input):
        r = subprocess.run([sys.executable, str(self.root / ".claude" / "hooks" / "agent_guard.py"), role],
                           input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
                           capture_output=True, text=True)
        return r.returncode

    def write(self, role, rel, tool="Write"):
        return self.run_guard(role, tool, {"file_path": str(self.root / rel), "content": "x"})

    def test_ideator_writes_only_ideas(self):
        self.assertEqual(self.write("ideator", "workspace/ideas/2026-01-01-01-wild-filtered.md"), 0)
        self.assertEqual(self.write("ideator", "workspace/corpus/planted.txt"), 2)
        self.assertEqual(self.write("ideator", "workspace/memory.md", "Edit"), 2)
        self.assertEqual(self.write("ideator", "CLAUDE.md"), 2)
        self.assertEqual(self.write("ideator", "workspace/ideas"), 2)   # the folder itself

    def test_drafter_writes_outbox_and_drafts(self):
        self.assertEqual(self.write("drafter", "workspace/outbox/2026-01-01-brief.md"), 0)
        self.assertEqual(self.write("drafter", "workspace/drafts/alt.md", "Edit"), 0)
        self.assertEqual(self.write("drafter", "workspace/corpus/x.txt"), 2)
        self.assertEqual(self.write("drafter", "workspace/logs/run.jsonl"), 2)
        self.assertEqual(self.write("drafter", "workspace/outbox/../corpus/x.txt"), 2)

    def test_relative_paths_resolve_against_repo(self):
        self.assertEqual(self.run_guard("drafter", "Write", {"file_path": "workspace/outbox/a.md"}), 0)
        self.assertEqual(self.run_guard("drafter", "Write", {"file_path": "workspace/corpus/a.md"}), 2)

    def test_symlink_out_of_allowed_folder_blocked(self):
        os.symlink(self.root / "workspace" / "corpus", self.root / "workspace" / "outbox" / "link")
        self.assertEqual(self.write("drafter", "workspace/outbox/link/x.txt"), 2)

    def test_researcher_has_no_shell_and_no_writes(self):
        self.assertEqual(self.run_guard("researcher", "Bash", {"command": "grep -r x workspace/corpus"}), 2)
        self.assertEqual(self.run_guard("researcher", "Bash", {"command": "echo hi > workspace/corpus/a"}), 2)
        self.assertEqual(self.write("researcher", "workspace/ideas/a.md"), 2)

    def test_researcher_may_run_semantic_search(self):
        py = Path.home() / ".venvs" / "ai-welfare-search" / "bin" / "python"
        good = f'{py} {self.root}/scripts/corpus_search.py "fish pain evidence" --k 8'
        self.assertEqual(self.run_guard("researcher", "Bash", {"command": good}), 0)
        self.assertEqual(self.run_guard("researcher", "Bash", {"command": good + "; rm -rf workspace"}), 2)

    def test_every_role_may_run_find_text_and_nothing_else(self):
        good = f'python3 {self.root}/scripts/find_text.py -i "fish pain" workspace/corpus'
        for role in ("researcher", "helper", "critic", "verifier", "ideator", "drafter"):
            with self.subTest(role=role):
                self.assertEqual(self.run_guard(role, "Bash", {"command": good}), 0)
                self.assertEqual(self.run_guard(role, "Bash", {"command": good + "; id"}), 2)
                self.assertEqual(self.run_guard(role, "Bash", {"command": "grep -r fish workspace"}), 2)
                self.assertEqual(self.run_guard(role, "PowerShell", {"command": good}), 0)

    def test_semantic_search_only_for_researcher_and_helper(self):
        py = Path.home() / ".venvs" / "ai-welfare-search" / "bin" / "python"
        good = f'{py} {self.root}/scripts/corpus_search.py "fish pain evidence" --k 8'
        self.assertEqual(self.run_guard("helper", "Bash", {"command": good}), 0)
        for role in ("critic", "verifier", "ideator", "drafter"):
            with self.subTest(role=role):
                self.assertEqual(self.run_guard(role, "Bash", {"command": good}), 2)

    def test_read_only_roles_write_nothing(self):
        for role in ("researcher", "critic", "verifier"):
            with self.subTest(role=role):
                self.assertEqual(self.write(role, "workspace/ideas/a.md"), 2)
                self.assertEqual(self.write(role, "workspace/outbox/a.md", "Edit"), 2)
                self.assertEqual(self.write(role, "workspace/helper/a.md"), 2)

    def test_helper_writes_only_its_folder(self):
        self.assertEqual(self.write("helper", "workspace/helper/outline.md"), 0)
        self.assertEqual(self.write("helper", "workspace/helper/panel/notes.md", "Edit"), 0)
        for rel in ("workspace/ideas/a.md", "workspace/outbox/a.md", "workspace/corpus/planted.txt",
                    "workspace/memory.md", "workspace/helper/../corpus/x.txt", "workspace/helper"):
            with self.subTest(rel=rel):
                self.assertEqual(self.write("helper", rel), 2)
        (self.root / "workspace" / "helper").mkdir()
        os.symlink(self.root / "workspace" / "corpus", self.root / "workspace" / "helper" / "link")
        self.assertEqual(self.write("helper", "workspace/helper/link/x.txt"), 2)

    def test_citation_check_for_helper_and_verifier_only(self):
        good = f"python3 {self.root}/scripts/check_citations.py workspace/outbox/2026-01-01-brief.md"
        for role in ("helper", "verifier"):
            with self.subTest(role=role):
                self.assertEqual(self.run_guard(role, "Bash", {"command": good}), 0)
                self.assertEqual(self.run_guard(role, "Bash", {"command": good + " --offline --json"}), 0)
                for bad in (good + "; id", good + " > workspace/corpus/x", good + " $(id)",
                            "python3 scripts/check_citations.py workspace/outbox/a.md",   # relative script
                            f"python3 {self.root}/scripts/check_citations.py 'a b.md'"):
                    self.assertEqual(self.run_guard(role, "Bash", {"command": bad}), 2, bad)
        for role in ("researcher", "critic", "ideator", "drafter"):
            with self.subTest(role=role):
                self.assertEqual(self.run_guard(role, "Bash", {"command": good}), 2)

    def test_commands_matched_as_given(self):
        """The guard doesn't strip: the preflight gate matches the raw command, so the two agree."""
        good = f'python3 {self.root}/scripts/find_text.py "fish"'
        self.assertEqual(self.run_guard("critic", "Bash", {"command": good}), 0)
        self.assertEqual(self.run_guard("critic", "Bash", {"command": " " + good}), 2)
        self.assertEqual(self.run_guard("critic", "Bash", {"command": good + "\n"}), 2)

    def test_read_tools_pass(self):
        self.assertEqual(self.run_guard("researcher", "Read", {"file_path": "/etc/hostname"}), 0)

    def test_unknown_role_and_bad_input_fail_closed(self):
        self.assertEqual(self.write("nobody", "workspace/ideas/a.md"), 2)
        r = subprocess.run([sys.executable, str(self.root / ".claude" / "hooks" / "agent_guard.py"), "drafter"],
                           input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class TestPathsAcrossSystems(unittest.TestCase):
    """The guards' path checks on Windows-style paths (a reported bug: a folder name with a space, and backslashes,
    blocked the brainstorm agent from its own file), and both kinds of run ID."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("guard_paths", ROOT_DIR / ".claude" / "hooks" / "guard_paths.py")
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)

    def test_windows_path_styles_match_the_folder(self):
        base = "C:\\Users\\Jo Example\\repo\\ai-welfare-workspace\\workspace\\ideas"
        for raw in ("C:\\Users\\Jo Example\\repo\\ai-welfare-workspace\\workspace\\ideas\\a-wild-1.md",
                    "/c/Users/Jo Example/repo/ai-welfare-workspace/workspace/ideas/a-wild-1.md",
                    "/mnt/c/Users/Jo Example/repo/ai-welfare-workspace/workspace/ideas/a-wild-1.md"):
            self.assertTrue(self.g.norm(raw).startswith(self.g.norm(base) + "/"), raw)

    def test_dotdot_cant_leave_the_folder(self):
        base = "C:\\ws\\workspace\\ideas"
        self.assertFalse(self.g.norm("C:\\ws\\workspace\\ideas\\..\\memory.md").startswith(self.g.norm(base) + "/"))

    def wild(self, rel):
        ev = {"tool_name": "Write", "tool_input": {"file_path": str(ROOT_DIR / rel)}, "cwd": str(ROOT_DIR)}
        return subprocess.run([sys.executable, str(ROOT_DIR / ".claude" / "hooks" / "wild_write_guard.py"), "--always"],
                              input=json.dumps(ev), capture_output=True, text=True).returncode

    def test_wild_ideator_run_ids_in_both_modes(self):
        self.assertEqual(self.wild("workspace/ideas/2026-11-04-01-wild-1.md"), 0)      # hardened
        self.assertEqual(self.wild("workspace/ideas/2026-11-04-oped-wild-2.md"), 0)    # casual
        self.assertEqual(self.wild("workspace/ideas/sub/x-wild-1.md"), 2)
        self.assertEqual(self.wild("workspace/memory-wild-1.md"), 2)
        self.assertEqual(self.wild("workspace/ideas/x-wild-9.md"), 2)


if __name__ == "__main__":
    unittest.main()
