---
name: fetch-sources
description: How to download a PDF or a code/data release from the web and run analysis code in the sandbox. Use when a task needs a paper that isn't in the corpus, a released dataset, or any computation.
---

# Fetching sources and running analysis

Hardened mode only. `<workspace>` below is this workspace's absolute path, from `my/profile.md` ("Machine"); these commands must be typed exactly, with that path, or the preflight hook refuses them. Give the same path to the `analyst` when you brief it.

## PDFs
    python3 <workspace>/scripts/fetch_pdf.py "<https-url>" <name>
(name = letters, digits, - or _). Only allowlisted research sites work. Some publishers turn the script away even for open-access papers: Springer sends it to a login page and Taylor & Francis returns 403. When a download is refused or fails, look for an open-access copy before giving up. WebFetch `https://api.semanticscholar.org/graph/v1/paper/DOI:<doi>?fields=externalIds,openAccessPdf`. If it lists an `ArXiv` id, fetch `https://arxiv.org/pdf/<id>`. Otherwise try the `openAccessPdf` URL, but only if its host isn't the one that just refused. PMC and Europe PMC PDF links don't work with the script either (a bot check and a 403). A preprint can differ from the published version, so cite which one you read. If no copy downloads, add the URL to `workspace/requests/pdfs.md` and tell the operator. Extract text inside the sandbox with pypdf (the inbox is mounted read-only at /data/inbox), via the `analyst` agent or the analysis runner below. Never move files into `workspace/corpus/`; the operator does that.

## Code and data releases (main session only)
    python3 <workspace>/scripts/fetch_repo.py "<https-url>" <name>
Accepted URLs name one fixed version:
- GitHub: `https://github.com/<owner>/<repo>/archive/<full 40-char commit sha>.tar.gz`
- Zenodo: `https://zenodo.org/records/<id>/files/<file>`
- Hugging Face datasets: `https://huggingface.co/datasets/<org>/<name>/resolve/<40-char sha>/<path>`
Branch and tag names are refused. If refused or unsupported, add the URL to `workspace/requests/repos.md` and tell the operator. Releases land read-only in `workspace/repos/<name>/` with provenance in `<name>.fetch.json`. Cite by name, URL and commit sha. Their code runs only inside the analysis container, never on the host. The container has no network, so it can re-analyze released data but can't re-run model evals; say so rather than working around it.

## Running analysis code
Scripts live in `workspace/analysis/<run-id>/` and run only through the sandbox:

    python3 <workspace>/scripts/run_analysis.py <run-id> workspace/analysis/<run-id>/<script>.py

It uses the pinned image with no network, a read-only root and capped resources. Exit 2 means refusal. Inside the container: /data/corpus, /data/assets, /data/inbox, /data/repos (read-only), /work (read-write), /tmp. Never run analysis scripts with bare `python3`. Rebuilding the image is the operator's job.

Tests: `python3 -m unittest -v tests/test_run_analysis.py`
