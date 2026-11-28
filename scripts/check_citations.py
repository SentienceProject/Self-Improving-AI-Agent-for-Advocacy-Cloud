#!/usr/bin/env python3
"""Check a draft's citations mechanically: do its links work, do its arXiv IDs and DOIs exist and match what the
draft says, and are its quotes word for word in the sources it cites? No AI model is involved.

    python3 <workspace>/scripts/check_citations.py <draft>          check everything, print a Markdown table
        --save workspace/citecheck/<run-id>    also save each linked page as text there, for the verifier
        --sources <folder>                     also check quotes against texts saved there (workspace/corpus/ always)
        --no-links                             don't open the draft's links
        --offline                              no network at all (quotes against the corpus and --sources only)
        --json                                 the same, as JSON
    python3 <workspace>/scripts/check_citations.py --fetch <url> [<url> ...] --save workspace/citecheck/<run-id>

In this workspace: the draft and every --sources folder must be inside workspace/, and --save inside
workspace/citecheck/ (symlinks are followed before checking; anything else is refused with exit code 2).
In hardened mode the script runs outside the Bash sandbox (layers/hardened.json excludedCommands, in the exact
form preflight.py allows), because drafts cite sites the sandbox doesn't list. Those guards are why that's safe:
it opens only http(s) links found in the draft, reads only inside workspace/, and writes only to the --save folder.

The draft can be Markdown or plain text (.md, .txt) or a Word file (.docx). On Windows, type `python` or `py`
instead of `python3`. It needs only Python 3.9+; for PDFs it uses `pdftotext` or the `pypdf` package if installed.

What it does:
  1. Links: opens every http(s) link in the draft and reports broken ones (404/410) and sites that refuse automated
     checks. With --save, each page is saved as plain text, so a person or the verifier can read the page itself
     (a web-fetch tool returns an AI summary of a page, which can misreport what it says).
  2. Identifiers: finds arXiv IDs written with a prefix (arXiv:2401.12345, arxiv.org/abs/... links) and DOIs
     (10.xxxx/..., doi.org/ links), looks each up in arXiv's and Crossref's public catalogues, and compares the
     title, first author and year with the line that cites it. Flags retractions and withdrawals where the
     catalogue records them (best-effort: Crossref's records are incomplete).
  3. Quotes: checks every quoted passage of 5+ words ("double" or 'single' quotes) against the source it cites
     (links in its sentence or paragraph, or the reference it points to with [1], [^1] or Author + year), or the
     --sources folder if it cites nothing. Ellipses and [bracketed insertions] are allowed. When a quote isn't word
     for word in its source, it shows the closest passage; when the words are in a different source from the one
     cited, it says so (a misattributed quote).

It never checks whether a source supports the claim it's cited for: that's the verifier's job, and a clean table
doesn't mean a draft is right. It sends the identifiers to arXiv and Crossref and opens the draft's links, so for an
unpublished draft those sites can see what you're reading. Set CROSSREF_MAILTO to your address to use Crossref's
"polite" pool (optional).

Exit code: 0 everything was checked and nothing flagged, 1 something flagged to look at, 2 nothing flagged but
something wasn't checked ("couldn't check", "refused" or "not checked" rows: offline, a lookup failed, a source
without text). A site whose name no longer resolves is a "broken link" if anything else got through, so a dead site
isn't mistaken for being offline.

The same checker ships as the public fact-check skill (fact-check/skills/fact-check/scripts/check_citations.py in the
repository); this copy adds the workspace defaults and path guards in the "workspace" section below.
"""
import argparse
import concurrent.futures
import difflib
import html.parser
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

UA = "Mozilla/5.0 (compatible; welfare-workspace-citation-check/2.0)"
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
MAX_BYTES = 30_000_000
MAX_LINKS = 80
MIN_WORDS = 150   # a page with less text than this is probably a paywall, login or JavaScript shell
# Asked only when no link or lookup in a run got through, to tell a dead site from being offline. Crossref is
# already contacted for DOIs, and this sends nothing from the draft.
PROBE_URL = "https://api.crossref.org/works?rows=0"

NEW_ARXIV = r"\d{4}\.\d{4,5}(?:v\d+)?"
OLD_ARXIV = r"[a-z][a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
ARXIV_RE = re.compile(rf"(?:arXiv[:\s]*|arxiv\.org/(?:abs|pdf|html)/|10\.48550/arXiv\.)({NEW_ARXIV}|{OLD_ARXIV})",
                      re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]\[]+)", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"'\]\[`|]+", re.I)
MIN_QUOTE_WORDS = 5   # shorter quoted phrases are mostly titles, names and scare quotes: counted, not checked
ITEM_START = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[\^?\d+\]:?)\s")
REF_DEF = re.compile(r"^\s*(?:\[\^?(\d+)\]:?|(\d+)[.)])\s")
REF_USE = re.compile(r"\[\^?(\d+)\]")
# A reference list's heading is just that ("Sources", "Notes and references"), not any heading with the word in it:
# a quote under "Notes from the council meeting" is still checked.
_REF_WORD = r"(?:citations?|references?|sources?|bibliography|works cited|notes|footnotes|endnotes|further reading)"
REF_HEADING = re.compile(rf"(?:\d+[.)]?\s*)?(?:list of |selected |key |main )?{_REF_WORD}"
                         rf"(?:\s*(?:and|&|,)\s*{_REF_WORD})*\s*:?", re.I)
# Not sentence ends: "et al.", "e.g.", "i.e.", "vs.", "Dr." and initials ("Smith, J. (2020)").
SENTENCE_END = re.compile(r"(?<!\bal)(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\bDr)(?<!\b[A-Z])[.!?](?=\s|$)")

VERDICT_HELP = {
    "matches catalogue": "",
    "check": "Compare the citation with the catalogue entry; often a formatting difference, sometimes a wrong paper.",
    "not found in catalogue": "Could be a typo in the ID rather than an invented source: look the paper up by title.",
    "retraction or withdrawal": "Read the notice before relying on this paper.",
    "couldn't check": "The site or catalogue didn't answer (down for now, or slow). Re-run later or open it "
                       "yourself; nothing was checked for this one.",
    "link works": "",
    "broken link": "The page is gone. Find where it moved (search its title, or try web.archive.org) or cite "
                   "something else.",
    "refused": "The site blocks automated checks. Open the link yourself: this says nothing about whether it works.",
    "found in source": "",
    "not verbatim": "The quote isn't word for word in the source it's attributed to: fix it from the source (the "
                    "note shows the closest passage found). A short one may be a title or a name: then ignore it.",
    "found in another source": "The words are in a different source from the one cited for them: check who said "
                               "them, and fix the attribution or the citation.",
    "not checked": "Nothing was checked for this one (offline, skipped, or its source gave no text): check it by "
                   "hand or with the verifier.",
}
FLAGGED = {"check", "not found in catalogue", "retraction or withdrawal", "broken link", "not verbatim",
           "found in another source"}
