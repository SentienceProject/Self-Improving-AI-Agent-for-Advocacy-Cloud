#!/usr/bin/env python3
"""Draw wild-ideator cards from workspace/assets/seed-decks/.

    python3 scripts/draw_card.py <n-ideators>

Prints one card per ideator, ready to paste verbatim into its prompt. The card
types rotate so parallel ideators don't get correlated cards:

    W1  SEED (tactic) + SPARK (random noun)
    W2  SEED (tactic) + LENS  (persona)
    W3  SEED (tactic) + IMAGE (a photo from workspace/assets/photos/, if any;
                               otherwise a second random noun)

Every card gets three BEINGS of different kinds. Beings listed in KINDS share
a kind; any other line is its own kind. No deck entry repeats across the
cards of one draw.

Uses the OS random source, so draws aren't reproducible; the cards printed
here are the record (paste them into the prompt, and the SubagentStop hook
logs every prompt).
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECKS = ROOT / "workspace" / "assets" / "seed-decks"
PHOTOS = ROOT / "workspace" / "assets" / "photos"
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

KINDS = {
    "fish/crustacean": ["lobsters", "shrimp on a farm", "fish in an aquarium tank", "farmed salmon"],
    "machine mind": ["a robot with feelings", "a neural network slowly gaining awareness",
                     "a chatbot no one knows is conscious"],
    "alien/cosmic": ["a humanoid alien", "an alien unlike any Earth life",
                     "a sentient Hydrogen cloud", "a conscious star"],
    "insect": ["honeybees", "insects"],
}
KIND_OF = {b: k for k, bs in KINDS.items() for b in bs}

rng = random.SystemRandom()


def deck(name):
    lines = (DECKS / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def take(pool, used):
    choice = rng.choice([x for x in pool if x not in used])
    used.add(choice)
    return choice


def beings(pool, used):
    picked, kinds = [], set()
    candidates = [b for b in pool if b not in used]
    rng.shuffle(candidates)
    for b in candidates:
        kind = KIND_OF.get(b, b)
        if kind not in kinds:
            picked.append(b); kinds.add(kind); used.add(b)
        if len(picked) == 3:
            return picked
    sys.exit("draw_card: not enough distinct kinds left in beings.txt")


def main(n):
    tactics, nouns, personas, all_beings = deck("tactics"), deck("nouns"), deck("personas"), deck("beings")
    photos = [p for p in PHOTOS.glob("*") if p.suffix.lower() in PHOTO_EXT] if PHOTOS.is_dir() else []
    used = set()
    for i in range(n):
        print(f"=== W{i + 1} ===")
        print(f"SEED:   {take(tactics, used)}")
        kind = i % 3
        if kind == 0:
            print(f"SPARK:  {take(nouns, used)}")
        elif kind == 1:
            print(f"LENS:   {take(personas, used)}")
        elif photos:
            print(f"IMAGE:  {take(photos, used)}")
        else:
            print(f"SPARK:  {take(nouns, used)}, {take(nouns, used)}")
        print("BEINGS: " + " / ".join(beings(all_beings, used)))
        print()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__ or "usage: python3 scripts/draw_card.py [n]   (n = 1-6 cards, default 3)")
    if len(sys.argv) > 1 and not sys.argv[1].isdigit():
        sys.exit("usage: python3 scripts/draw_card.py [n]   (n = 1-6 cards, default 3)")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    if not 1 <= n <= 6:
        sys.exit("draw_card: n must be 1-6")
    main(n)
