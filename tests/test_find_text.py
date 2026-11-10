"""Tests for scripts/find_text.py (the read-only search that stands in for Grep and Glob) and the exact command
form the hooks accept for it (FIND_TEXT_RE in .claude/hooks/preflight.py).

    python3 tests/test_find_text.py -v
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class FindTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "ws"
        (self.root / "scripts").mkdir(parents=True)
        shutil.copy(REPO / "scripts" / "find_text.py", self.root / "scripts" / "find_text.py")
        corpus = self.root / "workspace" / "corpus" / "normalized"
        corpus.mkdir(parents=True)
        (corpus / "a.txt").write_text("intro\nFish feel pain, say some.\nmiddle\nfishing is a word\nend\n")
        (corpus / "b.md").write_text("nothing here\n")
        (self.root / "workspace" / ".env").write_text("SECRET=fish\n")
        (self.root / "workspace" / "bin.dat").write_bytes(b"fish\0\0\0")
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "x.txt").write_text("fish outside\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_ft(self, *args):
        r = subprocess.run([sys.executable, str(self.root / "scripts" / "find_text.py"), *args],
                           capture_output=True, text=True, cwd=self.tmp, timeout=30)
        return r.returncode, r.stdout + r.stderr

    def test_search_finds_lines_relative_to_the_workspace(self):
        code, out = self.run_ft("-i", "fish")
        self.assertEqual(code, 0)
        self.assertIn("workspace/corpus/normalized/a.txt:2: Fish feel pain", out)
        self.assertIn("a.txt:4: fishing", out)

    def test_whole_words_and_files_only(self):
        code, out = self.run_ft("-i", "-w", "-l", "fish")
        self.assertEqual(out.splitlines()[0], "workspace/corpus/normalized/a.txt")
        self.assertIn("1 matching line(s) in 1 file(s)", out)

    def test_context_marks_matches_and_context(self):
        _, out = self.run_ft("-C", "1", "pain")
        self.assertIn("a.txt:1- intro", out)
        self.assertIn("a.txt:2: Fish", out)
        self.assertIn("a.txt:3- middle", out)

    def test_env_files_and_binaries_are_skipped(self):
        _, out = self.run_ft("SECRET|fish")
        self.assertNotIn(".env", out)
        self.assertNotIn("bin.dat", out)

    def test_paths_outside_the_workspace_are_refused(self):
        for p in (str(self.outside), "../outside", "workspace/../../outside"):
            with self.subTest(p=p):
                code, out = self.run_ft("fish", p)
                self.assertNotEqual(code, 0)
                self.assertNotIn("fish outside", out)

    def test_symlink_out_of_the_workspace_is_not_followed(self):
        os.symlink(self.outside / "x.txt", self.root / "workspace" / "link.txt")
        os.symlink(self.outside, self.root / "workspace" / "linkdir")
        _, out = self.run_ft("outside")
        self.assertNotIn("fish outside", out)
        code, _ = self.run_ft("fish", "workspace/linkdir")
        self.assertNotEqual(code, 0)

    def test_list_and_glob(self):
        _, out = self.run_ft("--list", "workspace/corpus/**/*.txt")
        self.assertIn("workspace/corpus/normalized/a.txt", out)
        self.assertNotIn("b.md", out)
        _, out = self.run_ft("--glob", "*.md", "nothing")
        self.assertIn("b.md:1:", out)

    def test_bad_regex_is_an_error_not_a_crash(self):
        code, out = self.run_ft("(unclosed")
        self.assertNotEqual(code, 0)
        self.assertIn("bad regex", out)


class FindTextCommandFormTest(unittest.TestCase):
    """The exact form the hooks let subagents run: no shell syntax can ride along."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("preflight", REPO / ".claude" / "hooks" / "preflight.py")
        self.pf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pf)
        self.base = f"python3 {self.pf.ROOT}/scripts/find_text.py"

    def ok(self, rest):
        return self.pf.FIND_TEXT_RE.fullmatch(f"{self.base} {rest}") is not None

    def test_allowed_forms(self):
        for rest in ['"fish pain"', '-i -w "fish|crab" workspace/corpus/normalized',
                     '-l -C 2 --glob "workspace/corpus/**/*.txt" "sentien(ce|t)"', '--list "workspace/**/*.md"',
                     f'"x" {self.pf.ROOT}/workspace/memory.md',
                     # regex escapes and end anchors that bash leaves as written inside double quotes
                     '"^---$"', '"(fish|crab)$"', '"a$|b"', '"\\bfish\\b"', '"\\d{4}\\.\\d+"', '"\\(x\\)"']:
            with self.subTest(rest=rest):
                self.assertTrue(self.ok(rest))

    def test_escapes_reach_find_text_unchanged(self):
        """What bash hands find_text.py is exactly what the pattern says, for every newly allowed form."""
        for pat in ["^---$", "(fish|crab)$", "a$|b", "\\bfish\\b", "\\d{4}\\.\\d+", "\\(x\\)"]:
            with self.subTest(pat=pat):
                out = subprocess.run(["bash", "-c", f'printf %s "{pat}"'], capture_output=True, text=True).stdout
                self.assertEqual(out, pat)

    def test_shell_syntax_refused(self):
        for rest in ['"fish"; rm -rf workspace', '"fish" | tee x', '"$(id)"', '"`id`"', '"a\\"; id; \\""',
                     '"$HOME"', '"a$b"', '"$$"', '"a\\$(id)"', '"a\\\\"', '"\\`id\\`"', '"a$" ; id',
                     '"fish" > workspace/corpus/x', '"fish" && id', "'fish'", '"fish" ~/.ssh', '"fish" *',
                     '"fish"\nid', '--list "x" "y"', '"x" -C 2']:
            with self.subTest(rest=rest):
                self.assertFalse(self.ok(rest))

    def test_other_scripts_refused(self):
        self.assertIsNone(self.pf.FIND_TEXT_RE.fullmatch('python3 /tmp/scripts/find_text.py "x"'))
        self.assertIsNone(self.pf.FIND_TEXT_RE.fullmatch('python3 scripts/find_text.py "x"'))


if __name__ == "__main__":
    unittest.main()