UNCHECKED = {"couldn't check", "refused", "not checked"}


# ---------------------------------------------------------------- text helpers
def norm(s, lower=True):
    """For comparing quotes: unify quotes, dashes, ligatures and whitespace; drop soft hyphens and line-break
    hyphenation; lower-case (unless lower=False, for showing a passage)."""
    s = unicodedata.normalize("NFKC", s).replace("­", "")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower() if lower else s


def words(s):
    return re.findall(r"[a-z0-9]+", norm(s))


def title_ratio(a, b):
    return difflib.SequenceMatcher(None, " ".join(words(a)), " ".join(words(b))).ratio()


def claimed_title(line):
    m = re.search(r"\*([^*]{8,})\*", line) or re.search(r"[\"“]([^\"“”]{8,})[\"”]", line)
    return m.group(1).strip() if m else None


YEAR = r"\b(19\d\d|20\d\d)\b"


def claimed_years(line):
    return {int(y) for y in re.findall(YEAR, line)}


def item_year(item):
    """The year a reference entry gives for the work: the first one in parentheses ("(2015)", "(May 2015)"), else
    the first outside its title, links and identifiers (so "Retrieved 2024" or a year in the title doesn't count)."""
    t = item
    title = claimed_title(item)
    if title:
        t = t.replace(title, " ")
    t = DOI_RE.sub(" ", URL_RE.sub(" ", t))
    m = re.search(r"\([^()]*?" + YEAR + r"[a-z]?[^()]*\)", t) or re.search(YEAR, t)
    return {int(m.group(1))} if m else set()


def citing_line(text, start):
    """(citation around position `start`, whether it's a list item or reference entry). An item is returned whole,
    with its wrapped lines; in a prose paragraph, just the identifier's sentence (the rest of the paragraph can quote
    or name other things, which aren't the paper's title)."""
    lines = text.splitlines()
    i = text.count("\n", 0, start)
    s = i
    while s > 0 and not ITEM_START.match(lines[s]) and lines[s - 1].strip() and not lines[s].startswith("#"):
        s -= 1
    e = i
    while e + 1 < len(lines) and lines[e + 1].strip() and not ITEM_START.match(lines[e + 1]) \
            and not lines[e + 1].startswith("#"):
        e += 1
    block = " ".join(l.strip() for l in lines[s:e + 1])
    if ITEM_START.match(lines[s]) or any(it[2] <= i <= it[3] for it in reference_items(text)):
        return block, True
    return sentence_at(text, start), False


def paragraph_at(text, start, end=None):
    """(start, end) of the paragraph holding text[start:end]."""
    p1 = text.find("\n\n", end if end is not None else start)
    return text.rfind("\n\n", 0, start) + 1, (p1 if p1 != -1 else len(text))


def sentence_at(text, start, end=None):
    """The sentence holding text[start:end], within its paragraph."""
    end = start if end is None else end
    p0, p1 = paragraph_at(text, start, end)
    before = [x.end() for x in SENTENCE_END.finditer(text, p0, start)]
    after = SENTENCE_END.search(text, end, p1)
    return text[(before[-1] if before else p0):(after.end() if after else p1)]


def clean_doi(d):
    d = d.rstrip(".,;:")
    while d.endswith(")") and d.count(")") > d.count("("):
        d = d[:-1]
    return d


def clean_url(u):
    u = u.rstrip(".,;:!?*_'")
    while u.endswith(")") and u.count(")") > u.count("("):
        u = u[:-1]
    return u.rstrip(".,;:!?*_'")


# ---------------------------------------------------------------- reading files
DETAILS_MARK = "[Page details]"            # html_text's first line: the page's dates, from its metadata
LINKS_MARK = "\n\n[Links in the page text]\n"   # and after the text: where its links point


