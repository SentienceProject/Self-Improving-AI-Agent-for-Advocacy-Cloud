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
  3. Quotes: checks every quoted passage of 8+ words against the pages it cites (links in the same paragraph, or
     the reference it points to with [1], [^1] or Author + year) and against the --sources folder. When a quote
     isn't word for word, it shows the closest passage it found.

It never checks whether a source supports the claim it's cited for: that's the verifier's job, and a clean table
doesn't mean a draft is right. It sends the identifiers to arXiv and Crossref and opens the draft's links, so for an
unpublished draft those sites can see what you're reading. Set CROSSREF_MAILTO to your address to use Crossref's
"polite" pool (optional).

Exit code: 0 nothing flagged, 1 something to look at, 2 a lookup failed (so the table is incomplete).

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

NEW_ARXIV = r"\d{4}\.\d{4,5}(?:v\d+)?"
OLD_ARXIV = r"[a-z][a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
ARXIV_RE = re.compile(rf"(?:arXiv[:\s]*|arxiv\.org/(?:abs|pdf|html)/|10\.48550/arXiv\.)({NEW_ARXIV}|{OLD_ARXIV})",
                      re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]\[]+)", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"'\]\[`|]+", re.I)
# Quote marks are paired left to right, never across a blank line, and short quotes are dropped afterwards (a
# minimum length inside the pattern would let a match start at a closing mark and pair the wrong ones).
QUOTE_RE = re.compile(r'“((?:[^“”\n]|\n(?!\s*\n))+)”|"((?:[^"\n]|\n(?!\s*\n))+)"')
ITEM_START = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[\^?\d+\]:?)\s")
REF_DEF = re.compile(r"^\s*(?:\[\^?(\d+)\]:?|(\d+)[.)])\s")
REF_USE = re.compile(r"\[\^?(\d+)\]")
REF_HEADING = re.compile(r"\b(citations?|references?|sources?|bibliography|works cited|notes|footnotes|endnotes)\b",
                         re.I)

VERDICT_HELP = {
    "matches catalogue": "",
    "check": "Compare the citation with the catalogue entry; often a formatting difference, sometimes a wrong paper.",
    "not found in catalogue": "Could be a typo in the ID rather than an invented source: look the paper up by title.",
    "retraction or withdrawal": "Read the notice before relying on this paper.",
    "couldn't check": "The lookup failed (network?). Re-run later; nothing was checked for this one.",
    "link works": "",
    "broken link": "The page is gone. Find where it moved (search its title, or try web.archive.org) or cite "
                   "something else.",
    "refused": "The site blocks automated checks. Open the link yourself: this says nothing about whether it works.",
    "found in source": "",
    "not verbatim": "The quote isn't word for word in the source it's attributed to: fix it from the source (the "
                    "note shows the closest passage found).",
    "not checked": "Its source wasn't available as text, so the quote couldn't be checked here: check it by hand "
                   "or with the verifier.",
}
FLAGGED = {"check", "not found in catalogue", "retraction or withdrawal", "broken link", "not verbatim"}


# ---------------------------------------------------------------- text helpers
def norm(s):
    """For comparing quotes: unify quotes, dashes, ligatures and whitespace; drop soft hyphens and line-break
    hyphenation; lower-case."""
    s = unicodedata.normalize("NFKC", s).replace("­", "")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


def words(s):
    return re.findall(r"[a-z0-9]+", norm(s))


def title_ratio(a, b):
    return difflib.SequenceMatcher(None, " ".join(words(a)), " ".join(words(b))).ratio()


def claimed_title(line):
    m = re.search(r"\*([^*]{8,})\*", line) or re.search(r"[\"“]([^\"“”]{8,})[\"”]", line)
    return m.group(1).strip() if m else None


