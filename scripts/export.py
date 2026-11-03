#!/usr/bin/env python3
"""Export a draft to Word, PDF or a web page, ready to send or print.

    python3 scripts/export.py <draft.md>                  Word (.docx), next to the draft
    python3 scripts/export.py <draft.md> --pdf            PDF
    python3 scripts/export.py <draft.md> --html           a web page (open it and print to PDF from the browser)
    python3 scripts/export.py <draft.md> --no-notes       only the deliverable: drop everything from the last
                                                          "---" line followed by a "## Notes..." heading (expected
                                                          value, uncertainty, citations, workspace notes)

An earlier export is never overwritten: if <draft>.docx exists, this writes "<draft> (2).docx", and so on.

Uses pandoc if it's installed (best results), otherwise a built-in converter that handles headings, paragraphs,
lists, bold, italics, links and quotes (tables come out as plain lines). For PDF it needs pandoc with a PDF engine,
or LibreOffice; without either it writes the web page and tells you to print it to PDF.
"""
import html
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


NOTES = re.compile(r"\n-{3,}[ \t]*\n\s*#{1,6}[ \t]+Notes\b[^\n]*", re.I)


def deliverable(text):
    """(the draft without its notes, words dropped). The notes start at the last "---" line followed by a heading
    that begins "## Notes" (the workspace's drafts use that marker); without one, nothing is dropped."""
    found = list(NOTES.finditer(text))
    if not found:
        return text, 0
    cut = found[-1].start()
    return text[:cut].rstrip() + "\n", len(text[cut:].split())


def free_name(path):
    """path, or "name (2).ext", "name (3).ext"... if it's taken."""
    n = 2
    while path.exists():
        path = path.with_name(f"{path.stem.rsplit(' (', 1)[0]} ({n}){path.suffix}")
        n += 1
    return path


