"""Tests for scripts/fetch_repo.py. No real network: the opener is faked.

    python3 tests/test_fetch_repo.py -v
"""

import importlib.util
import io
import os
import shutil
import stat
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "scripts" / "fetch_repo.py"
if not SRC.exists():                       # still in workspace/drafts/
    SRC = Path(__file__).resolve().with_name("fetch_repo.py")

SHA = "0123456789abcdef0123456789abcdef01234567"
GH_URL = f"https://github.com/someone/proj/archive/{SHA}.tar.gz"
ZEN_URL = "https://zenodo.org/records/1234567/files/data.zip?download=1"
ZEN_CSV = "https://zenodo.org/records/1234567/files/table.csv"


def load_copy(root: Path):
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SRC, root / "scripts" / "fetch_repo.py")
    spec = importlib.util.spec_from_file_location("fetch_repo_under_test",
                                                  root / "scripts" / "fetch_repo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_tar(entries) -> bytes:
    """entries: list of (name, bytes) for files, (name, None) for dirs,
    (name, ('sym', target)) for symlinks."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries:
            ti = tarfile.TarInfo(name)
            if data is None:
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            elif isinstance(data, tuple):
                ti.type, ti.linkname = tarfile.SYMTYPE, data[1]
                tf.addfile(ti)
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def make_zip(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


class FakeResp(io.BytesIO):
    status = 200


class FakeOpener:
    def __init__(self, body=b"", exc=None):
        self.body, self.exc, self.requests = body, exc, []

    def factory(self):
        class R:
            hops = []
        return self, R()

    def open(self, req, timeout=None):
        self.requests.append(req)
        if self.exc:
            raise self.exc
        return FakeResp(self.body)


GOOD_TAR = make_tar([
    (f"proj-{SHA}", None),
    (f"proj-{SHA}/README.md", b"hello"),
    (f"proj-{SHA}/src/a.py", b"print(1)"),
    (f"proj-{SHA}/link", ("sym", "/etc/passwd")),
])


class FetchRepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "repo"
        self.m = load_copy(self.root)
        self.repos = self.root / "workspace" / "repos"
        self.repos.mkdir(parents=True)

    def tearDown(self):
        for dirpath, _, _ in os.walk(self.tmp):
            os.chmod(dirpath, 0o755)
        shutil.rmtree(self.tmp)

    def fetch(self, url=GH_URL, name="proj", body=GOOD_TAR, exc=None):
        op = FakeOpener(body, exc)
        return self.m.run(url, name, opener_factory=op.factory), op

    # --- URL rules ---------------------------------------------------------

    def test_url_forms(self):
        ok = [GH_URL, f"https://codeload.github.com/someone/proj/tar.gz/{SHA}",
              ZEN_URL, ZEN_CSV,
              f"https://huggingface.co/datasets/org/ds/resolve/{SHA}/data/train.parquet"]
        for u in ok:
            self.m.check_url(u)
        bad = ["https://github.com/someone/proj/archive/main.tar.gz",        # branch
               "https://github.com/someone/proj/archive/v1.0.tar.gz",        # tag
               GH_URL.replace("https", "http"),
               "https://gitlab.com/someone/proj/-/archive/x.tar.gz",
               f"https://user@github.com/someone/proj/archive/{SHA}.tar.gz",
               f"https://huggingface.co/datasets/org/ds/resolve/{SHA}/../x",
               f"https://huggingface.co/datasets/org/ds/resolve/{SHA}//x",
               GH_URL + "?x=1", ""]
        for u in bad:
            with self.assertRaises(self.m.Refusal, msg=u):
                self.m.check_url(u)

    def test_redirect_hosts(self):
        self.m.check_redirect("https://codeload.github.com/x")
        for u in ["https://evil.example/x", "http://codeload.github.com/x",
                  "https://a@codeload.github.com/x",
                  "https://codeload.github.com:8443/x"]:
            with self.assertRaises(self.m.Refusal, msg=u):
                self.m.check_redirect(u)

    def test_bad_name_makes_no_request(self):
        (code, rec), op = self.fetch(name="../x")
        self.assertEqual(code, 2)
        self.assertEqual(op.requests, [])

    # --- unpacking ---------------------------------------------------------

    def test_github_tar_unpacked_stripped_and_frozen(self):
        (code, rec), _ = self.fetch()
        self.assertEqual(code, 0, rec)
        out = self.repos / "proj"
        self.assertEqual((out / "README.md").read_bytes(), b"hello")
        self.assertEqual((out / "src" / "a.py").read_bytes(), b"print(1)")
        self.assertFalse((out / "link").exists() or (out / "link").is_symlink())
        self.assertEqual(len(rec["skipped"]), 1)
        self.assertEqual(stat.S_IMODE(os.stat(out / "README.md").st_mode), 0o444)
        self.assertEqual(stat.S_IMODE(os.stat(out / "src").st_mode), 0o555)
        self.assertTrue((self.repos / "proj.fetch.json").exists())
        self.assertEqual(sorted(p.name for p in self.repos.iterdir()),
                         ["proj", "proj.fetch.json"])  # temp files gone

    def test_traversal_refuses_whole_archive(self):
        body = make_tar([("p/ok.txt", b"x"), ("p/../../escape.txt", b"x")])
        (code, rec), _ = self.fetch(body=body)
        self.assertEqual(code, 2)
        self.assertIn("..", rec["reason"])
        self.assertEqual(list(self.repos.iterdir()), [])
        self.assertFalse((self.root / "escape.txt").exists())

    def test_absolute_path_refused(self):
        body = make_tar([("/abs.txt", b"x")])
        (code, rec), _ = self.fetch(body=body)
        self.assertEqual(code, 2)
        self.assertEqual(list(self.repos.iterdir()), [])

    def test_unpacked_size_cap(self):
        self.m.MAX_UNPACKED = 10
        body = make_tar([("p/big.bin", b"x" * 100)])
        (code, rec), _ = self.fetch(body=body)
        self.assertEqual(code, 2)
        self.assertEqual(list(self.repos.iterdir()), [])

    def test_download_size_cap(self):
        self.m.MAX_DOWNLOAD = 10
        (code, rec), _ = self.fetch()
        self.assertEqual(code, 2)
        self.assertEqual(list(self.repos.iterdir()), [])

    def test_github_non_gzip_refused(self):
        (code, rec), _ = self.fetch(body=b"<html>not a tarball</html>")
        self.assertEqual(code, 2)

    def test_zenodo_zip(self):
        body = make_zip([("data/a.csv", "1,2\n"), ("data/b.csv", "3,4\n")])
        (code, rec), _ = self.fetch(url=ZEN_URL, name="zen", body=body)
        self.assertEqual(code, 0, rec)
        self.assertEqual((self.repos / "zen" / "a.csv").read_text(), "1,2\n")

    def test_zenodo_single_file_kept_as_is(self):
        (code, rec), _ = self.fetch(url=ZEN_CSV, name="tab", body=b"a,b\n1,2\n")
        self.assertEqual(code, 0, rec)
        self.assertEqual((self.repos / "tab" / "table.csv").read_bytes(), b"a,b\n1,2\n")

    def test_never_overwrites(self):
        self.fetch()
        (code, rec), op = self.fetch()
        self.assertEqual(code, 2)
        self.assertEqual(op.requests, [])

    def test_in_progress_files_left_alone(self):
        (self.repos / ".proj.part").mkdir()
        (code, rec), _ = self.fetch()
        self.assertEqual(code, 2)
        self.assertTrue((self.repos / ".proj.part").exists())

    def test_network_error_is_failed_not_refused(self):
        (code, rec), _ = self.fetch(exc=urllib.error.URLError("down"))
        self.assertEqual(code, 1)
        self.assertEqual(list(self.repos.iterdir()), [])

    def test_symlinked_repos_dir_refused(self):
        shutil.rmtree(self.repos)
        (self.tmp / "elsewhere").mkdir()
        self.repos.symlink_to(self.tmp / "elsewhere")
        (code, rec), op = self.fetch()
        self.assertEqual(code, 2)
        self.assertEqual(op.requests, [])


if __name__ == "__main__":
    unittest.main()
