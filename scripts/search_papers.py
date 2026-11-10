#!/usr/bin/env python3
"""Search scholarly indexes for papers and print a short list (step 1 of /add-papers).

    python3 scripts/search_papers.py "<query>" [--source openalex|s2|arxiv|europepmc|all] [--n 10]
                                               [--from-year 2018] [--sort relevance|cited]

Prints, for each hit: title, first authors, year, venue, citation count, DOI, whether it's open access and the
best free link (an arXiv HTML or PMC page when there is one), and the start of the abstract. Nothing is saved.
- openalex (default): matches title and abstract only (not full text, which buries the topic in loosely related
  papers), so put the topic's key terms in the query.
- s2: Semantic Scholar. It rate-limits anonymous use; if it says so, wait a minute or use another source.
- arxiv: preprints; every hit is free to read.
- europepmc: biology and medicine, including PubMed Central full texts.
- all: each of the above, with duplicates (same DOI or title) merged.
Runs inside Claude Code's sandbox, which lets it reach only these research sites. Uses no AI and sends only the
query.

Exit codes: 0 results printed (or none found), 1 every source failed, 2 bad arguments.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "ai-welfare-workspace-search_papers/1.0 (research use)"
TIMEOUT = 30
MAX_QUERY = 300
ABSTRACT_CHARS = 300


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, application/xml"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(5_000_000).decode("utf-8", "replace")


def clean(s, limit=None):
    s = re.sub(r"<[^>]+>", "", s or "")          # JATS/HTML tags in titles and abstracts
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + " ..."
    return s


def authors_line(names):
    names = [n for n in names if n]
    if not names:
        return "?"
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")


def bare_doi(doi):
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi or "", flags=re.I).lower() or None


def arxiv_html(url_or_id):
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", url_or_id or "")
    return f"https://arxiv.org/html/{m.group(1)}" if m else None


# --- sources -------------------------------------------------------------------------------------------------

def openalex(q, n, from_year, sort):
    filt = [f"title_and_abstract.search:{q.replace(',', ' ')}"]
    if from_year:
        filt.append(f"from_publication_date:{from_year}-01-01")
    params = {
        "filter": ",".join(filt),
        "per-page": n,
        "select": "title,doi,publication_year,authorships,cited_by_count,open_access,primary_location,"
                  "best_oa_location,abstract_inverted_index,type",
    }
    if sort == "cited":
        params["sort"] = "cited_by_count:desc"
    data = json.loads(get("https://api.openalex.org/works?" + urllib.parse.urlencode(params)))
    out = []
    for w in data.get("results", []):
        inv = w.get("abstract_inverted_index") or {}
        words = sorted((p, word) for word, ps in inv.items() for p in ps)
        oa = w.get("open_access") or {}
        best = w.get("best_oa_location") or {}
        link = best.get("landing_page_url") or best.get("pdf_url") or oa.get("oa_url")
        link = arxiv_html(link) if link and "arxiv.org" in link else link
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        out.append({
            "title": w.get("title"), "year": w.get("publication_year"),
            "authors": [((a.get("author") or {}).get("display_name")) for a in (w.get("authorships") or [])[:4]],
            "n_authors": len(w.get("authorships") or []),
            "venue": venue, "cited": w.get("cited_by_count"), "doi": bare_doi(w.get("doi")),
            "oa": oa.get("is_oa"), "link": link, "type": w.get("type"),
            "abstract": " ".join(word for _, word in words), "source": "openalex",
        })
    return out


def s2(q, n, from_year, sort):
    params = {"query": q, "limit": n,
              "fields": "title,year,authors,venue,citationCount,externalIds,openAccessPdf,abstract,url"}
    if from_year:
        params["year"] = f"{from_year}-"
    data = json.loads(get("https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)))
    out = []
    for p in data.get("data") or []:
        ids = p.get("externalIds") or {}
        pdf = (p.get("openAccessPdf") or {}).get("url")
        link = arxiv_html(ids["ArXiv"]) if ids.get("ArXiv") else (
            f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{ids['PubMedCentral']}/" if ids.get("PubMedCentral") else pdf)
        out.append({
            "title": p.get("title"), "year": p.get("year"),
            "authors": [a.get("name") for a in (p.get("authors") or [])[:4]], "n_authors": len(p.get("authors") or []),
            "venue": p.get("venue"), "cited": p.get("citationCount"), "doi": bare_doi(ids.get("DOI")),
            "oa": bool(link), "link": link or p.get("url"), "abstract": p.get("abstract"), "source": "s2",
        })
    if sort == "cited":
        out.sort(key=lambda r: -(r["cited"] or 0))
    return out


def arxiv(q, n, from_year, sort):
    terms = re.findall(r'"[^"]+"|\S+', q)
    sq = " AND ".join(f"all:{t}" for t in terms)
    params = {"search_query": sq, "start": 0, "max_results": n * (3 if from_year else 1), "sortBy": "relevance"}
    root = ET.fromstring(get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)))
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    out = []
    for e in root.findall("a:entry", ns):
        year = int((e.findtext("a:published", "", ns) or "0")[:4] or 0)
        if from_year and year < from_year:
            continue
        aid = re.search(r"(\d{4}\.\d{4,5})", e.findtext("a:id", "", ns) or "")
        doi = e.findtext("x:doi", None, ns) or (f"10.48550/arxiv.{aid.group(1)}" if aid else None)
        names = [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)]
        out.append({
            "title": e.findtext("a:title", "", ns), "year": year, "authors": names[:4], "n_authors": len(names),
            "venue": "arXiv", "cited": None, "doi": bare_doi(doi), "oa": True,
            "link": arxiv_html(e.findtext("a:id", "", ns)), "abstract": e.findtext("a:summary", "", ns),
            "source": "arxiv",
        })
    return out[:n]


def europepmc(q, n, from_year, sort):
    query = q + (f" AND PUB_YEAR:[{from_year} TO 3000]" if from_year else "")
    params = {"query": query, "format": "json", "resultType": "core", "pageSize": n}
    if sort == "cited":
        params["sort"] = "CITED desc"
    data = json.loads(get("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params)))
    out = []
    for r in (data.get("resultList") or {}).get("result", []):
        pmcid = r.get("pmcid")
        names = [a.strip() for a in (r.get("authorString") or "").rstrip(".").split(",")]
        out.append({
            "title": r.get("title"), "year": r.get("pubYear"), "authors": names[:4], "n_authors": len(names),
            "venue": r.get("journalTitle") or (r.get("journalInfo") or {}).get("journal", {}).get("title"),
            "cited": r.get("citedByCount"), "doi": bare_doi(r.get("doi")), "oa": r.get("isOpenAccess") == "Y" or bool(pmcid),
            "link": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else (
                f"https://doi.org/{r['doi']}" if r.get("doi") else None),
            "abstract": r.get("abstractText"), "source": "europepmc",
        })
    return out


SOURCES = {"openalex": openalex, "s2": s2, "arxiv": arxiv, "europepmc": europepmc}


# --- output --------------------------------------------------------------------------------------------------

def key(r):
    return r["doi"] or re.sub(r"[^a-z0-9]", "", (r["title"] or "").lower())[:80]


def fmt(i, r):
    names = authors_line(r["authors"])
    if r.get("n_authors", 0) > 3 and not names.endswith("et al."):
        names += " et al."
    meta = [str(r["year"] or "?")]
    if r.get("venue"):
        meta.append(clean(r["venue"], 60))
    if r.get("cited") is not None:
        meta.append(f"cited {r['cited']}")
    if r.get("type") and r["type"] not in ("article", "preprint"):
        meta.append(r["type"])
    lines = [f"{i}. {clean(r['title'], 200)}", f"   {names} | {' | '.join(meta)}"]
    if r["doi"]:
        lines.append(f"   doi: {r['doi']}")
    access = "open access" if r["oa"] else "no free copy found (may be paywalled)"
    lines.append(f"   {access}: {r['link']}" if r.get("link") else f"   {access}")
    if r.get("abstract"):
        lines.append(f"   {clean(r['abstract'], ABSTRACT_CHARS)}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("query")
    ap.add_argument("--source", choices=[*SOURCES, "all"], default="openalex")
    ap.add_argument("--n", type=int, default=10, help="results per source, 1-25 (default 10)")
    ap.add_argument("--from-year", type=int)
    ap.add_argument("--sort", choices=["relevance", "cited"], default="relevance")
    a = ap.parse_args(argv)
    q = a.query.strip()
    if not q or len(q) > MAX_QUERY:
        print(f"The query must be 1-{MAX_QUERY} characters.", file=sys.stderr)
        return 2
    if not 1 <= a.n <= 25:
        print("--n must be between 1 and 25.", file=sys.stderr)
        return 2
    if a.from_year is not None and not 1900 <= a.from_year <= 2100:
        print("--from-year must be a year like 2018.", file=sys.stderr)
        return 2

    names = list(SOURCES) if a.source == "all" else [a.source]
    seen, failures, printed = set(), 0, 0
    for name in names:
        try:
            rows = SOURCES[name](q, a.n, a.from_year, a.sort)
        except urllib.error.HTTPError as e:
            failures += 1
            hint = " (rate limit: wait a minute or try another --source)" if e.code == 429 else ""
            print(f"[{name}] failed: HTTP {e.code}{hint}\n")
            continue
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError) as e:
            failures += 1
            print(f"[{name}] failed: {e}. If the site was refused, it isn't on the sandbox's list of research "
                  f"sites.\n")
            continue
        rows = [r for r in rows if r.get("title") and key(r) not in seen]
        seen.update(key(r) for r in rows)
        print(f"[{name}] {len(rows)} result{'s' if len(rows) != 1 else ''}" + (" (new ones only)" if printed else ""))
        for i, r in enumerate(rows, 1):
            print(fmt(i, r))
        print()
        printed += len(rows)
    return 1 if failures == len(names) else 0


if __name__ == "__main__":
    sys.exit(main())
