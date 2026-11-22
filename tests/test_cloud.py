"""scripts/cloud.py: saving my/ and workspace/ from a cloud session, with a local bare repository as "GitHub"."""
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("cloud", HERE / "scripts" / "cloud.py")
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


def run(cwd, *args):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"{args}: {r.stderr}")
    return r.stdout.strip()


def ident(repo):
    run(repo, "git", "config", "user.name", "t")
    run(repo, "git", "config", "user.email", "t@example.com")


class CloudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.upstream = self.tmp / "upstream"           # the maintainer's public cloud copy
        self.upstream.mkdir()
        run(self.upstream, "git", "init", "-q", "-b", "main")
        ident(self.upstream)
        (self.upstream / ".gitignore").write_text("my/*\n!my/README.md\nworkspace/*\n")
        (self.upstream / "my").mkdir()
        (self.upstream / "my" / "README.md").write_text("yours\n")
        (self.upstream / "CLAUDE.md").write_text("v1\n")
        run(self.upstream, "git", "add", "-A")
        run(self.upstream, "git", "commit", "-qm", "v1")
        self.remote = self.tmp / "private.git"          # the user's private copy on GitHub
        run(self.tmp, "git", "clone", "-q", "--bare", str(self.upstream), str(self.remote))
        self.work = self.clone("work")
        env = {"CLAUDE_CODE_REMOTE": "true", "AI_WELFARE_CLOUD_UPSTREAM": str(self.upstream)}
        self.patches = [mock.patch.dict(os.environ, env), mock.patch.object(cloud, "ROOT", self.work),
                        mock.patch.object(cloud, "origin_slug", return_value="someone/private-copy"),
                        mock.patch.object(cloud, "is_public", return_value=False),
                        mock.patch.object(cloud, "UPSTREAM", str(self.upstream)),
                        mock.patch.object(cloud, "LOCAL", self.work / ".claude" / "settings.local.json"),
                        mock.patch.object(cloud, "reapply_rules", lambda: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        subprocess.run(["rm", "-rf", str(self.tmp)])

    def clone(self, name):
        d = self.tmp / name
        run(self.tmp, "git", "clone", "-q", str(self.remote), str(d))
        ident(d)
        run(d, "git", "checkout", "-q", "-b", "claude/session-" + name)   # cloud sessions work on their own branch
        return d

    def remote_files(self):
        return set(run(self.tmp, "git", "--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", "main")
                   .splitlines())

    def save(self, msg="test"):
        with redirect_stdout(io.StringIO()) as out:
            cloud.save(msg)
        return out.getvalue()

    def test_saves_ignored_folders_to_main(self):
        (self.work / "my" / "profile.md").write_text("me\n")
        (self.work / "workspace" / "outbox").mkdir(parents=True)
        (self.work / "workspace" / "outbox" / "draft.md").write_text("draft\n")
        (self.work / "workspace" / "__pycache__").mkdir()
        (self.work / "workspace" / "__pycache__" / "x.pyc").write_text("junk")
        (self.work / "my" / ".env").write_text("KEY=secret\n")
        nested = self.work / "workspace" / "repos" / "tool"
        nested.mkdir(parents=True)
        run(nested, "git", "init", "-q")
        (nested / "code.py").write_text("print(1)\n")
        self.assertIn("Saved 2", self.save())
        files = self.remote_files()
        self.assertTrue({"my/profile.md", "workspace/outbox/draft.md"} <= files)
        self.assertFalse(any("__pycache__" in f or f.endswith(".env") or "repos/" in f for f in files))

    def test_second_save_and_nothing_new(self):
        (self.work / "my" / "profile.md").write_text("me\n")
        self.save()
        (self.work / "my" / "profile.md").write_text("me, edited\n")
        self.assertIn("Saved 1", self.save())
        self.assertEqual(run(self.tmp, "git", "--git-dir", str(self.remote), "show", "main:my/profile.md"),
                         "me, edited")
        self.assertIn("Nothing new", self.save())
        branches = run(self.tmp, "git", "--git-dir", str(self.remote), "branch", "--format=%(refname:short)")
        self.assertIn("claude/session-work", branches.split())   # the session branch is kept in step

    def test_deletions_are_saved(self):
        (self.work / "workspace").mkdir()
        (self.work / "workspace" / "old.md").write_text("x\n")
        self.save()
        (self.work / "workspace" / "old.md").unlink()
        self.save()
        self.assertNotIn("workspace/old.md", self.remote_files())

    def test_another_session_saved_meanwhile(self):
        other = self.clone("other")
        (self.work / "workspace").mkdir()
        (self.work / "workspace" / "a.md").write_text("a\n")
        self.save()
        with mock.patch.object(cloud, "ROOT", other):
            (other / "workspace").mkdir()
            (other / "workspace" / "b.md").write_text("b\n")
            self.assertIn("after bringing in", self.save())
        self.assertTrue({"workspace/a.md", "workspace/b.md"} <= self.remote_files())

    def test_clash_goes_to_a_branch(self):
        other = self.clone("other")
        (self.work / "my" / "profile.md").write_text("one\n")
        self.save()
        with mock.patch.object(cloud, "ROOT", other):
            (other / "my" / "profile.md").write_text("two\n")
            with self.assertRaises(SystemExit) as e, redirect_stdout(io.StringIO()) as out:
                cloud.save("clash")
            self.assertEqual(e.exception.code, 2)
            self.assertIn("saved/", out.getvalue())
        self.assertEqual(run(self.tmp, "git", "--git-dir", str(self.remote), "show", "main:my/profile.md"), "one")

    def test_refuses_outside_the_cloud(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": ""}):
            with self.assertRaises(SystemExit):
                cloud.save("x")

    def test_refuses_public_repositories(self):
        (self.work / "my" / "profile.md").write_text("private\n")
        with mock.patch.object(cloud, "is_public", return_value=True), self.assertRaises(SystemExit) as e:
            cloud.save("x")
        self.assertIn("public", str(e.exception.code))
        self.assertNotIn("my/profile.md", self.remote_files())

    def test_update_merges_upstream_and_saves(self):
        (self.work / "my" / "profile.md").write_text("me\n")
        self.save()
        (self.upstream / "CLAUDE.md").write_text("v2\n")
        run(self.upstream, "git", "commit", "-qam", "v2")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cloud.behind(), 1)
            cloud.update(check_only=False)
            self.assertEqual(cloud.behind(), 0)
        self.assertEqual(run(self.tmp, "git", "--git-dir", str(self.remote), "show", "main:CLAUDE.md"), "v2")
        self.assertEqual(run(self.tmp, "git", "--git-dir", str(self.remote), "show", "main:my/profile.md"), "me")

    def test_template_copy_updates(self):
        """A copy made with "Use this template": one commit of its own holding v1's files, no shared history."""
        first = run(self.upstream, "git", "commit-tree", "HEAD^{tree}", "-m", "Initial commit")
        (self.upstream / "CLAUDE.md").write_text("v2\n")
        (self.upstream / "new.md").write_text("new\n")
        run(self.upstream, "git", "add", "-A")
        run(self.upstream, "git", "commit", "-qm", "v2")
        (self.upstream / "CLAUDE.md").write_text("v3\n")
        run(self.upstream, "git", "commit", "-qam", "v3")
        run(self.tmp, "rm", "-rf", str(self.remote), str(self.work))
        run(self.tmp, "git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        run(self.upstream, "git", "push", "-q", str(self.remote), first + ":refs/heads/main")
        self.work = self.clone("work")
        with mock.patch.object(cloud, "ROOT", self.work):
            (self.work / "my" / "profile.md").write_text("me\n")
            self.save()
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cloud.behind(), 2)
                cloud.update(check_only=False)
                self.assertEqual(cloud.behind(), 0)
            self.assertIn("v2", out.getvalue())
            self.assertEqual(run(self.work, "git", "replace", "-l"), "")   # the graft was only for that merge
            (self.upstream / "CLAUDE.md").write_text("v4\n")
            run(self.upstream, "git", "commit", "-qam", "v4")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cloud.behind(), 1)
                cloud.update(check_only=False)
        show = lambda f: run(self.tmp, "git", "--git-dir", str(self.remote), "show", "main:" + f)
        self.assertEqual((show("CLAUDE.md"), show("new.md"), show("my/profile.md")), ("v4", "new", "me"))

    def test_unmatched_history_is_refused(self):
        run(self.tmp, "rm", "-rf", str(self.remote), str(self.work))
        other = self.tmp / "other"
        other.mkdir()
        run(other, "git", "init", "-q", "-b", "main")
        ident(other)
        (other / "x.md").write_text("x\n")
        run(other, "git", "add", "-A")
        run(other, "git", "commit", "-qm", "unrelated")
        run(self.tmp, "git", "clone", "-q", "--bare", str(other), str(self.remote))
        self.work = self.clone("work")
        with mock.patch.object(cloud, "ROOT", self.work), self.assertRaises(SystemExit) as e:
            cloud.update(check_only=False)
        self.assertIn("doesn't match", str(e.exception.code))


class NetworkTest(unittest.TestCase):
    def check(self, error):
        with mock.patch.object(cloud.urllib.request, "urlopen", side_effect=error):
            return cloud.network_limited()

    def test_limited_full_and_unsure(self):
        import urllib.error
        self.assertTrue(self.check(urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))))
        self.assertTrue(self.check(urllib.error.HTTPError("u", 403, "Forbidden", {}, None)))
        self.assertFalse(self.check(urllib.error.HTTPError("u", 503, "busy", {}, None)))
        self.assertIsNone(self.check(urllib.error.URLError(OSError("timed out"))))
        with mock.patch.object(cloud.urllib.request, "urlopen", return_value=io.BytesIO()):
            self.assertFalse(cloud.network_limited())


class SlugTest(unittest.TestCase):
    def test_origin_slug_forms(self):
        for url, want in (("https://github.com/Me/Copy.git", "me/copy"), ("git@github.com:Me/Copy.git", "me/copy"),
                          ("/srv/repo.git", ""), ("", "")):
            with mock.patch.object(cloud, "git", return_value=subprocess.CompletedProcess([], 0, url + "\n", "")):
                self.assertEqual(cloud.origin_slug(), want, url)

    def test_known_public_repositories_need_no_network(self):
        with mock.patch.object(cloud.urllib.request, "urlopen", side_effect=AssertionError("no network")):
            self.assertTrue(cloud.is_public("sentienceproject/self-improving-ai-agent-for-advocacy-cloud"))


if __name__ == "__main__":
    unittest.main()
