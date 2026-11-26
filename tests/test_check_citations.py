"""scripts/check_citations.py with canned catalogue and page replies: no network. The checker is the public
fact-check skill's, plus this workspace's defaults and path guards (tested at the end)."""
import importlib.util
import json
import tempfile
import unittest
import urllib.error
import os
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_citations.py"
spec = importlib.util.spec_from_file_location("check_citations", SCRIPT)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

ARXIV_REPLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2308.08708v3</id>
    <published>2023-08-17T00:00:00Z</published>
    <title>Consciousness in Artificial Intelligence: Insights from the
      Science of Consciousness</title>
    <author><name>Patrick Butlin</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/hep-th/9901001v1</id>
    <published>1999-01-01T00:00:00Z</published>
    <title>An old-style paper</title>
    <author><name>Ann Old</name></author>
    <arxiv:comment>This paper has been withdrawn by the author</arxiv:comment>
  </entry>
</feed>"""

CROSSREF = {
    "10.1038/nature14539": {"title": ["Deep learning"], "author": [{"family": "LeCun"}],
                            "issued": {"date-parts": [[2015, 5]]}},
    "10.1016/S0140-6736(97)11096-0": {"title": ["RETRACTED: Ileal-lymphoid-nodular hyperplasia"],
                                      "author": [{"family": "Wakefield"}], "issued": {"date-parts": [[1998]]},
                                      "updated-by": [{"type": "retraction"}]},
}

FILLER = " ".join(["Background text about farmed animals and the law."] * 40)
PAGES = {
    "https://example.org/report": "<html><head><script>var x = 1;</script></head><body><nav>Menu</nav><p>"
                                  + FILLER + "</p><p>The survey found that <b>most respondents</b> supported "
                                  "ending the use of cages for laying hens by 2030.</p><p>" + FILLER
                                  + "</p></body></html>",
    "https://example.org/thin": "<html><body><p>Please log in to read this article.</p></body></html>",
}


def fake_fetch(url):
    if "export.arxiv.org" in url:
        return ARXIV_REPLY
    doi = url.split("/works/", 1)[1].replace("%28", "(").replace("%29", ")")
    if doi not in CROSSREF:
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)
    return json.dumps({"message": CROSSREF[doi]}).encode()


def fake_page(url):
    if url in PAGES:
        return "text/html", "utf-8", PAGES[url].encode(), url
    if "blocked" in url:
        raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)
    if "gone" in url:
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)
    raise urllib.error.HTTPError(url, 404, "not found", {}, None)   # anything else (e.g. arXiv pages): gone


DRAFT = """# Draft

As one review puts it, "indicator properties derived from scientific theories of consciousness can be assessed in
AI systems" (butlin-2023).

Another said "this sentence is nowhere in any source file at all, really".

## Citations

- Butlin et al. (2023). *Consciousness in Artificial Intelligence: Insights from the Science of Consciousness*.
  https://arxiv.org/abs/2308.08708v2
- Old (1999). *An old-style paper*. arXiv:hep-th/9901001
- LeCun et al. (2015). *Deep learning*. https://doi.org/10.1038/nature14539.
- Wakefield (1998). *Ileal-lymphoid-nodular hyperplasia*. (doi:10.1016/S0140-6736(97)11096-0)
- Nobody (2020). *Invented*. arXiv:2001.99999
- Arxiv DOI form: 10.48550/arXiv.2308.08708
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.d = self.root / "workspace"           # everything the checker may touch lives in workspace/
        (self.d / "sources").mkdir(parents=True)
        self._paths = cc.ROOT, cc.DATA, cc.CORPUS, cc.CITECHECK
        cc.ROOT, cc.DATA = self.root, self.d
        cc.CORPUS, cc.CITECHECK = self.d / "corpus", self.d / "citecheck"
        (self.d / "sources" / "butlin-2023.md").write_text(
            "We argue that indicator properties de-\nrived from scientific theories of consciousness can be "
            "assessed in AI sys­tems.\n")
        self.draft = self.d / "draft.md"
        self.draft.write_text(DRAFT)
        self._saved = cc.fetch, cc.fetch_page, cc.time.sleep
        cc.fetch, cc.fetch_page, cc.time.sleep = fake_fetch, fake_page, lambda s: None

    def tearDown(self):
        cc.fetch, cc.fetch_page, cc.time.sleep = self._saved
        cc.ROOT, cc.DATA, cc.CORPUS, cc.CITECHECK = self._paths
        self.tmp.cleanup()

    def run_check(self, text=None, **kw):
        if text is not None:
            self.draft.write_text(text)
        kw.setdefault("sources", [self.d / "sources"])
        return cc.check(self.draft, **kw)


