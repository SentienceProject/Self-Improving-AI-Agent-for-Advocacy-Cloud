"""scripts/check_citations.py with canned catalogue and page replies: no network. The checker is the public
fact-check skill's, plus this workspace's defaults and path guards (tested at the end)."""
import importlib.util
import json
import socket
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
    <summary>We argue for, and exemplify, a rigorous and empirically grounded approach to AI consciousness.</summary>
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
ARTICLE = ('<html><head><meta property="article:published_time" content="2025-04-24T00:00:00.000Z">'
           '<script type="application/ld+json">{"@graph": [{"@type": "Article", "dateModified": "2026-01-02"}]}'
           '</script></head><body><header><nav><a href="/">Home</a></nav>Site banner</header><main><article>'
           '<header><h1>Title here</h1><dl><dt>Published</dt><dd><time datetime="2025-04-24">Apr 24, 2025</time>'
           '</dd></dl></header><p>' + FILLER + '</p><p>As <a href="https://arxiv.org/abs/2411.00986">a recent '
           'report</a> from leading philosophers argued, models may soon matter morally.</p><p><a '
           'href="/research/other">Related post</a> <a href="#top">Back to top</a></p></article></main><footer>'
           '<a href="/careers">Careers</a></footer></body></html>')
LOGIN_WALL = ("<html><body><article><p>Please log in to read this article.</p><ul>"
              + "".join(f'<li><a href="/topic/{i}">Topic number {i} of our sections</a></li>' for i in range(20))
              + "</ul></article></body></html>")
PAGES = {
    "https://example.org/report": "<html><head><script>var x = 1;</script></head><body><nav>Menu</nav><p>"
                                  + FILLER + "</p><p>The survey found that <b>most respondents</b> supported "
                                  "ending the use of cages for laying hens by 2030.</p><p>" + FILLER
                                  + "</p></body></html>",
    "https://example.org/post": ARTICLE,
    "https://example.org/login-wall": LOGIN_WALL,
    "https://example.org/thin": "<html><body><p>Please log in to read this article.</p></body></html>",
}


def fake_fetch(url):
    if "rows=0" in url:   # check_citations.PROBE_URL
        return b'{"message": {}}'
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

    def test_article_header_dates_and_links_are_kept(self):
        t = cc.html_text(ARTICLE, "https://example.org/post")
        self.assertTrue(t.startswith("[Page details] Published: 2025-04-24 · Last modified: 2026-01-02"), t[:120])
        self.assertIn("Title here", t, "the article's own header is kept")
        self.assertIn("Apr 24, 2025", t)
        self.assertNotIn("Site banner", t, "the site's header isn't")
        self.assertIn("- a recent report: https://arxiv.org/abs/2411.00986", t)
        self.assertIn("- Related post: https://example.org/research/other", t, "relative links are resolved")
        links = t.split("[Links in the page text]", 1)[1]
        self.assertEqual(len(links.strip().splitlines()), 2, "no jump within the page, nothing from the site's parts")
        for left_out in ("Site banner", "Careers", "Home"):
            self.assertNotIn(left_out, t)

    def test_a_quote_across_a_link_still_matches(self):
        r = self.run_check('It said "as a recent report from leading philosophers argued, models may soon matter '
                           'morally" (https://example.org/post).\n', save=self.d / "pages")
        self.assertEqual(r["quotes"][0]["verdict"], "found in source")
        saved = Path(r["links"][0]["saved"]).read_text()
        self.assertIn("Published: 2025-04-24", saved)
        self.assertIn("https://arxiv.org/abs/2411.00986", saved)

    def test_a_list_of_links_doesnt_make_a_login_wall_readable(self):
        r = self.run_check("See https://example.org/login-wall.\n")
        self.assertIn("paywall", r["links"][0]["note"], "127 words of its own; the list of links doesn't count")

    def test_thin_page_is_noted(self):
        r = self.run_check("See https://example.org/thin.\n")
        self.assertIn("paywall", r["links"][0]["note"])

    def test_a_dead_site_is_a_broken_link_not_a_network_failure(self):
        def page(url):
            if "dead" in url:
                raise urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
            if "slow" in url:
                raise urllib.error.URLError(TimeoutError("timed out"))
            return fake_page(url)
        cc.fetch_page = page
        r = self.run_check("See https://example.org/report, https://dead.example/x and https://slow.example/y.\n")
        v = {x["url"]: x["verdict"] for x in r["links"]}
        self.assertEqual(v["https://dead.example/x"], "broken link")
        self.assertIn("gone", next(x["note"] for x in r["links"] if x["url"] == "https://dead.example/x"))
        self.assertEqual(v["https://slow.example/y"], "couldn't check", "a site that doesn't answer isn't called dead")
        self.assertFalse(r["network_failed"])
        self.assertNotIn("incomplete", cc.markdown(r))
        self.assertNotIn("reached", r["links"][0], "bookkeeping fields stay out of the JSON")
        self.assertEqual(cc.main([str(self.draft)]), 1, "flagged for the broken link, not 2 for the network")

    def test_a_lone_dead_link_asks_once_whether_the_network_works(self):
        asked = []

        def fetch(url):
            asked.append(url)
            return fake_fetch(url)

        def dead(url):
            if "dead" not in url:
                return fake_page(url)
            raise urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
        cc.fetch, cc.fetch_page = fetch, dead
        r = self.run_check("See https://dead.example/x.\n")
        self.assertEqual(asked, [cc.PROBE_URL])
        self.assertEqual(r["links"][0]["verdict"], "broken link")
        self.assertFalse(r["network_failed"])
        asked.clear()
        self.run_check("See https://example.org/report and https://dead.example/x.\n")
        self.assertEqual(asked, [], "no extra request when another link already got through")

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


