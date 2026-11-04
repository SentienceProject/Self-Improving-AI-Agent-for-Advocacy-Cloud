"""Path checks for the agent guards, the same on Linux, macOS and Windows.

Claude Code hands hooks the path an agent asked for. On Windows that can be `C:\\Users\\Ana Lima\\...`, and the
hook's Python may be a Windows one, or a Git Bash/MSYS or WSL one that writes the same folder as `/c/Users/...` or
`/mnt/c/Users/...`. A check that compared raw strings, or split on "/", blocked agents from their own folders there.
"""
import os
import posixpath
import re
from pathlib import Path

_WIN = re.compile(r"^[a-zA-Z]:[\\/]")
_POSIX_DRIVE = re.compile(r"^/(?:cygdrive/|mnt/)?([a-zA-Z])(?:/(.*))?$")


def norm(p):
    """A comparable form: forward slashes, `..` resolved, and drive paths (`C:\\x`, `/c/x`, `/mnt/c/x`) all written
    `c:/x` in lower case, since Windows paths aren't case-sensitive."""
    s = str(p).replace("\\", "/")
    m = _POSIX_DRIVE.match(s)
    if m:
        s = f"{m.group(1)}:/{m.group(2) or ''}"
    s = posixpath.normpath(s)
    return s.lower() if re.match(r"^[a-zA-Z]:/", s) else s


def resolve(raw, root, cwd=None):
    """The path an agent asked for, absolute, with symlinks resolved where this Python can see them."""
    s = str(raw)
    if _WIN.match(s) and os.name != "nt":
        return s   # a Windows path seen from a Git Bash or WSL Python: compared in the normalised form
    p = Path(s)
    if not p.is_absolute():
        p = Path(cwd or root) / p
    return os.path.realpath(p)


def inside(raw, base, root, cwd=None):
    """True if the path an agent asked for (raw) is inside the folder base, and isn't base itself."""
    n, nb = norm(resolve(raw, root, cwd)), norm(os.path.realpath(base)).rstrip("/")
    return n.startswith(nb + "/")


def name_inside(raw, root, cwd=None):
    """The normalised path, for checks on the file name itself."""
    return norm(resolve(raw, root, cwd))