class TestIdentifiers(Base):
    def verdicts(self, r):
        return {x["id"]: x["verdict"] for x in r["ids"]}

    def test_identifiers(self):
        r = self.run_check(links=False)
        self.assertEqual(self.verdicts(r), {
            "arxiv:2308.08708": "matches catalogue",
            "arxiv:hep-th/9901001": "retraction or withdrawal",
            "arxiv:2001.99999": "not found in catalogue",
            "doi:10.1038/nature14539": "matches catalogue",
            "doi:10.1016/S0140-6736(97)11096-0": "retraction or withdrawal",
        })
        self.assertFalse(r["network_failed"])

    def test_mismatch_is_check_not_wrong(self):
        x = self.run_check("- Smith (2019). *Deep learning*. doi:10.1038/nature14539\n", links=False)["ids"][0]
        self.assertEqual(x["verdict"], "check")
        self.assertIn("LeCun", x["note"])
        self.assertIn("2019", x["note"])

    def test_network_failure_is_loud(self):
        def down(url):
            raise urllib.error.URLError("no network")
        cc.fetch = cc.fetch_page = down
        r = self.run_check()
        self.assertTrue(r["network_failed"])
        self.assertEqual({x["verdict"] for x in r["ids"] + r["links"]}, {"couldn't check"})
        self.assertIn("incomplete", cc.markdown(r))

    def test_header_says_what_it_doesnt_check(self):
        self.assertIn("does not check that a source supports the claim", cc.markdown(self.run_check()))


class TestQuotes(Base):
    def test_quotes_against_saved_sources(self):
        q = {x["verdict"]: x for x in self.run_check(offline=True)["quotes"]}
        self.assertTrue(q["found in source"]["file"].endswith("butlin-2023.md"),
                        "hyphenation, soft hyphens and line breaks don't hide a verbatim quote")
        self.assertIn("not checked", q, "a quote with no source isn't reported as wrong")

    def test_misattributed_quote_is_not_verbatim(self):
        r = self.run_check('Butlin wrote "indicator properties can be assessed in any AI system whatsoever" '
                           "(butlin-2023).\n", offline=True)
        self.assertEqual(r["quotes"][0]["verdict"], "not verbatim")

    def test_quote_checked_against_linked_page_via_footnote(self):
        r = self.run_check('The survey found "most respondents supported ending the use of cages for laying hens '
                           'by 2030".[^1]\n\n[^1]: Example Org (2024). Survey report. https://example.org/report\n')
        self.assertEqual([x["verdict"] for x in r["quotes"]], ["found in source"])

    def test_wrong_quote_shows_closest_passage(self):
        r = self.run_check('The survey found "nearly all respondents supported ending the use of cages for '
                           'laying hens by 2025" [1].\n\n## References\n\n1. Example Org (2024). '
                           'https://example.org/report\n')
        x = r["quotes"][0]
        self.assertEqual(x["verdict"], "not verbatim")
        self.assertIn("by 2030", x["note"])

    def test_author_year_points_to_reference(self):
        r = self.run_check('Jones (2024) wrote that "nearly all respondents supported ending the use of cages '
                           'outright".\n\n## Sources\n\n- Jones, A. (2024). Survey. https://example.org/report\n')
        self.assertEqual(r["quotes"][0]["verdict"], "not verbatim")

    def test_paywalled_source_is_not_checked_not_wrong(self):
        r = self.run_check('She said "this is a long enough quote to be checked by the tool" '
                           "(https://example.org/thin).\n")
        self.assertEqual(r["quotes"][0]["verdict"], "not checked")
        self.assertIn("little or no text", r["quotes"][0]["note"])

    def test_titles_in_reference_list_are_not_quotes(self):
        r = self.run_check('Text.\n\n## References\n\n- Lee (2020). "A title that has more than eight words in '
                           'it here". https://example.org/report\n')
        self.assertEqual(r["quotes"], [])

    def test_short_quote_doesnt_shift_the_pairing(self):
        r = self.run_check('The "End the Cage Age" initiative collected many signatures. A review argued that '
                           '"indicator properties derived from scientific theories of consciousness can be '
                           'assessed in AI systems" (butlin-2023).\n', offline=True)
        self.assertEqual([(x["quote"][:9], x["verdict"]) for x in r["quotes"]], [("indicator", "found in source")])

    def test_attribution_follows_the_quotes_own_sentence(self):
        r = self.run_check('A survey was published [1]. A paper said "this is a long enough quote to be checked by '
                           'the tool" [2].\n\n## References\n\n1. Org (2024). https://example.org/report\n'
                           '2. Someone (2023). A paper without a link.\n')
        self.assertEqual((r["quotes"][0]["verdict"], r["quotes"][0]["file"]), ("not checked", None))

    def test_numbered_list_in_body_is_still_checked(self):
        r = self.run_check('Our asks:\n\n1. Adopt the line "this is a long enough quote to be checked by the tool '
                           'here".\n', offline=True)
        self.assertEqual(len(r["quotes"]), 1)


