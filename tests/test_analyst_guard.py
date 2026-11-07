"""Tests for .claude/hooks/analyst_guard.py (the analyst's PreToolUse guard).

Each test copies the guard into a throwaway repo layout, feeds it the JSON
Claude Code would send, and checks the exit code: 0 = allowed through,
2 = blocked. Standard library only.

    python3 tests/test_analyst_guard.py -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if os.name == "nt":   # they use Linux and macOS file features (O_NOFOLLOW, fcntl); so does hardened mode
    raise unittest.SkipTest("hardened mode's scripts need Linux, macOS or WSL2, not native Windows")

REPO = Path(__file__).resolve().parents[1]
GUARD_SRC = REPO / ".claude" / "hooks" / "analyst_guard.py"

GOOD = "python3 scripts/run_analysis.py r1 workspace/analysis/r1/a.py"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "repo"
        (self.root / ".claude" / "hooks").mkdir(parents=True)
        (self.root / "workspace" / "analysis" / "r1").mkdir(parents=True)
        (self.root / "workspace" / "analysis" / "r2").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.guard = self.root / ".claude" / "hooks" / "analyst_guard.py"
        shutil.copy(GUARD_SRC, self.guard)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def abs_(self, cmd):
        """Rewrite the relative command forms used in these tests to the
        absolute forms the hooks now require, for this test's repo."""
        r = str(self.root)
        return (cmd.replace("bash scripts/sandbox_probe.sh", f"bash {r}/scripts/sandbox_probe.sh")
                   .replace(" scripts/run_analysis.py", f" {r}/scripts/run_analysis.py")
                   .replace(" scripts/corpus_search.py", f" {r}/scripts/corpus_search.py"))

    def run_guard(self, payload, raw=None):
        stdin = raw if raw is not None else json.dumps(payload)
        r = subprocess.run([sys.executable, str(self.guard)], input=stdin,
                           capture_output=True, text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", "")})
        return r.returncode, r.stderr

    def bash(self, cmd, raw=False):
        cmd = cmd if raw else self.abs_(cmd)
        return self.run_guard({"tool_name": "Bash",
                               "tool_input": {"command": cmd}})[0]

    def write(self, path):
        return self.run_guard({"tool_name": "Write",
                               "tool_input": {"file_path": str(path),
                                              "content": "x"}})[0]

    def assertAllowed(self, code):
        self.assertEqual(code, 0)

    def assertBlocked(self, code):
        self.assertEqual(code, 2)


class TestBash(GuardTest):
    def test_exact_command_allowed(self):
        self.assertAllowed(self.bash(GOOD))

    def test_python_alias_allowed(self):
        self.assertAllowed(self.bash(GOOD.replace("python3", "python", 1)))

    def test_nested_script_allowed(self):
        self.assertAllowed(self.bash(
            "python3 scripts/run_analysis.py r1 workspace/analysis/r1/sub/a.py"))

    def test_shell_syntax_blocked(self):
        for extra in ["; ls", " && ls", " || ls", " | cat", " > out.txt",
                      " < in.txt", " & ", " $(id)", " `id`", "\nls", "\tx",
                      " 'x'", ' "x"', " *", " ?", " # c", " \\", " ~"]:
            with self.subTest(extra=extra):
                self.assertBlocked(self.bash(GOOD + extra))

    def test_relative_runner_blocked(self):
        self.assertBlocked(self.bash(GOOD, raw=True))

    def test_other_repo_runner_blocked(self):
        self.assertBlocked(self.bash(GOOD.replace(" scripts/", " /tmp/evil/scripts/"), raw=True))

    def test_shell_syntax_inside_path_blocked(self):
        # Shell syntax glued into the script path, still ending in .py.
        for path in ["workspace/analysis/r1/$(id).py",
                     "workspace/analysis/r1/`id`.py",
                     "workspace/analysis/r1/a;ls;b.py",
                     "workspace/analysis/r1/a&&b.py",
                     "workspace/analysis/r1/a|b.py",
                     "workspace/analysis/r1/a>b.py",
                     "workspace/analysis/r1/*.py",
                     "workspace/analysis/r1/$HOME.py",
                     "workspace/analysis/r1/a\nb.py",
                     "workspace/analysis/r1/a'b.py"]:
            with self.subTest(path=path):
                self.assertBlocked(self.bash(
                    f"python3 scripts/run_analysis.py r1 {path}"))

    def test_env_prefix_blocked(self):
        self.assertBlocked(self.bash("DOCKER_HOST=x " + GOOD))
        self.assertBlocked(self.bash("PATH=/tmp " + GOOD))

    def test_other_commands_blocked(self):
        for cmd in ["ls", "python3 -c print(1)", "python3 x.py",
                    "docker run hello-world", "bash", "sh -c ls",
                    "python3 scripts/other.py r1 workspace/analysis/r1/a.py",
                    "python3 ./scripts/run_analysis.py r1 workspace/analysis/r1/a.py",
                    "python3.11 scripts/run_analysis.py r1 workspace/analysis/r1/a.py",
                    "pip install requests", ""]:
            with self.subTest(cmd=cmd):
                self.assertBlocked(self.bash(cmd))

    def test_extra_or_missing_args_blocked(self):
        self.assertBlocked(self.bash(GOOD + " extra"))
        self.assertBlocked(self.bash("python3 scripts/run_analysis.py r1"))
        self.assertBlocked(self.bash("python3 -I " + GOOD[8:]))

    def test_bad_run_ids_blocked(self):
        for rid in ["-r1", "_r1", ".", "..", "r" * 65]:
            with self.subTest(rid=rid):
                self.assertBlocked(self.bash(
                    f"python3 scripts/run_analysis.py {rid} "
                    f"workspace/analysis/{rid}/a.py"))

    def test_script_paths_blocked(self):
        for path in ["workspace/analysis/r2/a.py",        # other run
                     "workspace/analysis/r1/../r2/a.py",  # ..
                     "workspace/analysis/r1/./a.py",
                     "workspace/analysis/r1//a.py",
                     "workspace/analysis/r1/a.txt",
                     "workspace/analysis/r1",
                     "/etc/passwd.py",
                     "scripts/run_analysis.py"]:
            with self.subTest(path=path):
                self.assertBlocked(self.bash(
                    f"python3 scripts/run_analysis.py r1 {path}"))


class TestWrite(GuardTest):
    def a(self, *parts):
        return self.root.joinpath("workspace", "analysis", *parts)

    def test_write_in_run_dir_allowed(self):
        self.assertAllowed(self.write(self.a("r1", "a.py")))
        self.assertAllowed(self.write(self.a("r1", "sub", "out.csv")))
        self.assertAllowed(self.write(self.a("new-run", "a.py")))

    def test_write_outside_blocked(self):
        for p in [self.root / "scripts" / "run_analysis.py",
                  self.root / ".claude" / "hooks" / "analyst_guard.py",
                  self.root / ".claude" / "settings.json",
                  self.root / "workspace" / "logs" / "r1-exec-1.json",
                  self.root / "workspace" / "requests" / "packages.md",
                  self.root / "workspace" / "corpus" / "x.txt",
                  self.outside / "x.py", Path("/etc/passwd")]:
            with self.subTest(p=p):
                self.assertBlocked(self.write(p))

    def test_write_directly_in_analysis_blocked(self):
        self.assertBlocked(self.write(self.a("a.py")))

    def test_bad_run_folder_name_blocked(self):
        self.assertBlocked(self.write(self.a("_bad", "a.py")))
        self.assertBlocked(self.write(self.a("bad name", "a.py")))

    def test_dotdot_blocked(self):
        self.assertBlocked(self.write(self.a("r1", "..", "r2", "a.py")))
        self.assertBlocked(self.write(
            self.a("r1", "..", "..", "..", "scripts", "x.py")))

    def test_relative_path_blocked(self):
        self.assertBlocked(self.write("workspace/analysis/r1/a.py"))

    def test_symlinked_file_out_blocked(self):
        target = self.outside / "target.py"
        target.write_text("x")
        os.symlink(target, self.a("r1", "link.py"))
        self.assertBlocked(self.write(self.a("r1", "link.py")))

    def test_symlinked_run_dir_out_blocked(self):
        os.symlink(self.outside, self.a("escape"))
        self.assertBlocked(self.write(self.a("escape", "a.py")))

    def test_symlinked_subdir_out_blocked(self):
        os.symlink(self.outside, self.a("r1", "sub"))
        self.assertBlocked(self.write(self.a("r1", "sub", "a.py")))

    def test_missing_file_path_blocked(self):
        code, _ = self.run_guard({"tool_name": "Write", "tool_input": {}})
        self.assertBlocked(code)


class TestOtherTools(GuardTest):
    def test_read_only_tools_allowed(self):
        for tool in ["Read", "Grep", "Glob"]:
            with self.subTest(tool=tool):
                code, _ = self.run_guard({"tool_name": tool,
                                          "tool_input": {"file_path": "/etc/hosts"}})
                self.assertAllowed(code)

    def test_handback_allowed(self):
        # Newer Claude Code builds deliver a subagent's report through this tool; blocking it lost every report.
        code, _ = self.run_guard({"tool_name": "SubagentHandback", "tool_input": {"message": "Results: ..."}})
        self.assertAllowed(code)

    def test_find_text_allowed_in_exact_form(self):
        shutil.copy(GUARD_SRC.parent / "preflight.py", self.root / ".claude" / "hooks" / "preflight.py")
        base = f"python3 {self.root}/scripts/find_text.py"
        self.assertAllowed(self.bash(f'{base} --list "workspace/repos/*.json"', raw=True))
        self.assertAllowed(self.bash(f'{base} -i "fish" workspace/corpus', raw=True))
        self.assertBlocked(self.bash(f'{base} "fish"; id', raw=True))
        self.assertBlocked(self.bash(f'{base} "$(id)"', raw=True))

    def test_find_text_blocked_without_the_pattern_module(self):
        # Fails closed: if preflight.py can't be imported, the search is refused rather than waved through.
        self.assertBlocked(self.bash(f'python3 {self.root}/scripts/find_text.py "fish"', raw=True))

    def test_everything_else_blocked(self):
        for tool in ["Edit", "NotebookEdit", "WebFetch", "WebSearch", "Agent",
                     "Task", "PowerShell", "Monitor", "Skill", "SendMessage",
                     "mcp__github__create_issue", "", None]:
            with self.subTest(tool=tool):
                code, _ = self.run_guard({"tool_name": tool, "tool_input": {}})
                self.assertBlocked(code)

    def test_malformed_input_fails_closed(self):
        for raw in ["", "not json", "[]", "null", '{"tool_name": "Bash"}',
                    '{"tool_name": "Bash", "tool_input": "ls"}',
                    '{"tool_name": "Write", "tool_input": {"file_path": 5}}']:
            with self.subTest(raw=raw):
                code, _ = self.run_guard(None, raw=raw)
                self.assertBlocked(code)

    def test_block_message_reaches_analyst(self):
        code, err = self.run_guard({"tool_name": "Bash",
                                    "tool_input": {"command": "ls"}})
        self.assertBlocked(code)
        self.assertIn("Blocked by analyst guard", err)


if __name__ == "__main__":
    unittest.main()