class TestChatFindings(Base):
    """Problems a test run in Claude chat found (2026-11-27), each with a planted error."""

    def quotes(self, text, **kw):
        kw.setdefault("offline", True)
        return [(x["quote"], x["verdict"]) for x in self.run_check(text, **kw)["quotes"]]

    def test_a_notes_heading_isnt_a_reference_list(self):
        r = self.quotes('## Notes from the council meeting\n\nThe chair said "the hens were all moved to open barns '
                        'last spring" (butlin-2023).\n')
        self.assertEqual([v for _, v in r], ["not verbatim"])
        items = cc.reference_items("## Notes and references\n\n- Lee (2020). Report.\n")
        self.assertEqual(len(items), 1, "a heading that is just a reference list's name still counts")

    def test_misattributed_quote_is_flagged(self):
        r = self.run_check('The blog post said "indicator properties derived from scientific theories of '
                           'consciousness can be assessed" (https://example.org/report).\n')
        x = r["quotes"][0]
        self.assertEqual(x["verdict"], "found in another source")
        self.assertTrue(x["file"].endswith("butlin-2023.md"))
        self.assertIn("example.org/report", x["note"])
        self.assertEqual(cc.exit_code(r), 1)

    def test_single_quotes_are_checked(self):
        r = self.quotes("Butlin wrote 'indicator properties can be assessed in any AI system whatsoever' "
                        "(butlin-2023).\n")
        self.assertEqual([v for _, v in r], ["not verbatim"])
        found = [q for _, _, q in cc.find_quotes(
            "She said ‘we can’t keep the hens in cages, it’s wrong’ yesterday. The farmers' union said 'nothing "
            "will change for the animals' today. ‘He told us “no cages” and left’. \"He said 'stop' and left\".")]
        self.assertEqual(found, ["we can’t keep the hens in cages, it’s wrong", "nothing will change for the animals",
                                 "He told us “no cages” and left", "He said 'stop' and left"])
        self.assertEqual(cc.find_quotes("Animals' needs. 'Tis the season. The ministers' office ignored the "
                                        "companies' pledges, and the '90s rules won't do."), [],
                         "apostrophes, possessives and elisions aren't quotes")

    def test_an_inch_mark_doesnt_steal_a_quote(self):
        text = 'In the 12" main pipe, the engineer said "the hens were kept in cages without any room" today.'
        self.assertEqual([q for _, _, q in cc.find_quotes(text)], ["the hens were kept in cages without any room"])
        self.assertEqual([q for _, _, q in cc.find_quotes('They said "cages will end by 2030" and left.')],
                         ["cages will end by 2030"], "a quote can still end with a number")

    def test_quotes_near_a_doi_in_prose_arent_its_title(self):
        r = self.run_check('As one critic put it, "deep networks are overhyped and should never be trusted," citing '
                           'a review by *LeCun and colleagues* (doi:10.1038/nature14539).\n', links=False)
        self.assertEqual(r["ids"][0]["verdict"], "matches catalogue", r["ids"][0]["note"])

    def test_a_wrong_year_isnt_excused_by_another_year_nearby(self):
        for text in ("- LeCun et al. (2014). *Deep learning*. Nature. Retrieved 2015. doi:10.1038/nature14539\n",
                     "In 2016, LeCun et al. (2014) reviewed the field (doi:10.1038/nature14539).\n"):
            with self.subTest(text=text):
                x = self.run_check(text, links=False)["ids"][0]
                self.assertEqual(x["verdict"], "check")
                self.assertIn("year 2014", x["note"])

    def test_an_online_first_year_matches(self):
        CROSSREF["10.1000/online-first"] = {"title": ["Hens and cages"], "author": [{"family": "Lee"}],
                                            "issued": {"date-parts": [[2015, 12]]},
                                            "published-online": {"date-parts": [[2015, 12]]},
                                            "published-print": {"date-parts": [[2016, 3]]}}
        try:
            for year in ("2015", "2016"):
                x = self.run_check(f"- Lee ({year}). *Hens and cages*. doi:10.1000/online-first\n", links=False)
                self.assertEqual(x["ids"][0]["verdict"], "matches catalogue", year)
        finally:
            del CROSSREF["10.1000/online-first"]

    def test_offline_isnt_reported_as_all_clear(self):
        self.draft.write_text("See https://example.org/report and doi:10.1038/nature14539.\n")
        r = cc.check(self.draft, offline=True)
        self.assertEqual({x["verdict"] for x in r["ids"] + r["links"]}, {"not checked"},
                         "what wasn't checked is listed, for checking by hand")
        self.assertEqual(cc.main([str(self.draft), "--offline"]), 2)
        self.assertIn("Not everything was checked", cc.markdown(r))
        self.draft.write_text("Nothing cited here.\n")
        self.assertEqual(cc.main([str(self.draft), "--offline"]), 0)

    def test_no_saved_pages_isnt_reported_as_saved(self):
        r = self.run_check("See https://example.org/gone.\n", save=self.d / "pages")
        md = cc.markdown(r)
        self.assertNotIn("were saved as text", md)
        self.assertIn("No pages were saved", md)

    def test_closest_passage_keeps_its_case_and_quote_marks(self):
        (self.d / "sources" / "council.md").write_text(
            'The chair said: "We will End the Cage Age by 2030 in every county of the region."\n')
        r = self.run_check('The chair said "we will end the cage age by 2035 in every county of the region" '
                           "(council).\n", offline=True)
        note = r["quotes"][0]["note"]
        self.assertIn("End the Cage Age by 2030", note)
        self.assertNotIn('""', note)

    def test_bracketed_insertions_are_allowed(self):
        r = self.quotes('Butlin wrote that "[i]ndicator properties derived from scientific theories of consciousness '
                        'can be assessed [in] AI systems" and "[the authors] argue that indicator properties derived '
                        'from scientific theories" (butlin-2023).\n')
        self.assertEqual([v for _, v in r], ["found in source", "found in source"])

    def test_short_quotes_are_checked_or_counted(self):
        r = self.run_check('Butlin wrote "consciousness can never arise in AI systems" (butlin-2023), and the '
                           '"so-called experts" agreed.\n', offline=True)
        self.assertEqual([x["verdict"] for x in r["quotes"]], ["not verbatim"], "a 7-word quote is checked")
        self.assertEqual(r["short_quotes"], 1)
        self.assertIn("under 5 words", cc.markdown(r))


    def test_a_quote_whose_sentence_cites_a_doi_doesnt_borrow_the_paragraphs_link(self):
        r = self.run_check('The report is at https://example.org/report. A review (doi:10.1038/nature14539) said '
                           '"deep learning allows computational models to learn representations of data".\n')
        self.assertEqual((r["quotes"][0]["verdict"], r["quotes"][0]["file"]), ("not checked", None))

    def test_quotes_are_checked_against_a_papers_abstract(self):
        r = self.run_check('Butlin said "we argue for, and exemplify, a rigorous and empirically grounded approach" '
                           '[1], and "the theories all agree that current systems are clearly not conscious" [1].'
                           '\n\n## References\n\n1. Butlin, P. et al. (2023). arXiv:2308.08708\n', links=False)
        found, missing = r["quotes"]
        self.assertEqual(found["verdict"], "found in source")
        self.assertIn("abstract", found["file"])
        self.assertEqual(missing["verdict"], "not checked", "the abstract is only part of the paper")
        self.assertIn("only its abstract", missing["note"])


