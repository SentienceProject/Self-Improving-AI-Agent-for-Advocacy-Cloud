#!/usr/bin/env python3
"""Record whether a draft was useful, so changes to the workspace can be judged by real use, not only by tests.

    python3 scripts/feedback.py y <draft> [why]     useful
    python3 scripts/feedback.py n <draft> [why]     not useful
    python3 scripts/feedback.py summary             useful rate before and after each applied tech-watch change

Answers go to workspace/feedback.jsonl, which stays on your computer. Tech watch's scouts read it, and
`techwatch/scripts/review.py` shows the summary. A handful of answers is noise; dozens start to mean something.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "workspace" / "feedback.jsonl"


def entries():
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def record(useful, draft="", why=""):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "useful": useful, "draft": draft,
         "why": why}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e) + "\n")


def summary(applied):
    """Lines comparing the useful rate before and after each applied change. applied: [(ts, id)] oldest first."""
    es = entries()
    if not es:
        return []
    bounds = [("", "the start")] + list(applied)
    out = []
    for i, (start, name) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else "9999"
        period = [e for e in es if start <= e.get("ts", "") < end]
        if period:
            n = sum(1 for e in period if e.get("useful"))
            out.append(f"since {name}: {n}/{len(period)} useful")
    return out


def main(a):
    if a and a[0] in ("y", "n", "yes", "no"):
        record(a[0].startswith("y"), a[1] if len(a) > 1 else "", " ".join(a[2:]))
        return print("Recorded. Thank you!")
    if a and a[0] == "summary":
        sys.path.insert(0, str(ROOT / "techwatch" / "scripts"))
        import review
        applied = sorted((e["ts"], e["id"]) for e in review.events() if e.get("event") in review.APPLIED)
        for line in summary(applied) or ["No answers recorded yet."]:
            print(line)
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
