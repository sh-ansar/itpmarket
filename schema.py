from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS catalog_products (
    product_code TEXT PRIMARY KEY,
    page_number INTEGER,
    position_on_page INTEGER,
    title_catalog TEXT,
    catalog_price_kzt INTEGER,
    catalog_rating REAL,
    catalog_reviews INTEGER,
    product_url TEXT,
    image_url TEXT,
    catalog_page_url TEXT,
    collected_at TEXT
);
CREATE TABLE IF NOT EXISTS product_details (
    product_code TEXT PRIMARY KEY,
    product_url TEXT,
    title_detail TEXT,
    price_kzt INTEGER,
    product_rating REAL,
    product_reviews INTEGER,
    specifications_json TEXT,
    detail_status TEXT,
    detail_error TEXT,
    detail_collected_at TEXT
);
CREATE TABLE IF NOT EXISTS offers (
    product_code TEXT,
    merchant_id TEXT,
    merchant_name TEXT,
    merchant_sku TEXT,
    price_kzt REAL,
    merchant_rating REAL,
    merchant_reviews_quantity INTEGER,
    purchase_count INTEGER,
    offer_title TEXT,
    delivery TEXT,
    delivery_duration TEXT,
    kaspi_delivery INTEGER,
    source_page INTEGER,
    collected_at TEXT,
    PRIMARY KEY(product_code, merchant_id, merchant_sku, price_kzt)
);
CREATE TABLE IF NOT EXISTS offer_scans (
    product_code TEXT PRIMARY KEY,
    status TEXT,
    total INTEGER,
    offers_count INTEGER,
    error TEXT,
    collected_at TEXT
);
CREATE TABLE IF NOT EXISTS search_matches (
    source_product_code TEXT,
    candidate_product_code TEXT,
    source_title TEXT,
    candidate_title TEXT,
    score REAL,
    decision TEXT,
    reason TEXT,
    collected_at TEXT,
    PRIMARY KEY(source_product_code, candidate_product_code)
);
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT,
    product_code TEXT,
    message TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS catalog_product_meta (
    product_code TEXT PRIMARY KEY,
    brand TEXT,
    category_id TEXT,
    category_codes_json TEXT,
    base_product_codes_json TEXT,
    groups_json TEXT,
    has_variants INTEGER,
    stock INTEGER,
    delivery_duration TEXT,
    best_merchant TEXT,
    source_segment TEXT,
    active INTEGER DEFAULT 1,
    own_offer_active INTEGER,
    price_before_discount_kzt REAL,
    discount_percent REAL,
    first_seen_at TEXT,
    last_seen_at TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_catalog_meta_active_brand ON catalog_product_meta(active, brand);
CREATE TABLE IF NOT EXISTS catalog_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT,
    reported_total INTEGER,
    collected_unique INTEGER,
    segments_total INTEGER,
    segments_success INTEGER,
    warning TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS catalog_segment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    segment_name TEXT,
    expected_count INTEGER,
    reported_count INTEGER,
    collected_unique INTEGER,
    strategy TEXT,
    request_url TEXT,
    status TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_catalog_segment_runs_run ON catalog_segment_runs(run_id, segment_name);
CREATE TABLE IF NOT EXISTS own_price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code TEXT,
    merchant_id TEXT,
    merchant_sku TEXT,
    price_kzt REAL,
    price_before_discount_kzt REAL,
    discount_percent REAL,
    status TEXT,
    error TEXT,
    captured_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_own_price_code_time ON own_price_snapshots(product_code, captured_at DESC);
CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    recovery_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    password_changed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_users_active ON app_users(is_active, role);
CREATE TABLE IF NOT EXISTS app_product_state (
    product_code TEXT PRIMARY KEY,
    watched INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'normal',
    note TEXT,
    expected_monthly_units INTEGER,
    updated_by INTEGER,
    updated_at TEXT,
    FOREIGN KEY(updated_by) REFERENCES app_users(id)
);
CREATE INDEX IF NOT EXISTS idx_app_product_state_watch ON app_product_state(watched, priority);
CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES app_users(id)
);
CREATE INDEX IF NOT EXISTS idx_app_events_time ON app_events(created_at DESC);
CREATE TABLE IF NOT EXISTS app_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    rows_count INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(created_by) REFERENCES app_users(id)
);
CREATE INDEX IF NOT EXISTS idx_app_reports_time ON app_reports(created_at DESC);

CREATE TABLE IF NOT EXISTS app_user_preferences (
    user_id INTEGER PRIMARY KEY,
    locale TEXT NOT NULL DEFAULT 'ru',
    display_currency TEXT NOT NULL DEFAULT 'KZT',
    rub_to_kzt REAL NOT NULL DEFAULT 5.50,
    usd_to_kzt REAL NOT NULL DEFAULT 520.00,
    eur_to_kzt REAL NOT NULL DEFAULT 565.00,
    default_monthly_units INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS market_match_overrides (
    source_platform TEXT NOT NULL,
    source_product_code TEXT NOT NULL,
    candidate_platform TEXT NOT NULL,
    candidate_product_code TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    updated_by INTEGER,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY(source_platform,source_product_code,candidate_platform,candidate_product_code),
    FOREIGN KEY(updated_by) REFERENCES app_users(id)
);

CREATE TABLE IF NOT EXISTS app_market_links (
    internal_product_code TEXT NOT NULL,
    platform TEXT NOT NULL,
    source_product_code TEXT NOT NULL,
    match_type TEXT NOT NULL,
    match_score REAL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY(internal_product_code,platform,source_product_code)
);
CREATE INDEX IF NOT EXISTS idx_app_market_links_source ON app_market_links(platform,source_product_code);

CREATE TABLE IF NOT EXISTS exact_offer_scans (
    product_code TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    offers_count INTEGER NOT NULL DEFAULT 0,
    competitor_count INTEGER NOT NULL DEFAULT 0,
    min_price_kzt REAL,
    max_price_kzt REAL,
    duration_seconds REAL,
    error TEXT,
    checked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_exact_offer_scans_status_time
ON exact_offer_scans(status,checked_at);

CREATE TABLE IF NOT EXISTS exact_offer_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    product_code TEXT NOT NULL,
    merchant_id TEXT,
    merchant_name TEXT,
    merchant_sku TEXT,
    price_kzt REAL,
    merchant_rating REAL,
    merchant_reviews INTEGER,
    is_own INTEGER NOT NULL DEFAULT 0,
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exact_offer_snapshots_product_time
ON exact_offer_snapshots(product_code,captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_exact_offer_snapshots_run
ON exact_offer_snapshots(run_id,product_code);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.executescript(BASE_SCHEMA)
        if "expected_monthly_units" not in _columns(conn, "app_product_state"):
            conn.execute("ALTER TABLE app_product_state ADD COLUMN expected_monthly_units INTEGER")
        conn.commit()
    finally:
        conn.close()
