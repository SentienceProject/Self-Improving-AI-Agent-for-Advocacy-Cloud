"""Download one PDF from an allowlisted research site into workspace/inbox/.

    python3 <repo>/scripts/fetch_pdf.py "<https-url>" <name>

Saves workspace/inbox/<name>.pdf. This runs OUTSIDE the Bash sandbox (it is an
excluded command, locked to that exact form by the preflight gate), so it is
deliberately small and strict:

  * https only, and only to hosts in ALLOWED_HOSTS (exact match, no
    subdomains) or under a government suffix in GOV_SUFFIXES (registries
    there are restricted to public bodies, whose server logs outsiders
    can't read). Every redirect hop must pass the same check.
  * Ignores proxy settings from the environment and verifies TLS.
  * Never parses the PDF. It only checks the first bytes are "%PDF-".
    Conversion (pdftotext) happens later, inside the sandbox.
  * Size and time limits; never overwrites an existing file; refuses if
    workspace/ or workspace/inbox/ is a symlink.
  * Writes workspace/logs/fetch-<time>-<n>.json for every attempt, including
    refusals.

Exit code: 0 saved, 2 refused, 1 download failed.

Residual risk, accepted: a URL can carry data in its path or query string, so
this is a narrow outbound channel to the allowlisted hosts. Reading it would
require access to one of those sites' server logs. Keep the list to
established publishers and preprint servers; never add sites where users host
their own content or can see access logs.

To change ALLOWED_HOSTS or any limit: lift the freeze, edit, review, run
tests/test_fetch_pdf.py, update integrity.json, re-freeze.
"""

import hashlib
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Government domains: only public bodies can register under these. Never add open TLDs (.gal, .eu, .org ...).
GOV_SUFFIXES = (
    "gov", "mil", "gov.uk", "gov.au", "gc.ca", "canada.ca", "govt.nz", "europa.eu", "gob.es", "boe.es", "xunta.gal",
    "gob.mx", "gob.pe", "gob.ar", "gob.cl", "gov.co", "gov.br", "gouv.fr", "bund.de", "admin.ch", "gv.at",
    "gov.it", "gov.ie", "overheid.nl", "go.jp", "gov.in", "gov.za",
)

ALLOWED_HOSTS = frozenset({
    "arxiv.org",
    "export.arxiv.org",
    "www.medrxiv.org",
    "www.biorxiv.org",
    "pmc.ncbi.nlm.nih.gov",
    "www.ncbi.nlm.nih.gov",
    "europepmc.org",
    "link.springer.com",
    "onlinelibrary.wiley.com",
    "www.tandfonline.com",
    "www.pnas.org",
    "www.cambridge.org",
    "nap.nationalacademies.org",
    "www.rand.org",
    "www.sentienceinstitute.org",
    "eleosai.org",
    "www.anthropic.com",
    "en.wikipedia.org",
    "eleosai.org",
    "leginfo.legislature.ca.gov",
    "nonhumanminds.org",
    "newsletter.ai-frontiers.org",
    "docs.legis.wisconsin.gov",
    "ohiohouse.gov",
    "effectivehealthcare.ahrq.gov",
    "forum.effectivealtruism.org",
    "www.sentienceinstitute.org",
    "legislature.idaho.gov",
    "cjai.co.uk",
    "search-prod.lis.state.oh.us",
    "www.nonhumanrights.org",
    "ai-frontiers.org",
    "harderproblem.fund",
    "link.springer.com",
    "www.law.nyu.edu",
    "www.acluidaho.org",
    "www.aclu-tn.org",
    "www.earthlawcenter.org",
    "www.researchgate.net",
    "law.justia.com",
    "cognition.ai",
    "www.rand.org",
    "journal.sjdm.org",
    "onlinelibrary.wiley.com",
    "www.tandfonline.com",
    "pubmed.ncbi.nlm.nih.gov",
    "www.sas.upenn.edu",
    "www.pnas.org",
    "www.cambridge.org", 
})

MAX_BYTES = 50 * 1024 * 1024
TIMEOUT_S = 60
MAX_REDIRECTS = 5
MAX_URL_LEN = 500
USER_AGENT = "ai-welfare-research/1.0 (fetch_pdf.py; single PDF downloads)"

# Characters bash leaves alone inside double quotes, minus spaces and '!'.
URL_CHARS_RE = re.compile(r"[A-Za-z0-9._~:/?#\[\]@&'()*+,;=%-]+")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2


class Refusal(Exception):
    pass


# --- validation ------------------------------------------------------------

