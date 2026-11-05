"""Tests for scripts/fetch_pdf.py. No real network: the opener is faked.

    python3 tests/test_fetch_pdf.py -v
"""

import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

if os.name == "nt":   # they use Linux and macOS file features (O_NOFOLLOW, fcntl); so does hardened mode
    raise unittest.SkipTest("hardened mode's scripts need Linux, macOS or WSL2, not native Windows")

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "scripts" / "fetch_pdf.py"
PDF = b"%PDF-1.7\n" + b"x" * 1000
GOOD_URL = "https://arxiv.org/pdf/2401.00001"


def load_copy(root: Path):
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SRC, root / "scripts" / "fetch_pdf.py")
    spec = importlib.util.spec_from_file_location("fetch_pdf_under_test",
                                                  root / "scripts" / "fetch_pdf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeResp(io.BytesIO):
    def __init__(self, body, url, status=200):
        super().__init__(body)
        self._url, self.status = url, status

    def geturl(self):
        return self._url


class FakeOpener:
    def __init__(self, body=PDF, final_url=GOOD_URL, exc=None, hops=()):
        self.body, self.final_url, self.exc, self.hops = body, final_url, exc, hops
        self.requests = []

    def factory(self):
        opener = self

        class R:
            hops = list(opener.hops)
        return self, R()

    def open(self, req, timeout=None):
        self.requests.append(req)
        if self.exc:
            raise self.exc
        return FakeResp(self.body, self.final_url)


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "repo"
        self.m = load_copy(self.root)
        self.inbox = self.root / "workspace" / "inbox"
        self.inbox.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_fetch(self, url=GOOD_URL, name="paper1", **kw):
        return self.m.run(url, name, opener_factory=FakeOpener(**kw).factory)


class TestUrlRules(FetchTest):
    def test_allowed(self):
        for url in [GOOD_URL, "https://ARXIV.org/pdf/2401.00001v2",
                    "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/a.pdf",
                    "https://www.pnas.org/doi/pdf/10.1073/pnas.1?download=true",
                    "https://arxiv.org:443/pdf/1"]:
            with self.subTest(url=url):
                self.m.check_url(url)

    def test_refused(self):
        for url in ["http://arxiv.org/pdf/1", "ftp://arxiv.org/x",
                    "https://evil.com/x.pdf", "https://evil.arxiv.org/x",
                    "https://arxiv.org.evil.com/x", "https://arxiv.org./x",
                    "https://arxiv.org@evil.com/x", "https://u:p@arxiv.org/x",
                    "https://arxiv.org:8443/x", "https://1.2.3.4/x.pdf",
                    "https://arxiv.org/$(id)", "https://arxiv.org/`id`",
                    "https://arxiv.org/a b", 'https://arxiv.org/a"b',
                    "https://arxiv.org/a!b", "https://arxiv.org/a\\b",
                    "https://arxiv.org/" + "a" * 500, "", "arxiv.org/pdf/1"]:
            with self.subTest(url=url):
                with self.assertRaises(self.m.Refusal):
                    self.m.check_url(url)

    def test_names(self):
        self.m.check_name("birch-2024_sentience")
        for name in ["", "../x", "a/b", ".hidden", "a.pdf", "-x", "a b",
                     "a" * 82, "$(id)"]:
            with self.subTest(name=name):
                with self.assertRaises(self.m.Refusal):
                    self.m.check_name(name)


class TestRedirects(FetchTest):
    def redirect(self, handler, new):
        req = urllib.request.Request(GOOD_URL)
        return handler.redirect_request(req, None, 302, "Found", {}, new)

    def test_allowed_hop_followed(self):
        h = self.m._CheckedRedirects()
        self.assertIsNotNone(self.redirect(h, "https://arxiv.org/pdf/1v2"))
        self.assertIsNotNone(self.redirect(h, "/pdf/1v3"))  # relative
        self.assertEqual(len(h.hops), 2)

    def test_bad_hops_refused(self):
        for new in ["https://evil.com/x", "http://arxiv.org/pdf/1",
                    "https://arxiv.org:8080/x", "https://evil.arxiv.org/x"]:
            with self.subTest(new=new):
                with self.assertRaises(self.m.Refusal):
                    self.redirect(self.m._CheckedRedirects(), new)

    def test_too_many_redirects(self):
        h = self.m._CheckedRedirects()
        for i in range(self.m.MAX_REDIRECTS):
            self.redirect(h, f"https://arxiv.org/pdf/{i}")
        with self.assertRaises(self.m.Refusal):
            self.redirect(h, "https://arxiv.org/pdf/last")

    def test_final_url_checked(self):
        code, rec = self.run_fetch(final_url="https://evil.com/x.pdf")
        self.assertEqual(code, 2)
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_opener_ignores_proxy_env(self):
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        try:
            opener, _ = self.m.build_opener()
            # An empty ProxyHandler replaces the default one (which would read
            # HTTPS_PROXY); no handler may carry a proxy.
            proxied = [h for h in opener.handlers
                       if isinstance(h, urllib.request.ProxyHandler) and h.proxies]
            self.assertEqual(proxied, [])
            self.assertFalse(any(hasattr(h, "https_open") and
                                 isinstance(h, urllib.request.ProxyHandler)
                                 for h in opener.handlers))
        finally:
            del os.environ["HTTPS_PROXY"]


class TestSaving(FetchTest):
    def test_saves_pdf(self):
        code, rec = self.run_fetch()
        self.assertEqual(code, 0)
        saved = self.inbox / "paper1.pdf"
        self.assertEqual(saved.read_bytes(), PDF)
        self.assertEqual(rec["bytes"], len(PDF))
        import hashlib
        self.assertEqual(rec["sha256"], hashlib.sha256(PDF).hexdigest())
        self.assertEqual([p.name for p in self.inbox.iterdir()], ["paper1.pdf"])

    def test_non_pdf_refused_and_nothing_left(self):
        for body in [b"<html>login</html>", b"", b"%PD"]:
            with self.subTest(body=body):
                code, rec = self.run_fetch(body=body)
                self.assertEqual(code, 2)
                self.assertEqual(list(self.inbox.iterdir()), [])

    def test_oversize_refused_and_nothing_left(self):
        self.m.MAX_BYTES = 2000
        code, rec = self.run_fetch(body=b"%PDF-" + b"x" * 5000)
        self.assertEqual(code, 2)
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_never_overwrites(self):
        (self.inbox / "paper1.pdf").write_bytes(b"original")
        code, rec = self.run_fetch()
        self.assertEqual(code, 2)
        self.assertEqual((self.inbox / "paper1.pdf").read_bytes(), b"original")

    def test_planted_symlink_not_followed(self):
        target = self.tmp / "outside.txt"
        target.write_text("keep")
        os.symlink(target, self.inbox / "paper1.pdf")
        code, rec = self.run_fetch()
        self.assertEqual(code, 2)
        self.assertEqual(target.read_text(), "keep")

    def test_symlinked_inbox_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        self.inbox.rmdir()
        os.symlink(outside, self.inbox)
        code, rec = self.run_fetch()
        self.assertEqual(code, 2)
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_workspace_refused(self):
        outside = self.tmp / "ws"
        (outside / "inbox").mkdir(parents=True)
        shutil.rmtree(self.root / "workspace")
        os.symlink(outside, self.root / "workspace")
        code, rec = self.run_fetch()
        self.assertEqual(code, 2)
        self.assertEqual(list((outside / "inbox").iterdir()), [])

    def test_missing_inbox_refused(self):
        self.inbox.rmdir()
        code, rec = self.run_fetch()
        self.assertEqual(code, 2)

    def test_network_error_is_failure_not_refusal(self):
        code, rec = self.run_fetch(exc=urllib.error.URLError("timed out"))
        self.assertEqual(code, 1)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_bad_url_never_reaches_network(self):
        fake = FakeOpener()
        code, rec = self.m.run("https://evil.com/x", "p", opener_factory=fake.factory)
        self.assertEqual(code, 2)
        self.assertEqual(fake.requests, [])


class TestLogging(FetchTest):
    def logs(self):
        return sorted((self.root / "workspace" / "logs").glob("fetch-*.json"))

    def test_every_attempt_logged_never_overwritten(self):
        for url in [GOOD_URL, "https://evil.com/x"]:
            code, rec = self.m.run(url, "p" + str(len(self.logs())),
                                   opener_factory=FakeOpener().factory)
            self.m.write_log(rec)
        logs = self.logs()
        self.assertEqual(len(logs), 2)
        statuses = sorted(json.loads(p.read_text())["status"] for p in logs)
        self.assertEqual(statuses, ["refused", "saved"])

    def test_wrong_arg_count_refused(self):
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.m.main(["only-one"]), 2)


if __name__ == "__main__":
    unittest.main()
