"""Run one agent-written analysis script inside the pinned sandbox container.

    python scripts/run_analysis.py <run-id> <script-path>

<script-path> is relative to the repo root and must resolve (symlinks included)
to a regular .py file inside workspace/analysis/<run-id>/. Absolute paths and
'..' are refused outright.

This is Layer B of Milestone 1: the container is the security boundary. The
script is meant to be frozen after review. It is intentionally small and
unconfigurable: there are no flags, and every limit is a module constant.

WHAT IT DOES
  1. Validates the run-id and the script path. Any failure is a refusal.
  2. Reads sandbox/image.lock ("sha256:<id>", from docker build --iidfile) and
     confirms with `docker image inspect` that the local image's Id matches
     exactly. The container is run by that Id, never by tag.
  3. Stages the code: every .py file under the run dir is read once, hashed,
     and those same bytes are written into workspace/staging/<run-id>-exec-<n>/
     (created exclusively), keeping relative paths. A .py that is a symlink,
     is not a regular file, or resolves outside the run dir is a refusal.
     Staged files are made 0444 and staged directories 0555. Staging folders
     are kept after the run as evidence.
  4. Runs the fixed `docker run` below: no network, read-only root, capped
     memory/swap/cpu/pids, no capabilities, uid 1000, six mounts, and no
     environment passed from the host. The staged copy is mounted read-only at
     /code and the script runs from there; the run dir stays mounted
     read-write at /work, which is the working directory (the image's WORKDIR).
  5. Kills the container from the host if it outlives HOST_TIMEOUT_S.
  6. Writes workspace/logs/<run-id>-exec-<n>.json (exclusive create, never
     overwritten) for every run *and* every refusal. script_sha256 is the hash
     of the staged copy; staged_dir and staged_manifest ({relative path:
     sha256}) record everything that was staged.

Exit code: the container's exit code, or 2 for a refusal. Exit codes
125-127 are logged with status "docker_error" instead of "ran".

The container runs the staged copy and cannot write to it, so a change to the
run dir after staging does not change what runs. KNOWN LIMIT: the 0444/0555
modes stop accidents, not the owner; a host process running as the same user
can chmod the staged copy and rewrite it before launch. Code in /work is still
visible to the container but is not on the import path unless the script puts
it there.
"""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "workspace" / "analysis"
CORPUS_DIR = ROOT / "workspace" / "corpus"
ASSETS_DIR = ROOT / "workspace" / "assets"
INBOX_DIR = ROOT / "workspace" / "inbox"
REPOS_DIR = ROOT / "workspace" / "repos"
LOGS_DIR = ROOT / "workspace" / "logs"
STAGING_DIR = ROOT / "workspace" / "staging"
IMAGE_LOCK = ROOT / "sandbox" / "image.lock"
# Absolute, so PATH can't substitute another binary. The first that exists:
# Linux packages, then Docker Desktop on macOS (Intel, then Homebrew/ARM).
DOCKER = next((d for d in ("/usr/bin/docker", "/usr/local/bin/docker",
                           "/opt/homebrew/bin/docker") if os.path.exists(d)),
              "/usr/bin/docker")

# Limits. Constants, not flags: tests patch them, agents cannot change them.
CONTAINER_TIMEOUT_S = 900       # `timeout` inside the container
HOST_TIMEOUT_S = 930            # host-side backstop; kills the named container
MEMORY = "8g"                   # --memory and --memory-swap (equal: no swap)
CPUS = "4"                      # both are ceilings: lowered to fit what Docker
                                # has (e.g. a small Docker Desktop VM)
PIDS_LIMIT = 256
TMPFS_SIZE = "512m"
HEAD_TAIL = 2048                # bytes of stdout/stderr kept at each end

RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
LOCK_RE = re.compile(r"sha256:[0-9a-f]{64}")
EXIT_REFUSED = 2


class Refusal(Exception):
    pass


# --- validation ------------------------------------------------------------

def valid_run_id(run_id: str) -> bool:
    return RUN_ID_RE.fullmatch(run_id) is not None