class TestLinks(Base):
    def test_link_verdicts_and_saving(self):
        r = self.run_check("See https://example.org/report, https://example.org/gone and "
                           "[a blocked site](https://blocked.example.com/x).\n", save=self.d / "pages")
        v = {x["url"]: x["verdict"] for x in r["links"]}
        self.assertEqual(v, {"https://example.org/report": "link works", "https://example.org/gone": "broken link",
                             "https://blocked.example.com/x": "refused"})
        saved = Path(next(x["saved"] for x in r["links"] if x.get("saved")))
        body = saved.read_text()
        self.assertTrue(body.startswith("Source: https://example.org/report"))
        self.assertIn("most respondents supported", body)
        self.assertNotIn("var x", body, "scripts are dropped")
        self.assertNotIn("Menu", body, "navigation is dropped")

    def test_thin_page_is_noted(self):
        r = self.run_check("See https://example.org/thin.\n")
        self.assertIn("paywall", r["links"][0]["note"])

    def test_saved_pages_work_as_sources_later(self):
        self.run_check("See https://example.org/report.\n", save=self.d / "pages")
        r = self.run_check('It said "most respondents supported ending the use of cages for laying hens" '
                           "(https://example.org/report).\n", sources=[self.d / "pages"], offline=True)
        self.assertEqual(r["quotes"][0]["verdict"], "found in source")


class TestDocx(Base):
    def test_docx_text_links_and_footnotes(self):
        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        doc = (f'<w:document {w}><w:body>'
               '<w:p><w:r><w:t xml:space="preserve">The survey found "most respondents supported ending the use '
               'of cages for laying hens".</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
               '<w:p><w:r><w:t xml:space="preserve">See </w:t></w:r><w:hyperlink r:id="rId5"><w:r>'
               '<w:t>the report</w:t></w:r></w:hyperlink><w:r><w:t xml:space="preserve"> too.</w:t></w:r></w:p>'
               '</w:body></w:document>')
        notes = (f'<w:footnotes {w}><w:footnote w:id="0"><w:p/></w:footnote><w:footnote w:id="1"><w:p><w:r>'
                 '<w:t>Example Org (2024). https://example.org/report</w:t></w:r></w:p></w:footnote></w:footnotes>')
        rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId5" Target="https://example.org/gone" TargetMode="External"/></Relationships>')
        p = self.d / "draft.docx"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", doc)
            z.writestr("word/footnotes.xml", notes)
            z.writestr("word/_rels/document.xml.rels", rels)
        text = cc.read_draft(p)
        self.assertIn("See the report (https://example.org/gone) too.", text)
        r = cc.check(p)
        self.assertEqual({x["url"]: x["verdict"] for x in r["links"]},
                         {"https://example.org/report": "link works", "https://example.org/gone": "broken link"})
        self.assertEqual(r["quotes"][0]["verdict"], "found in source")


