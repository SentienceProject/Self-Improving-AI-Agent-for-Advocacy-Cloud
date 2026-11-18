#!/usr/bin/env python3
"""Record how useful a draft was, 1 to 10, so changes to the workspace can be judged by real use, not only by tests.

    python3 scripts/feedback.py 7 <draft> [why]     rate a draft (1 = useless, 10 = exactly what I needed)
    python3 scripts/feedback.py undo                remove the last rating (e.g. a reply that wasn't one)
    python3 scripts/feedback.py summary             average rating before and after each applied tech-watch change

Usually nobody runs this by hand: when Claude ends a reply with "How useful was this, 1 to 10?", the hook
.claude/hooks/rating.py saves an answer that starts with a number, and Claude never sees it. Answers go to workspace/feedback.jsonl, which
stays on your computer. Tech watch's scouts read it, and `techwatch/scripts/review.py` shows the summary. A handful
of answers is noise; dozens start to mean something. Older answers were yes/no ("useful": true/false); they're
kept and summed up separately.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "workspace" / "feedback.jsonl"
NUMBER = re.compile(r"(10|[1-9])(\.\d)?$")


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


def parse_rating(s):
    """A rating from 1 to 10 (int, or float like 7.5), or None."""
    m = NUMBER.match(s.strip())
    if not m:
        return None
    r = float(m.group(0))
    if not 1 <= r <= 10:
        return None
    return int(r) if r == int(r) else r


def record(rating, draft="", why="", by="session", reply=""):
    """reply: the id of the Claude reply being rated (the hook uses it to rate each reply once)."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rating": rating, "draft": draft,
         "why": why, "by": by}
    if reply:
        e["reply"] = reply
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e) + "\n")


def undo():
    """Remove the last entry; returns it, or None if there was none."""
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None
    last = lines.pop()
    LOG.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return {"raw": last}


def describe(period):
    """'avg 7.4/10 over 5 ratings', plus older yes/no answers if the period has any."""
    rated = [e["rating"] for e in period if isinstance(e.get("rating"), (int, float))]
    yn = [e for e in period if "rating" not in e and "useful" in e]
    parts = []
    if rated:
        parts.append(f"avg {sum(rated) / len(rated):.1f}/10 over {len(rated)} rating{'s' if len(rated) != 1 else ''}")
    if yn:
        parts.append(f"{sum(1 for e in yn if e.get('useful'))}/{len(yn)} useful (older yes/no answers)")
    return "; ".join(parts)


def summary(applied):
    """Lines comparing ratings before and after each applied change. applied: [(ts, id)] oldest first."""
    es = entries()
    if not es:
        return []
    bounds = [("", "the start")] + list(applied)
    out = []
    for i, (start, name) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else "9999"
        text = describe([e for e in es if start <= e.get("ts", "") < end])
        if text:
            out.append(f"since {name}: {text}")
    return out


def main(a):
    if a and parse_rating(a[0]) is not None:
        record(parse_rating(a[0]), a[1] if len(a) > 1 else "", " ".join(a[2:]))
        return print("Recorded. Thank you!")
    if a and a[0] in ("y", "n", "yes", "no"):
        sys.exit("Answers are now a rating from 1 to 10, e.g. `feedback.py 7 <draft> [why]`.")
    if a and a[0] == "undo":
        e = undo()
        return print(f"Removed: {json.dumps(e)}" if e else "Nothing to remove.")
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