class HTMLText(html.parser.HTMLParser):
    """A page's readable text. Site-wide parts (navigation, the site's header and footer, sidebars, forms) are left
    out, but an article's own header is kept: that's where its title, byline and date usually are. It also collects
    the page's dates from its metadata (meta tags, JSON-LD, <time>) and the links in the text it keeps."""
    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "button", "aside", "template"}
    CONTENT = {"article", "main"}
    BLOCK = {"p", "div", "section", "article", "main", "li", "tr", "br", "blockquote", "figcaption", "table",
             "pre", "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd"}
    PUBLISHED, MODIFIED = "Published", "Last modified"
    DATE_KEYS = {"article:published_time": PUBLISHED, "datepublished": PUBLISHED, "citation_publication_date":
                 PUBLISHED, "citation_date": PUBLISHED, "citation_online_date": PUBLISHED, "dc.date": PUBLISHED,
                 "dc.date.issued": PUBLISHED, "dcterms.issued": PUBLISHED, "dcterms.date": PUBLISHED,
                 "date": PUBLISHED, "pubdate": PUBLISHED, "publish-date": PUBLISHED, "og:published_time": PUBLISHED,
                 "article:modified_time": MODIFIED, "datemodified": MODIFIED, "dcterms.modified": MODIFIED,
                 "last-modified": MODIFIED}
    MAX_LINKS = 200

    def __init__(self, base=""):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.skipping, self.content, self.base = [], 0, [], 0, base
        self.dates, self.links, self.link, self.ld, self.time = {}, {}, None, None, None

    def date(self, key, value):
        label = self.DATE_KEYS.get((key or "").lower())
        value = (value or "").strip()
        if label and re.match(r"\d{4}", value):
            self.dates.setdefault(label, value[:10] if re.match(r"\d{4}-\d\d-\d\dT", value) else value[:40])

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "meta":
            self.date(a.get("property") or a.get("name") or a.get("itemprop"), a.get("content"))
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.ld = []
        if tag in self.SKIP:
            counts = not (tag == "header" and self.content and not self.skip)   # an article's own header stays
            self.skipping.append(counts)
            self.skip += counts
            return
        if self.skip:
            return
        if tag in self.CONTENT:
            self.content += 1
        if tag in self.BLOCK:
            self.out.append("\n\n")
        if tag == "time" or a.get("itemprop", "").lower() in ("datepublished", "datemodified"):
            when = a.get("datetime") or a.get("content")
            self.date(a.get("itemprop") or "datepublished", when)
            if tag == "time" and when:
                self.time = when
        if tag == "a" and self.link is None and len(self.links) < self.MAX_LINKS:
            href = a.get("href", "").strip()
            url = (urllib.parse.urljoin(self.base, href) if self.base else href).split("#")[0]
            if (urllib.parse.urlsplit(url).scheme in ("http", "https") and not href.startswith("#")
                    and url != self.base.split("#")[0]):   # not a jump within this page
                self.link = (url, [])

    def handle_endtag(self, tag):
        if tag == "script" and self.ld is not None:
            self.read_ld("".join(self.ld))
            self.ld = None
        if tag in self.SKIP:
            if self.skipping:
                self.skip -= self.skipping.pop()
            return
        if self.skip:
            return
        if tag in self.CONTENT and self.content:
            self.content -= 1
        if tag == "time" and self.time:
            if self.time not in "".join(self.out[-3:]):
                self.out.append(f" ({self.time})")
            self.time = None
        if tag == "a" and self.link is not None:
            url, words = self.link
            self.links.setdefault(url, " ".join(" ".join(words).split()))
            self.link = None
        if tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self.ld is not None:
            self.ld.append(data)
        if not self.skip:
            self.out.append(data)
            if self.link is not None:
                self.link[1].append(data)

    def read_ld(self, raw):
        try:
            todo = [json.loads(raw)]
        except ValueError:
            return
        while todo:
            x = todo.pop(0)
            if isinstance(x, dict):
                for k, v in x.items():
                    if isinstance(v, str):
                        self.date(k, v)
                    else:
                        todo.append(v)
            elif isinstance(x, list):
                todo.extend(x)

    def text(self):
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.out))
        t = "\n".join(line.strip() for line in t.splitlines())
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        dates = [f"{k}: {self.dates[k]}" for k in (self.PUBLISHED, self.MODIFIED) if k in self.dates]
        if dates:
            t = f"{DETAILS_MARK} {' · '.join(dates)} (from the page's metadata)\n\n{t}"
        if self.links:
            t += LINKS_MARK + "\n".join(f"- {words or '(no link text)'}: {url}" for url, words in self.links.items())
        return t


def html_text(s, base=""):
    """A web page's text: its dates first, if its metadata has them, then the text, then where its links point
    (relative links only with `base`, the page's address). Links are listed after the text, not inline, so a quote
    that runs across a link still matches word for word."""
    p = HTMLText(base)
    p.feed(s)
    return p.text()


def body_words(text):
    """Words in a page's own text, without the dates and links html_text adds around it (for the paywall test)."""
    t = text.split(LINKS_MARK, 1)[0]
    if t.startswith(DETAILS_MARK):
        t = t.split("\n", 1)[1] if "\n" in t else ""
    return len(t.split())


def pdf_text(data):
    """Text of a PDF, or None if there's no converter (pdftotext or pypdf)."""
    with tempfile.TemporaryDirectory() as d:
        pdf = Path(d) / "in.pdf"
        pdf.write_bytes(data)
        if shutil.which("pdftotext"):
            # reading order (-layout would put two-column papers' columns side by side, jumbling quotes)
            r = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        try:
            import pypdf
            return "\n\n".join((p.extract_text() or "") for p in pypdf.PdfReader(str(pdf)).pages)
        except ImportError:
            return None
        except Exception:   # a damaged or encrypted PDF
            return None


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _docx_part(z, part):
    """Paragraph texts of one part of a .docx, with link targets in brackets and footnote marks as [^n]."""
    try:
        root = ET.fromstring(z.read(part))
    except KeyError:
        return []
    rels_part = part.replace("word/", "word/_rels/") + ".rels"
    try:
        links = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read(rels_part))}
    except KeyError:
        links = {}

    def walk(el, bits):
        for ch in el:
            if ch.tag == W + "t":
                bits.append(ch.text or "")
            elif ch.tag == W + "tab":
                bits.append(" ")
            elif ch.tag in (W + "footnoteReference", W + "endnoteReference"):
                bits.append(f"[^{ch.get(W + 'id')}]")
            elif ch.tag == W + "hyperlink":   # "link text (https://...)"
                sub = []
                walk(ch, sub)
                text, target = "".join(sub), links.get(ch.get(R + "id")) or ""
                bits.append(text + (f" ({target})" if target.startswith("http") and target not in text else ""))
            else:
                walk(ch, bits)

    def para_text(p):
        bits = []
        walk(p, bits)
        return "".join(bits)

    if part.endswith(("footnotes.xml", "endnotes.xml")):
        out = []
        for note in root:
            nid = note.get(W + "id")
            if nid is None or int(nid) <= 0:
                continue   # separators
            text = " ".join(para_text(p) for p in note.iter(W + "p")).strip()
            out.append(f"[^{nid}]: {text}")
        return out
    return [para_text(p) for p in root.iter(W + "p")]


def read_draft(path):
    p = Path(path)
    if p.suffix.lower() == ".docx":
        with zipfile.ZipFile(p) as z:
            paras = _docx_part(z, "word/document.xml")
            notes = _docx_part(z, "word/footnotes.xml") + _docx_part(z, "word/endnotes.xml")
        return "\n\n".join(paras + (["## Notes"] + notes if notes else []))
    return p.read_text(encoding="utf-8", errors="replace")


def read_source_file(p):
    """Text of a saved source: .md, .txt, .html or .pdf; None for anything else."""
    suf = p.suffix.lower()
    if suf in (".md", ".txt"):
        return p.read_text(encoding="utf-8", errors="replace")
    if suf in (".html", ".htm"):
        return html_text(p.read_text(encoding="utf-8", errors="replace"))
    if suf == ".pdf":
        return pdf_text(p.read_bytes())
    return None