class TestSecondChatRun(Base):
    """Problems a second claude.ai test run found (2026-11-27)."""

    def test_a_footnote_mark_right_after_a_quote_keeps_it(self):
        for mark in ("[1]", "[^1]", "¹", "*"):
            with self.subTest(mark=mark):
                text = f'They wrote "these results are preliminary and need replication"{mark} last year.'
                self.assertEqual([q for _, _, q in cc.find_quotes(text)],
                                 ["these results are preliminary and need replication"])
        self.assertEqual([q for _, _, q in cc.find_quotes('In italics, *"these results are preliminary"* here.')],
                         ["these results are preliminary"])

    def test_an_unlabelled_attached_file_still_catches_a_misquote(self):
        (self.d / "attached").mkdir()
        (self.d / "attached" / "iea-data.txt").write_text(FILLER + " The survey found that most respondents "
                                                       "supported ending the use of cages for laying hens by 2030.")
        draft = ('The survey found that "nearly all respondents supported ending the use of cages for laying hens by '
                 '2030" (https://example.org/report).\n')
        r = self.run_check(draft, offline=True, sources=[self.d / "attached"])
        x = r["quotes"][0]
        self.assertEqual(x["verdict"], "not verbatim")
        self.assertIn("most respondents supported", x["note"])
        self.assertIn("doesn't say where it's from", x["note"])
        self.assertEqual(cc.exit_code(r), 1)
        r = self.run_check('Someone said "a sentence that appears in no attached file at all, honestly" '
                           "(https://example.org/report).\n", offline=True, sources=[self.d / "attached"])
        self.assertEqual(r["quotes"][0]["verdict"], "not checked", "nothing close: not called a misquote")

    def test_a_short_misquote_still_shows_the_real_sentence(self):
        (self.d / "sources" / "lab.md").write_text("In the coming years we expect further gains in efficiency.")
        r = self.run_check('The lab said "we expect huge further gains" (lab).\n', offline=True)
        self.assertEqual(r["quotes"][0]["verdict"], "not verbatim")
        self.assertIn("we expect further gains", r["quotes"][0]["note"])

    def test_a_bare_doi_in_prose_isnt_asked_for_an_author(self):
        r = self.run_check("Since 2019 the evidence has grown (doi:10.1038/nature14539).\n", links=False)
        self.assertEqual(r["ids"][0]["verdict"], "matches catalogue", r["ids"][0]["note"])
        r = self.run_check("One review (Smith 2015, doi:10.1038/nature14539) said so.\n", links=False)
        self.assertIn("LeCun", r["ids"][0]["note"], "a citation that names an author is still compared")

    def test_unreachable_links_arent_called_empty(self):
        def down(url):
            raise urllib.error.URLError("no network")
        cc.fetch = cc.fetch_page = down
        md = cc.markdown(self.run_check("See https://example.org/report.\n", save=self.d / "pages"))
        self.assertIn("none of the links could be opened", md)
        self.assertNotIn("gave no text", md)


if __name__ == "__main__":
    unittest.main()
