#!/usr/bin/env python3
"""Download one open-access paper and add it to the corpus as text.

    python3 scripts/add_paper.py "<https-url>" <name> [--title "<title>"]

Writes workspace/corpus/<name>.md: a header (title, source URL, retrieval date) and the paper's text.
- HTML pages (e.g. arxiv.org/html/..., PubMed Central articles) are converted with Python's own HTML parser.
- PDFs are converted with `pdftotext` (poppler) or the `pypdf` package, whichever is installed. If neither is,
  the PDF is saved to workspace/inbox/<name>.pdf and the script says so.
Runs inside Claude Code's sandbox, so it can only reach the research sites the settings allow; any other site is
refused there (or asks you, depending on your permission mode). It never overwrites an existing file.

Use only copies that are free to read: open-access articles, preprints, authors' own pages. Never shadow libraries.
The corpus stays on your computer (it's gitignored); don't publish the papers themselves.

Exit codes: 0 added, 1 failed, 2 refused (bad URL or name, or the file already exists).
"""
import html.parser
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS, INBOX = ROOT / "workspace" / "corpus", ROOT / "workspace" / "inbox"
MAX_BYTES = 40_000_000
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")


class Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "button", "aside"}
    BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "blockquote", "figcaption", "table", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title = [], 0, ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif re.fullmatch(r"h[1-6]", tag) and not self.skip:
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in self.BLOCK and not self.skip:
            self.out.append("\n\n" if tag != "li" else "\n- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif re.fullmatch(r"h[1-6]", tag) and not self.skip:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)

    def text(self):
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.out))
        t = "\n".join(line.strip() for line in t.splitlines())
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def pdf_text(data):
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


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    url, name = argv[0], argv[1]
    title = argv[argv.index("--title") + 1] if "--title" in argv else ""
    if not url.startswith("https://"):
        print("Refused: https URLs only.")
        return 2
    if not NAME_RE.fullmatch(name):
        print("Refused: name must be letters, digits, - or _ (e.g. birch-2021-decapods).")
        return 2
    out = CORPUS / f"{name}.md"
    if out.exists():
        print(f"Refused: {out.relative_to(ROOT)} already exists. Pick another name.")
        return 2
    req = urllib.request.Request(url, headers={"User-Agent": "welfare-workspace/1.0 (personal research corpus)",
                                               "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(MAX_BYTES + 1)
            ctype = r.headers.get_content_type()
            charset = r.headers.get_content_charset() or "utf-8"
            final_url = r.geturl()
    except Exception as e:
        print(f"Download failed: {type(e).__name__}: {e}")
        return 1
    if len(data) > MAX_BYTES:
        print("Refused: larger than 40 MB.")
        return 2
    if data[:5] == b"%PDF-" or ctype == "application/pdf":
        text = pdf_text(data)
        if text is None:
            INBOX.mkdir(parents=True, exist_ok=True)
            (INBOX / f"{name}.pdf").write_bytes(data)
            print(f"Saved the PDF to workspace/inbox/{name}.pdf, but there's no PDF converter here (pdftotext or "
                  "pypdf). Ask the operator to install one, or look for an HTML version of the paper.")
            return 1
        kind = "PDF, converted automatically"
    else:
        p = Text()
        p.feed(data.decode(charset, errors="replace"))
        text, title = p.text(), title or re.sub(r"\s+", " ", p.title).strip()
        kind = "web page, converted automatically"
    if len(text.split()) < 300:
        print(f"Only {len(text.split())} words came out: probably a landing page, a paywall or a page that needs "
              "JavaScript. Nothing saved. Look for the full-text HTML or PDF link instead.")
        return 1
    CORPUS.mkdir(parents=True, exist_ok=True)
    header = (f"# {title or name}\n\n- Source: {final_url}\n"
              f"- Retrieved: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ({kind})\n"
              "- Check quotes against the original: conversion can garble tables, equations and footnotes.\n\n---\n\n")
    out.write_text(header + text + "\n", encoding="utf-8")
    print(f"Added {out.relative_to(ROOT)} ({len(text.split())} words).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