# ---------------------------------------------------------------- extraction
def find_ids(text, where=True):
    """[(kind, id, where)], each id once. where: {line, item, para, near} — the citing item or sentence, whether
    it's a list item or reference entry, and (in prose) the paragraph and the parentheses around the id."""
    seen, out = set(), []

    def locate(m, end):
        line, item = citing_line(text, m.start())
        p0, p1 = paragraph_at(text, m.start())
        o, c = text.rfind("(", p0, m.start()), text.find(")", end, p1)
        near = text[o:c + 1] if o != -1 and c != -1 and ")" not in text[o:m.start()] else ""
        return {"line": line, "item": item, "para": text[p0:p1], "near": near}

    for m in ARXIV_RE.finditer(text):
        aid = re.sub(r"v\d+$", "", m.group(1))
        if ("arxiv", aid) not in seen:
            seen.add(("arxiv", aid))
            out.append(("arxiv", aid, locate(m, m.end()) if where else None))
    for m in DOI_RE.finditer(text):
        doi = clean_doi(m.group(1))
        if doi.lower().startswith("10.48550/arxiv."):
            continue  # an arXiv DOI: looked up on arXiv above
        if ("doi", doi.lower()) not in seen:
            seen.add(("doi", doi.lower()))
            # the pattern can take in a closing ")" that clean_doi drops: the parentheses end after the DOI itself
            out.append(("doi", doi, locate(m, m.start(1) + len(doi)) if where else None))
    return out


def cited_urls(text):
    """The links in `text`, plus "arxiv:<id>" and "doi:<doi>" for the paper identifiers in it."""
    return set(find_urls(text)) | {f"{kind}:{ident.lower()}" for kind, ident, _ in find_ids(text, where=False)}


def find_urls(text):
    seen, out = set(), []
    for m in URL_RE.finditer(text):
        u = clean_url(m.group(0))
        if u not in seen and urllib.parse.urlsplit(u).netloc:
            seen.add(u)
            out.append(u)
    return out


def reference_items(text):
    """[(number or None, item text, first line, last line)]: footnote definitions ([^1]: ...), numbered references
    ([1] ... or 1. ...), and each entry under a Citations / References / Sources / Notes heading."""
    lines, items, in_refs, i = text.splitlines(), [], False, 0
    while i < len(lines):
        line = lines[i]
        h = re.match(r"^\s*#{1,6}\s+(.*)", line) or re.match(r"^\s*\*\*([^*]{1,40})\*\*\s*:?\s*$", line) \
            or re.match(r"^\s*([A-Za-z ]{3,25}?)\s*:?\s*$", line)
        heading = h and re.sub(r"[*_#]+", "", h.group(1)).strip()
        if h and (line.lstrip().startswith(("#", "**")) or REF_HEADING.fullmatch(heading)):
            in_refs = bool(REF_HEADING.fullmatch(heading))
            i += 1
            continue
        d = REF_DEF.match(line)
        if d and not in_refs and d.group(1) is None:
            d = None   # "1." in the body is an ordinary list; only [1] and [^1]: define references there
        if line.strip() and (d or in_refs):
            j = i
            while j + 1 < len(lines) and lines[j + 1].strip() and not ITEM_START.match(lines[j + 1]) \
                    and not lines[j + 1].lstrip().startswith("#"):
                j += 1
            items.append(((d.group(1) or d.group(2)) if d else None,
                          " ".join(l.strip() for l in lines[i:j + 1]), i, j))
            i = j + 1
            continue
        i += 1
    return items


def item_key(body):
    """(first author's surname, year) of a reference entry, as best can be told."""
    b = re.sub(r"^\s*(?:[-*+]|\d+[.)]|\[\^?\d+\]:?)\s*", "", body)
    m = re.match(r"[*_\"“\s]*([^\W\d_][\w'’\-]+)", b)
    y = re.search(r"\b(19\d\d|20\d\d)\b", b)
    return (m.group(1) if m else None), (y.group(1) if y else None)


def cites(para, item):
    num, body = item[0], item[1]
    if num and num in REF_USE.findall(para):
        return True
    surname, year = item_key(body)
    return bool(surname and year and len(surname) > 2 and year in para
                and re.search(rf"\b{re.escape(surname)}\b", para))


# ---------------------------------------------------------------- lookups
def fetch(url):
    """Bytes from a catalogue API. Raises on failure."""
    headers = {"User-Agent": UA + (f" (mailto:{os.environ['CROSSREF_MAILTO']})"
                                   if os.environ.get("CROSSREF_MAILTO") else "")}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return r.read()


