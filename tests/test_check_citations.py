"""scripts/check_citations.py with canned catalogue replies: no network."""
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_citations", ROOT / "scripts" / "check_citations.py")
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

DRAFT = """# Draft

As one review puts it, "indicator properties derived from scientific theories of consciousness can be assessed in
AI systems" (butlin-2023).

Another said "this sentence is nowhere in any corpus file at all, really".

## Citations

- Butlin et al. (2023). *Consciousness in Artificial Intelligence: Insights from the Science of Consciousness*.
  https://arxiv.org/abs/2308.08708v2
- Old (1999). *An old-style paper*. arXiv:hep-th/9901001
- LeCun et al. (2015). *Deep learning*. https://doi.org/10.1038/nature14539.
- Wakefield (1998). *Ileal-lymphoid-nodular hyperplasia*. (doi:10.1016/S0140-6736(97)11096-0)
- Nobody (2020). *Invented*. arXiv:2001.99999
- Arxiv DOI form: 10.48550/arXiv.2308.08708
"""


def fake_fetch(url):
    if "export.arxiv.org" in url:
        return ARXIV_REPLY
    doi = url.split("/works/", 1)[1].replace("%28", "(").replace("%29", ")")
    if doi not in CROSSREF:
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)
    return json.dumps({"message": CROSSREF[doi]}).encode()


class TestCitations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "corpus").mkdir()
        (d / "corpus" / "butlin-2023.md").write_text(
            "We argue that indicator properties de-\nrived from scientific theories of consciousness can be "
            "assessed in AI sys­tems.\n")
        self.draft = d / "draft.md"
        self.draft.write_text(DRAFT)
        self._corpus, self._fetch, self._sleep = cc.CORPUS, cc.fetch, cc.time.sleep
        cc.CORPUS, cc.fetch, cc.time.sleep = d / "corpus", fake_fetch, lambda s: None

    def tearDown(self):
        cc.CORPUS, cc.fetch, cc.time.sleep = self._corpus, self._fetch, self._sleep
        self.tmp.cleanup()

    def verdicts(self, r):
        return {x["id"]: x["verdict"] for x in r["ids"]}

    def test_identifiers(self):
        r = cc.check(self.draft)
        self.assertEqual(self.verdicts(r), {
            "arxiv:2308.08708": "matches catalogue",
            "arxiv:hep-th/9901001": "retraction or withdrawal",
            "arxiv:2001.99999": "not found in catalogue",
            "doi:10.1038/nature14539": "matches catalogue",
            "doi:10.1016/S0140-6736(97)11096-0": "retraction or withdrawal",
        })
        self.assertFalse(r["network_failed"])

    def test_quotes(self):
        q = {x["verdict"]: x for x in cc.check(self.draft, offline=True)["quotes"]}
        self.assertEqual(q["found in corpus"]["file"], "butlin-2023.md",
                         "hyphenation, soft hyphens and line breaks don't hide a verbatim quote")
        self.assertIn("not checked", q, "a quote with no corpus source isn't reported as wrong")

    def test_misattributed_quote_is_not_verbatim(self):
        self.draft.write_text('Butlin wrote "indicator properties can be assessed in any AI system whatsoever" '
                              "(butlin-2023).\n")
        self.assertEqual(cc.check(self.draft, offline=True)["quotes"][0]["verdict"], "not verbatim")

    def test_mismatch_is_check_not_wrong(self):
        self.draft.write_text("- Smith (2019). *Deep learning*. doi:10.1038/nature14539\n")
        x = cc.check(self.draft)["ids"][0]
        self.assertEqual(x["verdict"], "check")
        self.assertIn("LeCun", x["note"])
        self.assertIn("2019", x["note"])

    def test_network_failure_is_loud(self):
        def down(url):
            raise urllib.error.URLError("no network")
        cc.fetch = down
        r = cc.check(self.draft)
        self.assertTrue(r["network_failed"])
        self.assertEqual(set(self.verdicts(r).values()), {"couldn't check"})
        self.assertIn("incomplete", cc.markdown(r))

    def test_header_says_what_it_doesnt_check(self):
        self.assertIn("does not check that a source supports the claim", cc.markdown(cc.check(self.draft)))


if __name__ == "__main__":
    unittest.main()
