"""Archive old memory.md entries so the file stays lean.

    python scripts/prune_memory.py

Moves to workspace/memory_archive.md (append-only, never edited otherwise):
  - "Ruled out" entries older than 180 days
  - "Time-sensitive" entries past their recheck date, UNLESS the section
    heading contains "stable" -- those are left alone regardless of age

Nothing is deleted. This is a move, not a cleanup in the destructive sense --
if a pruned entry turns out to matter, it's still in the archive file,
grep-able, just out of the file every run pays context for.

This script does simple heuristic text matching on the memory.md structure
documented in that file's own header. If you restructure memory.md, update
the SECTION_* markers below to match.
"""

import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEMORY = ROOT / "workspace" / "memory.md"
ARCHIVE = ROOT / "workspace" / "memory_archive.md"

SECTION_RULED_OUT = "## Ruled out"
SECTION_TIME_SENSITIVE = "## Time-sensitive"
RULED_OUT_MAX_AGE_DAYS = 180

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def parse_date(text: str) -> date | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def split_sections(text: str) -> dict[str, str]:
    """Split on '## ' headings, keyed by the heading line."""
    parts = re.split(r"(?m)^(## .+)$", text)
    sections = {}
    # parts[0] is preamble before first heading
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections[heading] = body
    return sections


def entry_blocks(body: str) -> list[str]:
    """Entries are separated by blank lines; HTML comments are their own block."""
    blocks = [b for b in re.split(r"\n\s*\n", body) if b.strip()]
    return blocks


def should_archive_ruled_out(block: str, today: date) -> bool:
    if block.strip().startswith("<!--"):
        return False
    d = parse_date(block)
    if d is None:
        return False  # can't tell age -- leave it, don't guess
    return (today - d).days > RULED_OUT_MAX_AGE_DAYS


def should_archive_time_sensitive(block: str, today: date) -> bool:
    if block.strip().startswith("<!--"):
        return False
    if "recheck: stable" in block.lower():
        return False
    m = re.search(r"recheck:\s*(\d{4}-\d{2}-\d{2})", block, re.IGNORECASE)
    if not m:
        return False  # no parseable recheck date -- leave it, flag via stdout
    recheck = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    return today > recheck


def main():
    if not MEMORY.exists():
        sys.exit(f"No {MEMORY} found.")

    today = date.today()
    text = MEMORY.read_text(encoding="utf-8")
    sections = split_sections(text)

    archived = []
    unparseable = []

    for heading, body in list(sections.items()):
        if heading.startswith(SECTION_RULED_OUT):
            check_fn = should_archive_ruled_out
        elif heading.startswith(SECTION_TIME_SENSITIVE):
            check_fn = should_archive_time_sensitive
        else:
            continue

        blocks = entry_blocks(body)
        keep, move = [], []
        for b in blocks:
            if b.strip().startswith("<!--"):
                keep.append(b)
                continue
            if check_fn(b, today):
                move.append(b)
                archived.append((heading, b))
            else:
                keep.append(b)
                if parse_date(b) is None and heading.startswith(SECTION_RULED_OUT):
                    unparseable.append(b[:80])

        sections[heading] = "\n\n".join(keep) + ("\n\n" if keep else "\n")

    if not archived:
        print("Nothing to archive.")
        if unparseable:
            print(f"\n{len(unparseable)} entries have no parseable date and "
                  f"were left in place -- consider adding one:")
            for u in unparseable:
                print(f"  {u}...")
        return

    # Rebuild memory.md
    preamble = text.split("## ", 1)[0]
    new_text = preamble + "".join(
        f"## {h.split(' ', 1)[1] if h.startswith('## ') else h}\n\n{sections[h]}"
        if h.startswith("## ") else f"{h}\n\n{sections[h]}"
        for h in sections
    )
    # simpler, robust rebuild:
    new_text = preamble
    for heading, body in sections.items():
        new_text += f"{heading}\n\n{body.rstrip()}\n\n"
    MEMORY.write_text(new_text.rstrip() + "\n", encoding="utf-8")

    # Append to archive
    stamp = f"\n<!-- archived {today.isoformat()} -->\n\n"
    archive_text = stamp + "\n\n".join(f"[{h}]\n{b}" for h, b in archived) + "\n"
    with ARCHIVE.open("a", encoding="utf-8") as f:
        f.write(archive_text)

    print(f"Archived {len(archived)} entries to {ARCHIVE.name}.")
    if unparseable:
        print(f"{len(unparseable)} ruled-out entries had no date and were kept "
              f"as-is -- add dates so they can be pruned later.")


if __name__ == "__main__":
    main()
