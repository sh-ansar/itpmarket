from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from schema import ensure_database

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "unityre_kaspi.db"
BACKUPS = ROOT / "backups"
OZON_ROOT = ROOT / "collectors" / "ozon"
OZON_DB_PATH = OZON_ROOT / "data" / "ozon_registry.db"
START_URLS_PATH = OZON_ROOT / "START_URLS.txt"
START_URL_PATH = OZON_ROOT / "START_URL.txt"
EXPECTED_SELLER_PATH = OZON_ROOT / "EXPECTED_SELLER.txt"

MARKET_URLS = [
    "https://www.ozon.ru/category/shiny-8502/",
    "https://www.ozon.ru/category/shiny-letnie-8506/",
    "https://www.ozon.ru/category/shiny-zimnie-8803/?__rr=1",
    "https://www.ozon.ru/category/vsesezonnye-shiny-37388/",
]

OZON_SOURCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_sources (
    source_url TEXT PRIMARY KEY,
    source_type TEXT NOT NULL DEFAULT 'MARKET_CATEGORY',
    label TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS product_sources (
    article TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'MARKET_CATEGORY',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL DEFAULT '',
    page_no INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(article,source_url)
);
CREATE INDEX IF NOT EXISTS idx_product_sources_type
    ON product_sources(source_type,article);
CREATE TABLE IF NOT EXISTS market_search_jobs (
    client_article TEXT PRIMARY KEY,
    query_text TEXT NOT NULL DEFAULT '',
    query_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'NEW',
    attempts INTEGER NOT NULL DEFAULT 0,
    candidates_found INTEGER NOT NULL DEFAULT 0,
    exact_found INTEGER NOT NULL DEFAULT 0,
    comparable_found INTEGER NOT NULL DEFAULT 0,
    last_search_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    last_run_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS market_search_candidates (
    client_article TEXT NOT NULL,
    candidate_article TEXT NOT NULL,
    query_text TEXT NOT NULL DEFAULT '',
    query_url TEXT NOT NULL DEFAULT '',
    catalog_rank INTEGER NOT NULL DEFAULT 0,
    match_level TEXT NOT NULL DEFAULT 'REJECTED',
    match_score REAL NOT NULL DEFAULT 0,
    match_method TEXT NOT NULL DEFAULT '',
    match_reason TEXT NOT NULL DEFAULT '',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(client_article,candidate_article)
);
CREATE INDEX IF NOT EXISTS idx_market_search_candidates_level
    ON market_search_candidates(client_article,active,match_level,match_score DESC);
"""


def lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        value.strip()
        for value in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if value.strip() and not value.strip().startswith("#")
    ]


def normalized(value: object) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def source_type(url: str) -> str:
    lower_url = url.casefold()
    if "/seller/" in lower_url:
        return "CLIENT_CATALOG"
    if "/search/" in lower_url:
        return "MARKET_SEARCH"
    return "MARKET_CATEGORY"


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def row_value(row: sqlite3.Row, key: str, default: object = "") -> object:
    return row[key] if key in row.keys() else default


def count_or_zero(conn: sqlite3.Connection, sql: str) -> int:
    try:
        return int(conn.execute(sql).fetchone()[0])
    except sqlite3.Error:
        return 0


def backup_main_database() -> Path | None:
    if not DB_PATH.exists():
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUPS / f"pre_migrate_spyon_{stamp}.db"
    shutil.copy2(DB_PATH, backup)
    return backup


def ensure_main_database() -> int:
    backup = backup_main_database()
    ensure_database(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE app_user_preferences SET theme='system' "
            "WHERE theme IS NULL OR theme NOT IN ('system','light','dark')"
        )
        conn.commit()
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        tenant = conn.execute("SELECT name FROM tenants ORDER BY id LIMIT 1").fetchone()
        print("Spyon database migration: OK" if integrity == "ok" else "Spyon database migration: FAILED")
        if backup:
            print(f"Main database backup: {backup}")
        print(f"Database integrity: {integrity}")
        print(f"Default workspace: {tenant[0] if tenant else 'not created'}")
        print(f"Users: {count_or_zero(conn, 'SELECT COUNT(*) FROM app_users')}")
        print(f"Tenant memberships: {count_or_zero(conn, 'SELECT COUNT(*) FROM tenant_users')}")
        print(f"Integrations: {count_or_zero(conn, 'SELECT COUNT(*) FROM tenant_integrations')}")
        print(f"Schedules: {count_or_zero(conn, 'SELECT COUNT(*) FROM operation_schedules')}")
        return 0 if integrity == "ok" else 2
    finally:
        conn.close()


def merge_ozon_market_urls() -> int:
    existing = lines(START_URLS_PATH) or lines(START_URL_PATH)
    merged: list[str] = []
    seen: set[str] = set()
    for url in [*existing, *MARKET_URLS]:
        key = url.strip().casefold().rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(url)
    START_URLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    START_URLS_PATH.write_text("\n".join(merged) + "\n", encoding="utf-8")
    if merged:
        START_URL_PATH.write_text(merged[0] + "\n", encoding="utf-8")
    return len(merged)


def seller_ids_from_urls(urls: list[str]) -> set[str]:
    seller_ids: set[str] = set()
    for url in urls:
        lower_url = url.casefold()
        marker = "/seller/"
        if marker not in lower_url:
            continue
        tail = url[lower_url.index(marker) + len(marker) :].split("/", 1)[0]
        if "-" in tail:
            seller_ids.add(tail.rsplit("-", 1)[-1])
    return seller_ids


def ensure_ozon_registry() -> None:
    url_count = merge_ozon_market_urls()
    if not OZON_DB_PATH.exists():
        print("Ozon registry not found; schema will be created after the first collector run.")
        print(f"Ozon source URLs: {url_count}")
        return

    expected = normalized((lines(EXPECTED_SELLER_PATH) or [""])[0])
    seller_ids = seller_ids_from_urls(lines(START_URLS_PATH))

    conn = sqlite3.connect(OZON_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(OZON_SOURCE_SCHEMA)
        now = datetime.now().isoformat(timespec="seconds")
        with conn:
            if table_exists(conn, "products"):
                for row in conn.execute("SELECT * FROM products").fetchall():
                    article = str(row_value(row, "article") or "").strip()
                    url = str(row_value(row, "discovery_url") or "").strip()
                    if not article:
                        continue
                    if not url:
                        continue
                    kind = source_type(url)
                    first_seen = str(row_value(row, "first_seen_at", now) or now)
                    last_seen = str(row_value(row, "last_seen_at", first_seen) or first_seen)
                    conn.execute(
                        """
                        INSERT INTO catalog_sources(source_url,source_type,label,first_seen_at,last_seen_at,active)
                        VALUES(?,?,?,?,?,1)
                        ON CONFLICT(source_url) DO UPDATE SET
                            source_type=excluded.source_type,
                            last_seen_at=excluded.last_seen_at,
                            active=1
                        """,
                        (url, kind, "", first_seen, last_seen),
                    )
                    conn.execute(
                        """
                        INSERT INTO product_sources(
                            article,source_url,source_type,first_seen_at,last_seen_at,last_run_id,page_no
                        ) VALUES(?,?,?,?,?,'',0)
                        ON CONFLICT(article,source_url) DO UPDATE SET
                            source_type=excluded.source_type,
                            last_seen_at=excluded.last_seen_at
                        """,
                        (article, url, kind, first_seen, last_seen),
                    )

            if table_exists(conn, "offers"):
                for row in conn.execute("SELECT * FROM offers").fetchall():
                    if "active" in row.keys() and int(row["active"] or 0) != 1:
                        continue
                    article = str(row_value(row, "article") or "").strip()
                    if not article:
                        continue
                    own = (
                        str(row_value(row, "seller_id") or "").strip() in seller_ids
                        or (expected and normalized(row_value(row, "seller_name")) == expected)
                    )
                    if not own:
                        continue
                    seller_id = str(row_value(row, "seller_id") or "").strip()
                    seller_url = str(row_value(row, "seller_url") or "").strip()
                    url = seller_url or f"seller://{seller_id or expected}"
                    first_seen = str(row_value(row, "first_seen_at", now) or now)
                    last_seen = str(row_value(row, "last_seen_at", first_seen) or first_seen)
                    conn.execute(
                        """
                        INSERT INTO catalog_sources(source_url,source_type,label,first_seen_at,last_seen_at,active)
                        VALUES(?,'CLIENT_CATALOG','',?,?,1)
                        ON CONFLICT(source_url) DO UPDATE SET
                            source_type='CLIENT_CATALOG',
                            last_seen_at=excluded.last_seen_at,
                            active=1
                        """,
                        (url, first_seen, last_seen),
                    )
                    conn.execute(
                        """
                        INSERT INTO product_sources(
                            article,source_url,source_type,first_seen_at,last_seen_at,last_run_id,page_no
                        ) VALUES(?,?,'CLIENT_CATALOG',?,?, '',0)
                        ON CONFLICT(article,source_url) DO UPDATE SET
                            source_type='CLIENT_CATALOG',
                            last_seen_at=excluded.last_seen_at
                        """,
                        (article, url, first_seen, last_seen),
                    )

            conn.execute(
                "UPDATE catalog_sources SET source_type='MARKET_SEARCH' WHERE lower(source_url) LIKE '%/search/%'"
            )
            conn.execute(
                "UPDATE product_sources SET source_type='MARKET_SEARCH' WHERE lower(source_url) LIKE '%/search/%'"
            )

        client_count = count_or_zero(
            conn,
            "SELECT COUNT(DISTINCT article) FROM product_sources WHERE source_type='CLIENT_CATALOG'",
        )
        market_count = count_or_zero(
            conn,
            "SELECT COUNT(DISTINCT article) FROM product_sources "
            "WHERE source_type IN ('MARKET_CATEGORY','MARKET_SEARCH')",
        )
        print("Ozon registry migration: OK")
        print(f"Ozon registry: {OZON_DB_PATH}")
        print(f"Ozon source URLs: {url_count}")
        print(f"Client catalogue: {client_count}")
        print(f"Market listings: {market_count}")
    finally:
        conn.close()


def main() -> int:
    main_status = ensure_main_database()
    ensure_ozon_registry()
    print("Existing products, prices, schedules, browser profiles and collector history were preserved.")
    return main_status


if __name__ == "__main__":
    raise SystemExit(main())
