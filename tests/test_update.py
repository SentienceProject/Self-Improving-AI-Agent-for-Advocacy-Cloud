"""scripts/update.py: a maintainer's update arrives while the user and tech watch have changed files."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@unittest.skipUnless(shutil.which("git"), "needs git")
class TestUpdate(unittest.TestCase):
    SUB = ""   # where the workspace is inside the repository ("" = it is the repository)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.up, root, user_root = t / "upstream.git", t / "maint", t / "user"
        self.maint, self.user = root / self.SUB, user_root / self.SUB
        subprocess.run(["git", "init", "--bare", "-q", str(self.up)], check=True)
        m = self.maint
        m.mkdir(parents=True)
        if self.SUB:   # a sibling folder in the same repository, as the published one has the eval harness
            (root / "harness").mkdir()
            (root / "harness" / "h.md").write_text("harness v1\n")
        shutil.copytree(REPO / "techwatch", m / "techwatch",
                        ignore=shutil.ignore_patterns("inbox", "proposals", "staged", "state", "logs", "evaluations",
                                                      "__pycache__"))
        (m / "scripts").mkdir()
        for f in ("update.py", "validate_proposal.py"):
            shutil.copy(REPO / "scripts" / f, m / "scripts")
        shutil.copy(REPO / ".gitignore", m)
        for name in ("CLAUDE.md", "notes.md", "other.md"):
            (m / name).write_text(f"{name} v1\nline two\n")
        git(root, "init", "-q", "-b", "main")
        git(m, "-c", "user.name=m", "-c", "user.email=m@x", "add", "-A")
        git(m, "-c", "user.name=m", "-c", "user.email=m@x", "commit", "-qm", "v1")
        git(m, "remote", "add", "origin", str(self.up))
        git(m, "push", "-q", "-u", "origin", "main")
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.up), str(user_root)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def maintainer_changes(self, **files):
        for name, text in files.items():
            (self.maint / name).write_text(text)
        git(self.maint, "-c", "user.name=m", "-c", "user.email=m@x", "commit", "-qam", "v2")
        git(self.maint, "push", "-q")

    def techwatch_applies(self, pid, target, text):
        review = self.review()
        d = self.user / "techwatch" / "proposals" / pid
        d.mkdir(parents=True)
        (d / "proposal.md").write_text("# p\n")
        (d / "meta.json").write_text(json.dumps({"id": pid, "kind": "add", "significance": "notable"}))
        b = self.user / "techwatch" / "staged" / pid
        (b / "files").mkdir(parents=True)
        (b / "files" / "x").write_text(text)
        (b / "manifest.json").write_text(json.dumps({"changes": [{"action": "write", "target": target,
                                                                  "source": "files/x"}]}))
        review.record(pid, "staged")
        review.ask = lambda q, choices="y/N": "y"
        review.cmd_apply(pid)

    def review(self):
        spec = importlib.util.spec_from_file_location(f"review_{id(self)}",
                                                      self.user / "techwatch" / "scripts" / "review.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def update(self):
        r = subprocess.run([sys.executable, str(self.user / "scripts" / "update.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def read(self, name):
        return (self.user / name).read_text()

    def test_everything_kept_when_nothing_clashes(self):
        self.techwatch_applies("p1", "CLAUDE.md", "CLAUDE.md by tech watch\n")
        (self.user / "notes.md").write_text("notes.md v1\nmy own line\n")
        self.maintainer_changes(**{"other.md": "other.md v2\n"})
        out = self.update()
        self.assertEqual(self.read("other.md"), "other.md v2\n")
        self.assertEqual(self.read("CLAUDE.md"), "CLAUDE.md by tech watch\n")
        self.assertEqual(self.read("notes.md"), "notes.md v1\nmy own line\n")
        self.assertIn("kept: p1", out)
        self.assertEqual(self.review().status_of("p1"), "applied")

    def test_techwatch_change_to_an_updated_file_is_restaged(self):
        self.techwatch_applies("p1", "CLAUDE.md", "CLAUDE.md by tech watch\n")
        self.maintainer_changes(**{"CLAUDE.md": "CLAUDE.md v2\n"})
        out = self.update()
        self.assertEqual(self.read("CLAUDE.md"), "CLAUDE.md v2\n")
        self.assertEqual(self.review().status_of("p1"), "approved", "the next tech-watch run prepares it again")
        self.assertIn("prepare it again", out)

    def test_clashing_own_edit_is_saved_not_lost(self):
        (self.user / "notes.md").write_text("notes.md MINE\nline two\n")
        self.maintainer_changes(**{"notes.md": "notes.md v2\nline two\n"})
        out = self.update()
        self.assertEqual(self.read("notes.md"), "notes.md v2\nline two\n")
        saved = list((self.user / "techwatch" / "state" / "update-backups").glob("*/notes.md"))
        self.assertEqual(saved[0].read_text(), "notes.md MINE\nline two\n")
        self.assertIn("clashed", out)
        self.assertEqual(git(self.user, "stash", "list"), "", "no leftover stash")

    def test_your_edit_after_a_techwatch_change_is_kept(self):
        self.techwatch_applies("p1", "CLAUDE.md", "CLAUDE.md by tech watch\n")
        (self.user / "CLAUDE.md").write_text("CLAUDE.md by tech watch\nand then my edit\n")
        self.maintainer_changes(**{"other.md": "other.md v2\n"})
        out = self.update()
        self.assertEqual(self.read("CLAUDE.md"), "CLAUDE.md by tech watch\nand then my edit\n")
        self.assertIn("your version is kept", out)

    def test_a_file_tech_watch_created_and_you_edited_survives(self):
        self.techwatch_applies("p1", "new.md", "made by tech watch\n")
        (self.user / "new.md").write_text("made by tech watch\nedited by me\n")
        self.maintainer_changes(**{"other.md": "other.md v2\n"})
        self.update()
        self.assertEqual(self.read("new.md"), "made by tech watch\nedited by me\n")

    def test_drifted_protected_file_is_not_reblessed(self):
        import hashlib
        manifest = self.user / "scripts" / "integrity.json"
        manifest.write_text(json.dumps({"notes.md": hashlib.sha256(b"notes.md v1\nline two\n").hexdigest(),
                                        "other.md": hashlib.sha256(b"other.md v1\nline two\n").hexdigest()}))
        (self.user / "notes.md").write_text("tampered\n")
        self.maintainer_changes(**{"other.md": "other.md v2\n"})
        out = self.update()
        data = json.loads(manifest.read_text())
        self.assertEqual(data["other.md"], hashlib.sha256(b"other.md v2\n").hexdigest(), "the update is blessed")
        self.assertEqual(data["notes.md"], hashlib.sha256(b"notes.md v1\nline two\n").hexdigest(),
                         "the tampered file still fails the check")
        self.assertIn("still flags them: notes.md", out)

    def test_own_commits_stop_it(self):
        (self.user / "notes.md").write_text("committed\n")
        git(self.user, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-qam", "mine")
        self.maintainer_changes(**{"other.md": "other.md v2\n"})
        r = subprocess.run([sys.executable, str(self.user / "scripts" / "update.py")], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("commit(s) of its own", r.stderr)
        self.assertEqual(self.read("other.md"), "other.md v1\nline two\n")


class TestUpdateInsideARepository(TestUpdate):
    """The same, with the workspace in a folder of a bigger repository (the published layout)."""
    SUB = "ai-welfare-workspace"

    def test_sibling_folder_is_updated_but_left_alone(self):
        root, user_root = self.maint.parent, self.user.parent
        (user_root / "harness" / "h.md").write_text("harness v1\nmy own harness note\n")
        (root / "harness" / "h2.md").write_text("new harness file\n")
        git(root, "add", "-A")
        self.maintainer_changes(**{"notes.md": "notes.md v2\nline two\n"})
        self.update()
        self.assertEqual(self.read("notes.md"), "notes.md v2\nline two\n")
        self.assertTrue((user_root / "harness" / "h2.md").exists(), "the whole repository is updated")
        self.assertIn("my own harness note", (user_root / "harness" / "h.md").read_text(),
                      "edits outside the workspace aren't stashed or touched")


if __name__ == "__main__":
    unittest.main()