def fetch_page(url):
    """(content type, charset, bytes, final url) of a linked page. Raises HTTPError, URLError or OSError."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/pdf;q=0.9,text/plain;q=0.8,*/*;q=0.5"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.headers.get_content_type(), r.headers.get_content_charset(), r.read(MAX_BYTES), r.geturl()


def lookup_arxiv(ids):
    """{id: {title, authors, year, withdrawn} or None if not found}. Raises on network failure."""
    out = {}
    for i in range(0, len(ids), 50):
        if i:
            time.sleep(3)   # arXiv asks for 3 s between requests
        batch = ids[i:i + 50]
        url = ("https://export.arxiv.org/api/query?max_results=100&id_list="
               + urllib.parse.quote(",".join(batch), safe=",/"))
        root = ET.fromstring(fetch(url))
        found = {}
        for e in root.findall("a:entry", ATOM):
            eid = (e.findtext("a:id", "", ATOM) or "").rsplit("/abs/", 1)[-1]
            eid = re.sub(r"v\d+$", "", eid)
            title = re.sub(r"\s+", " ", e.findtext("a:title", "", ATOM) or "").strip()
            if not eid or title.lower() == "error":
                continue
            comment = e.findtext("arxiv:comment", "", ATOM) or ""
            year = int((e.findtext("a:published", "", ATOM) or "0000")[:4] or 0)
            updated = int((e.findtext("a:updated", "", ATOM) or "0000")[:4] or 0)
            found[eid] = {"title": title,
                          "authors": [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)],
                          "year": year, "years": {y for y in (year, updated) if y},   # first and latest version
                          "withdrawn": bool(re.search(r"withdraw", comment + title, re.I)),
                          "abstract": e.findtext("a:summary", "", ATOM) or ""}
        if not found and len(batch) > 1:
            # one malformed ID can make arXiv answer the whole batch with an error: look them up one by one
            for aid in batch:
                time.sleep(3)
                out.update(lookup_arxiv([aid]))
            continue
        for aid in batch:
            out[aid] = found.get(aid)
    return out


def lookup_doi(doi):
    """{title, authors, year, retracted} or None if Crossref doesn't know it. Raises on network failure."""
    try:
        msg = json.loads(fetch("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")))["message"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    years = {}   # online-first papers have a print year too: a citation may give either
    for k in ("issued", "published", "published-print", "published-online", "created"):
        parts = (msg.get(k) or {}).get("date-parts") or [[None]]
        if parts[0] and parts[0][0]:
            years[k] = int(parts[0][0])
    year = next(iter(years.values()), None)
    if len(years) > 1:
        years.pop("created", None)   # the date the record was deposited, only if there's nothing better
    updates = (msg.get("update-to") or []) + (msg.get("updated-by") or [])
    retracted = any(str(u.get("type", "")).lower() in ("retraction", "withdrawal", "removal") for u in updates)
    retracted |= any(k.startswith("is-retracted") for k in (msg.get("relation") or {}))
    retracted |= str(msg.get("subtype", "")).lower() == "retraction"
    return {"title": " ".join(msg.get("title") or []),
            "authors": [a.get("family") or a.get("name", "") for a in msg.get("author") or []],
            "year": year, "years": set(years.values()), "retracted": retracted,
            "abstract": re.sub(r"<[^>]+>", " ", msg.get("abstract") or "")}   # JATS markup


def judge(rec, where, kind):
    """(verdict, note) for one identifier's catalogue record against where the draft cites it (find_ids)."""
    if rec is None:
        return "not found in catalogue", ""
    if rec.get("withdrawn") or rec.get("retracted"):
        return "retraction or withdrawal", "the catalogue records a retraction or withdrawal"
    if isinstance(where, str):
        where = {"line": where, "item": True, "para": where, "near": ""}
    line, notes = where["line"], []
    first = (rec.get("authors") or [""])[0]
    surname = first.split()[-1] if " " in first and kind == "arxiv" else first
    if where["item"]:   # a reference entry: its title, author and year are the paper's
        t, years, named_in = claimed_title(line), item_year(line), line
    else:
        # prose: a quote or italics nearby is rarely the paper's title, and other years are about other things.
        # Compare only a year in the id's own parentheses or right after the author's name ("LeCun et al. (2015)").
        t, named_in = None, where["para"]
        years = claimed_years(where["near"])
        if surname and words(surname):
            for m in re.finditer(rf"\b{re.escape(surname)}\b", where["para"], re.I):
                years |= {int(y) for y in re.findall(YEAR, where["para"][m.end():m.end() + 40])[:1]}
    if t and title_ratio(t, rec["title"]) < 0.8:
        notes.append(f"title differs (draft: \"{t[:80]}\")")
    # a bare ID names no author to compare: in prose, only "(LeCun 2015, doi:...)"-style parentheses do
    full = bool(t or claimed_years(line)) if where["item"] else bool(claimed_years(where["near"]))
    if full and surname and words(surname) and not set(words(surname)) & set(words(named_in)):
        notes.append(f"first author {surname} isn't named in the citation")
    known = rec.get("years") or ({rec["year"]} if rec.get("year") else set())
    if years and known and not years & known:
        notes.append(f"year {', '.join(map(str, sorted(years)))} vs catalogue {', '.join(map(str, sorted(known)))}")
    return ("check" if notes else "matches catalogue"), "; ".join(notes)


# ---------------------------------------------------------------- links
def page_text(ctype, charset, data, base=""):
    """(text or None, what it was). base: the page's address, for its relative links."""
    if data[:5] == b"%PDF-" or ctype == "application/pdf":
        t = pdf_text(data)
        return t, ("PDF" if t is not None else "PDF (no PDF converter installed: pdftotext or pypdf)")
    if ctype in ("text/html", "application/xhtml+xml") or data.lstrip()[:1] == b"<":
        return html_text(data.decode(charset or "utf-8", errors="replace"), base), "web page"
    if ctype.startswith("text/"):
        return data.decode(charset or "utf-8", errors="replace"), "text"
    return None, ctype


def check_link(url):
    """{url, verdict, note, text, final_url, network}."""
    row = {"url": url, "verdict": "link works", "note": "", "text": None, "final_url": url, "network": False,
           "reached": True, "no_such_site": False}
    try:
        ctype, charset, data, final = fetch_page(url)
    except urllib.error.HTTPError as e:   # the site answered, even if with an error
        e.close()
        if e.code in (404, 410):
            row.update(verdict="broken link", note=f"HTTP {e.code}")
        elif e.code in (401, 402, 403, 406, 429, 451, 999):
            row.update(verdict="refused", note=f"HTTP {e.code}")
        else:
            row.update(verdict="couldn't check", note=f"HTTP {e.code}")
        return row
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        dns = (isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_NONAME) or any(
            s in str(reason) for s in ("Name or service not known", "getaddrinfo failed", "nodename nor servname"))
        note = ("the site's name doesn't resolve (a dead site, or you're offline)" if dns else str(reason)[:80])
        row.update(verdict="couldn't check", note=note, network=True, reached=False, no_such_site=dns)
        return row
    text, kind = page_text(ctype, charset, data, final)
    row["final_url"] = final
    if text is None:
        row["note"] = f"{kind}: no text to check quotes against"
    else:
        n = body_words(text)
        row["text"] = text
        row["note"] = kind + (f"; only {n} words of text (a paywall, a login, or a page that needs JavaScript)"
                              if n < MIN_WORDS else "")
    return row


def slug(url):
    p = urllib.parse.urlsplit(url)
    s = re.sub(r"[^A-Za-z0-9]+", "-", (p.netloc.removeprefix("www.") + p.path)).strip("-")
    return s[:60].rstrip("-") or "page"


def save_pages(rows, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    first = 1 + max((int(m.group(1)) for f in folder.iterdir() if (m := re.match(r"(\d+)-", f.name))), default=0)
    for n, row in enumerate(rows, first):
        if not row.get("text"):
            continue
        out = folder / f"{n:02d}-{slug(row['url'])}.txt"
        out.write_text(f"Source: {row['final_url']}\nRetrieved: {stamp} (converted to text automatically; tables, "
                       "figures and footnotes may be garbled)\n\n---\n\n" + row["text"] + "\n", encoding="utf-8")
        row["saved"] = str(out)


def network_up():
    """True if a well-known site answers (PROBE_URL), so a link that couldn't connect isn't blamed on being offline."""
    try:
        fetch(PROBE_URL)
    except urllib.error.HTTPError as e:
        e.close()
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return True


def settle_unreachable(rows):
    """With the network known to work: a site whose name doesn't resolve is gone (a broken link); one that didn't
    answer stays "couldn't check" on its own row, without marking the whole table incomplete."""
    for r in rows:
        if r.get("network"):
            r["network"] = False
            if r.get("no_such_site"):
                r.update(verdict="broken link", note="the site's name no longer resolves: the site seems to be gone")


def check_links(urls):
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(check_link, urls[:MAX_LINKS]))


# ---------------------------------------------------------------- quotes
class Source:
    """Text to check quotes against. urls: where it's from (pages' addresses, "arxiv:<id>" or "doi:<doi>"); partial:
    only part of the work (an abstract), so a quote missing from it isn't a misquote."""
    def __init__(self, label, text, urls=(), fetched=False, partial=False):
        self.label, self.urls, self.partial = label, set(urls), partial
        self.norm = norm(text or "")
        # a fetched page with little text is probably a paywall or login; a file someone saved is taken as is
        self.readable = (body_words(text or "") >= MIN_WORDS) if fetched else len(self.norm.split()) >= 1
        shown = norm(text or "", lower=False).split()   # the same words as self.norm, in their own case
        keys = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in shown]
        if len(shown) != len(self.norm.split()):   # lower-casing split a word (rare): show lower case
            shown = self.norm.split()
            keys = [re.sub(r"[^a-z0-9]", "", t) for t in shown]
        self.raw = [t for t, k in zip(shown, keys) if k]
        self.keys = [k for k in keys if k]

    def has(self, pieces):
        """True if every piece of a quote is in this source, in order."""
        pos = 0
        for p in pieces:
            pos = self.norm.find(p, pos)
            if pos == -1:
                return False
            pos += len(p)
        return bool(self.norm)


