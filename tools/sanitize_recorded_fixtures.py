from __future__ import annotations

import argparse
import re
from pathlib import Path


TOKEN_PATTERNS = (
    re.compile(
        rb'("(?:userToken|pageToken|widgetToken|accessToken|refreshToken)"\s*:\s*")[^"]*(")'
    ),
    re.compile(
        rb'(\\"(?:userToken|pageToken|widgetToken|accessToken|refreshToken)\\"\s*:\s*\\")[^"\\]*(\\")'
    ),
)


def sanitize(path: Path) -> int:
    original = path.read_bytes()
    updated = original
    replacements = 0
    for pattern in TOKEN_PATTERNS:
        updated, count = pattern.subn(rb'\1[REDACTED_FIXTURE_TOKEN]\2', updated)
        replacements += count
    if updated != original:
        path.write_bytes(updated)
    return replacements


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    total = 0
    for path in args.paths:
        count = sanitize(path)
        total += count
        print(f"{path}: {count}")
    print(f"sanitized={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
