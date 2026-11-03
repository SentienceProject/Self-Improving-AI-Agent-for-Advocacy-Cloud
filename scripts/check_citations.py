#!/usr/bin/env python3
"""Check a draft's citations mechanically: do its arXiv IDs and DOIs exist and match what the draft says, and are
its quotes verbatim in the corpus? No model is involved.

    python3 scripts/check_citations.py <draft.md>             the table, in Markdown
    python3 scripts/check_citations.py <draft.md> --offline   quotes only (no network)
    python3 scripts/check_citations.py <draft.md> --json      the same, as JSON

What it does:
  1. finds arXiv IDs written with a prefix (arXiv:2401.12345, arXiv:hep-th/9901001, arxiv.org/abs/... links,
     10.48550/arXiv.* DOIs; a bare number like 2401.12345 is too easily something else) and DOIs (10.xxxx/...,
     doi.org/ links);
  2. looks each up in arXiv's and Crossref's public catalogues (no key; export.arxiv.org and api.crossref.org,
     which the sandbox allows), and compares the title, first author and year with the line that cites it;
  3. flags retractions and withdrawals where the catalogue records them (Crossref's records are incomplete, and
     arXiv withdrawals are spotted from the comment field, so this is best-effort);
  4. checks every quoted passage of 8+ words against workspace/corpus/.

It checks identifiers and quotes only, never whether a source supports the claim it's cited for: that's the
verifier's job, and a clean table doesn't mean a draft is verified. Lookups send only the IDs to arXiv and
Crossref; for an unpublished draft, that reveals what you're reading. Set CROSSREF_MAILTO to an address of yours
to use Crossref's "polite" pool (optional).

Exit code: 0 nothing flagged, 1 something to look at, 2 a lookup failed (so the table is incomplete). Quotes from
sources that aren't in the corpus can't be checked here; the summary line counts them, and they're not "clean".
"""
import difflib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "workspace" / "corpus"
UA = "welfare-workspace-citation-check/1.0"
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

NEW_ARXIV = r"\d{4}\.\d{4,5}(?:v\d+)?"
OLD_ARXIV = r"[a-z][a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
ARXIV_RE = re.compile(rf"(?:arXiv[:\s]*|arxiv\.org/(?:abs|pdf)/|10\.48550/arXiv\.)({NEW_ARXIV}|{OLD_ARXIV})", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]\[]+)", re.I)
QUOTE_RE = re.compile(r"[\"“]([^\"“”]{20,}?)[\"”]")

VERDICT_HELP = {
    "matches catalogue": "",
    "check": "Compare the citation with the catalogue entry; often a formatting difference, sometimes a wrong paper.",
    "not found in catalogue": "Could be a typo in the ID rather than an invented source: look the paper up by title.",
    "retraction or withdrawal": "Read the notice before relying on this paper.",
    "couldn't check": "The lookup failed (network?). Re-run later; nothing was checked for this one.",
    "found in corpus": "",
    "not verbatim": "The quote isn't word for word in the file it's attributed to: fix it from the source.",
    "not checked": "The source isn't in the corpus, so the quote couldn't be checked here.",
}
FLAGGED = {"check", "not found in catalogue", "retraction or withdrawal", "not verbatim"}


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


ITEM_START = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[\d+\])\s")


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


# ---------------------------------------------------------------- lookups
def fetch(url):
    headers = {"User-Agent": UA + (f" (mailto:{os.environ['CROSSREF_MAILTO']})"
                                   if os.environ.get("CROSSREF_MAILTO") else "")}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return r.read()


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


# ---------------------------------------------------------------- quotes
def corpus_texts():
    if not CORPUS.exists():
        return {}
    return {p.name: norm(p.read_text(encoding="utf-8", errors="replace"))
            for p in sorted(CORPUS.iterdir()) if p.suffix in (".md", ".txt") and p.is_file()}


