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
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class AgentGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "repo"
        (self.root / ".claude" / "hooks").mkdir(parents=True)
        for d in ("ideas", "outbox", "drafts", "corpus", "logs"):
            (self.root / "workspace" / d).mkdir(parents=True)
        for f in ("agent_guard.py", "preflight.py"):
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

    def test_read_tools_pass(self):
        self.assertEqual(self.run_guard("researcher", "Read", {"file_path": "/etc/hostname"}), 0)

    def test_unknown_role_and_bad_input_fail_closed(self):
        self.assertEqual(self.write("nobody", "workspace/ideas/a.md"), 2)
        r = subprocess.run([sys.executable, str(self.root / ".claude" / "hooks" / "agent_guard.py"), "drafter"],
                           input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
