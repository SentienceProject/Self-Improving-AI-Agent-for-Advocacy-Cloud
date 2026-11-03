"""Build seed-decks/nouns.txt from dariusk/corpora (pinned release).

Usage: python3 -I build_nouns.py <corpora data dir> <out file>
Collects string items from list-valued fields of chosen concrete-noun files,
lowercases, dedupes, and drops anything odd-looking.
"""
import json, random, re, sys
from pathlib import Path

FILES = """
words/nouns.json objects/objects.json objects/clothing.json objects/containers.json
architecture/rooms.json architecture/passages.json
foods/breads_and_pastries.json foods/condiments.json foods/fruits.json
foods/vegetables.json foods/herbs_n_spices.json foods/tea.json
materials/building-materials.json materials/fabrics.json materials/fibers.json
materials/natural-materials.json materials/packaging.json materials/scents.json
materials/sculpture-materials.json materials/decorative-stones.json
music/instruments.json technology/appliances.json technology/knots.json
technology/fireworks.json science/meteorology.json science/weather_conditions.json
""".split()
CAP = {"foods/tea.json": 40, "technology/knots.json": 40, "science/meteorology.json": 80,
       "foods/herbs_n_spices.json": 60, "materials/fabrics.json": 60}
SKIP_KEYS ={"description", "source", "sources", "url", "meta"}
OK = re.compile(r"^[a-z][a-z' -]{1,28}[a-z]$")


def strings(node, key=None):
    if key in SKIP_KEYS:
        return
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for x in node:
            yield from strings(x)
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (list, dict)):
                yield from strings(v, k)
            elif k in ("name", "word", "title") and isinstance(v, str):
                yield v


def main(data, out):
    seen, counts = set(), {}
    for rel in FILES:
        p = Path(data) / rel
        if not p.exists():
            print("missing", rel); continue
        new = []
        for s in strings(json.loads(p.read_text(encoding="utf-8"))):
            s = s.strip().lower()
            if OK.match(s) and len(s.split()) <= 3 and s not in seen and s not in new:
                new.append(s)
        if len(new) > CAP.get(rel, 10**6):  # niche lists would swamp the deck
            new = random.Random(0).sample(new, CAP[rel])
        seen.update(new); n = len(new)
        counts[rel] = n
    Path(out).write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")
    for k, v in counts.items():
        print(f"{v:5d} {k}")
    print(f"{len(seen):5d} total")


main(*sys.argv[1:])
