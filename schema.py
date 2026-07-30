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
    theme TEXT NOT NULL DEFAULT 'system',
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


CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    registration_number TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    plan_code TEXT NOT NULL DEFAULT 'demo',
    contact_email TEXT,
    contact_phone TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status, name);

CREATE TABLE IF NOT EXISTS tenant_users (
    tenant_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    tenant_role TEXT NOT NULL DEFAULT 'viewer',
    is_primary INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, user_id),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tenant_users_user ON tenant_users(user_id, is_primary, is_active);

CREATE TABLE IF NOT EXISTS tenant_integrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    integration_code TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'disabled',
    config_json TEXT NOT NULL DEFAULT '{}',
    product_count INTEGER NOT NULL DEFAULT 0,
    last_sync_at TEXT,
    last_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, integration_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tenant_integrations_status ON tenant_integrations(tenant_id, status, integration_code);


CREATE TABLE IF NOT EXISTS user_marketplace_access (
    tenant_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,user_id,marketplace_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_user_marketplace_access_user
ON user_marketplace_access(user_id,tenant_id,is_enabled,marketplace_code);

CREATE TABLE IF NOT EXISTS registration_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    registration_number TEXT,
    contact_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    integrations_json TEXT NOT NULL DEFAULT '[]',
    estimated_products INTEGER NOT NULL DEFAULT 0,
    comment TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    tenant_id INTEGER,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id),
    FOREIGN KEY(reviewed_by) REFERENCES app_users(id)
);
CREATE INDEX IF NOT EXISTS idx_registration_requests_status ON registration_requests(status, created_at DESC);

CREATE TABLE IF NOT EXISTS product_attribute_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    product_type TEXT NOT NULL,
    attribute_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'text',
    unit TEXT,
    is_identity INTEGER NOT NULL DEFAULT 0,
    is_required INTEGER NOT NULL DEFAULT 0,
    normalization_rule TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, product_type, attribute_key),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_attribute_definitions_type ON product_attribute_definitions(tenant_id, product_type, display_order);

CREATE TABLE IF NOT EXISTS product_attribute_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    source_product_code TEXT NOT NULL,
    definition_id INTEGER NOT NULL,
    raw_value TEXT,
    normalized_text TEXT,
    normalized_number REAL,
    normalized_boolean INTEGER,
    source TEXT,
    collected_at TEXT NOT NULL,
    UNIQUE(tenant_id, platform, source_product_code, definition_id),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(definition_id) REFERENCES product_attribute_definitions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_attribute_values_product ON product_attribute_values(tenant_id, platform, source_product_code);

CREATE TABLE IF NOT EXISTS operation_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    action TEXT NOT NULL,
    platform TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'all',
    recurrence_type TEXT NOT NULL DEFAULT 'daily',
    time_of_day TEXT,
    run_date TEXT,
    weekdays_json TEXT NOT NULL DEFAULT '[]',
    interval_minutes INTEGER,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    retry_count INTEGER NOT NULL DEFAULT 1,
    max_duration_minutes INTEGER NOT NULL DEFAULT 180,
    last_run_at TEXT,
    next_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES app_users(id)
);
CREATE INDEX IF NOT EXISTS idx_operation_schedules_due ON operation_schedules(is_enabled, next_run_at, tenant_id);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    task_id TEXT,
    status TEXT NOT NULL,
    message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY(schedule_id) REFERENCES operation_schedules(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_time ON schedule_runs(schedule_id, started_at DESC);


CREATE TABLE IF NOT EXISTS platform_settings (
    setting_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}',
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(updated_by) REFERENCES app_users(id)
);

CREATE TABLE IF NOT EXISTS platform_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    tenant_id INTEGER,
    entity_type TEXT,
    entity_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(actor_user_id) REFERENCES app_users(id),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id)
);
CREATE INDEX IF NOT EXISTS idx_platform_audit_time ON platform_audit_log(created_at DESC);

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

CREATE TABLE IF NOT EXISTS halyk_products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    product_url TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    price_kzt REAL,
    price_full_kzt REAL,
    currency TEXT NOT NULL DEFAULT 'KZT',
    category_ids_json TEXT NOT NULL DEFAULT '[]',
    categories_json TEXT NOT NULL DEFAULT '[]',
    specs_json TEXT NOT NULL DEFAULT '[]',
    params_json TEXT NOT NULL DEFAULT '{}',
    raw_json TEXT NOT NULL DEFAULT '{}',
    seller_name TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_catalog_at TEXT,
    last_market_at TEXT,
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_halyk_products_active_brand
ON halyk_products(active,brand,last_seen_at DESC);

