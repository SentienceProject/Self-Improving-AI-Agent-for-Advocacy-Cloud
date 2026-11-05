"""Tests for scripts/run_analysis.py. Standard library only.

    python -m unittest -v tests/test_run_analysis.py

Every test builds a throwaway fake repo in a temp dir and points the module's
path constants at it, so nothing here touches the real workspace/logs. The
image lock is the real one, so container tests run the real pinned image.
Container tests are skipped (with a reason) if Docker or the image is missing.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

if os.name == "nt":   # they use Linux and macOS file features (O_NOFOLLOW, fcntl); so does hardened mode
    raise unittest.SkipTest("hardened mode's scripts need Linux, macOS or WSL2, not native Windows")

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "run_analysis", REPO / "scripts" / "run_analysis.py")
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

REAL_LOCK = REPO / "sandbox" / "image.lock"


def _docker_ready():
    if shutil.which("docker") is None:
        return False, "docker not on PATH"
    try:
        image_id = REAL_LOCK.read_text().strip()
        r = subprocess.run(["docker", "image", "inspect", image_id],
                           capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001 - any failure means "skip"
        return False, f"docker check failed: {e}"
    if r.returncode != 0:
        return False, "pinned image not present locally"
    return True, ""


DOCKER_OK, DOCKER_WHY = _docker_ready()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def _rmtree(path: Path):
    # Staged dirs are 0555; give write back so their files can be removed.
    for dirpath, _, _ in os.walk(path):
        try:
            os.chmod(dirpath, 0o755)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


class FakeRepo(unittest.TestCase):
    """Base: a fake repo tree, module constants patched to point into it."""

    RUN = "r1"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ra-test-"))
        self.root = self.tmp / "repo"
        ws = self.root / "workspace"
        self.analysis = ws / "analysis"
        self.corpus = ws / "corpus"
        self.assets = ws / "assets"
        self.inbox = ws / "inbox"
        self.repos = ws / "repos"
        self.logs = ws / "logs"
        self.staging = ws / "staging"
        for d in (self.analysis / self.RUN, self.analysis / "r2",
                  self.corpus, self.assets, self.inbox, self.repos,
                  self.logs):
            d.mkdir(parents=True)
        (self.corpus / "paper.md").write_text("corpus text\n")
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

        patches = {
            "ROOT": self.root,
            "ANALYSIS_DIR": self.analysis,
            "CORPUS_DIR": self.corpus,
            "ASSETS_DIR": self.assets,
            "INBOX_DIR": self.inbox,
            "REPOS_DIR": self.repos,
            "LOGS_DIR": self.logs,
            "STAGING_DIR": self.staging,
            "IMAGE_LOCK": REAL_LOCK,
        }
        for name, value in patches.items():
            p = mock.patch.object(ra, name, value)
            p.start()
            self.addCleanup(p.stop)

        # Run from an unrelated cwd to prove the script never uses it.
        old_cwd = os.getcwd()
        os.chdir(self.outside)
        self.addCleanup(os.chdir, old_cwd)
        self.addCleanup(_rmtree, self.tmp)

    # helpers ---------------------------------------------------------------

    def script(self, name, body, run=None):
        p = self.analysis / (run or self.RUN) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))
        return f"workspace/analysis/{run or self.RUN}/{name}"

    def run_main(self, run_id, path):
        with mock.patch("sys.stderr"), mock.patch("sys.stdout"):
            return ra.main([run_id, path])

    def logs_written(self):
        return sorted(self.logs.glob("*.json"))

    def last_log(self):
        logs = self.logs_written()
        self.assertTrue(logs, "no log was written")
        newest = max(logs, key=lambda p: p.stat().st_mtime_ns)
        return json.loads(newest.read_text())


# ---------------------------------------------------------------------------
# Refusals: no container may start. _launch is patched to fail loudly.
# ---------------------------------------------------------------------------

class TestRefusals(FakeRepo):

    def setUp(self):
        super().setUp()
        p = mock.patch.object(
            ra, "_launch",
            side_effect=AssertionError("container launched on a refusal path"))
        p.start()
        self.addCleanup(p.stop)
        # Path tests must fail before the image check; make the image check
        # pass without Docker so they are independent of it.
        q = mock.patch.object(ra, "_check_image",
                              return_value="sha256:" + "0" * 64)
        self.check_image = q.start()
        self.addCleanup(q.stop)

    def assertRefused(self, run_id, path, reason_fragment):
        rc = self.run_main(run_id, path)
        self.assertNotEqual(rc, 0)
        log = self.last_log()
        self.assertEqual(log["status"], "refused")
        self.assertIn(reason_fragment, log["reason"])
        self.assertIsNone(log.get("exit_code"))
        return log

    # run-ids

    def test_bad_run_ids(self):
        good = self.script("ok.py", "print(1)\n")
        for bad in ["", "-a", "_a", "a/b", "../a", "a b", ".a", "é",
                    "a" * 65, "a\n", "a.b", "a\x00"]:
            with self.subTest(run_id=repr(bad)):
                log = self.assertRefused(bad, good, "run-id")
                self.assertEqual(log["run_id"], repr(bad))
        # Refusals for invalid run-ids go under a fixed prefix, never the id.
        for p in self.logs_written():
            self.assertTrue(p.name.startswith("_refused-exec-"), p.name)

    def test_max_length_run_id_ok(self):
        rid = "a" * 64
        path = self.script("ok.py", "print(1)\n", run=rid)
        # Passes validation and reaches the (patched) launch.
        with self.assertRaisesRegex(AssertionError, "container launched"):
            self.run_main(rid, path)

    # paths

    def test_absolute_path(self):
        self.script("ok.py", "print(1)\n")
        abs_path = str(self.analysis / self.RUN / "ok.py")
        self.assertRefused(self.RUN, abs_path, "absolute")

    def test_dotdot_escape(self):
        (self.root / "workspace" / "evil.py").write_text("print(1)\n")
        self.assertRefused(self.RUN, "workspace/analysis/r1/../../evil.py", "..")

    def test_dotdot_that_stays_inside_is_still_refused(self):
        self.script("ok.py", "print(1)\n")
        self.assertRefused(self.RUN, "workspace/analysis/r1/../r1/ok.py", "..")

    def test_other_run_id_folder(self):
        other = self.script("x.py", "print(1)\n", run="r2")
        self.assertRefused(self.RUN, other, "workspace/analysis/r1/")

    def test_path_outside_analysis(self):
        (self.corpus / "x.py").write_text("print(1)\n")
        self.assertRefused(self.RUN, "workspace/corpus/x.py",
                           "workspace/analysis/r1/")

    def test_prefix_trick(self):
        # r1 must not match r10.
        self.script("x.py", "print(1)\n", run="r10")
        self.assertRefused(self.RUN, "workspace/analysis/r10/x.py",
                           "workspace/analysis/r1/")

    def test_symlink_out_to_tmp_file(self):
        target = self.outside / "evil.py"
        target.write_text("print(1)\n")
        (self.analysis / self.RUN / "link.py").symlink_to(target)
        self.assertRefused(self.RUN, "workspace/analysis/r1/link.py", "outside")

    def test_symlink_out_to_etc_passwd(self):
        (self.analysis / self.RUN / "pw.py").symlink_to("/etc/passwd")
        self.assertRefused(self.RUN, "workspace/analysis/r1/pw.py", "outside")

    def test_symlink_into_other_run(self):
        self.script("x.py", "print(1)\n", run="r2")
        (self.analysis / self.RUN / "x.py").symlink_to(
            self.analysis / "r2" / "x.py")
        self.assertRefused(self.RUN, "workspace/analysis/r1/x.py", "outside")

    def test_symlinked_subdirectory_out(self):
        (self.outside / "evil.py").write_text("print(1)\n")
        (self.analysis / self.RUN / "sub").symlink_to(self.outside)
        self.assertRefused(self.RUN, "workspace/analysis/r1/sub/evil.py",
                           "outside")

    def test_run_dir_is_symlink_elsewhere(self):
        shutil.rmtree(self.analysis / self.RUN)
        (self.outside / "x.py").write_text("print(1)\n")
        (self.analysis / self.RUN).symlink_to(self.outside)
        self.assertRefused(self.RUN, "workspace/analysis/r1/x.py", "run dir")

    def test_missing_file(self):
        self.assertRefused(self.RUN, "workspace/analysis/r1/nope.py",
                           "does not exist")

    def test_missing_run_dir(self):
        self.assertRefused("r9", "workspace/analysis/r9/x.py", "run dir")

    def test_directory(self):
        (self.analysis / self.RUN / "d").mkdir()
        self.assertRefused(self.RUN, "workspace/analysis/r1/d", "regular")

    def test_directory_named_py(self):
        (self.analysis / self.RUN / "d.py").mkdir()
        self.assertRefused(self.RUN, "workspace/analysis/r1/d.py", "regular")

    def test_not_py(self):
        self.script("x.sh", "echo hi\n")
        self.assertRefused(self.RUN, "workspace/analysis/r1/x.sh", ".py")

    def test_symlink_to_non_py_inside(self):
        self.script("data.txt", "print(1)\n")
        (self.analysis / self.RUN / "x.py").symlink_to("data.txt")
        self.assertRefused(self.RUN, "workspace/analysis/r1/x.py", ".py")

    def test_fifo(self):
        os.mkfifo(self.analysis / self.RUN / "f.py")
        self.assertRefused(self.RUN, "workspace/analysis/r1/f.py", "regular")

    def test_missing_assets_dir(self):
        self.script("ok.py", "print(1)\n")
        self.assets.rmdir()
        self.assertRefused(self.RUN, "workspace/analysis/r1/ok.py", "assets")

    def test_missing_inbox_dir(self):
        self.script("ok.py", "print(1)\n")
        self.inbox.rmdir()
        self.assertRefused(self.RUN, "workspace/analysis/r1/ok.py", "inbox")

    def test_missing_repos_dir(self):
        self.script("ok.py", "print(1)\n")
        self.repos.rmdir()
        self.assertRefused(self.RUN, "workspace/analysis/r1/ok.py", "repos")

    def test_symlink_loop(self):
        d = self.analysis / self.RUN
        (d / "a.py").symlink_to(d / "b.py")
        (d / "b.py").symlink_to(d / "a.py")
        self.assertRefused(self.RUN, "workspace/analysis/r1/a.py", "")

    # staging

    def test_symlinked_sibling_py_refused(self):
        # The entry script is fine; a symlinked .py next to it is not.
        path = self.script("main.py", "print(1)\n")
        self.script("real_helper.py", "X = 1\n")
        (self.analysis / self.RUN / "helper.py").symlink_to("real_helper.py")
        self.assertRefused(self.RUN, path, "symlink")
        self.assertFalse((self.staging / f"{self.RUN}-exec-1").exists())

    def test_symlinked_py_pointing_outside_refused(self):
        path = self.script("main.py", "print(1)\n")
        (self.outside / "evil.py").write_text("X = 1\n")
        sub = self.analysis / self.RUN / "sub"
        sub.mkdir()
        (sub / "helper.py").symlink_to(self.outside / "evil.py")
        self.assertRefused(self.RUN, path, "symlink")

    def test_symlinked_entry_script_refused(self):
        # Previously allowed when the target stayed inside the run dir.
        self.script("real.py", "print(1)\n")
        (self.analysis / self.RUN / "x.py").symlink_to("real.py")
        self.assertRefused(self.RUN, "workspace/analysis/r1/x.py", "symlink")

    def test_existing_staging_dir_refused(self):
        path = self.script("ok.py", "print(1)\n")
        (self.staging / f"{self.RUN}-exec-1").mkdir(parents=True)
        self.assertRefused(self.RUN, path, "already exists")

    def test_staging_dir_symlink_refused(self):
        path = self.script("ok.py", "print(1)\n")
        self.staging.symlink_to(self.outside)
        self.assertRefused(self.RUN, path, "staging")
        self.assertEqual(list(self.outside.iterdir()), [])

    # logging

    def test_refusal_never_overwrites(self):
        existing = self.logs / f"{self.RUN}-exec-1.json"
        existing.write_text("SENTINEL")
        self.run_main(self.RUN, "/abs.py")
        self.assertEqual(existing.read_text(), "SENTINEL")
        self.assertTrue((self.logs / f"{self.RUN}-exec-2.json").exists())


class TestImageLock(FakeRepo):
    """Image checks against the real Docker daemon."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(
            ra, "_launch",
            side_effect=AssertionError("container launched on a refusal path"))
        p.start()
        self.addCleanup(p.stop)
        self.path = self.script("ok.py", "print(1)\n")
        self.lock = self.tmp / "image.lock"
        q = mock.patch.object(ra, "IMAGE_LOCK", self.lock)
        q.start()
        self.addCleanup(q.stop)

    def refused(self, fragment):
        rc = self.run_main(self.RUN, self.path)
        self.assertNotEqual(rc, 0)
        log = self.last_log()
        self.assertEqual(log["status"], "refused")
        self.assertIn(fragment, log["reason"])

    def test_missing_lock(self):
        self.refused("image.lock")

    def test_malformed_lock(self):
        for bad in ["", "b6aff360", "sha256:xyz", "sha256:" + "A" * 64,
                    "welfare-analysis:latest"]:
            with self.subTest(lock=bad):
                self.lock.write_text(bad)
                self.refused("image.lock")

    @unittest.skipUnless(DOCKER_OK, DOCKER_WHY)
    def test_lock_mismatch(self):
        # Valid format, but no such image.
        self.lock.write_text("sha256:" + "0" * 64 + "\n")
        self.refused("image")

    @unittest.skipUnless(DOCKER_OK, DOCKER_WHY)
    def test_lock_tag_refused(self):
        # A tag names a real local image, but only a full sha256 Id is
        # accepted; refused by the format check before docker is asked.
        self.lock.write_text("welfare-analysis:latest")
        self.refused("image.lock")

    def test_lock_id_differs_from_inspected(self):
        # docker finds an image but reports a different Id than the lock.
        # Mocked, because a real daemon never answers an Id lookup with
        # another Id; this pins the exact-comparison branch.
        self.lock.write_text("sha256:" + "a" * 64)
        fake = subprocess.CompletedProcess(
            [], 0, stdout="sha256:" + "b" * 64 + "\n", stderr="")
        with mock.patch.object(ra.subprocess, "run", return_value=fake):
            self.refused("does not match")


