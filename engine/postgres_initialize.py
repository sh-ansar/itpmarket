from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.postgres_bootstrap import apply, sources
from engine.postgres_migration import (
    finalize_target_schema,
    inventory,
    prepare_target_schema,
)


def initialization_state(root: Path, database_url: str) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Установите зависимости из requirements-postgres.txt.") from exc
    expected = {
        schema: {item.table for item in inventory(path, schema)}
        for schema, path in sources(root)
    }
    existing: dict[str, set[str]] = {}
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for schema in expected:
                cursor.execute(
                    """SELECT table_name FROM information_schema.tables
                       WHERE table_schema=%s AND table_type='BASE TABLE'""",
                    (schema,),
                )
                existing[schema] = {str(row[0]) for row in cursor.fetchall()}
    missing = {
        schema: sorted(tables - existing.get(schema, set()))
        for schema, tables in expected.items()
        if tables - existing.get(schema, set())
    }
    present_count = sum(len(expected[schema] & existing.get(schema, set())) for schema in expected)
    expected_count = sum(len(value) for value in expected.values())
    return {
        "ready": not missing,
        "empty": present_count == 0,
        "expected_tables": expected_count,
        "present_tables": present_count,
        "missing": missing,
    }


def initialize(root: Path, database_url: str) -> dict[str, Any]:
    state = initialization_state(root, database_url)
    if state["ready"]:
        return {"ok": True, "initialized": False, **state}
    if not state["empty"]:
        source_map = dict(sources(root))
        populated_missing: dict[str, list[str]] = {}
        for schema, names in state["missing"].items():
            plans = {item.table: item for item in inventory(source_map[schema], schema)}
            populated = [name for name in names if plans[name].rows > 0]
            if populated:
                populated_missing[schema] = populated
        if populated_missing:
            raise RuntimeError(
                "Новые таблицы содержат SQLite-данные и требуют явной append-only миграции: "
                + json.dumps(populated_missing, ensure_ascii=False)
            )
        upgrades = []
        for schema in state["missing"]:
            source = source_map[schema]
            prepared = prepare_target_schema(source, schema, database_url)
            finalized = finalize_target_schema(source, schema, database_url)
            upgrades.append({"schema": schema, **prepared, **finalized})
        final_state = initialization_state(root, database_url)
        if not final_state["ready"]:
            raise RuntimeError("Инкрементальное обновление схемы PostgreSQL не завершено.")
        return {
            "ok": True, "initialized": False, "upgraded": True,
            **final_state, "schema_upgrades": upgrades,
        }
    result = apply(root, database_url)
    final_state = initialization_state(root, database_url)
    if not final_state["ready"]:
        raise RuntimeError("Инициализация PostgreSQL не завершена.")
    return {"ok": True, "initialized": True, **final_state, "migration": result}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely initialize PostgreSQL once; never overwrite a live database."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    database_url = str(args.database_url or os.environ.get("DATABASE_URL") or "").strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("Укажите DATABASE_URL для PostgreSQL.")
    root = args.root.resolve()
    result = initialization_state(root, database_url) if args.check else initialize(root, database_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