# ---------------------------------------------------------------- a small Markdown reader
def blocks(md):
    """[(kind, level, text)]: kind is h, p, ul, ol, quote or hr."""
    out, para = [], []

    def flush():
        if para:
            out.append(("p", 0, " ".join(para)))
            para.clear()
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush()
        elif m := re.match(r"^(#{1,6})\s+(.*)", s):
            flush()
            out.append(("h", len(m.group(1)), m.group(2)))
        elif re.match(r"^\s*(-{3,}|\*{3,})\s*$", s):
            flush()
            out.append(("hr", 0, ""))
        elif m := re.match(r"^(\s*)[-*+]\s+(.*)", s):
            flush()
            out.append(("ul", len(m.group(1)) // 2, m.group(2)))
        elif m := re.match(r"^(\s*)\d+[.)]\s+(.*)", s):
            flush()
            out.append(("ol", len(m.group(1)) // 2, m.group(2)))
        elif m := re.match(r"^>\s?(.*)", s):
            flush()
            out.append(("quote", 0, m.group(1)))
        elif out and out[-1][0] in ("ul", "ol") and line.startswith("  ") and not para:
            k, lv, t = out[-1]
            out[-1] = (k, lv, t + " " + s.strip())
        else:
            para.append(s.strip())
    flush()
    return out


INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`|\[[^\]]+\]\([^)\s]+\))")


def runs(text):
    """[(text, bold, italic, link)] for inline Markdown."""
    out = []
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append((part[2:-2], True, False, None))
        elif (part.startswith("*") and part.endswith("*")) or (part.startswith("_") and part.endswith("_")):
            out.append((part[1:-1], False, True, None))
        elif part.startswith("`"):
            out.append((part[1:-1], False, False, None))
        elif m := re.fullmatch(r"\[([^\]]+)\]\(([^)\s]+)\)", part):
            out.append((m.group(1), False, False, m.group(2)))
        else:
            out.append((part, False, False, None))
    return out


# ---------------------------------------------------------------- web page
def to_html(md, title):
    body, lists = [], []
    for kind, level, text in blocks(md):
        inline = "".join(
            (f'<a href="{html.escape(link)}">' if link else "") + ("<strong>" if b else "") + ("<em>" if i else "")
            + html.escape(t) + ("</em>" if i else "") + ("</strong>" if b else "") + ("</a>" if link else "")
            for t, b, i, link in runs(text))
        if kind in ("ul", "ol"):
            if not lists or lists[-1] != kind:
                body.append(f"<{kind}>")
                lists.append(kind)
            body.append(f"<li>{inline}</li>")
            continue
        while lists:
            body.append(f"</{lists.pop()}>")
        body.append({"h": f"<h{level}>{inline}</h{level}>", "p": f"<p>{inline}</p>", "hr": "<hr>",
                     "quote": f"<blockquote>{inline}</blockquote>"}[kind])
    while lists:
        body.append(f"</{lists.pop()}>")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>"
            "body{font-family:Georgia,serif;max-width:42em;margin:2em auto;padding:0 1em;line-height:1.5}"
            "blockquote{border-left:3px solid #999;margin-left:0;padding-left:1em;color:#444}"
            "@media print{body{margin:0}}</style></head><body>\n" + "\n".join(body) + "\n</body></html>\n")


# ---------------------------------------------------------------- Word, without dependencies
def to_docx(md, out):
    rels, paras = [], []

    def run_xml(t, b, i, link):
        props = ("<w:b/>" if b else "") + ("<w:i/>" if i else "") + (
            '<w:color w:val="0563C1"/><w:u w:val="single"/>' if link else "")
        r = f'<w:r>{"<w:rPr>" + props + "</w:rPr>" if props else ""}<w:t xml:space="preserve">{html.escape(t)}</w:t></w:r>'
        if link:
            rid = f"rId{len(rels) + 10}"
            rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        f'relationships/hyperlink" Target="{html.escape(link)}" TargetMode="External"/>')
            return f'<w:hyperlink r:id="{rid}">{r}</w:hyperlink>'
        return r
    counters = {}
    for kind, level, text in blocks(md):
        style, prefix = None, ""
        if kind == "h":
            style = f"Heading{min(level, 3)}"
        elif kind == "ul":
            prefix = "    " * level + "• "
        elif kind == "ol":
            counters[level] = counters.get(level, 0) + 1
            prefix = "    " * level + f"{counters[level]}. "
        elif kind == "quote":
            style = "Quote"
        elif kind == "hr":
            paras.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="999999"/>'
                         '</w:pBdr></w:pPr></w:p>')
            continue
        if kind != "ol":
            counters.clear()
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body = (run_xml(prefix, False, False, None) if prefix else "") + "".join(run_xml(*r) for r in runs(text))
        paras.append(f"<w:p>{ppr}{body}</w:p>")
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}>'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/>'
              '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/>'
              '</w:pPr></w:pPrDefault></w:docDefaults>'
              + "".join(f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/>'
                        f'<w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240"/></w:pPr>'
                        f'<w:rPr><w:b/><w:sz w:val="{36 - 4 * n}"/></w:rPr></w:style>' for n in (1, 2, 3))
              + '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:pPr><w:ind w:left="567"/>'
                '</w:pPr><w:rPr><w:i/></w:rPr></w:style></w:styles>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.'
                   'org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats'
                   '-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override '
                   'PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                   'wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   + "".join(rels) + '</Relationships>')
        z.writestr("word/styles.xml", styles)
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W} {R}>'
                   f'<w:body>{"".join(paras)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" '
                   'w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>')


def main(a):
    if not a or a[0].startswith("-"):
        sys.exit(__doc__)
    src = Path(a[0])
    md = src.read_text(encoding="utf-8")
    if "--no-notes" in a:
        md, dropped = deliverable(md)
        print(f"Left out the notes ({dropped} words)." if dropped else
              "No notes marker found (a \"---\" line, then a \"## Notes\" heading), so nothing was left out.")
    fmt = "pdf" if "--pdf" in a else "html" if "--html" in a else "docx"
    out = free_name(src.with_suffix(f".{fmt}"))
    title = next((t for k, _, t in blocks(md) if k == "h"), src.stem)
    pandoc = shutil.which("pandoc")
    if fmt == "html":
        out.write_text(to_html(md, title), encoding="utf-8")
    elif pandoc and fmt == "docx":
        subprocess.run([pandoc, "-f", "markdown", "-o", str(out)], input=md, text=True, check=True)
    elif fmt == "docx":
        to_docx(md, out)
    else:
        if pandoc and subprocess.run([pandoc, "-f", "markdown", "-o", str(out)], input=md, text=True).returncode == 0:
            pass
        elif office := (shutil.which("soffice") or shutil.which("libreoffice")):
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d) / f"{out.stem}.docx"
                to_docx(md, tmp)
                subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir", d, str(tmp)],
                               capture_output=True, check=True)
                shutil.move(str(Path(d) / f"{out.stem}.pdf"), out)
        else:
            out = free_name(src.with_suffix(".html"))
            out.write_text(to_html(md, title), encoding="utf-8")
            return print(f"No PDF converter here (pandoc or LibreOffice), so I wrote {out}: open it in a browser "
                         "and print it to PDF.")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
