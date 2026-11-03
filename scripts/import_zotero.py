#!/usr/bin/env python3
"""Add papers from your Zotero library to the corpus.

In Zotero: right-click a collection → Export Collection… → format "BibTeX" (or "Better BibTeX"), tick "Export
Files", and save the folder somewhere. Then:

    python3 scripts/import_zotero.py <exported folder>            add every item that has a PDF
    python3 scripts/import_zotero.py <exported folder> --dry-run  list what would be added

Each paper becomes workspace/corpus/<citekey>.md, with a header (title, authors, year, DOI, "from Zotero") and the
PDF's text. Items without a PDF are listed at the end (`/add-papers` can look for open-access copies). Existing
corpus files are never overwritten. Needs pdftotext (poppler) or pypdf, like add_paper.py. Nothing leaves your
computer: the corpus is gitignored.
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import add_paper   # noqa: E402

CORPUS = add_paper.CORPUS
ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.S)


def fields(body):
    """{name: value} for a BibTeX entry body (values in braces or quotes; nested braces kept simple)."""
    out, i = {}, 0
    for m in re.finditer(r"(\w+)\s*=\s*", body):
        if m.start() < i:
            continue
        j = m.end()
        if j < len(body) and body[j] == "{":
            depth, k = 0, j
            while k < len(body):
                depth += {"{": 1, "}": -1}.get(body[k], 0)
                if depth == 0:
                    break
                k += 1
            value, i = body[j + 1:k], k + 1
        elif j < len(body) and body[j] == '"':
            k = body.index('"', j + 1)
            value, i = body[j + 1:k], k + 1
        else:
            k = body.find(",", j)
            value, i = body[j:k if k != -1 else None], (k if k != -1 else len(body))
        out[m.group(1).lower()] = re.sub(r"\s+", " ", value.replace("{", "").replace("}", "")).strip()
    return out


def pdf_paths(file_field, root):
    """Zotero's file field: 'Title:files/12/a.pdf:application/pdf;...' (Better BibTeX: plain paths)."""
    out = []
    for part in re.split(r";(?=[^;]*\.pdf)", file_field or ""):
        m = re.search(r"([^:;]*\.pdf)", part, re.I)
        if m:
            p = (root / m.group(1).replace("\\:", ":")).resolve()
            if p.is_file() and root.resolve() in p.parents:
                out.append(p)
    return out


def safe_name(key):
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", key).strip("-")[:80] or "zotero-item"
    return name if add_paper.NAME_RE.fullmatch(name) else f"z-{name}"[:80]


def main(a):
    if not a or a[0].startswith("-"):
        sys.exit(__doc__)
    root = Path(a[0]).expanduser()
    bibs = sorted(root.glob("*.bib"))
    if not bibs:
        sys.exit(f"No .bib file in {root}. Export from Zotero as BibTeX with \"Export Files\" ticked.")
    dry = "--dry-run" in a
    added, no_pdf, skipped, failed = [], [], [], []
    for bib in bibs:
        for m in ENTRY.finditer(bib.read_text(encoding="utf-8", errors="replace")):
            key, f = m.group(2), fields(m.group(3))
            title = f.get("title", key)
            pdfs = pdf_paths(f.get("file", ""), root)
            if not pdfs:
                no_pdf.append(title)
                continue
            out = CORPUS / f"{safe_name(key)}.md"
            if out.exists():
                skipped.append(out.name)
                continue
            if dry:
                added.append(out.name)
                continue
            text = add_paper.pdf_text(pdfs[0].read_bytes())
            if not text or len(text.split()) < 300:
                failed.append(f"{title} ({'no PDF converter' if text is None else 'too little text: a scan?'})")
                continue
            CORPUS.mkdir(parents=True, exist_ok=True)
            header = (f"# {title}\n\n- Authors: {f.get('author', '?')}\n- Year: {f.get('year', '?')}\n"
                      + (f"- DOI: {f['doi']}\n" if f.get("doi") else "")
                      + (f"- URL: {f['url']}\n" if f.get("url") else "")
                      + f"- Source: your Zotero library ({pdfs[0].name})\n"
                      f"- Imported: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC (PDF, converted automatically)\n"
                      "- Check quotes against the original: conversion can garble tables, equations and footnotes."
                      "\n\n---\n\n")
            out.write_text(header + text + "\n", encoding="utf-8")
            added.append(out.name)
    print(f"{'Would add' if dry else 'Added'} {len(added)} paper(s) to workspace/corpus/"
          + (f": {', '.join(added[:10])}{' ...' if len(added) > 10 else ''}" if added else "."))
    if skipped:
        print(f"Already in the corpus (left as they are): {len(skipped)}")
    if failed:
        print("Couldn't convert: " + "; ".join(failed[:10]))
    if no_pdf:
        print(f"No PDF attached ({len(no_pdf)}): " + "; ".join(no_pdf[:10]) + (" ..." if len(no_pdf) > 10 else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