def check_quotes(text, corpus):
    """[(quote, verdict, file)] for quotes of 8+ words."""
    out = []
    for m in QUOTE_RE.finditer(text):
        q = m.group(1)
        if len(words(q)) < 8:
            continue
        pieces = [norm(p).strip(" .,;:") for p in re.split(r"\.\.\.|…|\[\.\.\.\]", q)]
        pieces = [p for p in pieces if len(p.split()) >= 4] or [norm(q)]
        hit = next((name for name, body in corpus.items() if all(p in body for p in pieces)), None)
        if hit:
            out.append((q, "found in corpus", hit))
            continue
        end = text.find("\n\n", m.end())
        para = text[text.rfind("\n\n", 0, m.start()) + 1:end if end != -1 else len(text)]
        named = next((name for name in corpus if Path(name).stem.lower() in para.lower()), None)
        out.append((q, "not verbatim", named) if named else (q, "not checked", None))
    return out


# ---------------------------------------------------------------- report
def check(path, offline=False):
    text = Path(path).read_text(encoding="utf-8")
    rows, network_failed = [], False
    ids = [] if offline else find_ids(text)
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
            rows.append({"id": f"{kind}:{ident}", "verdict": "couldn't check", "catalogue": "", "note": str(rec)[:80]})
            continue
        verdict, note = judge(rec, line, kind)
        rows.append({"id": f"{kind}:{ident}", "verdict": verdict, "note": note,
                     "catalogue": "" if rec is None else f"{rec['title'][:90]} ({(rec.get('authors') or ['?'])[0]}, "
                                                         f"{rec.get('year') or '?'})"})
    quotes = [{"quote": q, "verdict": v, "file": f} for q, v, f in check_quotes(text, corpus_texts())]
    return {"draft": str(path), "ids": rows, "quotes": quotes, "network_failed": network_failed,
            "offline": offline}


def markdown(r):
    L = [f"# Citation check: {r['draft']}", "",
         "**This checks identifiers and corpus quotes only. It does not check that a source supports the claim "
         "it's cited for.** A clean table doesn't mean the draft is verified.", ""]
    if r["offline"]:
        L += ["(Offline: arXiv IDs and DOIs weren't looked up.)", ""]
    elif r["network_failed"]:
        L += ["**Some lookups failed, so this table is incomplete.** Re-run when the network is back.", ""]
    if r["ids"]:
        L += ["| id | verdict | catalogue says | note |", "|---|---|---|---|"]
        L += [f"| {x['id']} | {x['verdict']} | {x['catalogue'].replace('|', '/')} | {x['note'].replace('|', '/')} |"
              for x in r["ids"]] + [""]
    elif not r["offline"]:
        L += ["No arXiv IDs or DOIs found.", ""]
    if r["quotes"]:
        L += ["| quote (start) | verdict | corpus file |", "|---|---|---|"]
        L += [f"| \"{' '.join(x['quote'].split()[:10])}…\" | {x['verdict']} | {x['file'] or ''} |"
              for x in r["quotes"]] + [""]
    unchecked = sum(x["verdict"] == "not checked" for x in r["quotes"])
    if unchecked:
        L += [f"**{unchecked} quote(s) couldn't be checked** (their sources aren't in the corpus): check those by "
              "hand or with the verifier.", ""]
    used = sorted({x["verdict"] for x in r["ids"] + r["quotes"]} - {"matches catalogue", "found in corpus"})
    if used:
        L += ["What to do:", ""] + [f"- **{v}**: {VERDICT_HELP[v]}" for v in used] + [""]
    return "\n".join(L)


def main(a):
    if not a or a[0].startswith("-"):
        sys.exit(__doc__)
    r = check(a[0], offline="--offline" in a)
    print(json.dumps(r, indent=1) if "--json" in a else markdown(r))
    if r["network_failed"]:
        sys.exit(2)
    sys.exit(1 if any(x["verdict"] in FLAGGED for x in r["ids"] + r["quotes"]) else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