def check_paths(run_id: str, script_arg: str) -> tuple[Path, Path, str]:
    """Return (resolved run dir, resolved script, script path relative to
    the run dir, POSIX-style)."""
    if not script_arg or script_arg.startswith("/") or os.path.isabs(script_arg):
        raise Refusal(f"absolute path refused: {script_arg!r}")
    if "\x00" in script_arg or "\\" in script_arg:
        raise Refusal(f"illegal character in path: {script_arg!r}")
    parts = PurePosixPath(script_arg).parts
    if ".." in parts:
        raise Refusal(f"'..' is not allowed in the script path: {script_arg!r}")
    if parts[:3] != ("workspace", "analysis", run_id) or len(parts) < 4:
        raise Refusal(
            f"script must be under workspace/analysis/{run_id}/: {script_arg!r}")

    analysis_real = ANALYSIS_DIR.resolve(strict=False)
    try:
        run_dir = (ANALYSIS_DIR / run_id).resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise Refusal(f"run dir workspace/analysis/{run_id}/ is unusable: {e}")
    if run_dir != analysis_real / run_id or not run_dir.is_dir():
        raise Refusal(
            f"run dir workspace/analysis/{run_id}/ resolves outside "
            f"workspace/analysis/ or is not a directory: {run_dir}")

    try:
        script = (ROOT / script_arg).resolve(strict=True)
    except FileNotFoundError:
        raise Refusal(f"script does not exist: {script_arg!r}")
    except (OSError, RuntimeError, ValueError) as e:
        raise Refusal(f"cannot resolve script path {script_arg!r}: {e}")
    if not script.is_relative_to(run_dir):
        raise Refusal(
            f"script resolves outside workspace/analysis/{run_id}/: {script}")
    if not stat.S_ISREG(os.stat(script).st_mode):
        raise Refusal(f"script is not a regular file: {script_arg!r}")
    if script.suffix != ".py":
        raise Refusal(f"script must be a .py file (resolved to {script.name!r})")

    for label, d in (("corpus", CORPUS_DIR), ("assets", ASSETS_DIR), ("inbox", INBOX_DIR), ("repos", REPOS_DIR)):
        if not d.is_dir():
            raise Refusal(f"workspace/{label}/ is missing; refusing to let "
                          f"docker create it")

    return run_dir, script, script.relative_to(run_dir).as_posix()


def _check_image() -> str:
    try:
        lock = IMAGE_LOCK.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise Refusal(f"cannot read sandbox image.lock: {e}. The analyst needs hardened mode's Docker sandbox: "
                      "python3 setup.py --hardened, then bash sandbox/build.sh (see the README)")
    if not LOCK_RE.fullmatch(lock):
        raise Refusal(f"sandbox image.lock is malformed: {lock[:80]!r}")
    try:
        r = subprocess.run(
            [DOCKER, "image", "inspect", "--format", "{{.Id}}", lock],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refusal(f"docker image inspect failed: {e}")
    if r.returncode != 0:
        raise Refusal(f"image {lock} not found locally: {r.stderr.strip()}")
    if r.stdout.strip() != lock:
        raise Refusal(f"image Id {r.stdout.strip()!r} does not match "
                      f"image.lock {lock!r}")
    return lock


def _fit_limits() -> tuple[str, str]:
    """MEMORY and CPUS, lowered if Docker has less: at most all its CPUs and
    3/4 of its memory. Docker refuses --cpus above its CPU count. If docker
    info fails, the constants are used unchanged."""
    try:
        r = subprocess.run(
            [DOCKER, "info", "--format", "{{.NCPU}} {{.MemTotal}}"],
            capture_output=True, text=True, timeout=30)
        ncpu, mem_total = (int(x) for x in r.stdout.split())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return MEMORY, CPUS
    units = {"k": 2**10, "m": 2**20, "g": 2**30}
    want = int(MEMORY[:-1]) * units[MEMORY[-1].lower()]
    memory = MEMORY if want <= mem_total * 3 // 4 else f"{mem_total * 3 // 4 // 2**20}m"
    cpus = CPUS if float(CPUS) <= ncpu else str(ncpu)
    return memory, cpus


# --- staging ---------------------------------------------------------------

def _collect_py(run_dir: Path) -> list[str]:
    """Relative POSIX paths of every .py file under run_dir. Refuses on any
    .py that is a symlink, not a regular file, or resolves outside run_dir.
    Symlinked directories are not descended into."""
    found = []
    for dirpath, _, filenames in os.walk(run_dir, followlinks=False):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(run_dir).as_posix()
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                raise Refusal(f"symlinked .py in the run dir: {rel}")
            if not stat.S_ISREG(mode):
                raise Refusal(f".py in the run dir is not a regular file: {rel}")
            if not path.resolve(strict=True).is_relative_to(run_dir):
                raise Refusal(f".py resolves outside the run dir: {rel}")
            found.append(rel)
    return sorted(found)


def _open_beneath(run_dir: Path, rel: str) -> int:
    """Open run_dir/rel for reading without following a symlink at any
    component, so a directory swapped for a symlink after the scan can't
    redirect the read. O_NONBLOCK so a swapped-in FIFO can't hang us."""
    parts = PurePosixPath(rel).parts
    fd = os.open(run_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=fd)
            os.close(fd)
            fd = nfd
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=fd)
    finally:
        os.close(fd)