class TestArgv(unittest.TestCase):

    def test_argv_shape(self):
        argv = ra.build_argv("r1", 3, Path("/x/run"), Path("/x/staged"),
                             "sub/a.py", "sha256:" + "1" * 64)
        self.assertEqual(argv[:2], ["/usr/bin/docker", "run"])
        for flag in ("-e", "--env", "--env-file"):
            self.assertNotIn(flag, argv)
        self.assertFalse(any(a.startswith(("--env=", "--env-file=", "-e="))
                             for a in argv))
        self.assertEqual(argv.count("-v"), 6)
        self.assertNotIn("-w", argv)
        self.assertFalse(any(a.startswith("--workdir") for a in argv))
        self.assertFalse(any(a.startswith(("--volume", "--mount")) for a in argv))
        self.assertEqual(argv[argv.index("--memory") + 1], "8g")
        self.assertEqual(argv[argv.index("--memory-swap") + 1], "8g")
        self.assertEqual(argv[argv.index("--name") + 1], "r1-exec-3")
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "256")
        self.assertEqual(argv[argv.index("--cpus") + 1], "4")
        self.assertEqual(argv[argv.index("--user") + 1], "1000:1000")
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
        self.assertEqual(argv[argv.index("--security-opt") + 1],
                         "no-new-privileges")
        self.assertEqual(argv[argv.index("--tmpfs") + 1], "/tmp:size=512m")
        self.assertIn("--read-only", argv)
        self.assertIn("--rm", argv)
        self.assertEqual(argv[-5:], ["sha256:" + "1" * 64, "timeout", "900",
                                     "python", "/code/sub/a.py"])
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        self.assertTrue(mounts[0].endswith("/workspace/corpus:/data/corpus:ro"))
        self.assertTrue(mounts[1].endswith("/workspace/assets:/data/assets:ro"))
        self.assertTrue(mounts[2].endswith("/workspace/inbox:/data/inbox:ro"))
        self.assertTrue(mounts[3].endswith("/workspace/repos:/data/repos:ro"))
        self.assertEqual(mounts[4], "/x/run:/work:rw")
        self.assertEqual(mounts[5], "/x/staged:/code:ro")

    def test_argv_refuses_colon_in_staged_dir(self):
        with self.assertRaises(ra.Refusal):
            ra.build_argv("r1", 1, Path("/x/run"), Path("/x/a:b"), "a.py",
                          "sha256:" + "1" * 64)

    def test_host_timeout_exceeds_container_timeout(self):
        self.assertGreater(ra.HOST_TIMEOUT_S, ra.CONTAINER_TIMEOUT_S)
        self.assertEqual(ra.CONTAINER_TIMEOUT_S, 900)

    def test_root_from_file_not_cwd(self):
        self.assertEqual(ra.ROOT, REPO)