def check_url(url: str) -> str:
    """Return the lowercased host, or raise Refusal."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LEN:
        raise Refusal(f"URL missing or longer than {MAX_URL_LEN} characters")
    if not URL_CHARS_RE.fullmatch(url):
        raise Refusal("URL contains characters that are not allowed")
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise Refusal("only https URLs are allowed")
    if "@" in p.netloc:
        raise Refusal("URLs with a user name or password are not allowed")
    if p.port is not None and p.port != 443:
        raise Refusal("only the default https port is allowed")
    host = (p.hostname or "").lower()
    if host not in ALLOWED_HOSTS and not any(host == s or host.endswith("." + s) for s in GOV_SUFFIXES):
        raise Refusal(f"host {host!r} is not on the allowlist (research sites and government domains only)")
    return host


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise Refusal("name must be letters, digits, '-' or '_' (no dots or "
                      "slashes, at most 81 characters)")
    return name


def open_dir_beneath(base: Path, parts) -> int:
    """Open base/parts... as a directory fd, refusing any symlink on the way."""
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            except FileNotFoundError:
                raise Refusal(f"{part}/ is missing; create workspace/inbox/ first")
            except OSError:
                raise Refusal(f"{part}/ is a symlink or not a directory")
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


# --- network ---------------------------------------------------------------

class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self):
        super().__init__()
        self.hops = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        check_url(newurl)  # raises Refusal for a bad hop
        if len(self.hops) >= MAX_REDIRECTS:
            raise Refusal(f"more than {MAX_REDIRECTS} redirects")
        self.hops.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener():
    redirects = _CheckedRedirects()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),  # ignore proxy env vars
        urllib.request.HTTPSHandler(),     # default context verifies TLS
        redirects)
    return opener, redirects


# --- saving ----------------------------------------------------------------

def save_pdf(stream, inbox_fd: int, name: str) -> tuple:
    """Stream into <name>.pdf under inbox_fd. Returns (bytes, sha256)."""
    final, part = f"{name}.pdf", f".{name}.pdf.part"
    try:
        os.stat(final, dir_fd=inbox_fd, follow_symlinks=False)
        raise Refusal(f"workspace/inbox/{final} already exists; pick another name")
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(part, flags, 0o644, dir_fd=inbox_fd)
    except FileExistsError:
        raise Refusal(f"a download of {final} seems to be in progress")
    h, total, first = hashlib.sha256(), 0, b""
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                if len(first) < 5:
                    first += chunk[:5 - len(first)]
                    if len(first) == 5 and first != b"%PDF-":
                        raise Refusal("the response is not a PDF")
                total += len(chunk)
                if total > MAX_BYTES:
                    raise Refusal(f"the PDF is larger than {MAX_BYTES // 2**20} MB")
                h.update(chunk)
                f.write(chunk)
        if first != b"%PDF-":
            raise Refusal("the response is not a PDF")
        os.link(part, final, src_dir_fd=inbox_fd, dst_dir_fd=inbox_fd)
    finally:
        try:
            os.unlink(part, dir_fd=inbox_fd)
        except FileNotFoundError:
            pass
    return total, h.hexdigest()


# --- logging ---------------------------------------------------------------

def write_log(record: dict) -> Path:
    logs = ROOT / "workspace" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n = 1
    while True:
        path = logs / f"fetch-{stamp}-{n}.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o644)
            break
        except FileExistsError:
            n += 1
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    return path


# --- main ------------------------------------------------------------------

def run(url: str, name: str, opener_factory=build_opener) -> tuple:
    """Returns (exit code, log record)."""
    rec = {"event": "fetch_pdf", "url": url, "name": name,
           "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    inbox_fd = None
    try:
        check_url(url)
        check_name(name)
        inbox_fd = open_dir_beneath(ROOT, ["workspace", "inbox"])
        opener, redirects = opener_factory()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(req, timeout=TIMEOUT_S) as resp:
                rec["final_url"] = resp.geturl()
                rec["redirects"] = list(redirects.hops)
                rec["http_status"] = getattr(resp, "status", None)
                check_url(resp.geturl())
                size, digest = save_pdf(resp, inbox_fd, name)
        except Refusal:
            raise
        except (urllib.error.URLError, OSError, ValueError) as e:
            rec.update(status="failed", reason=repr(e))
            return EXIT_FAILED, rec
        rec.update(status="saved", saved=f"workspace/inbox/{name}.pdf",
                   bytes=size, sha256=digest)
        return EXIT_OK, rec
    except Refusal as e:
        rec.update(status="refused", reason=str(e))
        return EXIT_REFUSED, rec
    finally:
        if inbox_fd is not None:
            os.close(inbox_fd)


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return EXIT_REFUSED
    code, rec = run(argv[0], argv[1])
    log = write_log(rec)
    if code == EXIT_OK:
        print(f"Saved {rec['saved']} ({rec['bytes']} bytes, sha256 "
              f"{rec['sha256'][:12]}...). Convert it inside the sandbox with "
              f"pdftotext. Log: {log}")
    else:
        print(f"{rec['status'].upper()}: {rec['reason']}\nLog: {log}",
              file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
