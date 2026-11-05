"""Terminal styling for the workspace's scripts: easy to read on dark, light and PowerShell's blue backgrounds.

    from style import S
    print(S.head("Tech watch"))       a step heading: bold, with a rule
    print(S.ask("Run it?"))           a question: bold cyan
    print(S.ok("Done"))               success: bold green
    print(S.warn("Python is old"))    a warning: bold yellow, with "!"
    print(S.cmd("python3 setup.py"))  a command to type: bold, on its own indented line
    print(S.b("important"))           bold only

No dim gray (unreadable on blue) and no dark blue or dark red; bright colours, always bold, so they stand out on
every background. Off when output isn't a terminal, when NO_COLOR is set, or when an old Windows console can't show
colours. On Windows it switches the console's colour support on first.
"""
import os
import sys


def _supported():
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    try:
        if not sys.stdout.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if os.name != "nt":
        return True
    try:   # Windows 10+: turn on VT processing for this console (Windows Terminal has it on already)
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(h, mode.value | 0x0004) or (mode.value & 0x0004))
    except Exception:
        return False


class _Style:
    def __init__(self, on):
        self.on = on

    def _w(self, code, text):
        return f"\033[{code}m{text}\033[0m" if self.on else str(text)

    def b(self, t):
        return self._w("1", t)

    def ask(self, t):
        return self._w("1;96", t)

    def ok(self, t):
        return self._w("1;92", t)

    def warn(self, t):
        return self._w("1;93", f"! {t}")

    def err(self, t):
        return self._w("1;91", t)

    def accent(self, t):
        return self._w("1;95", t)

    def head(self, t):
        line = "─" if self._utf8() else "-"
        return "\n" + self._w("1;97", f"{line * 2} {t} " + line * max(4, 58 - len(t)))

    def cmd(self, t):
        return "      " + self._w("1;97", t)

    @staticmethod
    def _utf8():
        return (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") in ("utf8", "utf8sig")


S = _Style(_supported())


class _Highlight:
    """Wraps stdout so progress logs' alarm words stand out (bold yellow), without touching each print."""

    def __init__(self, out):
        import re
        self._out = out
        self._re = re.compile(r"\b(STOPPED|FAILED|FAIL|ERROR|WARNING|HARD STOP|TRAP CAP|PAUSED)\b")

    def write(self, t):
        return self._out.write(self._re.sub(lambda m: f"\033[1;93m{m.group(1)}\033[0m", t))

    def __getattr__(self, name):
        return getattr(self._out, name)


def highlight_stdout():
    """For long-running scripts' logs: call once at start-up. Does nothing when colour is off."""
    if S.on and not isinstance(sys.stdout, _Highlight):
        sys.stdout = _Highlight(sys.stdout)
