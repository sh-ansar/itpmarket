from __future__ import annotations

from pathlib import Path
from typing import Any
from storage.postgres_compat import configure_connection, connect_database


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS ozon_kz_connector_metadata (
    id INTEGER PRIMARY KEY CHECK(id=1),
    status TEXT NOT NULL DEFAULT 'source_required',
    source_url TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    auth_mode TEXT NOT NULL DEFAULT '',
    credential_ref TEXT NOT NULL DEFAULT '',
    last_success_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO ozon_kz_connector_metadata(id) VALUES(1);

CREATE TABLE IF NOT EXISTS ozon_kz_products (
    product_id TEXT PRIMARY KEY,
    seller_sku TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    specifications_json TEXT NOT NULL DEFAULT '[]',
    canonical_url TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'KZT' CHECK(currency='KZT'),
    own_price_kzt REAL,
    availability_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    active INTEGER NOT NULL DEFAULT 1,
    source_payload_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ozon_kz_products_active
ON ozon_kz_products(active,last_seen_at DESC);

CREATE TABLE IF NOT EXISTS ozon_kz_offers (
    product_id TEXT NOT NULL,
    seller_id TEXT NOT NULL,
    seller_name TEXT NOT NULL DEFAULT '',
    seller_url TEXT NOT NULL DEFAULT '',
    price_kzt REAL,
    regular_price_kzt REAL,
    availability_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    is_own INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    captured_at TEXT NOT NULL,
    PRIMARY KEY(product_id,seller_id),
    FOREIGN KEY(product_id) REFERENCES ozon_kz_products(product_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ozon_kz_offers_product_price
ON ozon_kz_offers(product_id,active,price_kzt);

CREATE TABLE IF NOT EXISTS ozon_kz_price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    seller_id TEXT NOT NULL,
    price_kzt REAL,
    availability_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    captured_at TEXT NOT NULL,
    FOREIGN KEY(product_id) REFERENCES ozon_kz_products(product_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ozon_kz_history_product_time
ON ozon_kz_price_history(product_id,captured_at DESC);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_database(path, timeout=30)
    return configure_connection(conn, foreign_keys=True, busy_timeout=30000)


def ensure_schema(path: Path) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def status(path: Path) -> dict[str, Any]:
    ensure_schema(path)
    conn = connect(path)
    try:
        metadata = dict(conn.execute(
            "SELECT * FROM ozon_kz_connector_metadata WHERE id=1"
        ).fetchone())
        metadata["products"] = int(conn.execute(
            "SELECT COUNT(*) FROM ozon_kz_products WHERE active=1"
        ).fetchone()[0])
        metadata["offers"] = int(conn.execute(
            "SELECT COUNT(*) FROM ozon_kz_offers WHERE active=1"
        ).fetchone()[0])
        # A credential reference is safe to expose only as presence/absence.
        metadata["credential_configured"] = bool(metadata.pop("credential_ref", ""))
        return metadata
    finally:
        conn.close()
