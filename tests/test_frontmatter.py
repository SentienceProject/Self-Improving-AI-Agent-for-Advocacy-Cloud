"""Every agent, skill and command file's frontmatter loads in Claude Code (scripts/check_frontmatter.py), and the
checker's --fix quotes a value YAML would misread.

    python3 tests/test_frontmatter.py -v
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(root):
    spec = importlib.util.spec_from_file_location(f"check_frontmatter_{id(root)}",
                                                  root / "scripts" / "check_frontmatter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WorkspaceFiles(unittest.TestCase):
    def test_every_agent_skill_and_command_loads(self):
        cf = load(REPO)
        found = cf.files()
        self.assertTrue(any("/agents/" in p.as_posix() for p in found), "found no agent files to check")
        bad = [f"{p.relative_to(REPO)}{f':{n}' if n else ''}: {msg}" for p in found for n, msg in cf.problems(p)]
        self.assertEqual(bad, [], "\n" + "\n".join(bad) + "\nFix: python3 scripts/check_frontmatter.py --fix")


class Checker(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "check_frontmatter.py", self.tmp / "scripts")
        (self.tmp / ".claude" / "agents").mkdir(parents=True)
        self.cf = load(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def agent(self, description, extra=""):
        p = self.tmp / ".claude" / "agents" / "a.md"
        p.write_text(f"---\nname: a\ndescription: {description}\n{extra}---\n\nBody: with a colon is fine.\n")
        return p

    def test_a_colon_in_a_value_is_caught_and_fixed(self):
        p = self.agent("For any role the main session invents: a reader panel, a red team.")
        self.assertIn("contains ': '", self.cf.problems(p)[0][1])
        self.assertEqual(self.cf.fix(p), 1)
        self.assertEqual(self.cf.problems(p), [])
        self.assertIn('description: "For any role the main session invents: a reader panel, a red team."',
                      p.read_text())
        self.assertIn("Body: with a colon is fine.", p.read_text(), "the body is untouched")
        if self.cf.yaml:
            data = self.cf.yaml.safe_load("\n".join(self.cf.split(p.read_text())[0]))
            self.assertEqual(data["description"], "For any role the main session invents: a reader panel, a red team.")

    def test_quotes_and_backslashes_survive_the_fix(self):
        p = self.agent('Say "hi": then C:\\path # here')
        self.cf.fix(p)
        self.assertEqual(self.cf.problems(p), [])
        if self.cf.yaml:
            data = self.cf.yaml.safe_load("\n".join(self.cf.split(p.read_text())[0]))
            self.assertEqual(data["description"], 'Say "hi": then C:\\path # here')

    def test_good_files_pass_and_are_left_alone(self):
        p = self.agent("A colon:without a space, a URL https://x.org and a dash - all fine.", "tools: Read, Grep\n")
        before = p.read_text()
        self.assertEqual(self.cf.problems(p), [])
        self.assertEqual(self.cf.fix(p), 0)
        self.assertEqual(p.read_text(), before)

    def test_already_quoted_values_pass(self):
        self.assertEqual(self.cf.problems(self.agent('"Quoted: fine"')), [])

    def test_missing_frontmatter_or_description(self):
        p = self.tmp / ".claude" / "agents" / "b.md"
        p.write_text("no frontmatter\n")
        self.assertIn("no frontmatter", self.cf.problems(p)[0][1])
        p.write_text("---\nname: b\n---\n")
        self.assertIn("missing description", self.cf.problems(p)[0][1])


if __name__ == "__main__":
    unittest.main()
