"""One-off fix: stop the index pointing at PDFs, using the Markdown copies.

For each PDF in the index, this finds which workspace/corpus/*.md file has the
same text (by word overlap, so no name matching is assumed), then:

  * if that .md file is already in the index too, the PDF's chunks are
    duplicates, so they are DROPPED (otherwise every search would return the
    same passage twice);
  * if it isn't, the PDF's chunks are REPOINTED to the .md file. The old path
    is kept in an "original_pdf" metadata field.

A PDF is only changed when one .md file is a clear match. Otherwise it's
reported and left alone. Chunk text and embeddings are never changed.

    <venv python> scripts/fix_index_pdf_sources.py            # dry run
    <venv python> scripts/fix_index_pdf_sources.py --apply    # write changes

Run outside the sandbox, with nothing else using the index. The original index
on the C: drive is the backup.
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import chromadb

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "workspace" / "corpus"
CHROMA_PATH = ROOT / "workspace" / "index" / "chroma_db_qwen3"
SOURCE_KEY = "source"
MIN_SCORE = 0.6    # share of the PDF's words found in the .md file
MIN_MARGIN = 0.15  # lead over the second-best .md file
BATCH = 500


def words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]{4,}", text.lower()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--collection", default=None)
    args = ap.parse_args()

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    names = [c if isinstance(c, str) else c.name for c in client.list_collections()]
    name = args.collection or (names[0] if len(names) == 1 else None)
    if name is None:
        sys.exit(f"Several collections found, pick one with --collection: {names}")
    col = client.get_collection(name)
    got = col.get(include=["metadatas", "documents"])

    chunks = defaultdict(list)  # source -> [(id, metadata, text)]
    for i, m, d in zip(got["ids"], got["metadatas"], got["documents"]):
        chunks[(m or {}).get(SOURCE_KEY)].append((i, m or {}, d or ""))

    md_files = sorted(CORPUS.rglob("*.md"))
    if not md_files:
        sys.exit("No .md files found in workspace/corpus/.")
    md_words = {f: words(f.read_text(encoding="utf-8", errors="replace"))
                for f in md_files}
    indexed = {s for s in chunks if isinstance(s, str)}

    pdfs = sorted(s for s in indexed if s.lower().endswith(".pdf"))
    print(f"Collection '{name}': {len(got['ids'])} chunks from {len(indexed)} "
          f"sources, {len(pdfs)} of them PDFs.\n")

    drops, updates, skipped = [], [], 0
    for pdf in pdfs:
        pw = words(" ".join(t for _, _, t in chunks[pdf]))
        scores = sorted(((len(pw & mw) / max(len(pw), 1), f)
                         for f, mw in md_words.items()), reverse=True)
        best_score, best = scores[0]
        second = scores[1][0] if len(scores) > 1 else 0.0
        rel = best.relative_to(ROOT).as_posix()
        n = len(chunks[pdf])
        print(f"{pdf}  ({n} chunks)")
        print(f"    best match {rel}: {best_score:.2f} (next best {second:.2f})")
        if best_score < MIN_SCORE or best_score - second < MIN_MARGIN:
            print("    -> NO CLEAR MATCH, left unchanged")
            skipped += 1
        elif rel in indexed:
            print("    -> .md already indexed: DROP these duplicate chunks")
            drops += [i for i, _, _ in chunks[pdf]]
        else:
            print("    -> REPOINT these chunks to the .md file")
            for i, m, _ in chunks[pdf]:
                updates.append((i, {**m, SOURCE_KEY: rel, "original_pdf": pdf}))

    print(f"\nPlan: drop {len(drops)} chunks, repoint {len(updates)}, "
          f"leave {skipped} PDF(s) unchanged.")
    if not args.apply:
        print("Dry run only. Re-run with --apply to write these changes.")
        return
    for s in range(0, len(drops), BATCH):
        col.delete(ids=drops[s:s + BATCH])
    for s in range(0, len(updates), BATCH):
        b = updates[s:s + BATCH]
        col.update(ids=[i for i, _ in b], metadatas=[m for _, m in b])
    left = sum(1 for m in col.get(include=["metadatas"])["metadatas"]
               if str((m or {}).get(SOURCE_KEY, "")).lower().endswith(".pdf"))
    print(f"Done. Chunks still pointing at a PDF: {left}.")


if __name__ == "__main__":
    main()