def claimed_years(line):
    return {int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", line)}


def citing_line(text, start):
    """The citation around position `start`: its whole list item or line, including wrapped lines."""
    lines = text.splitlines()
    i = text.count("\n", 0, start)
    s = i
    while s > 0 and not ITEM_START.match(lines[s]) and lines[s - 1].strip() and not lines[s].startswith("#"):
        s -= 1
    e = i
    while e + 1 < len(lines) and lines[e + 1].strip() and not ITEM_START.match(lines[e + 1]) \
            and not lines[e + 1].startswith("#"):
        e += 1
    return " ".join(l.strip() for l in lines[s:e + 1])


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
class HTMLText(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "button", "aside"}
    BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "blockquote", "figcaption", "table", "pre",
             "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK and not self.skip:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK and not self.skip:
            self.out.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def text(self):
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.out))
        t = "\n".join(line.strip() for line in t.splitlines())
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def html_text(s):
    p = HTMLText()
    p.feed(s)
    return p.text()


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
def find_ids(text):
    """[(kind, id, citing line)], each id once."""
    seen, out = set(), []
    for m in ARXIV_RE.finditer(text):
        aid = re.sub(r"v\d+$", "", m.group(1))
        if ("arxiv", aid) not in seen:
            seen.add(("arxiv", aid))
            out.append(("arxiv", aid, citing_line(text, m.start())))
    for m in DOI_RE.finditer(text):
        doi = clean_doi(m.group(1))
        if doi.lower().startswith("10.48550/arxiv."):
            continue  # an arXiv DOI: looked up on arXiv above
        if ("doi", doi.lower()) not in seen:
            seen.add(("doi", doi.lower()))
            out.append(("doi", doi, citing_line(text, m.start())))
    return out


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
        if h and (line.lstrip().startswith(("#", "**")) or REF_HEADING.fullmatch(h.group(1).strip())):
            in_refs = bool(REF_HEADING.search(h.group(1)))
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
            found[eid] = {"title": title,
                          "authors": [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)],
                          "year": int((e.findtext("a:published", "", ATOM) or "0000")[:4] or 0),
                          "withdrawn": bool(re.search(r"withdraw", comment + title, re.I))}
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
    year = None
    for k in ("issued", "published-print", "published-online", "created"):
        parts = (msg.get(k) or {}).get("date-parts") or [[None]]
        if parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    updates = (msg.get("update-to") or []) + (msg.get("updated-by") or [])
    retracted = any(str(u.get("type", "")).lower() in ("retraction", "withdrawal", "removal") for u in updates)
    retracted |= any(k.startswith("is-retracted") for k in (msg.get("relation") or {}))
    retracted |= str(msg.get("subtype", "")).lower() == "retraction"
    return {"title": " ".join(msg.get("title") or []),
            "authors": [a.get("family") or a.get("name", "") for a in msg.get("author") or []],
            "year": year, "retracted": retracted}


def judge(rec, line, kind):
    """(verdict, note) for one identifier's catalogue record against the line citing it."""
    if rec is None:
        return "not found in catalogue", ""
    if rec.get("withdrawn") or rec.get("retracted"):
        return "retraction or withdrawal", "the catalogue records a retraction or withdrawal"
    notes = []
    t = claimed_title(line)
    if t and title_ratio(t, rec["title"]) < 0.8:
        notes.append(f"title differs (draft: \"{t[:80]}\")")
    first = (rec.get("authors") or [""])[0]
    surname = first.split()[-1] if " " in first and kind == "arxiv" else first
    full = bool(t or claimed_years(line))   # a bare ID in prose names no author to compare
    if full and surname and words(surname) and not set(words(surname)) & set(words(line)):
        notes.append(f"first author {surname} isn't named in the citation")
    years = claimed_years(line)
    if years and rec.get("year") and not any(abs(y - rec["year"]) <= 1 for y in years):
        notes.append(f"year {', '.join(map(str, sorted(years)))} vs catalogue {rec['year']}")
    return ("check" if notes else "matches catalogue"), "; ".join(notes)


