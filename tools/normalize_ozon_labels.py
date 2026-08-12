from __future__ import annotations

import argparse
import re
from pathlib import Path


AMBIGUOUS_OZON = re.compile(r"\bOzon\b(?!\.(?:ru|kz))")


def normalize(path: Path, write: bool) -> int:
    source = path.read_text(encoding="utf-8")
    updated, count = AMBIGUOUS_OZON.subn("Ozon.ru", source)
    if count and write:
        path.write_text(updated, encoding="utf-8")
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    found = 0
    for path in args.paths:
        count = normalize(path, args.write)
        if count:
            print(f"{path}: {count}")
            found += count
    if found and not args.write:
        print(f"ambiguous_labels={found}")
        return 1
    print(f"normalized={found}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