QUOTE_MARKS = "\"'“”‘’"
OPEN_AFTER = "([{—–-/:;*_"          # a straight quote mark after these (or a space) can open a quote
CLOSE_BEFORE = ".,;:!?)]}…—–-/[*_¹²³⁴⁵⁶⁷⁸⁹⁰"   # and before these (or a space) can close one: "…"[1], "…"¹, *"…"*


def mark_roles(text, i):
    """(can open, can close) for the quote mark at text[i]. Curly marks say which they are, except that ’ is also
    an apostrophe (can’t). A straight mark is judged by its neighbours: 12" (inches) and don't can't open a quote."""
    ch = text[i]
    prev = text[i - 1] if i else " "
    nxt = text[i + 1] if i + 1 < len(text) else " "
    if ch in "“‘":
        return True, False
    if ch == "”":
        return False, True
    if ch == "’":
        return False, not nxt.isalnum() and not prev.isspace()
    if ch == "'" and prev.isalnum() and nxt.isalnum():
        return False, False   # an apostrophe
    can_open = (prev.isspace() or prev in OPEN_AFTER) and not nxt.isspace() and \
        (ch == '"' or nxt.isalpha() or nxt in "\"“")   # '90s is an elision, not a quote
    if ch == "'" and re.match(r"(?:tis|twas|em|cause|til|n|bout|round)\b", text[i + 1:i + 7], re.I):
        can_open = False   # 'Tis, 'em, rock 'n' roll
    can_close = not prev.isspace() and (nxt.isspace() or nxt in CLOSE_BEFORE)
    if can_close and ch == "'" and prev in "sS" and re.match(r" [^\W\d_]", text[i + 1:i + 3]):
        can_close = "maybe"   # "the animals' today" ends a quote; "the ministers' office" is a possessive
    return can_open, can_close


def pair_marks(text, marks):
    """[(start, end, inner text)] of passages between the given quote marks, paired left to right, never across a
    blank line. A mark that can't open is skipped while no quote is open (an inch mark, a stray apostrophe), and an
    opening mark while one is open means the earlier one was stray. A mark that may be a possessive closes a quote
    only if no surer closing mark follows in the paragraph."""
    toks = [None if m.group().startswith("\n") else (m.start(), *mark_roles(text, m.start()))
            for m in re.finditer(rf"\n[ \t]*\n|[{marks}]", text)]
    out, opened = [], None
    for k, tok in enumerate(toks):
        if tok is None:
            opened = None
            continue
        pos, can_open, can_close = tok
        if opened is not None and can_close == "maybe":
            later = []
            for t in toks[k + 1:]:
                if t is None or t[1]:   # the paragraph ends, or another quote opens
                    break
                later.append(t[2] is True)
            can_close = not any(later)
        if opened is not None and can_close:
            out.append((opened, pos + 1, text[opened + 1:pos]))
            opened = None
        elif can_open:
            opened = pos
    return out


def find_quotes(text):
    """[(start, end, quoted text)] for "double" and 'single' quotes. A single-quoted passage inside a double-quoted
    one is part of it; one that holds double quotes (British style: ‘he said “no”’) replaces them."""
    keep = pair_marks(text, "\"“”")
    for s0, s1, q in pair_marks(text, "'‘’"):
        if any(d0 <= s0 and s1 <= d1 for d0, d1, _ in keep):
            continue
        inside = [d for d in keep if s0 <= d[0] and d[1] <= s1]
        others = [d for d in keep if d not in inside]
        if any(d[0] < s1 and s0 < d[1] for d in others):
            continue   # crosses a double-quoted passage: probably apostrophes, not a quote
        keep = others + [(s0, s1, q)]
    return sorted(keep)


def quote_pieces(q):
    """A quote's parts between ellipses and [bracketed insertions], normalized, each to be found in order."""
    parts = [norm(p).strip(" .,;:") for p in re.split(r"\.\.\.|…|\[[^\]]*\]", q)]
    return ([p for p in parts if len(p.split()) >= 4] or [p for p in parts if len(p.split()) >= 2]
            or [norm(re.sub(r"\[[^\]]*\]", " ", q)).strip(" .,;:")])


def closest(quote, src, min_ratio=0.5):
    """The passage of `src` most like `quote`, if any is reasonably close. Candidates start where three words in a
    row match, or two if none do (a short quote with one word changed has no three-word match left)."""
    q = words(quote)
    if len(q) < 3 or not src.keys:
        return None
    starts = []
    for n in (3, 2):
        index = {}
        for i in range(len(src.keys) - n + 1):
            index.setdefault(tuple(src.keys[i:i + n]), []).append(i)
        for off in range(len(q) - n + 1):
            starts += [max(0, pos - off) for pos in index.get(tuple(q[off:off + n]), [])[:10]]
        if starts:
            break
    best, best_ratio = None, min_ratio
    for start in list(dict.fromkeys(starts))[:300]:
        ratio = difflib.SequenceMatcher(None, q, src.keys[start:start + len(q) + 3]).ratio()
        if ratio > best_ratio:
            best, best_ratio = (start, start + len(q) + 3), ratio
    if not best:
        return None
    return " ".join(src.raw[best[0]:best[1]]).strip(QUOTE_MARKS + " ")