# ---------------------------------------------------------------- links
def page_text(ctype, charset, data):
    """(text or None, what it was)."""
    if data[:5] == b"%PDF-" or ctype == "application/pdf":
        t = pdf_text(data)
        return t, ("PDF" if t is not None else "PDF (no PDF converter installed: pdftotext or pypdf)")
    if ctype in ("text/html", "application/xhtml+xml") or data.lstrip()[:1] == b"<":
        return html_text(data.decode(charset or "utf-8", errors="replace")), "web page"
    if ctype.startswith("text/"):
        return data.decode(charset or "utf-8", errors="replace"), "text"
    return None, ctype


def check_link(url):
    """{url, verdict, note, text, final_url, network}."""
    row = {"url": url, "verdict": "link works", "note": "", "text": None, "final_url": url, "network": False}
    try:
        ctype, charset, data, final = fetch_page(url)
    except urllib.error.HTTPError as e:
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
        note = ("the site's name doesn't resolve (a dead site, or you're offline)"
                if "Name or service not known" in str(reason) or "getaddrinfo" in str(reason) or
                "nodename nor servname" in str(reason) else str(reason)[:80])
        row.update(verdict="couldn't check", note=note, network=True)
        return row
    text, kind = page_text(ctype, charset, data)
    row["final_url"] = final
    if text is None:
        row["note"] = f"{kind}: no text to check quotes against"
    else:
        n = len(text.split())
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


def check_links(urls):
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(check_link, urls[:MAX_LINKS]))


# ---------------------------------------------------------------- quotes
class Source:
    def __init__(self, label, text, urls=(), fetched=False):
        self.label, self.urls = label, set(urls)
        self.norm = norm(text or "")
        # a fetched page with little text is probably a paywall or login; a file someone saved is taken as is
        self.readable = len(self.norm.split()) >= (MIN_WORDS if fetched else 1)
        raw = self.norm.split()
        keys = [re.sub(r"[^a-z0-9]", "", t) for t in raw]
        self.raw = [t for t, k in zip(raw, keys) if k]
        self.keys = [k for k in keys if k]


def closest(quote, src):
    """The passage of `src` most like `quote`, if any is reasonably close."""
    q = words(quote)
    if len(q) < 3 or not src.keys:
        return None
    index = {}
    for i in range(len(src.keys) - 2):
        index.setdefault(tuple(src.keys[i:i + 3]), []).append(i)
    starts = []
    for off in range(len(q) - 2):
        starts += [max(0, pos - off) for pos in index.get(tuple(q[off:off + 3]), [])[:10]]
    best, best_ratio = None, 0.5
    for start in list(dict.fromkeys(starts))[:300]:
        ratio = difflib.SequenceMatcher(None, q, src.keys[start:start + len(q) + 3]).ratio()
        if ratio > best_ratio:
            best, best_ratio = (start, start + len(q) + 3), ratio
    if not best:
        return None
    return " ".join(src.raw[best[0]:best[1]])


