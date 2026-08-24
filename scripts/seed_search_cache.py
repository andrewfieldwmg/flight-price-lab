"""Safely seed the persistent search cache from reconstructable raw captures."""

import argparse
from pathlib import Path

from flight_price_lab.storage.database import SearchResponseCache


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    cache = SearchResponseCache()
    seeded = 0
    skipped = 0
    for path in args.paths:
        candidates = path.rglob("*.json") if path.is_dir() else (path,)
        for capture in candidates:
            if cache.seed_capture(capture):
                seeded += 1
            else:
                skipped += 1
    print(f"Seeded: {seeded}")
    print(f"Skipped as unsafe/ambiguous: {skipped}")


if __name__ == "__main__":
    main()