def check_quotes(text, sources):
    """([{quote, verdict, file, note}], number of short quotes skipped) for quotes outside the reference list. A
    quote is checked against the source it cites; only a quote that cites nothing is looked for in every source."""
    items = reference_items(text)
    ref_lines = {n for it in items for n in range(it[2], it[3] + 1)}
    out, short = [], 0
    for start, end, q in find_quotes(text):
        if text.count("\n", 0, start) in ref_lines:
            continue   # titles in the reference list
        if len(words(q)) < MIN_QUOTE_WORDS:
            short += len(words(q)) > 1
            continue
        pieces = quote_pieces(q)
        p0, p1 = paragraph_at(text, start, end)
        named, cited = [], []
        for context in (sentence_at(text, start, end), text[p0:p1]):   # its own sentence first, then the paragraph
            urls = cited_urls(context)
            cited = [it for it in items if cites(context, it)]
            for it in cited:
                urls |= cited_urls(it[1])
            named = [s for s in sources if s.urls & urls
                     or (not s.urls and Path(s.label).stem.lower() in context.lower())]
            if named or cited or urls:   # a sentence that cites something doesn't borrow the paragraph's sources
                break
        home = next((s for s in named if s.has(pieces)), None)
        elsewhere = None if home else next((s for s in sources if s not in named and s.has(pieces)), None)
        readable = [s for s in named if s.readable and not s.partial]
        row = {"quote": q, "file": None, "note": ""}
        if home:
            row.update(verdict="found in source", file=home.label)
        elif readable and elsewhere:
            row.update(verdict="found in another source", file=elsewhere.label,
                       note=f"not in {readable[0].label}, the source cited for it")
        elif readable:
            near = next((c for c in (closest(q, s) for s in readable) if c), None)
            row.update(verdict="not verbatim", file=readable[0].label,
                       note=f"closest passage: \"{near}\"" if near else "nothing close found")
        elif elsewhere:   # its own source can't be read (or it cites none): the words are in another one
            row.update(verdict="found in source", file=elsewhere.label,
                       note="" if not (named or cited) else
                       "its cited source couldn't be read here: check this is the same work")
        elif unlabelled := next(((s, c) for s in sources if not s.urls and not s.partial and s.readable
                                 and s not in named and (c := closest(q, s, 0.6))), None):
            # a file without a "Source: <url>" line (attached in chat, say) can't be matched to the link a quote
            # cites; a passage this close is very likely the one quoted
            row.update(verdict="not verbatim", file=unlabelled[0].label,
                       note=f"closest passage: \"{unlabelled[1]}\" (this file doesn't say where it's from: check "
                            "it's the source cited)")
        elif named and all(s.partial for s in named):   # the paper itself may still have it: not a misquote
            near = next((c for c in (closest(q, s) for s in named) if c), None)
            row.update(verdict="not checked", file=named[0].label,
                       note="only its abstract was available, and the quote isn't in that"
                       + (f" (closest there: \"{near}\")" if near else ""))
        elif named:
            row.update(verdict="not checked", file=named[0].label,
                       note="its source gave little or no text (paywall, login, JavaScript or PDF)")
        else:
            row.update(verdict="not checked", note="no linked or saved source found for it")
        out.append(row)
    return out, short


# ---------------------------------------------------------------- report
def local_sources(folders):
    out = []
    for folder in folders or []:
        for p in inside_data(sorted(Path(folder).rglob("*"))):
            if p.is_file():
                t = read_source_file(p)
                if t:
                    m = re.match(r"Source: (\S+)", t)
                    out.append(Source(str(p), t, [m.group(1)] if m else []))
    return out


def check(path, sources=(), save=None, links=True, offline=False):
    text = read_draft(path)
    network_failed = reached = False   # reached: some lookup or link got an answer, so the network works
    rows, link_rows, abstracts = [], [], []
    ids = find_ids(text)
    skipped = "offline" if offline else "--no-links: not opened" if not links else None
    if offline:   # listed anyway, so whoever checks them by hand has the list
        rows = [{"id": f"{kind}:{ident}", "verdict": "not checked", "catalogue": "", "note": "offline"}
                for kind, ident, _ in ids]
    if skipped:
        link_rows = [{"url": u, "verdict": "not checked", "note": skipped, "network": False} for u in find_urls(text)]
    if not offline:
        arxiv = [i for k, i, _ in ids if k == "arxiv"]
        try:
            recs = lookup_arxiv(arxiv) if arxiv else {}
        except (urllib.error.URLError, OSError, ET.ParseError) as e:
            recs, network_failed = {a: e for a in arxiv}, True
        for kind, ident, line in ids:
            if kind == "arxiv":
                rec = recs.get(ident)
            else:
                try:
                    rec = lookup_doi(ident)
                except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                    rec, network_failed = e, True
            if isinstance(rec, Exception):
                rows.append({"id": f"{kind}:{ident}", "verdict": "couldn't check", "catalogue": "",
                             "note": str(rec)[:80]})
                continue
            reached = True
            if rec and rec.get("abstract", "").strip():
                abstracts.append(Source(f"{kind}:{ident} (abstract, from the catalogue)", rec["abstract"],
                                        {f"{kind}:{ident.lower()}"}, partial=True))
            verdict, note = judge(rec, line, kind)
            rows.append({"id": f"{kind}:{ident}", "verdict": verdict, "note": note,
                         "catalogue": "" if rec is None else
                         f"{rec['title'][:90]} ({(rec.get('authors') or ['?'])[0]}, {rec.get('year') or '?'})"})
        if links:
            urls = find_urls(text)
            link_rows = check_links(urls)
            if any(r["network"] for r in link_rows):
                if reached or any(r["reached"] for r in link_rows) or network_up():
                    settle_unreachable(link_rows)   # one dead or down site, not a dead connection
                else:
                    network_failed = True
            for r in link_rows:
                r.pop("reached", None), r.pop("no_such_site", None)
            if save:
                save_pages(link_rows, save)
            if len(urls) > MAX_LINKS:
                link_rows.append({"url": f"(and {len(urls) - MAX_LINKS} more)", "verdict": "couldn't check",
                                  "note": f"only the first {MAX_LINKS} links were opened", "network": False})
    srcs = local_sources(sources) + [Source(r.get("saved") or r["url"], r["text"], {r["url"], r["final_url"]}, fetched=True)
                                     for r in link_rows if r.get("text")] + abstracts
    quotes, short = check_quotes(text, srcs)
    for r in link_rows:
        r.pop("text", None)
    return {"draft": str(path), "ids": rows, "links": link_rows, "quotes": quotes, "short_quotes": short,
            "network_failed": network_failed, "offline": offline, "links_checked": links and not offline,
            "saved_to": str(save) if any(r.get("saved") for r in link_rows) else None,
            "save_asked": bool(save) and links and not offline}


