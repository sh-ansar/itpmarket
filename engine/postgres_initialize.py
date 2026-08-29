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
from engine.postgres_schema import provision_schema
from engine.postgres_migration import (
    finalize_target_schema,
    inventory,
    prepare_target_schema,
)

SCHEMA_MANIFEST_PATH = Path(__file__).with_name("postgres_schema_manifest.json")


def expected_schema_tables(root: Path) -> dict[str, set[str]]:
    try:
        raw_manifest = json.loads(SCHEMA_MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest = raw_manifest.get("schemas") if isinstance(raw_manifest, dict) else None
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("PostgreSQL schema manifest is missing or invalid.") from exc
    if not isinstance(manifest, dict) or not manifest:
        raise RuntimeError("PostgreSQL schema manifest is empty.")
    expected = {
        str(schema): {str(table) for table in tables}
        for schema, tables in manifest.items()
        if isinstance(tables, list) and tables
    }
    if not expected:
        raise RuntimeError("PostgreSQL schema manifest has no tables.")
    # The manifest is the stable provisioning contract.  Legacy SQLite source
    # discovery belongs to data import and must not expand this contract.
    return expected
    # A migration source may contain tables introduced after the checked-in
    # manifest. Include them so upgrades remain append-only and explicit.
    for schema, path in sources(root):
        expected.setdefault(schema, set()).update(
            item.table for item in inventory(path, schema)
        )
    return expected


def initialization_state(root: Path, database_url: str) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Установите зависимости из requirements-postgres.txt.") from exc
    expected = expected_schema_tables(root)
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
            if schema not in source_map:
                raise RuntimeError(
                    f"PostgreSQL schema {schema} is incomplete and this clean clone "
                    "has no SQLite migration source. Restore a verified PostgreSQL "
                    "backup or run migration from the source installation."
                )
            plans = {item.table: item for item in inventory(source_map[schema], schema)}
            unsupported = [name for name in names if name not in plans]
            if unsupported:
                raise RuntimeError(
                    f"PostgreSQL schema {schema} misses tables that are absent from "
                    f"the migration source: {unsupported}"
                )
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


def provisioned_initialize(root: Path, database_url: str) -> dict[str, Any]:
    """Provision canonical namespaces without consulting legacy SQLite files."""
    state = initialization_state(root, database_url)
    # Provisioning consists exclusively of CREATE/ALTER ... IF NOT EXISTS
    # statements and marker inserts with ON CONFLICT DO NOTHING.  Run it even
    # when the table manifest is already complete: a prior version may have
    # created all tables while lacking a newly added column, index, or
    # baseline marker.  Returning early here made --check look healthy while
    # leaving migration adoption blocked on exactly that stale state.
    provision = provision_schema(database_url)
    final_state = initialization_state(root, database_url)
    if not final_state["ready"]:
        raise RuntimeError("PostgreSQL schema provisioning did not complete.")
    return {
        "ok": True,
        "initialized": state["empty"],
        **final_state,
        "provision": provision,
    }


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
    result = initialization_state(root, database_url) if args.check else provisioned_initialize(root, database_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
