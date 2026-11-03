"""Semantic search over the corpus.

Usage:
    python scripts/corpus_search.py "moral status of digital minds" --k 8

Returns chunk text with source filenames. Results are POINTERS: the caller is
expected to Read the source file around the hit before citing anything.

UNTESTED against your actual index -- the CONFIG block below assumes ChromaDB
with Ollama embeddings. If your store differs, only that block should need to
change.
"""

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------- CONFIG ----
CHROMA_PATH = Path(__file__).resolve().parent.parent / "workspace" / "index" / "chroma_db_qwen3"
COLLECTION = "langchain"
EMBED_MODEL = "qwen3-embedding:0.6b"   # must match what built the index
OLLAMA_URL = "http://localhost:11434"
# -----------------------------------------------------------------------------


def embed(text: str) -> list[float]:
    import requests
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def search(query: str, k: int):
    import chromadb

    if not CHROMA_PATH.exists():
        sys.exit(
            f"No index at {CHROMA_PATH}.\n"
            "Point CHROMA_PATH at your existing store, or re-index there."
        )

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    try:
        collection = client.get_collection(COLLECTION)
    except Exception:
        names = [c.name for c in client.list_collections()]
        sys.exit(f"No collection '{COLLECTION}'. Found: {names or '(none)'}")

    res = collection.query(query_embeddings=[embed(query)], n_results=k)

    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]

    if not docs:
        print("No results.")
        return

    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
        meta = meta or {}
        source = meta.get("source") or meta.get("file") or "UNKNOWN SOURCE"
        print(f"\n--- result {i}  (distance {dist:.4f}) ---")
        print(f"source: {source}")
        for key in ("page", "section", "chunk_index"):
            if key in meta:
                print(f"{key}: {meta[key]}")
        print()
        print(doc.strip())

    print(
        "\n"
        "These are ~800-character fragments. Read the source file around each "
        "hit before citing it -- a fragment can reverse meaning in the sentence "
        "immediately after it."
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("query")
    p.add_argument("--k", type=int, default=8, help="number of results")
    args = p.parse_args()

    if args.k < 1 or args.k > 25:
        sys.exit("--k must be between 1 and 25")

    search(args.query, args.k)


if __name__ == "__main__":
    main()
