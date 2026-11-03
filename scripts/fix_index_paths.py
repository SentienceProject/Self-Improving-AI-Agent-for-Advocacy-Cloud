"""One-off fix: rewrite Windows file paths in the Chroma index's metadata to
the matching files in workspace/corpus/, without re-chunking or re-embedding.

Only metadata changes. The chunk text and the embeddings are left untouched.

    <venv python> scripts/fix_index_paths.py            # dry run: shows the plan
    <venv python> scripts/fix_index_paths.py --apply    # writes the changes

Matching: each Windows path is matched to a corpus file by file name (and, if
two corpus files share a name, by parent folder too). Anything it can't match
exactly once is reported and left unchanged. Run it outside the sandbox, with
the search venv's python, while nothing else is using the index.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path, PureWindowsPath

import chromadb

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "workspace" / "corpus"
CHROMA_PATH = ROOT / "workspace" / "index" / "chroma_db_qwen3"
BATCH = 500


def looks_windows(v) -> bool:
    return isinstance(v, str) and len(v) > 2 and v[1] == ":" and ("\\" in v or "/" in v)


def build_lookup():
    by_name = defaultdict(list)
    for f in CORPUS.rglob("*"):
        if f.is_file():
            by_name[f.name.lower()].append(f)
    return by_name


def match(win_value: str, by_name):
    w = PureWindowsPath(win_value)
    candidates = by_name.get(w.name.lower(), [])
    if len(candidates) > 1:  # same file name in two folders: use the parent
        candidates = [c for c in candidates
                      if c.parent.name.lower() == w.parent.name.lower()]
    if len(candidates) != 1:
        return None
    return candidates[0].relative_to(ROOT).as_posix()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--collection", default=None,
                    help="collection name (default: the only one in the index)")
    args = ap.parse_args()

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    names = [c if isinstance(c, str) else c.name for c in client.list_collections()]
    name = args.collection or (names[0] if len(names) == 1 else None)
    if name is None:
        sys.exit(f"Several collections found, pick one with --collection: {names}")
    col = client.get_collection(name)

    got = col.get(include=["metadatas"])
    ids, metas = got["ids"], got["metadatas"]
    by_name = build_lookup()

    plan, mapping, unmatched = [], {}, set()
    for i, m in zip(ids, metas):
        if not m:
            continue
        new = dict(m)
        changed = False
        for key, value in m.items():
            if looks_windows(value):
                target = mapping.get(value) if value in mapping else match(value, by_name)
                mapping[value] = target
                if target is None:
                    unmatched.add(value)
                else:
                    new[key] = target
                    changed = True
        if changed:
            plan.append((i, new))

    print(f"Collection '{name}': {len(ids)} chunks, {len(plan)} to update.\n")
    for old, new in sorted(mapping.items()):
        print(f"  {old}\n    -> {new or 'NO UNIQUE MATCH (left unchanged)'}")
    if unmatched:
        print(f"\n{len(unmatched)} path(s) could not be matched; those chunks keep "
              f"their Windows paths. Check the file names in workspace/corpus/.")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to write these changes.")
        return
    for start in range(0, len(plan), BATCH):
        batch = plan[start:start + BATCH]
        col.update(ids=[i for i, _ in batch], metadatas=[m for _, m in batch])
    left = sum(1 for m in col.get(include=["metadatas"])["metadatas"]
               if m and any(looks_windows(v) for v in m.values()))
    print(f"\nUpdated {len(plan)} chunks. Chunks still holding Windows paths: {left}.")


if __name__ == "__main__":
    main()
