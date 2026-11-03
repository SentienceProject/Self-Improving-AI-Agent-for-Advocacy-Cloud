"""Download one pinned code or data release into workspace/repos/<name>/.

    python3 <repo>/scripts/fetch_repo.py "<https-url>" <name>

Accepted URL forms (nothing else). Each one names an immutable version:

  https://github.com/<owner>/<repo>/archive/<40-hex commit sha>.tar.gz
  https://codeload.github.com/<owner>/<repo>/tar.gz/<40-hex commit sha>
  https://zenodo.org/records/<record id>/files/<file>[?download=1]
  https://huggingface.co/datasets/<org>/<dataset>/resolve/<40-hex sha>/<path>

Branch names and tags are refused: they can move, so a re-fetch could give
different code under the same name.

Like fetch_pdf.py, this runs OUTSIDE the Bash sandbox, so it is small and strict:

  * https only. The first URL must match a pattern above. Redirects may go
    only to hosts in REDIRECT_HOSTS. Ignores proxy env vars; verifies TLS.
  * NEVER runs anything it downloads. Archives are read with tarfile/zipfile
    and unpacked by hand: regular files and directories only. Symlinks,
    hard links and devices are skipped and logged. An absolute path or '..'
    refuses the whole archive.
  * Caps on download size, unpacked size (counted from the bytes actually
    written, not the archive's own claims) and file count.
  * Unpacks into a hidden temp folder and renames it into place only on
    success. Never overwrites. Files end up 0444 and folders 0555.
  * A .tar.gz or .zip is unpacked, and one top-level folder (GitHub's
    <repo>-<sha>/) is stripped. Any other file is saved as is.
  * Writes workspace/logs/fetch-repo-<time>-<n>.json for every attempt and,
    on success, workspace/repos/<name>.fetch.json (provenance).

Exit code: 0 saved, 2 refused, 1 download failed.

Residual risk, accepted: GitHub, Zenodo and Hugging Face host content their
users upload. A URL's path is a narrow outbound channel to those sites, and
repo owners can see some aggregate traffic data. The fixed URL shapes keep
the channel small. They don't close it. Downloaded code is untrusted data:
read it or run it only inside the analysis container.

To delete a fetched release: chmod -R u+w workspace/repos/<name> first.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent

SHA = r"[0-9a-f]{40}"
OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
SLUG = r"[A-Za-z0-9._-]{1,100}"
URL_PATTERNS = {
    "github": re.compile(rf"https://github\.com/({OWNER})/({SLUG})/archive/({SHA})\.tar\.gz"),
    "codeload": re.compile(rf"https://codeload\.github\.com/({OWNER})/({SLUG})/tar\.gz/({SHA})"),
    "zenodo": re.compile(r"https://zenodo\.org/records/([0-9]{1,12})/files/([A-Za-z0-9._-]{1,200})(?:\?download=1)?"),
    "huggingface": re.compile(rf"https://huggingface\.co/datasets/({SLUG})/({SLUG})/resolve/({SHA})/([A-Za-z0-9._/-]{{1,300}})"),
}

# Hosts a redirect may land on. The Hugging Face CDN names change from time
# to time. If a fetch is refused for a redirect host, confirm the host
# belongs to Hugging Face before you add it.
REDIRECT_HOSTS = frozenset({
    "github.com",
    "codeload.github.com",
    "zenodo.org",
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs.hf.co",
    "cdn-lfs-us-1.hf.co",
    "cas-bridge.xethub.hf.co",
})

MAX_DOWNLOAD = 500 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024
MAX_FILES = 100_000
TIMEOUT_S = 120
MAX_REDIRECTS = 5
MAX_URL_LEN = 500
MAX_SKIPPED_LOGGED = 50
USER_AGENT = "ai-welfare-research/1.0 (fetch_repo.py; pinned release downloads)"

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2


class Refusal(Exception):
    pass


# --- validation ------------------------------------------------------------

def check_url(url: str) -> str:
    """Return which source the URL is from, or raise Refusal."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LEN:
        raise Refusal(f"URL missing or longer than {MAX_URL_LEN} characters")
    for kind, pat in URL_PATTERNS.items():
        m = pat.fullmatch(url)
        if m:
            if kind == "huggingface":
                parts = m.group(4).split("/")
                if ".." in parts or "." in parts or "" in parts:
                    raise Refusal("bad path in Hugging Face URL")
            return kind
    raise Refusal("URL is not a pinned GitHub commit archive, Zenodo record "
                  "file, or Hugging Face dataset file at a commit sha "
                  "(see the forms at the top of fetch_repo.py)")


