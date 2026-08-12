from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors.ozon_kz.storage import ensure_schema, status


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ozon_kz_registry.db"


def validate_source_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https":
        raise ValueError("Источник Ozon.kz должен использовать HTTPS.")
    if host != "ozon.kz" and not host.endswith(".ozon.kz"):
        raise ValueError("Источник должен принадлежать отдельному домену ozon.kz.")
    if host == "ozon.ru" or host.endswith(".ozon.ru"):
        raise ValueError("Источник Ozon.ru нельзя подключить к контуру Ozon.kz.")
    return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ozon.kz: отдельный безопасный контур (источник ещё не подключён)."
    )
    parser.add_argument("action", choices=("status", "validate-source"))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--source-url", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db).resolve()
    if args.action == "validate-source":
        try:
            source_url = validate_source_url(args.source_url)
        except ValueError as exc:
            print(json.dumps({
                "ok": False,
                "marketplace": "ozon_kz",
                "label": "Ozon.kz",
                "error": str(exc),
            }, ensure_ascii=False))
            return 2
        print(json.dumps({
            "ok": True,
            "marketplace": "ozon_kz",
            "label": "Ozon.kz",
            "source_url": source_url,
            "persisted": False,
        }, ensure_ascii=False))
        return 0
    ensure_schema(db_path)
    print(json.dumps({
        "ok": True,
        "marketplace": "ozon_kz",
        "label": "Ozon.kz",
        **status(db_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