def _stage(run_id: str, n: int, run_dir: Path) -> tuple[Path, dict]:
    """Copy every .py under run_dir into STAGING_DIR/<run_id>-exec-<n>/.
    Each file's bytes are read once; the hash and the staged copy both come
    from those bytes. Returns (staged dir, {relative path: sha256})."""
    rels = _collect_py(run_dir)
    if STAGING_DIR.is_symlink():
        raise Refusal(f"workspace/staging/ is a symlink: {STAGING_DIR}")
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    dest = STAGING_DIR / f"{run_id}-exec-{n}"
    try:
        os.mkdir(dest, 0o755)
    except FileExistsError:
        raise Refusal(f"staging dir already exists: {dest}")

    manifest, dirs = {}, [dest]
    for rel in rels:
        for parent in reversed(PurePosixPath(rel).parents[:-1]):
            d = dest / parent
            if not d.is_dir():
                os.mkdir(d, 0o755)
                dirs.append(d)
        try:
            src_fd = _open_beneath(run_dir, rel)
        except OSError as e:
            raise Refusal(f"cannot open {rel} for staging (changed since the "
                          f"scan?): {e}; partial staging left at {dest}")
        with os.fdopen(src_fd, "rb") as src:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                raise Refusal(f".py in the run dir is not a regular file: "
                              f"{rel}; partial staging left at {dest}")
            data = src.read()
        manifest[rel] = hashlib.sha256(data).hexdigest()
        out_fd = os.open(dest / rel,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
        with os.fdopen(out_fd, "wb") as out:
            out.write(data)
            os.fchmod(out.fileno(), 0o444)
    for d in dirs:
        os.chmod(d, 0o555)
    return dest, manifest


# --- container -------------------------------------------------------------

def build_argv(run_id: str, n: int, run_dir: Path, staged_dir: Path,
               script_rel: str, image_id: str) -> list[str]:
    mounts = [
        (CORPUS_DIR.resolve(), "/data/corpus", "ro"),
        (ASSETS_DIR.resolve(), "/data/assets", "ro"),
        (INBOX_DIR.resolve(), "/data/inbox", "ro"),
        (REPOS_DIR.resolve(), "/data/repos", "ro"),
        (run_dir, "/work", "rw"),
        (staged_dir, "/code", "ro"),
    ]
    for host, _, _ in mounts:
        if ":" in str(host) or "," in str(host):
            raise Refusal(f"mount path contains ':' or ',': {host}")
    container_script = "/code/" + script_rel.lstrip("/")
    memory, cpus = _fit_limits()
    argv = [
        DOCKER, "run", "--rm",
        "--name", f"{run_id}-exec-{n}",
        "--network", "none",
        "--read-only", "--tmpfs", f"/tmp:size={TMPFS_SIZE}",
        "--memory", memory, "--memory-swap", memory,
        "--cpus", cpus, "--pids-limit", str(PIDS_LIMIT),
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "1000:1000",
    ]
    for host, target, mode in mounts:
        argv += ["-v", f"{host}:{target}:{mode}"]
    argv += [image_id, "timeout", str(CONTAINER_TIMEOUT_S),
             "python", container_script]
    return argv


class _Capture:
    """Keeps the first and last HEAD_TAIL bytes of a stream and its size,
    passing the stream through to `sink` as it arrives."""

    def __init__(self, stream, sink):
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self._stream = stream
        self._sink = sink
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        for chunk in iter(lambda: self._stream.read1(65536), b""):
            self.total += len(chunk)
            if len(self.head) < HEAD_TAIL:
                self.head += chunk[:HEAD_TAIL - len(self.head)]
            self.tail += chunk
            if len(self.tail) > HEAD_TAIL:
                del self.tail[:-HEAD_TAIL]
            try:
                self._sink.write(chunk)
                self._sink.flush()
            except (OSError, ValueError):
                pass

    def fields(self, name: str) -> dict:
        return {
            f"{name}_head": self.head.decode("utf-8", "replace"),
            f"{name}_tail": self.tail.decode("utf-8", "replace"),
            f"{name}_bytes": self.total,
        }


def _kill(name: str, proc: subprocess.Popen):
    # Retry briefly: the kill can race the container's creation.
    for _ in range(5):
        subprocess.run([DOCKER, "kill", name], capture_output=True, timeout=30)
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue
    proc.kill()
    proc.wait()


def _launch(argv: list[str], name: str) -> dict:
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = _Capture(proc.stdout, getattr(sys.stdout, "buffer", sys.stdout))
    err = _Capture(proc.stderr, getattr(sys.stderr, "buffer", sys.stderr))
    status, timed_out = "ran", False
    try:
        proc.wait(timeout=HOST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        status, timed_out = "timeout", True
        _kill(name, proc)
    except KeyboardInterrupt:
        status = "interrupted"
        _kill(name, proc)
    out.thread.join(timeout=30)
    err.thread.join(timeout=30)
    rc = proc.returncode
    if status != "ran" and rc == 0:
        rc = 124
    # 125: docker run itself failed. 126/127: the command couldn't be invoked
    # or wasn't found. A script calling sys.exit(125..127) is logged the same
    # way; the codes can't tell the two apart.
    if status == "ran" and rc in (125, 126, 127):
        status = "docker_error"
    return {"status": status, "timed_out": timed_out, "exit_code": rc,
            **out.fields("stdout"), **err.fields("stderr")}


# --- logging ---------------------------------------------------------------

def _reserve_log(prefix: str) -> tuple[int, int, Path]:
    """Exclusively create the next free <prefix>-exec-<n>.json. Returns
    (fd, n, path). Never opens an existing file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    n = 1
    while True:
        path = LOGS_DIR / f"{prefix}-exec-{n}.json"
        try:
            return os.open(path, flags, 0o644), n, path
        except FileExistsError:
            n += 1


def _write_log(fd: int, record: dict):
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --- main ------------------------------------------------------------------

def main(args: list[str]) -> int:
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return EXIT_REFUSED
    run_id, script_arg = args

    if not valid_run_id(run_id):
        fd, n, path = _reserve_log("_refused")
        reason = "invalid run-id (must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$)"
        _write_log(fd, {"run_id": repr(run_id), "n": n, "status": "refused",
                        "reason": reason, "script": script_arg,
                        "exit_code": None, "time": _now()})
        print(f"REFUSED: {reason}\nlog: {path}", file=sys.stderr)
        return EXIT_REFUSED

    fd, n, log_path = _reserve_log(run_id)
    record = {"run_id": run_id, "n": n, "script": script_arg}
    try:
        run_dir, _, script_rel = check_paths(run_id, script_arg)
        image_id = _check_image()
        name = f"{run_id}-exec-{n}"
        staged_dir, manifest = _stage(run_id, n, run_dir)
        record.update(staged_dir=str(staged_dir), staged_manifest=manifest)
        if script_rel not in manifest:
            raise Refusal(f"script was not staged: {script_rel}")
        record["script_sha256"] = manifest[script_rel]
        argv = build_argv(run_id, n, run_dir, staged_dir, script_rel, image_id)
    except Refusal as e:
        record.update(status="refused", reason=str(e), exit_code=None,
                      time=_now())
        _write_log(fd, record)
        print(f"REFUSED: {e}\nlog: {log_path}", file=sys.stderr)
        return EXIT_REFUSED
    except BaseException as e:
        record.update(status="refused", reason=f"internal error: {e!r}",
                      exit_code=None, time=_now())
        _write_log(fd, record)
        raise

    record.update(image_id=image_id, argv=argv, reason=None)
    try:
        record["started"] = _now()
        t0 = time.monotonic()
        result = _launch(argv, name)
        record["duration_s"] = round(time.monotonic() - t0, 3)
        record["ended"] = _now()
        record.update(result)
    except BaseException as e:
        record.update(status="error", reason=repr(e), exit_code=None,
                      ended=_now())
        _write_log(fd, record)
        raise
    _write_log(fd, record)
    print(f"\n[run_analysis] exit {record['exit_code']} ({record['status']}); "
          f"log: {log_path}", file=sys.stderr)
    return record["exit_code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