CREATE TABLE IF NOT EXISTS halyk_offers (
    product_id TEXT NOT NULL,
    merchant_key TEXT NOT NULL,
    merchant_name TEXT NOT NULL DEFAULT '',
    price_kzt REAL,
    offer_type TEXT NOT NULL DEFAULT '',
    is_own INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(product_id,merchant_key),
    FOREIGN KEY(product_id) REFERENCES halyk_products(product_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_halyk_offers_product_active
ON halyk_offers(product_id,active,is_own,price_kzt);

CREATE TABLE IF NOT EXISTS halyk_price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    merchant_key TEXT NOT NULL,
    merchant_name TEXT NOT NULL DEFAULT '',
    price_kzt REAL,
    is_own INTEGER NOT NULL DEFAULT 0,
    captured_at TEXT NOT NULL,
    UNIQUE(run_id,product_id,merchant_key),
    FOREIGN KEY(product_id) REFERENCES halyk_products(product_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_halyk_price_history_product_time
ON halyk_price_history(product_id,captured_at DESC);

CREATE TABLE IF NOT EXISTS halyk_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    seller_name TEXT NOT NULL DEFAULT '',
    location_id TEXT NOT NULL DEFAULT '',
    total_reported INTEGER NOT NULL DEFAULT 0,
    products_seen INTEGER NOT NULL DEFAULT 0,
    offers_seen INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_halyk_sync_runs_time
ON halyk_sync_runs(started_at DESC);
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
        if "theme" not in _columns(conn, "app_user_preferences"):
            conn.execute("ALTER TABLE app_user_preferences ADD COLUMN theme TEXT NOT NULL DEFAULT 'system'")
        if "capabilities_json" not in _columns(conn, "registration_requests"):
            conn.execute("ALTER TABLE registration_requests ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '[]'")
        if "consent_version" not in _columns(conn, "registration_requests"):
            conn.execute("ALTER TABLE registration_requests ADD COLUMN consent_version TEXT NOT NULL DEFAULT ''")
        if "consent_at" not in _columns(conn, "registration_requests"):
            conn.execute("ALTER TABLE registration_requests ADD COLUMN consent_at TEXT")
        if "locale" not in _columns(conn, "registration_requests"):
            conn.execute("ALTER TABLE registration_requests ADD COLUMN locale TEXT NOT NULL DEFAULT 'ru'")
        if "source_page" not in _columns(conn, "registration_requests"):
            conn.execute("ALTER TABLE registration_requests ADD COLUMN source_page TEXT NOT NULL DEFAULT 'public_site'")
        if "run_date" not in _columns(conn, "operation_schedules"):
            conn.execute("ALTER TABLE operation_schedules ADD COLUMN run_date TEXT")
        if "platform_role" not in _columns(conn, "app_users"):
            conn.execute("ALTER TABLE app_users ADD COLUMN platform_role TEXT NOT NULL DEFAULT ''")
        if "tenant_id" not in _columns(conn, "app_events"):
            conn.execute("ALTER TABLE app_events ADD COLUMN tenant_id INTEGER")
        if "tenant_id" not in _columns(conn, "app_reports"):
            conn.execute("ALTER TABLE app_reports ADD COLUMN tenant_id INTEGER")
        if "tenant_id" not in _columns(conn, "app_product_state"):
            conn.execute("ALTER TABLE app_product_state ADD COLUMN tenant_id INTEGER")

        stamp = conn.execute("SELECT datetime('now')").fetchone()[0]
        tenant = conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()
        if tenant is None:
            cursor = conn.execute(
                """
                INSERT INTO tenants(name,slug,status,plan_code,contact_email,created_at,updated_at,approved_at)
                VALUES('Current Workspace','current-workspace','active','demo','',?,?,?)
                """,
                (stamp, stamp, stamp),
            )
            default_tenant_id = int(cursor.lastrowid)
        else:
            default_tenant_id = int(tenant[0])

        integrations = (
            ('kaspi','Kaspi','active'),
            ('ozon','Ozon','active'),
            ('forte_market','Forte Market','coming_soon'),
            ('halyk_market','Halyk Market','active'),
        )
        for code,title,status in integrations:
            conn.execute(
                """
                INSERT INTO tenant_integrations(tenant_id,integration_code,display_name,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(tenant_id,integration_code) DO NOTHING
                """,
                (default_tenant_id,code,title,status,stamp,stamp),
            )

        users = conn.execute("SELECT id,role FROM app_users").fetchall()
        for user_id,role in users:
            conn.execute(
                """
                INSERT INTO tenant_users(tenant_id,user_id,tenant_role,is_primary,is_active,created_at)
                VALUES(?,?,?,?,1,?)
                ON CONFLICT(tenant_id,user_id) DO NOTHING
                """,
                (default_tenant_id,int(user_id),str(role or 'viewer'),1,stamp),
            )
        memberships = conn.execute(
            "SELECT tenant_id,user_id FROM tenant_users WHERE is_active=1"
        ).fetchall()
        conn.execute(
            "UPDATE tenant_integrations SET status='active',updated_at=? "
            "WHERE integration_code='halyk_market' AND status='coming_soon'",
            (stamp,),
        )
        for membership in memberships:
            tenant_id = int(membership[0])
            user_id = int(membership[1])
            integrations = conn.execute(
                "SELECT integration_code,status FROM tenant_integrations WHERE tenant_id=?",
                (tenant_id,),
            ).fetchall()
            for integration_code, integration_status in integrations:
                enabled = 1 if str(integration_status) in {"active", "setup"} else 0
                conn.execute(
                    """
                    INSERT INTO user_marketplace_access(
                        tenant_id,user_id,marketplace_code,is_enabled,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(tenant_id,user_id,marketplace_code) DO NOTHING
                    """,
                    (tenant_id,user_id,str(integration_code),enabled,stamp,stamp),
                )

        conn.execute(
            """
            UPDATE user_marketplace_access
            SET is_enabled=1,updated_at=?
            WHERE marketplace_code='halyk_market'
              AND is_enabled=0
              AND tenant_id IN (
                  SELECT tenant_id FROM tenant_integrations
                  WHERE integration_code='halyk_market' AND status='active'
              )
            """,
            (stamp,),
        )

        first_admin = conn.execute("SELECT id FROM app_users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        if first_admin is not None:
            conn.execute(
                "UPDATE app_users SET platform_role='superadmin' WHERE id=? AND COALESCE(platform_role,'')=''",
                (int(first_admin[0]),),
            )
        conn.execute("UPDATE app_events SET tenant_id=? WHERE tenant_id IS NULL",(default_tenant_id,))
        conn.execute("UPDATE app_reports SET tenant_id=? WHERE tenant_id IS NULL",(default_tenant_id,))
        conn.execute("UPDATE app_product_state SET tenant_id=? WHERE tenant_id IS NULL",(default_tenant_id,))
        conn.commit()
    finally:
        conn.close()
