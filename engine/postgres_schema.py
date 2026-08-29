"""SQLite-independent PostgreSQL schema provisioning.

The checked-in runtime schema declarations are the source material for the
application contract.  This module compiles their portable DDL directly to
PostgreSQL; it never opens a legacy SQLite database.  Legacy databases remain
the responsibility of :mod:`engine.postgres_migration` only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from collectors.ozon.registry import SCHEMA as OZON_RU_SCHEMA
from collectors.ozon_kz.storage import SCHEMA as OZON_KZ_SCHEMA
from engine.postgres_migrations import split_sql
from schema import BASE_SCHEMA


APP_RUNTIME_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL
);
ALTER TABLE app_events ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
ALTER TABLE app_product_state ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
ALTER TABLE product_attribute_definitions ADD COLUMN IF NOT EXISTS last_seen_at TEXT;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS platform_role TEXT NOT NULL DEFAULT '';
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS email_verified_at TEXT;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE registration_requests ADD COLUMN IF NOT EXISTS capabilities_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE registration_requests ADD COLUMN IF NOT EXISTS consent_version TEXT NOT NULL DEFAULT '';
ALTER TABLE registration_requests ADD COLUMN IF NOT EXISTS consent_at TEXT;
ALTER TABLE registration_requests ADD COLUMN IF NOT EXISTS locale TEXT NOT NULL DEFAULT 'ru';
ALTER TABLE registration_requests ADD COLUMN IF NOT EXISTS source_page TEXT NOT NULL DEFAULT 'public_site';
CREATE TABLE IF NOT EXISTS market_search_runs (
    source_product_code TEXT PRIMARY KEY, query_text TEXT, status TEXT,
    candidates_found INTEGER, candidates_validated INTEGER, accepted_count INTEGER,
    review_count INTEGER, error TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS market_candidates (
    source_product_code TEXT, candidate_product_code TEXT, search_page INTEGER,
    search_position INTEGER, candidate_title TEXT, candidate_url TEXT,
    candidate_price_kzt INTEGER, candidate_rating REAL, candidate_reviews INTEGER,
    fast_score REAL, fast_decision TEXT, fast_reason TEXT, candidate_title_detail TEXT,
    candidate_specs_json TEXT, detail_score REAL, final_decision TEXT,
    detail_reason TEXT, checked_at TEXT,
    PRIMARY KEY(source_product_code,candidate_product_code)
);
CREATE TABLE IF NOT EXISTS market_seller_offers (
    source_product_code TEXT, candidate_product_code TEXT, merchant_id TEXT,
    merchant_name TEXT, merchant_sku TEXT, price_kzt REAL, merchant_rating REAL,
    merchant_reviews INTEGER, captured_at TEXT,
    PRIMARY KEY(source_product_code,candidate_product_code,merchant_id,merchant_sku,price_kzt)
);
CREATE TABLE IF NOT EXISTS market_search_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_product_code TEXT, stage TEXT,
    message TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS v9_product_cache (
    product_code TEXT PRIMARY KEY, title TEXT, product_url TEXT,
    specifications_json TEXT, product_type TEXT, group_key TEXT, fetched_at TEXT,
    source TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS v9_group_search_cache (
    group_key TEXT PRIMARY KEY, query_text TEXT, cards_json TEXT, cards_count INTEGER,
    search_pages INTEGER, fetched_at TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS v9_discovery_state (
    source_product_code TEXT PRIMARY KEY, group_key TEXT, query_text TEXT, status TEXT,
    exact_status TEXT, candidates_found INTEGER, candidates_validated INTEGER,
    accepted_count INTEGER, review_count INTEGER, cache_hits INTEGER, cache_misses INTEGER,
    duration_seconds REAL, error TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS v9_price_state (
    candidate_product_code TEXT PRIMARY KEY, status TEXT, offers_count INTEGER,
    min_price_kzt REAL, duration_seconds REAL, error TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS v9_price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_product_code TEXT,
    candidate_product_code TEXT, merchant_id TEXT, merchant_name TEXT,
    merchant_sku TEXT, price_kzt REAL, merchant_rating REAL,
    merchant_reviews INTEGER, captured_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_v9_price_snapshots_code_time
ON v9_price_snapshots(candidate_product_code,captured_at);
CREATE TABLE IF NOT EXISTS v9_run_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT, item_key TEXT, status TEXT,
    duration_seconds REAL, cache_hits INTEGER, cache_misses INTEGER, attempts INTEGER,
    error TEXT, started_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_v9_metrics_command_time ON v9_run_metrics(command,finished_at);
CREATE TABLE IF NOT EXISTS v9_catalog_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT, rows_read INTEGER,
    rows_valid INTEGER, inserted_count INTEGER, updated_count INTEGER, imported_at TEXT
);
CREATE TABLE IF NOT EXISTS v9_catalog_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_count INTEGER, unique_count INTEGER,
    duplicate_count INTEGER, max_page INTEGER, typical_page_size INTEGER,
    last_page_size INTEGER, details_ok INTEGER, details_error INTEGER,
    details_missing INTEGER, expected_count INTEGER, coverage_pct REAL,
    suspected_truncation INTEGER, audit_json TEXT, audited_at TEXT
);
CREATE TABLE IF NOT EXISTS user_marketplace_access (
    tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, marketplace_code TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,user_id,marketplace_code)
);
CREATE INDEX IF NOT EXISTS idx_tenant_sellers_approval
ON tenant_marketplace_sellers(tenant_id, marketplace_code, approval_status, status);
CREATE INDEX IF NOT EXISTS idx_tenant_integrations_approval
ON tenant_integrations(approval_status, submitted_at, tenant_id, integration_code);
"""