# ---------------------------------------------------------------------------
# Staging, without Docker: image check and launch are stubbed.
# ---------------------------------------------------------------------------

class TestStaging(FakeRepo):

    FAKE_RESULT = {"status": "ran", "timed_out": False, "exit_code": 0,
                   "stdout_head": "", "stdout_tail": "", "stdout_bytes": 0,
                   "stderr_head": "", "stderr_tail": "", "stderr_bytes": 0}

    def setUp(self):
        super().setUp()
        q = mock.patch.object(ra, "_check_image",
                              return_value="sha256:" + "0" * 64)
        q.start()
        self.addCleanup(q.stop)
        p = mock.patch.object(ra, "_launch", return_value=self.FAKE_RESULT)
        self.launch = p.start()
        self.addCleanup(p.stop)

    def test_logged_hash_equals_staged_file_hash(self):
        path = self.script("t.py", "print('hi')\n")
        self.assertEqual(self.run_main(self.RUN, path), 0)
        log = self.last_log()
        staged = Path(log["staged_dir"])
        self.assertEqual(staged, self.staging / f"{self.RUN}-exec-1")
        self.assertEqual(log["script_sha256"], _sha(staged / "t.py"))
        self.assertEqual(log["staged_manifest"], {"t.py": _sha(staged / "t.py")})

    def test_manifest_covers_every_py_with_relative_paths(self):
        path = self.script("main.py", "import helper\n")
        self.script("helper.py", "X = 1\n")
        self.script("pkg/__init__.py", "")
        self.script("pkg/deep/mod.py", "Y = 2\n")
        self.script("data.csv", "a,b\n")
        self.script("notes.txt", "x\n")
        self.run_main(self.RUN, path)
        log = self.last_log()
        staged = Path(log["staged_dir"])
        self.assertEqual(sorted(log["staged_manifest"]),
                         ["helper.py", "main.py", "pkg/__init__.py",
                          "pkg/deep/mod.py"])
        for rel, digest in log["staged_manifest"].items():
            self.assertEqual(_sha(staged / rel), digest, rel)
            self.assertEqual((staged / rel).read_bytes(),
                             (self.analysis / self.RUN / rel).read_bytes())
        self.assertFalse((staged / "data.csv").exists())
        self.assertFalse((staged / "notes.txt").exists())

    def test_staged_permissions(self):
        path = self.script("main.py", "print(1)\n")
        self.script("sub/helper.py", "X = 1\n")
        self.run_main(self.RUN, path)
        staged = Path(self.last_log()["staged_dir"])
        for p in (staged / "main.py", staged / "sub" / "helper.py"):
            self.assertEqual(stat_mode(p), 0o444, p)
        for d in (staged, staged / "sub"):
            self.assertEqual(stat_mode(d), 0o555, d)

    def test_nested_script_runs_from_code(self):
        path = self.script("sub/a.py", "print(1)\n")
        self.run_main(self.RUN, path)
        log = self.last_log()
        self.assertEqual(log["argv"][-1], "/code/sub/a.py")
        self.assertIn(f"{log['staged_dir']}:/code:ro", log["argv"])
        self.assertEqual(log["script_sha256"],
                         log["staged_manifest"]["sub/a.py"])

    def test_staging_kept_and_numbered_per_run(self):
        path = self.script("t.py", "print(1)\n")
        self.run_main(self.RUN, path)
        self.run_main(self.RUN, path)
        self.assertEqual(sorted(p.name for p in self.staging.iterdir()),
                         [f"{self.RUN}-exec-1", f"{self.RUN}-exec-2"])

    def test_hash_is_of_staged_bytes_not_later_original(self):
        path = self.script("t.py", "print('ORIGINAL')\n")
        original = _sha(self.analysis / self.RUN / "t.py")
        real_stage = ra._stage

        def stage_then_modify(*a, **k):
            out = real_stage(*a, **k)
            (self.analysis / self.RUN / "t.py").write_text("print('MODIFIED')\n")
            return out

        with mock.patch.object(ra, "_stage", stage_then_modify):
            self.run_main(self.RUN, path)
        log = self.last_log()
        self.assertEqual(log["script_sha256"], original)
        self.assertEqual((Path(log["staged_dir"]) / "t.py").read_text(),
                         "print('ORIGINAL')\n")


