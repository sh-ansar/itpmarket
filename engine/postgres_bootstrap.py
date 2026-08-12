from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.postgres_migration import (
    finalize_target_schema,
    inventory,
    migrate_append_only,
    prepare_target_schema,
    verify_migration,
)


def sources(root: Path) -> list[tuple[str, Path]]:
    candidates = [
        ("app", root / "data" / "unityre_kaspi.db"),
        ("ozon_ru", root / "collectors" / "ozon" / "data" / "ozon_registry.db"),
        ("ozon_kz", root / "collectors" / "ozon_kz" / "data" / "ozon_kz_registry.db"),
    ]
    return [(schema, path.resolve()) for schema, path in candidates if path.exists()]


def plan(root: Path) -> dict[str, Any]:
    result = []
    for schema, path in sources(root):
        tables = inventory(path, schema)
        result.append({
            "schema": schema, "source": str(path), "tables": len(tables),
            "rows": sum(item.rows for item in tables),
        })
    return {
        "mode": "read_only_plan", "sources": result,
        "schemas": len(result), "rows": sum(item["rows"] for item in result),
    }


def apply(root: Path, database_url: str) -> dict[str, Any]:
    results = []
    for schema, path in sources(root):
        prepared = prepare_target_schema(path, schema, database_url)
        source_id = hashlib.sha256(f"{path}:{schema}".encode()).hexdigest()[:20]
        migrated = migrate_append_only(path, schema, database_url, source_id)
        finalized = finalize_target_schema(path, schema, database_url)
        verified = verify_migration(path, schema, database_url)
        if not verified["ok"]:
            raise RuntimeError(f"Проверка PostgreSQL schema {schema} не пройдена.")
        results.append({
            "schema": schema, "source": str(path), **prepared, **migrated,
            **finalized, "verified_rows": verified["target_rows"],
        })
    return {"ok": True, "schemas": results, "rows": sum(item["verified_rows"] for item in results)}


def verify(root: Path, database_url: str) -> dict[str, Any]:
    results = [
        verify_migration(path, schema, database_url)
        for schema, path in sources(root)
    ]
    return {
        "ok": all(item["ok"] for item in results), "schemas": results,
        "rows": sum(item["target_rows"] for item in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize and verify every Spyon PostgreSQL schema."
    )
    parser.add_argument("action", choices=("plan", "apply", "verify"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "plan":
        print(json.dumps(plan(root), ensure_ascii=False, indent=2))
        return 0
    database_url = str(args.database_url or os.environ.get("DATABASE_URL") or "").strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("Укажите PostgreSQL URL через --database-url или DATABASE_URL.")
    if args.action == "apply":
        if not args.apply:
            raise SystemExit("Для миграции требуется явный флаг --apply.")
        result = apply(root, database_url)
    else:
        result = verify(root, database_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