def _postgres_statement(statement: str) -> str | None:
    value = statement.strip()
    if not value or re.match(r"^PRAGMA\b", value, re.I):
        return None
    value = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
        value,
        flags=re.I,
    )
    value = re.sub(r"\bAUTOINCREMENT\b", "", value, flags=re.I)
    # SQLite REAL is an IEEE-754 double. PostgreSQL REAL is float4, so using
    # it loses source precision during a legacy import.
    value = re.sub(r"\bREAL\b", "DOUBLE PRECISION", value, flags=re.I)
    value = re.sub(r"\s+COLLATE\s+NOCASE\b", "", value, flags=re.I)
    value = re.sub(r"datetime\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", value, flags=re.I)
    value = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", value, flags=re.I)
    if re.match(r"^INSERT\s+INTO\b", value, re.I) and "ON CONFLICT" not in value.upper():
        value = value.rstrip() + " ON CONFLICT DO NOTHING"
    return value


def _statements(script: str) -> Iterable[str]:
    for statement in split_sql(script):
        converted = _postgres_statement(statement)
        if converted:
            yield converted


def provision_schema(database_url: str) -> dict[str, object]:
    """Create all supported namespaces and their current empty schema."""
    import psycopg

    sources = {
        "app": (BASE_SCHEMA, APP_RUNTIME_SCHEMA),
        "ozon_ru": (OZON_RU_SCHEMA,),
        # KZ includes the generic Ozon registry contract plus KZ-specific data.
        "ozon_kz": (OZON_RU_SCHEMA, OZON_KZ_SCHEMA),
    }
    result: dict[str, int] = {}
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for namespace, scripts in sources.items():
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{namespace}"')
                cursor.execute(f'SET LOCAL search_path TO "{namespace}"')
                pending = [
                    statement
                    for script in scripts
                    for statement in _statements(script)
                ]
                count = 0
                # SQLite accepts forward foreign-key references.  PostgreSQL
                # does not, so retry only statements whose referenced table is
                # not yet available, using savepoints to keep the transaction
                # usable after an expected dependency error.
                while pending:
                    deferred: list[str] = []
                    progressed = False
                    for statement in pending:
                        try:
                            with connection.transaction():
                                cursor.execute(statement)
                        except psycopg.errors.UndefinedTable:
                            deferred.append(statement)
                            continue
                        count += 1
                        progressed = True
                    if not progressed:
                        raise RuntimeError(
                            f"Unresolved PostgreSQL DDL dependencies in schema {namespace}: "
                            + " | ".join(deferred[:3])
                        )
                    pending = deferred
                if namespace == "app":
                    for marker in (
                        "schema_multi_seller_v1_backfilled",
                        "schema_inventory_matching_v1",
                        "schema_telegram_notifications_v1",
                        "schema_email_auth_notifications_v1",
                    ):
                        cursor.execute(
                            """INSERT INTO app.metadata(key,value) VALUES(%s,CURRENT_TIMESTAMP::text)
                               ON CONFLICT(key) DO NOTHING""",
                            (marker,),
                        )
                result[namespace] = count
    return {"ok": True, "schemas": result}