def check_quotes(text, sources):
    """[{quote, verdict, file, note}] for quotes of 8+ words outside the reference list."""
    items = reference_items(text)
    ref_lines = {n for it in items for n in range(it[2], it[3] + 1)}
    out = []
    for m in QUOTE_RE.finditer(text):
        q = m.group(1) or m.group(2)
        if len(words(q)) < 8 or text.count("\n", 0, m.start()) in ref_lines:
            continue
        pieces = [norm(p).strip(" .,;:") for p in re.split(r"\.\.\.|…|\[\.\.\.\]", q)]
        pieces = [p for p in pieces if len(p.split()) >= 4] or [norm(q)]
        hit = next((s for s in sources if s.norm and all(p in s.norm for p in pieces)), None)
        if hit:
            out.append({"quote": q, "verdict": "found in source", "file": hit.label, "note": ""})
            continue
        end = text.find("\n\n", m.end())
        p0, p1 = text.rfind("\n\n", 0, m.start()) + 1, (end if end != -1 else len(text))
        para = text[p0:p1]
        stop = re.compile(r"[.!?](?:\s|$)")
        before = [x.end() for x in stop.finditer(text, p0, m.start())]
        after = stop.search(text, m.end(), p1)
        sentence = text[(before[-1] if before else p0):(after.end() if after else p1)]
        named = []
        for context in (sentence, para):   # the quote's own sentence first; the paragraph if that cites nothing
            urls = set(find_urls(context))
            cited = [it for it in items if cites(context, it)]
            for it in cited:
                urls |= set(find_urls(it[1]))
            named = [s for s in sources if s.urls & urls
                     or (not s.urls and Path(s.label).stem.lower() in context.lower())]
            if named or cited:
                break
        readable = [s for s in named if s.readable]
        if readable:
            near = next((c for c in (closest(q, s) for s in readable) if c), None)
            out.append({"quote": q, "verdict": "not verbatim", "file": readable[0].label,
                        "note": f"closest passage: \"{near}\"" if near else "nothing close found"})
        elif named:
            out.append({"quote": q, "verdict": "not checked", "file": named[0].label,
                        "note": "its source gave little or no text (paywall, login, JavaScript or PDF)"})
        else:
            out.append({"quote": q, "verdict": "not checked", "file": None,
                        "note": "no linked or saved source found for it"})
    return out


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
    network_failed = False
    rows, link_rows = [], []
    if not offline:
        ids = find_ids(text)
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
            verdict, note = judge(rec, line, kind)
            rows.append({"id": f"{kind}:{ident}", "verdict": verdict, "note": note,
                         "catalogue": "" if rec is None else
                         f"{rec['title'][:90]} ({(rec.get('authors') or ['?'])[0]}, {rec.get('year') or '?'})"})
        if links:
            urls = find_urls(text)
            link_rows = check_links(urls)
            network_failed |= any(r["network"] for r in link_rows)
            if save:
                save_pages(link_rows, save)
            if len(urls) > MAX_LINKS:
                link_rows.append({"url": f"(and {len(urls) - MAX_LINKS} more)", "verdict": "couldn't check",
                                  "note": f"only the first {MAX_LINKS} links were opened", "network": False})
    srcs = local_sources(sources) + [Source(r.get("saved") or r["url"], r["text"], {r["url"], r["final_url"]}, fetched=True)
                                     for r in link_rows if r.get("text")]
    quotes = check_quotes(text, srcs)
    for r in link_rows:
        r.pop("text", None)
    return {"draft": str(path), "ids": rows, "links": link_rows, "quotes": quotes,
            "network_failed": network_failed, "offline": offline, "links_checked": links and not offline,
            "saved_to": str(save) if save and not offline else None}


def cell(s):
    return str(s or "").replace("|", "/").replace("\n", " ")


def markdown(r):
    L = [f"# Citation check: {r['draft']}", "",
         "**This checks links, identifiers and quotes only. It does not check that a source supports the claim "
         "it's cited for.** A clean table doesn't mean the draft is right.", ""]
    if r["offline"]:
        L += ["(Offline: links, arXiv IDs and DOIs weren't checked.)", ""]
    elif r["network_failed"]:
        L += ["**Some lookups failed, so this table is incomplete.** Re-run when the network is back.", ""]
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
    unchecked = sum(x["verdict"] == "not checked" for x in r["quotes"])
    if unchecked:
        L += [f"**{unchecked} quote(s) couldn't be checked** (no readable source): check those by hand or with the "
              "verifier.", ""]
    used = sorted({x["verdict"] for x in r["ids"] + r["links"] + r["quotes"]}
                  - {"matches catalogue", "found in source", "link works"})
    if used:
        L += ["What to do:", ""] + [f"- **{v}**: {VERDICT_HELP[v]}" for v in used] + [""]
    if r.get("saved_to"):
        L += [f"Linked pages were saved as text in `{r['saved_to']}`.", ""]
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
    if r["network_failed"]:
        return 2
    return 1 if any(x["verdict"] in FLAGGED for x in r["ids"] + r["links"] + r["quotes"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