class TestCli(Base):
    def test_exit_codes(self):
        self.draft.write_text("Nothing cited here.\n")
        self.assertEqual(cc.main([str(self.draft)]), 0)
        self.draft.write_text("See https://example.org/gone.\n")
        self.assertEqual(cc.main([str(self.draft)]), 1)

    def test_fetch_mode(self):
        p = self.d / "citecheck" / "run1"
        self.assertEqual(cc.main(["--fetch", "https://example.org/report", "--save", str(p)]), 0)
        self.assertEqual(len(list(p.iterdir())), 1)
        cc.main(["--fetch", "https://example.org/thin", "https://example.org/report", "--save", str(p)])
        self.assertEqual(sorted(f.name[:3] for f in p.iterdir()), ["01-", "02-", "03-"],
                         "a second fetch into the same folder continues the numbering")



class TestWorkspaceGuards(Base):
    """It runs outside the sandbox in hardened mode, so it must refuse to read or write outside workspace/."""

    def refused(self, argv):
        return cc.main(argv) == 2

    def test_corpus_is_always_a_source(self):
        (self.d / "corpus").mkdir()
        (self.d / "corpus" / "lee-2020.md").write_text("Indeed the hens were kept in cages without any room at all.")
        self.draft.write_text('Lee said "the hens were kept in cages without any room at all" (lee-2020).\n')
        self.assertEqual(cc.main([str(self.draft), "--offline"]), 0)

    def test_relative_paths_resolve_from_the_workspace_root(self):
        self.draft.write_text("Nothing cited.\n")
        self.assertEqual(cc.main(["workspace/draft.md", "--offline", "--save", "workspace/citecheck/r1"]), 0)

    def test_draft_outside_workspace_refused(self):
        outside = self.root / "notes.md"
        outside.write_text("x")
        for draft in (str(outside), "../notes.md", "my/profile.md", ".claude/settings.json", "/etc/passwd"):
            with self.subTest(draft=draft):
                self.assertTrue(self.refused([draft, "--offline"]))

    def test_sources_outside_workspace_refused(self):
        for src in (str(Path.home() / ".ssh"), str(self.root), "workspace/../my", "/tmp"):
            with self.subTest(src=src):
                self.assertTrue(self.refused([str(self.draft), "--offline", "--sources", src]))

    def test_save_only_inside_citecheck(self):
        for save in ("workspace/outbox", "workspace", str(self.root / "x"), "workspace/citecheck/../logs"):
            with self.subTest(save=save):
                self.assertTrue(self.refused([str(self.draft), "--offline", "--save", save]))
        self.assertTrue(self.refused(["--fetch", "https://example.org/report", "--save", "workspace/outbox"]))

    def test_symlinks_cant_point_out(self):
        secret = self.root / "secret.txt"
        secret.write_text("the hens were kept in cages without any room at all, said the private note")
        os.symlink(secret, self.d / "link.md")
        self.assertTrue(self.refused([str(self.d / "link.md"), "--offline"]))
        os.symlink(secret, self.d / "sources" / "leak.md")   # inside a sources folder: skipped, not read
        self.draft.write_text('"the hens were kept in cages without any room at all" (leak).\n')
        r = cc.check(self.draft, sources=[self.d / "sources"], offline=True)
        self.assertNotIn("found in source", [x["verdict"] for x in r["quotes"]])

    def test_env_files_refused(self):
        env = self.d / ".env"
        env.write_text("KEY=1")
        self.assertTrue(self.refused([str(env), "--offline"]))


if __name__ == "__main__":
    unittest.main()