def exit_code(r):
    """0 everything checked, nothing flagged; 1 something flagged; 2 nothing flagged, but something wasn't checked."""
    rows = r["ids"] + r["links"] + r["quotes"]
    if any(x["verdict"] in FLAGGED for x in rows):
        return 1
    return 2 if r["network_failed"] or any(x["verdict"] in UNCHECKED for x in rows) else 0


def cell(s):
    return str(s or "").replace("|", "/").replace("\n", " ")


def markdown(r):
    L = [f"# Citation check: {r['draft']}", "",
         "**This checks links, identifiers and quotes only. It does not check that a source supports the claim "
         "it's cited for.** A clean table doesn't mean the draft is right.", ""]
    if r["offline"]:
        L += ["(Offline: links, arXiv IDs and DOIs weren't checked.)", ""]
    elif r["network_failed"]:
        L += ["**Some lookups failed, so this table is incomplete** (the \"couldn't check\" rows). Check your "
              "internet connection and re-run.", ""]
    if r["ids"]:
        L += ["## Paper identifiers", "", "| id | verdict | catalogue says | note |", "|---|---|---|---|"]
        L += [f"| {x['id']} | {x['verdict']} | {cell(x['catalogue'])} | {cell(x['note'])} |" for x in r["ids"]]
        L += [""]
    if r["links"]:
        L += ["## Links", "", "| link | verdict | note | saved as |", "|---|---|---|---|"]
        L += [f"| {cell(x['url'])} | {x['verdict']} | {cell(x['note'])} | {cell(x.get('saved'))} |"
              for x in r["links"]] + [""]
    elif r["links_checked"]:
        L += ["No links found.", ""]
    if r["quotes"]:
        L += ["## Quotes", "", "| quote (start) | verdict | source | note |", "|---|---|---|---|"]
        L += [f"| \"{cell(' '.join(x['quote'].split()[:10]))}…\" | {x['verdict']} | {cell(x['file'])} | "
              f"{cell(x['note'])} |" for x in r["quotes"]] + [""]
    gaps = [n for n, part in (("paper identifier", "ids"), ("link", "links"), ("quote", "quotes"))
            for x in r[part] if x["verdict"] in UNCHECKED]
    if gaps:
        counts = ", ".join(f"{gaps.count(n)} {n}{'s' if gaps.count(n) > 1 else ''}" for n in dict.fromkeys(gaps))
        L += [f"**Not everything was checked** ({counts}): check those by hand or with the verifier.", ""]
    if r.get("short_quotes"):
        L += [f"{r['short_quotes']} quoted phrase(s) under {MIN_QUOTE_WORDS} words weren't checked (usually titles, "
              "names or scare quotes).", ""]
    used = sorted({x["verdict"] for x in r["ids"] + r["links"] + r["quotes"]}
                  - {"matches catalogue", "found in source", "link works"})
    if used:
        L += ["What to do:", ""] + [f"- **{v}**: {VERDICT_HELP[v]}" for v in used] + [""]
    if r.get("saved_to"):
        L += [f"Linked pages were saved as text in `{r['saved_to']}`.", ""]
    elif r.get("save_asked") and r["links"]:
        opened = any(x["verdict"] == "link works" for x in r["links"])
        L += ["No pages were saved: " + ("the pages that opened gave no text." if opened else
                                         "none of the links could be opened."), ""]
    return "\n".join(L)


# ---------------------------------------------------------------- workspace
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "workspace"
CORPUS = DATA / "corpus"
CITECHECK = DATA / "citecheck"


class Refused(Exception):
    pass


def guarded(path, within, what):
    """`path` resolved (relative paths from the workspace root, symlinks followed), if it's inside `within`."""
    p = Path(path).expanduser()
    p = (p if p.is_absolute() else ROOT / p).resolve()
    if not (p == within or within in p.parents) or ".env" in p.name.lower():
        raise Refused(f"{what} must be inside {within.relative_to(ROOT)}/ (got {path})")
    return p


def inside_data(paths):
    """Only files that resolve inside workspace/: a symlink in a sources folder can't point elsewhere."""
    return [p for p in paths if DATA in p.resolve().parents and ".env" not in p.name.lower()]


def main(argv):
    ap = argparse.ArgumentParser(description="Check a draft's links, paper identifiers and quotes.",
                                 epilog="See the top of this file for details.")
    ap.add_argument("draft", nargs="?", help="the draft: .md, .txt or .docx")
    ap.add_argument("--sources", action="append", metavar="FOLDER",
                    help="a folder of source texts (.md .txt .html .pdf) to check quotes against; repeatable")
    ap.add_argument("--save", metavar="FOLDER", help="save each linked page as text in this folder")
    ap.add_argument("--no-links", action="store_true", help="don't open the draft's links")
    ap.add_argument("--offline", action="store_true", help="no network at all")
    ap.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    ap.add_argument("--fetch", nargs="+", metavar="URL", help="just save these pages as text (needs --save)")
    a = ap.parse_args(argv)
    try:
        if a.save:
            a.save = guarded(a.save, CITECHECK, "--save")
        a.sources = [guarded(f, DATA, "--sources") for f in (a.sources or [])]
        if a.draft:
            a.draft = guarded(a.draft, DATA, "The draft")
    except Refused as e:
        print(f"Refused: {e}")
        return 2
    if CORPUS.is_dir() and CORPUS not in a.sources:
        a.sources.insert(0, CORPUS)
    if a.fetch:
        if not a.save:
            ap.error("--fetch needs --save <folder>")
        rows = check_links([clean_url(u) for u in a.fetch])
        save_pages(rows, a.save)
        for row in rows:
            print(f"{row['url']}: {row['verdict']}" + (f" ({row['note']})" if row["note"] else "")
                  + (f" -> {row['saved']}" if row.get("saved") else ""))
        return 0 if all(row.get("saved") for row in rows) else 1
    if not a.draft:
        ap.error("give a draft file (or --fetch <url> --save <folder>)")
    r = check(a.draft, a.sources, a.save, links=not a.no_links, offline=a.offline)
    print(json.dumps(r, indent=1) if a.json else markdown(r))
    return exit_code(r)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