def check_redirect(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise Refusal("redirect to a non-https URL")
    if "@" in p.netloc:
        raise Refusal("redirect URL carries a user name or password")
    if p.port is not None and p.port != 443:
        raise Refusal("redirect to a non-default port")
    host = (p.hostname or "").lower()
    if host not in REDIRECT_HOSTS:
        raise Refusal(f"redirect host {host!r} is not allowed")
    return host


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise Refusal("name must be letters, digits, '-' or '_' (no dots or "
                      "slashes, at most 81 characters)")
    return name


def repos_dir() -> Path:
    for p in (ROOT / "workspace", ROOT / "workspace" / "repos"):
        if p.is_symlink():
            raise Refusal(f"{p} is a symlink")
        if not p.is_dir():
            raise Refusal(f"{p} is missing; create workspace/repos/ first")
    return ROOT / "workspace" / "repos"


# --- network ---------------------------------------------------------------

class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self):
        super().__init__()
        self.hops = []   # hosts only: CDN redirect URLs carry signed tokens

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        host = check_redirect(newurl)
        if len(self.hops) >= MAX_REDIRECTS:
            raise Refusal(f"more than {MAX_REDIRECTS} redirects")
        self.hops.append(host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener():
    redirects = _CheckedRedirects()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(),
        redirects)
    return opener, redirects


def download(stream, dest: Path) -> tuple:
    """Stream into dest (created exclusively). Returns (bytes, sha256)."""
    h, total = hashlib.sha256(), 0
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD:
                raise Refusal(f"download is larger than {MAX_DOWNLOAD // 2**20} MB")
            h.update(chunk)
            f.write(chunk)
    return total, h.hexdigest()


# --- unpacking -------------------------------------------------------------

def clean_parts(name: str) -> tuple:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise Refusal(f"archive entry has an unsafe path: {name!r}")
    parts = tuple(p for p in PurePosixPath(name).parts if p != ".")
    if ".." in parts:
        raise Refusal(f"archive entry has '..' in its path: {name!r}")
    return parts


def common_top(paths: list) -> int:
    """1 if every entry sits under the same single top-level folder, else 0."""
    tops = {p[0] for p in paths if p}
    deep = any(len(p) > 1 for p in paths)
    return 1 if len(tops) == 1 and deep else 0


class Unpacker:
    def __init__(self, dest: Path):
        self.dest, self.files, self.bytes, self.skipped = dest, 0, 0, []

    def skip(self, name: str, why: str):
        if len(self.skipped) < MAX_SKIPPED_LOGGED:
            self.skipped.append(f"{name} ({why})")
        else:
            self.skipped_more = getattr(self, "skipped_more", 0) + 1

    def mkdir(self, parts: tuple):
        if parts:
            os.makedirs(self.dest.joinpath(*parts), mode=0o755, exist_ok=True)

    def write(self, parts: tuple, src):
        if not parts:
            return
        self.files += 1
        if self.files > MAX_FILES:
            raise Refusal(f"archive has more than {MAX_FILES} files")
        self.mkdir(parts[:-1])
        path = self.dest.joinpath(*parts)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            raise Refusal(f"archive lists {'/'.join(parts)} twice")
        with os.fdopen(fd, "wb") as f:
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                self.bytes += len(chunk)
                if self.bytes > MAX_UNPACKED:
                    raise Refusal(f"unpacked size exceeds {MAX_UNPACKED // 2**30} GB")
                f.write(chunk)


def unpack_tar(archive: Path, u: Unpacker):
    with tarfile.open(archive, mode="r:gz") as tf:
        members = tf.getmembers()
        paths = [clean_parts(m.name) for m in members]
        strip = common_top(paths)
        for m, parts in zip(members, paths):
            parts = parts[strip:]
            if m.isdir():
                u.mkdir(parts)
            elif m.isfile():
                src = tf.extractfile(m)
                u.write(parts, src)
            else:
                u.skip(m.name, "link or special file")


def unpack_zip(archive: Path, u: Unpacker):
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        paths = [clean_parts(i.filename) for i in infos]
        strip = common_top(paths)
        for info, parts in zip(infos, paths):
            parts = parts[strip:]
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                u.skip(info.filename, "symlink")
            elif info.is_dir():
                u.mkdir(parts)
            elif stat.S_IFMT(mode) and not stat.S_ISREG(mode):  # many zips store no type bits
                u.skip(info.filename, "special file")
            else:
                with zf.open(info) as src:
                    u.write(parts, src)