# ---------------------------------------------------------------------------
# Container behaviour: real Docker, real pinned image.
# ---------------------------------------------------------------------------

@unittest.skipUnless(DOCKER_OK, DOCKER_WHY)
class TestContainer(FakeRepo):

    def run_script(self, body, name="t.py"):
        path = self.script(name, body)
        rc = self.run_main(self.RUN, path)
        return rc, self.last_log()

    def test_clean_run_logged(self):
        rc, log = self.run_script("print('hello from the box')\n")
        self.assertEqual(rc, 0)
        self.assertEqual(log["status"], "ran")
        self.assertEqual(log["exit_code"], 0)
        self.assertIn("hello from the box", log["stdout_head"])
        self.assertIn("hello from the box", log["stdout_tail"])
        self.assertEqual(log["stdout_bytes"], len("hello from the box\n"))
        self.assertEqual(log["image_id"], REAL_LOCK.read_text().strip())
        self.assertRegex(log["script_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(log["script"], "workspace/analysis/r1/t.py")
        for k in ("started", "ended", "duration_s", "stderr_head",
                  "stderr_tail", "stderr_bytes", "argv", "staged_dir",
                  "staged_manifest"):
            self.assertIn(k, log)
        self.assertFalse(log["timed_out"])

    def test_logged_hash_equals_staged_file_hash(self):
        rc, log = self.run_script("print('x')\n")
        self.assertEqual(rc, 0, log)
        staged = Path(log["staged_dir"]) / "t.py"
        self.assertEqual(log["script_sha256"], _sha(staged))
        self.assertEqual(log["staged_manifest"]["t.py"], _sha(staged))

    def test_modifying_original_after_staging_does_not_change_what_runs(self):
        path = self.script("t.py", "print('MARKER-ORIGINAL')\n")
        real_stage = ra._stage

        def stage_then_modify(*a, **k):
            out = real_stage(*a, **k)
            (self.analysis / self.RUN / "t.py").write_text(
                "print('MARKER-MODIFIED')\n")
            return out

        with mock.patch.object(ra, "_stage", stage_then_modify):
            rc = self.run_main(self.RUN, path)
        log = self.last_log()
        self.assertEqual(rc, 0, log)
        self.assertIn("MARKER-ORIGINAL", log["stdout_head"])
        self.assertNotIn("MARKER-MODIFIED", log["stdout_head"])
        self.assertEqual(log["script_sha256"],
                         hashlib.sha256(b"print('MARKER-ORIGINAL')\n").hexdigest())

    def test_cannot_write_code(self):
        rc, log = self.run_script("""
            import os, sys
            attempts = [
                lambda: open("/code/t.py", "a").write("x"),
                lambda: open("/code/new.py", "w").write("x"),
                lambda: os.chmod("/code/t.py", 0o666),
                lambda: os.mkdir("/code/d"),
                lambda: os.unlink("/code/t.py"),
            ]
            for i, f in enumerate(attempts):
                try:
                    f()
                except OSError as e:
                    print("DENIED", i, e.errno)
                else:
                    print("ALLOWED", i); sys.exit(1)
        """)
        self.assertEqual(rc, 0, log)
        self.assertEqual(log["stdout_head"].count("DENIED"), 5)
        staged = Path(log["staged_dir"])
        self.assertEqual(sorted(p.name for p in staged.iterdir()), ["t.py"])
        self.assertEqual(_sha(staged / "t.py"), log["script_sha256"])

    def test_sibling_helper_import(self):
        self.script("helper.py", "VALUE = 'from-helper'\n")
        self.script("pkg/__init__.py", "")
        self.script("pkg/mod.py", "DEEP = 'from-pkg'\n")
        rc, log = self.run_script("""
            import helper, pkg.mod
            print(helper.VALUE, pkg.mod.DEEP, helper.__file__)
        """, name="main.py")
        self.assertEqual(rc, 0, log)
        self.assertIn("from-helper from-pkg /code/helper.py", log["stdout_head"])
        for rel in ("helper.py", "pkg/__init__.py", "pkg/mod.py", "main.py"):
            self.assertIn(rel, log["staged_manifest"])
        self.assertEqual(log["staged_manifest"]["helper.py"],
                         _sha(self.analysis / self.RUN / "helper.py"))

    def test_can_read_corpus_and_write_work_and_tmp(self):
        rc, log = self.run_script("""
            import os
            print(open('/data/corpus/paper.md').read().strip())
            open('/work/out.txt', 'w').write('ok')
            open('relative.txt', 'w').write('ok')
            open('/tmp/x', 'w').write('ok')
            print('CWD', os.getcwd())
            print('WROTE')
        """)
        self.assertEqual(rc, 0, log)
        self.assertIn("corpus text", log["stdout_head"])
        self.assertIn("CWD /work", log["stdout_head"])
        self.assertEqual((self.analysis / self.RUN / "out.txt").read_text(), "ok")
        self.assertEqual((self.analysis / self.RUN / "relative.txt").read_text(),
                         "ok")

    def test_network_ip_blocked(self):
        rc, log = self.run_script("""
            import socket, sys
            for host, port in [("1.1.1.1", 53), ("8.8.8.8", 443),
                               ("172.17.0.1", 80)]:
                try:
                    socket.create_connection((host, port), timeout=3)
                except OSError as e:
                    print("BLOCKED", host, type(e).__name__)
                else:
                    print("CONNECTED", host); sys.exit(1)
        """)
        self.assertEqual(rc, 0, log)
        self.assertEqual(log["stdout_head"].count("BLOCKED"), 3)
        self.assertNotIn("CONNECTED", log["stdout_head"])

    def test_network_dns_blocked(self):
        rc, log = self.run_script("""
            import socket, sys
            try:
                socket.getaddrinfo("example.com", 80)
            except OSError as e:
                print("BLOCKED", type(e).__name__)
            else:
                print("RESOLVED"); sys.exit(1)
        """)
        self.assertEqual(rc, 0, log)
        self.assertIn("BLOCKED", log["stdout_head"])

    def test_only_loopback_interface(self):
        rc, log = self.run_script("""
            import socket
            print(sorted(n for _, n in socket.if_nameindex()))
        """)
        self.assertEqual(rc, 0, log)
        self.assertIn("['lo']", log["stdout_head"])

    def test_write_corpus_fails(self):
        rc, log = self.run_script("""
            import sys
            for p in ["/data/corpus/paper.md", "/data/corpus/new.txt"]:
                try:
                    open(p, "a").write("x")
                except OSError as e:
                    print("DENIED", p, e.errno)
                else:
                    print("WROTE", p); sys.exit(1)
        """)
        self.assertEqual(rc, 0, log)
        self.assertEqual(log["stdout_head"].count("DENIED"), 2)
        self.assertEqual((self.corpus / "paper.md").read_text(), "corpus text\n")
        self.assertFalse((self.corpus / "new.txt").exists())

    def test_write_outside_work_and_tmp_fails(self):
        rc, log = self.run_script("""
            import sys
            targets = ["/x", "/etc/x", "/usr/x", "/data/assets/x",
                       "/data/inbox/x", "/data/repos/x",
                       "/data/x", "/usr/local/lib/python3.11/site-packages/x.py",
                       "/var/tmp/x", "/root/x", "/home/x"]
            for p in targets:
                try:
                    open(p, "w").write("x")
                except OSError as e:
                    print("DENIED", p)
                else:
                    print("WROTE", p); sys.exit(1)
            print("COUNT", len(targets))
        """)
        self.assertEqual(rc, 0, log)
        self.assertEqual(log["stdout_head"].count("DENIED"), 11)
        self.assertFalse((self.assets / "x").exists())
        self.assertFalse((self.inbox / "x").exists())
        self.assertFalse((self.repos / "x").exists())

    def test_env_has_no_host_variables(self):
        sentinel = "RUN_ANALYSIS_SENTINEL_7f3a"
        with mock.patch.dict(os.environ, {sentinel: "leaked-value-91c2",
                                          "AWS_SECRET_ACCESS_KEY": "nope"}):
            rc, log = self.run_script("""
                import os
                for k, v in sorted(os.environ.items()):
                    print(f"{k}={v}")
            """)
        self.assertEqual(rc, 0, log)
        out = log["stdout_head"] + log["stdout_tail"]
        self.assertLess(log["stdout_bytes"], 2048, "env too big to check fully")
        self.assertNotIn(sentinel, out)
        self.assertNotIn("leaked-value-91c2", out)
        self.assertNotIn("AWS_SECRET", out)
        self.assertNotIn(os.path.expanduser("~"), out)
        self.assertIn("HOME=/tmp", out)  # the image's own ENV, not the host's

    def test_fork_bomb_stopped_by_pids_limit(self):
        rc, log = self.run_script("""
            import os, time
            children, err = [], None
            for _ in range(2000):
                try:
                    pid = os.fork()
                except OSError as e:
                    err = type(e).__name__
                    break
                if pid == 0:
                    time.sleep(30)
                    os._exit(0)
                children.append(pid)
            print("FORKED", len(children), "ERR", err)
            for pid in children:
                os.kill(pid, 9)
            for pid in children:
                os.waitpid(pid, 0)
        """)
        self.assertEqual(rc, 0, log)
        out = log["stdout_head"]
        self.assertIn("ERR BlockingIOError", out)
        forked = int(out.split("FORKED")[1].split()[0])
        self.assertLess(forked, 256)

    def test_memory_hog_oom_killed(self):
        with mock.patch.object(ra, "MEMORY", "512m"):
            rc, log = self.run_script("""
                hog = []
                for i in range(100):
                    hog.append(bytearray(64 * 1024 * 1024))
                    print("MB", (i + 1) * 64, flush=True)
                print("SURVIVED")
            """)
        self.assertEqual(rc, 137, log)
        self.assertNotIn("SURVIVED", log["stdout_tail"])
        self.assertIn("--memory-swap", log["argv"])
        self.assertEqual(log["argv"][log["argv"].index("--memory-swap") + 1],
                         "512m")

    def test_container_timeout(self):
        with mock.patch.object(ra, "CONTAINER_TIMEOUT_S", 2):
            rc, log = self.run_script("import time; time.sleep(120)\n")
        self.assertEqual(rc, 124, log)
        self.assertLess(log["duration_s"], 30)

    def test_host_timeout_kills_container(self):
        with mock.patch.object(ra, "CONTAINER_TIMEOUT_S", 60), \
             mock.patch.object(ra, "HOST_TIMEOUT_S", 3):
            rc, log = self.run_script("import time; time.sleep(120)\n")
        self.assertNotEqual(rc, 0)
        self.assertTrue(log["timed_out"])
        self.assertEqual(log["status"], "timeout")
        self.assertLess(log["duration_s"], 30)
        name = log["argv"][log["argv"].index("--name") + 1]
        left = subprocess.run(
            ["docker", "ps", "-a", "-q", "--filter", f"name=^{name}$"],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(left, "", f"container {name} still exists")

    def test_large_output_bounded(self):
        rc, log = self.run_script("""
            import sys
            sys.stdout.write("A" * 3_000_000 + "END")
            sys.stderr.write("E" * 2_000_000 + "FIN")
        """)
        self.assertEqual(rc, 0, log)
        self.assertEqual(log["stdout_bytes"], 3_000_003)
        self.assertEqual(log["stderr_bytes"], 2_000_003)
        for k in ("stdout_head", "stdout_tail", "stderr_head", "stderr_tail"):
            self.assertLessEqual(len(log[k].encode()), 2048, k)
        self.assertTrue(log["stdout_tail"].endswith("END"))
        self.assertTrue(log["stderr_tail"].endswith("FIN"))

    def test_log_numbers_increment(self):
        path = self.script("t.py", "print(1)\n")
        self.run_main(self.RUN, path)
        self.run_main(self.RUN, path)
        names = sorted(p.name for p in self.logs_written())
        self.assertEqual(names, ["r1-exec-1.json", "r1-exec-2.json"])

    def test_docker_failure_is_docker_error(self):
        # A bad docker flag makes `docker run` itself exit 125.
        real = ra.build_argv

        def broken(*a, **k):
            argv = real(*a, **k)
            return argv[:2] + ["--no-such-flag"] + argv[2:]

        with mock.patch.object(ra, "build_argv", broken):
            rc, log = self.run_script("print('never runs')\n")
        self.assertEqual(rc, 125, log)
        self.assertEqual(log["status"], "docker_error")
        self.assertNotIn("never runs", log["stdout_head"])

    def test_exit_126_127_are_docker_error(self):
        for code in (126, 127):
            with self.subTest(code=code):
                rc, log = self.run_script(f"import sys; sys.exit({code})\n",
                                          name=f"e{code}.py")
                self.assertEqual(rc, code)
                self.assertEqual(log["status"], "docker_error")

    def test_all_docker_calls_use_absolute_path(self):
        calls = []
        real_run, real_popen = subprocess.run, subprocess.Popen

        def spy_run(argv, *a, **k):
            calls.append(argv[0])
            return real_run(argv, *a, **k)

        def spy_popen(argv, *a, **k):
            calls.append(argv[0])
            return real_popen(argv, *a, **k)

        with mock.patch.object(ra.subprocess, "run", spy_run), \
             mock.patch.object(ra.subprocess, "Popen", spy_popen), \
             mock.patch.object(ra, "HOST_TIMEOUT_S", 3), \
             mock.patch.object(ra, "CONTAINER_TIMEOUT_S", 60):
            self.run_script("import time; time.sleep(60)\n")
        # inspect, run, and kill (the host timeout forces a kill)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(set(calls), {"/usr/bin/docker"})

    def test_nonzero_exit_propagates(self):
        rc, log = self.run_script("import sys; print('boom', file=sys.stderr); sys.exit(7)\n")
        self.assertEqual(rc, 7)
        self.assertEqual(log["exit_code"], 7)
        self.assertIn("boom", log["stderr_head"])


if __name__ == "__main__":
    unittest.main()
