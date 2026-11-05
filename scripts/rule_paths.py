"""Absolute paths in Claude Code permission rules, written the way Claude Code reads them on every system.

In a rule like `Read(<path>/**)`, a path starting with "//" is absolute; one starting with a single "/" is relative
to the settings file. On Windows, Claude Code writes `C:\\Users\\Ana` as `/c/Users/Ana`, so the absolute form is
`//c/Users/Ana`. Gluing "/" onto a path happens to give "//home/ana" on Linux and macOS, but "/C:/Users/Ana" on
Windows, which Claude Code reads as a relative path: every rule built that way silently did nothing there (tech
watch's scouts couldn't save their notes, and the read-block rules didn't block).

This is for permission rules only. Sandbox settings (sandbox > filesystem) take ordinary paths.
"""
import re


def rule_path(p):
    """`//home/ana/x` or `//c/Users/Ana/x` for an absolute path (a str or Path, any system's form)."""
    s = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/*(.*)$", s)
    if m:
        s = f"/{m.group(1).lower()}/{m.group(2)}"
    return "/" + "/" + s.lstrip("/")
