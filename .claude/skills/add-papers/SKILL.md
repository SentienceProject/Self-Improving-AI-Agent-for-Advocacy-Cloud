---
name: add-papers
description: Find good open-access papers on a topic and add the ones the operator picks to workspace/corpus/ as text. Use when the operator asks to add a paper (by title, DOI or link), to build or grow the corpus, or to find the best papers on a question for later work.
---

# Adding papers to the corpus

The corpus is what researchers and the verifier can quote exactly, so what goes in should be worth relying on, and
the operator decides what goes in.

1. **Find candidates** (skip if the operator named the papers). Search the literature, not the news: scholarly
   search and APIs (`api.openalex.org/works?search=...`, `api.semanticscholar.org/graph/v1/paper/search?query=...`,
   arXiv, PubMed Central, Europe PMC, PhilPapers). Prefer reviews, much-cited primary work, and recent papers that
   change the picture. Include at least one strong paper that cuts against the operator's likely view.
2. **Check each is free to read legally:** an open-access version from the publisher, arXiv/bioRxiv/PsyArXiv and
   similar preprint servers, PubMed Central, a university repository, or the author's own page. **Never** use shadow
   libraries or paywall workarounds. If only a paywalled copy exists, list it as "paywalled" and move on.
3. **Show the operator a short list** before downloading anything: for each, title, authors, year, one line on why
   it's worth having, and the free link (prefer an HTML full-text link, e.g. `https://arxiv.org/html/<id>` or the
   PMC article page, which converts more cleanly than a PDF). Ask which to add.
4. **Add each one they pick:**

       python3 scripts/add_paper.py "<https-url>" <short-name> --title "<title>"

   `<short-name>` like `birch-2021-decapod-sentience` (letters, digits, - or _). The script writes
   `workspace/corpus/<short-name>.md` with the source and date on top. If it says it got very few words, the link
   was a landing page: find the full-text link and try again. If a site is refused, it isn't on the sandbox's list
   of research sites: tell the operator, who can add the paper by hand (save it as text in `workspace/corpus/`) or
   allow that site.
5. **Spot-check** the start of each new file: is it the paper (not a cookie banner or an abstract page)? Then tell
   the operator what was added, in a line each.
6. If `workspace/corpus/SOURCES.md` exists, suggest a line for each new paper (title, authors, link); the operator
   may want to keep that list.

The corpus is gitignored and stays on this computer. If the operator shares the workspace, the papers stay out of
it; that's deliberate (copyright).