def freeze(top: Path):
    """Files 0444, folders 0555, deepest first."""
    for dirpath, dirnames, filenames in os.walk(top, topdown=False):
        for f in filenames:
            os.chmod(os.path.join(dirpath, f), 0o444)
        os.chmod(dirpath, 0o555)


def thaw_and_remove(top: Path):
    if not top.exists():
        return
    for dirpath, _, _ in os.walk(top):
        os.chmod(dirpath, 0o755)
    shutil.rmtree(top)


def detect(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] == b"\x1f\x8b":
        return "tar.gz"
    if head == b"PK\x03\x04":
        return "zip"
    return "file"


# --- logging ---------------------------------------------------------------

def write_json_exclusive(path: Path, record: dict):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")


def write_log(record: dict) -> Path:
    logs = ROOT / "workspace" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n = 1
    while True:
        path = logs / f"fetch-repo-{stamp}-{n}.json"
        try:
            write_json_exclusive(path, record)
            return path
        except FileExistsError:
            n += 1


# --- main ------------------------------------------------------------------

def run(url: str, name: str, opener_factory=build_opener) -> tuple:
    """Returns (exit code, log record)."""
    rec = {"event": "fetch_repo", "url": url, "name": name,
           "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    tmp_dl = tmp_dir = None
    try:
        kind = check_url(url)
        check_name(name)
        repos = repos_dir()
        final = repos / name
        if final.exists() or final.is_symlink() or (repos / f"{name}.fetch.json").exists():
            raise Refusal(f"workspace/repos/{name} already exists; pick another name")
        rec["source"] = kind
        dl, part = repos / f".{name}.download", repos / f".{name}.part"
        if dl.exists() or part.exists():
            raise Refusal(f"a fetch of {name} seems to be in progress (or a "
                          f"previous one crashed: remove .{name}.* by hand)")
        tmp_dl, tmp_dir = dl, part   # ours to clean up from here on

        opener, redirects = opener_factory()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(req, timeout=TIMEOUT_S) as resp:
                rec["redirect_hosts"] = list(redirects.hops)
                rec["http_status"] = getattr(resp, "status", None)
                size, digest = download(resp, tmp_dl)
        except Refusal:
            raise
        except (urllib.error.URLError, OSError, ValueError) as e:
            rec.update(status="failed", reason=repr(e))
            return EXIT_FAILED, rec
        rec.update(download_bytes=size, sha256=digest)

        fmt = detect(tmp_dl)
        if kind in ("github", "codeload") and fmt != "tar.gz":
            raise Refusal("GitHub archive response is not gzip")
        rec["format"] = fmt
        os.mkdir(tmp_dir, 0o700)
        u = Unpacker(tmp_dir)
        try:
            if fmt == "tar.gz":
                unpack_tar(tmp_dl, u)
            elif fmt == "zip":
                unpack_zip(tmp_dl, u)
            else:
                fname = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
                with open(tmp_dl, "rb") as src:
                    u.write((fname,), src)
        except (tarfile.TarError, zipfile.BadZipFile, EOFError) as e:
            raise Refusal(f"archive could not be read: {e!r}")
        freeze(tmp_dir)
        os.rename(tmp_dir, final)
        tmp_dir = None
        rec.update(status="saved", saved=f"workspace/repos/{name}/",
                   files=u.files, unpacked_bytes=u.bytes, skipped=u.skipped,
                   skipped_not_listed=getattr(u, "skipped_more", 0))
        write_json_exclusive(repos / f"{name}.fetch.json", rec)
        return EXIT_OK, rec
    except Refusal as e:
        rec.update(status="refused", reason=str(e))
        return EXIT_REFUSED, rec
    finally:
        if tmp_dl is not None and tmp_dl.exists():
            tmp_dl.unlink()
        if tmp_dir is not None:
            thaw_and_remove(tmp_dir)


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return EXIT_REFUSED
    code, rec = run(argv[0], argv[1])
    log = write_log(rec)
    if code == EXIT_OK:
        print(f"Saved {rec['saved']} ({rec['files']} files, {rec['unpacked_bytes']} "
              f"bytes; download sha256 {rec['sha256'][:12]}...; "
              f"{len(rec['skipped']) + rec['skipped_not_listed']} entries skipped). "
              f"Log: {log}")
    else:
        print(f"{rec['status'].upper()}: {rec['reason']}\nLog: {log}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
