from __future__ import annotations

import sqlite3
from pathlib import Path
from storage.database_backend import DatabaseBackend, DatabaseSettings

from marketplace_registry import MARKETPLACES
from tenant_security import ROLE_DEFAULT_PERMISSIONS, ROLE_LABELS


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
    password_changed_at TEXT,
    email_verified_at TEXT,
    session_version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_app_users_active ON app_users(is_active, role);
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_email_normalized ON app_users(lower(email));
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
CREATE TABLE IF NOT EXISTS app_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL DEFAULT 'system',
    event_type TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    action_url TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    read_at TEXT,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_app_notifications_user_time ON app_notifications(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_notifications_unread ON app_notifications(user_id,read_at,created_at DESC);
CREATE TABLE IF NOT EXISTS auth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL,
    request_ip TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_active
ON auth_tokens(user_id,purpose,consumed_at,expires_at,id DESC);
CREATE TABLE IF NOT EXISTS email_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER,
    user_id INTEGER,
    recipient TEXT NOT NULL,
    template_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','sending','retry','sent','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_email_outbox_due
ON email_outbox(status,next_attempt_at,id);
CREATE INDEX IF NOT EXISTS idx_email_outbox_user
ON email_outbox(user_id,id DESC);
CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    in_app_enabled INTEGER NOT NULL DEFAULT 1 CHECK(in_app_enabled IN (0,1)),
    email_enabled INTEGER NOT NULL DEFAULT 1 CHECK(email_enabled IN (0,1)),
    telegram_enabled INTEGER NOT NULL DEFAULT 1 CHECK(telegram_enabled IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id,category),
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_notification_preferences_user
ON notification_preferences(user_id,category);
CREATE TABLE IF NOT EXISTS telegram_user_links (
    user_id INTEGER PRIMARY KEY,
    tenant_id INTEGER,
    chat_id INTEGER NOT NULL UNIQUE,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    telegram_username TEXT NOT NULL DEFAULT '',
    telegram_display_name TEXT NOT NULL DEFAULT '',
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0,1)),
    notification_start_id INTEGER NOT NULL DEFAULT 0,
    linked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_telegram_user_links_tenant
ON telegram_user_links(tenant_id,is_enabled);
CREATE TABLE IF NOT EXISTS telegram_notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    tenant_id INTEGER,
    chat_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','retry','sent','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    telegram_message_id INTEGER,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE(notification_id,chat_id),
    FOREIGN KEY(notification_id) REFERENCES app_notifications(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_telegram_deliveries_pending
ON telegram_notification_deliveries(status,next_attempt_at,id);
CREATE INDEX IF NOT EXISTS idx_telegram_deliveries_user
ON telegram_notification_deliveries(user_id,id DESC);
CREATE TABLE IF NOT EXISTS app_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    rows_count INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    tenant_id INTEGER,
    platforms_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(created_by) REFERENCES app_users(id),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
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
    workspace_profile_json TEXT NOT NULL DEFAULT '{}',
    contact_email TEXT,
    contact_phone TEXT,
    legal_address TEXT NOT NULL DEFAULT '',
    actual_address TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS legal_acceptances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    document_type TEXT NOT NULL CHECK(document_type IN ('offer','privacy')),
    document_number TEXT NOT NULL,
    document_version TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    locale TEXT NOT NULL DEFAULT 'ru',
    acceptance_text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'registration',
    created_at TEXT NOT NULL,
    UNIQUE(user_id, document_type, document_version),
    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_legal_acceptances_tenant ON legal_acceptances(tenant_id, accepted_at DESC);

CREATE TABLE IF NOT EXISTS tenant_roles (
    tenant_id INTEGER NOT NULL,
    role_code TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_system INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,role_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tenant_role_permissions (
    tenant_id INTEGER NOT NULL,
    role_code TEXT NOT NULL,
    permission_code TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,role_code,permission_code),
    FOREIGN KEY(tenant_id,role_code) REFERENCES tenant_roles(tenant_id,role_code) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tenant_role_permissions_lookup
ON tenant_role_permissions(tenant_id,role_code,is_enabled,permission_code);

CREATE TABLE IF NOT EXISTS tenant_user_permissions (
    tenant_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    permission_code TEXT NOT NULL,
    is_enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,user_id,permission_code),
    FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_users(tenant_id,user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tenant_user_permissions_lookup
ON tenant_user_permissions(tenant_id,user_id,is_enabled,permission_code);

CREATE TABLE IF NOT EXISTS tenant_user_marketplace_access (
    tenant_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    is_allowed INTEGER NOT NULL DEFAULT 1,
    updated_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,user_id,marketplace_code),
    FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_users(tenant_id,user_id) ON DELETE CASCADE,
    FOREIGN KEY(updated_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_user_marketplace_access_lookup
ON tenant_user_marketplace_access(tenant_id,user_id,is_allowed,marketplace_code);

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
    seller_name TEXT NOT NULL DEFAULT '',
    seller_identifier TEXT NOT NULL DEFAULT '',
    seller_url TEXT NOT NULL DEFAULT '',
    discovery_status TEXT NOT NULL DEFAULT 'idle',
    approval_status TEXT NOT NULL DEFAULT 'draft',
    discovery_json TEXT NOT NULL DEFAULT '{}',
    submitted_by INTEGER,
    submitted_at TEXT,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, integration_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(submitted_by) REFERENCES app_users(id) ON DELETE SET NULL,
    FOREIGN KEY(reviewed_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_integrations_status ON tenant_integrations(tenant_id, status, integration_code);

CREATE TABLE IF NOT EXISTS tenant_marketplace_sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    external_seller_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    credential_ref TEXT,
    status TEXT NOT NULL DEFAULT 'setup',
    config_json TEXT NOT NULL DEFAULT '{}',
    discovery_status TEXT NOT NULL DEFAULT 'idle',
    approval_status TEXT NOT NULL DEFAULT 'draft',
    discovery_json TEXT NOT NULL DEFAULT '{}',
    submitted_by INTEGER,
    submitted_at TEXT,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT '',
    product_count INTEGER NOT NULL DEFAULT 0,
    last_sync_at TEXT,
    last_status TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id,marketplace_code,external_seller_id),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(submitted_by) REFERENCES app_users(id) ON DELETE SET NULL,
    FOREIGN KEY(reviewed_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_sellers_marketplace
ON tenant_marketplace_sellers(tenant_id,marketplace_code,status);
CREATE TABLE IF NOT EXISTS tenant_seller_catalog_products (
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    tenant_seller_id INTEGER NOT NULL,
    source_product_code TEXT NOT NULL,
    catalog_id INTEGER,
    seller_sku TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    category_name TEXT NOT NULL DEFAULT '',
    price_amount REAL,
    currency TEXT NOT NULL DEFAULT '',
    availability_status TEXT NOT NULL DEFAULT '',
    attributes_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_updated_at TEXT,
    PRIMARY KEY(tenant_id,marketplace_code,tenant_seller_id,source_product_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE CASCADE,
    FOREIGN KEY(catalog_id) REFERENCES tenant_catalogs(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_seller_products_visible
ON tenant_seller_catalog_products(
    tenant_id,marketplace_code,tenant_seller_id,active,last_seen_at
);

CREATE TABLE IF NOT EXISTS tenant_seller_price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    tenant_seller_id INTEGER NOT NULL,
    source_product_code TEXT NOT NULL,
    seller_sku TEXT NOT NULL DEFAULT '',
    price_amount REAL,
    price_before_discount REAL,
    discount_percent REAL,
    currency TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tenant_seller_price_history
ON tenant_seller_price_snapshots(
    tenant_id,marketplace_code,tenant_seller_id,source_product_code,captured_at DESC
);

CREATE TABLE IF NOT EXISTS tenant_seller_offer_scans (
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    tenant_seller_id INTEGER NOT NULL,
    source_product_code TEXT NOT NULL,
    status TEXT NOT NULL,
    offers_count INTEGER NOT NULL DEFAULT 0,
    competitor_count INTEGER NOT NULL DEFAULT 0,
    min_price REAL,
    max_price REAL,
    duration_seconds REAL,
    error TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL,
    PRIMARY KEY(
        tenant_id,marketplace_code,tenant_seller_id,source_product_code
    ),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tenant_seller_offer_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    tenant_seller_id INTEGER NOT NULL,
    source_product_code TEXT NOT NULL,
    merchant_id TEXT NOT NULL DEFAULT '',
    merchant_name TEXT NOT NULL DEFAULT '',
    merchant_sku TEXT NOT NULL DEFAULT '',
    price_amount REAL,
    currency TEXT NOT NULL DEFAULT '',
    merchant_rating REAL,
    merchant_reviews INTEGER,
    is_own INTEGER NOT NULL DEFAULT 0,
    captured_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tenant_seller_offer_history
ON tenant_seller_offer_snapshots(
    tenant_id,marketplace_code,tenant_seller_id,source_product_code,captured_at DESC
);

CREATE TABLE IF NOT EXISTS tenant_catalogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    tenant_seller_id INTEGER,
    external_catalog_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    product_count INTEGER NOT NULL DEFAULT 0,
    last_sync_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id,marketplace_code,external_catalog_id,tenant_seller_id),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_catalogs_marketplace
ON tenant_catalogs(tenant_id,marketplace_code,status);

CREATE TABLE IF NOT EXISTS tenant_catalog_products (
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    source_product_code TEXT NOT NULL,
    catalog_id INTEGER,
    tenant_seller_id INTEGER,
    seller_sku TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    category_name TEXT NOT NULL DEFAULT '',
    price_amount REAL,
    currency TEXT NOT NULL DEFAULT '',
    availability_status TEXT NOT NULL DEFAULT '',
    attributes_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_updated_at TEXT,
    PRIMARY KEY(tenant_id,marketplace_code,source_product_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(catalog_id) REFERENCES tenant_catalogs(id) ON DELETE SET NULL,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_catalog_products_visible
ON tenant_catalog_products(tenant_id,marketplace_code,active,last_seen_at);

CREATE TABLE IF NOT EXISTS tenant_catalog_import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    tenant_seller_id INTEGER,
    catalog_id INTEGER,
    status TEXT NOT NULL,
    products_seen INTEGER NOT NULL DEFAULT 0,
    products_saved INTEGER NOT NULL DEFAULT 0,
    pages_loaded INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE SET NULL,
    FOREIGN KEY(catalog_id) REFERENCES tenant_catalogs(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_catalog_import_runs_time
ON tenant_catalog_import_runs(tenant_id,marketplace_code,started_at DESC);

CREATE TABLE IF NOT EXISTS tenant_marketplace_access (
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    is_allowed INTEGER NOT NULL DEFAULT 0,
    granted_by INTEGER,
    granted_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,marketplace_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(granted_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_marketplace_access_allowed
ON tenant_marketplace_access(tenant_id,is_allowed,marketplace_code);


CREATE TABLE IF NOT EXISTS tenant_product_state (
    tenant_id INTEGER NOT NULL,
    product_code TEXT NOT NULL,
    watched INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'normal',
    note TEXT,
    expected_monthly_units INTEGER,
    updated_by INTEGER,
    updated_at TEXT,
    PRIMARY KEY(tenant_id,product_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(updated_by) REFERENCES app_users(id)
);
CREATE INDEX IF NOT EXISTS idx_tenant_product_state_watch
ON tenant_product_state(tenant_id,watched,priority,updated_at);

CREATE TABLE IF NOT EXISTS tenant_inventory_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    internal_sku TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    quantity_on_hand INTEGER NOT NULL DEFAULT 0 CHECK(quantity_on_hand >= 0),
    purchase_price_kzt REAL CHECK(purchase_price_kzt IS NULL OR purchase_price_kzt >= 0),
    target_markup_percent REAL NOT NULL DEFAULT 20
        CHECK(target_markup_percent >= 0 AND target_markup_percent <= 1000),
    notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id,id),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES app_users(id) ON DELETE SET NULL,
    FOREIGN KEY(updated_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_inventory_internal_sku
ON tenant_inventory_products(tenant_id,internal_sku)
WHERE internal_sku<>'';
CREATE INDEX IF NOT EXISTS idx_tenant_inventory_updated
ON tenant_inventory_products(tenant_id,updated_at DESC);

CREATE TABLE IF NOT EXISTS tenant_product_listings (
    tenant_id INTEGER NOT NULL,
    listing_code TEXT NOT NULL,
    inventory_product_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    tenant_seller_id INTEGER,
    source_product_code TEXT NOT NULL,
    match_method TEXT NOT NULL DEFAULT 'MANUAL',
    match_score REAL,
    confirmed_by INTEGER,
    confirmed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,listing_code),
    FOREIGN KEY(tenant_id,inventory_product_id)
        REFERENCES tenant_inventory_products(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY(confirmed_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_product_listings_inventory
ON tenant_product_listings(tenant_id,inventory_product_id,marketplace_code);
CREATE INDEX IF NOT EXISTS idx_tenant_product_listings_source
ON tenant_product_listings(tenant_id,marketplace_code,tenant_seller_id,source_product_code);

CREATE TABLE IF NOT EXISTS tenant_product_match_decisions (
    tenant_id INTEGER NOT NULL,
    source_listing_code TEXT NOT NULL,
    candidate_listing_code TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('confirmed','rejected')),
    match_method TEXT NOT NULL DEFAULT '',
    match_score REAL,
    reason TEXT NOT NULL DEFAULT '',
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,source_listing_code,candidate_listing_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(updated_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_match_decisions_candidate
ON tenant_product_match_decisions(tenant_id,candidate_listing_code,decision);

CREATE TABLE IF NOT EXISTS tenant_inventory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    inventory_product_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id,inventory_product_id)
        REFERENCES tenant_inventory_products(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_inventory_events_item
ON tenant_inventory_events(tenant_id,inventory_product_id,created_at DESC);

CREATE TABLE IF NOT EXISTS encrypted_credentials (
    credential_ref TEXT PRIMARY KEY,
    tenant_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    credential_name TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    key_id TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id,marketplace_code,credential_name),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES app_users(id)
);
CREATE INDEX IF NOT EXISTS idx_encrypted_credentials_tenant
ON encrypted_credentials(tenant_id,marketplace_code,credential_name);

CREATE TABLE IF NOT EXISTS seller_encrypted_credentials (
    credential_ref TEXT PRIMARY KEY,
    tenant_id INTEGER NOT NULL,
    tenant_seller_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    credential_name TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    key_id TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id,tenant_seller_id,marketplace_code,credential_name),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_seller_encrypted_credentials_tenant
ON seller_encrypted_credentials(tenant_id,tenant_seller_id,marketplace_code,credential_name);

CREATE TABLE IF NOT EXISTS registration_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    registration_number TEXT,
    contact_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    legal_address TEXT NOT NULL DEFAULT '',
    actual_address TEXT NOT NULL DEFAULT '',
    integrations_json TEXT NOT NULL DEFAULT '[]',
    workspace_profile_json TEXT NOT NULL DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS product_attribute_sources (
    tenant_id INTEGER NOT NULL,
    definition_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    source_attribute TEXT NOT NULL,
    sample_values_json TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,definition_id,marketplace_code,source_attribute),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(definition_id) REFERENCES product_attribute_definitions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_attribute_sources_marketplace
ON product_attribute_sources(tenant_id,marketplace_code,source_attribute);

CREATE TABLE IF NOT EXISTS tenant_catalog_filters (
    tenant_id INTEGER NOT NULL,
    attribute_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    display_order INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,attribute_key),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_catalog_filters_enabled
ON tenant_catalog_filters(tenant_id,is_enabled,display_order,attribute_key);

CREATE TABLE IF NOT EXISTS operation_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    action TEXT NOT NULL,
    platform TEXT NOT NULL,
    tenant_seller_id INTEGER,
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
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES app_users(id)
);
CREATE INDEX IF NOT EXISTS idx_operation_schedules_due ON operation_schedules(is_enabled, next_run_at, tenant_id);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    tenant_seller_id INTEGER,
    task_id TEXT,
    status TEXT NOT NULL,
    message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY(schedule_id) REFERENCES operation_schedules(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(tenant_seller_id) REFERENCES tenant_marketplace_sellers(id) ON DELETE SET NULL
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

CREATE TABLE IF NOT EXISTS forte_products (
    product_id TEXT PRIMARY KEY,
    short_id TEXT NOT NULL DEFAULT '',
    slug TEXT NOT NULL DEFAULT '',
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
    raw_json TEXT NOT NULL DEFAULT '{}',
    seller_name TEXT NOT NULL DEFAULT '',
    merchant_id TEXT NOT NULL DEFAULT '',
    catalog_rating REAL,
    catalog_reviews INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_catalog_at TEXT,
    last_market_at TEXT,
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_forte_products_active_brand
ON forte_products(active,brand,last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_forte_products_merchant
ON forte_products(merchant_id,active,last_seen_at DESC);

CREATE TABLE IF NOT EXISTS forte_offers (
    product_id TEXT NOT NULL,
    merchant_key TEXT NOT NULL,
    merchant_id TEXT NOT NULL DEFAULT '',
    merchant_name TEXT NOT NULL DEFAULT '',
    price_kzt REAL,
    merchant_rating REAL,
    merchant_reviews INTEGER,
    offer_type TEXT NOT NULL DEFAULT '',
    availability_status TEXT NOT NULL DEFAULT '',
    is_own INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(product_id,merchant_key),
    FOREIGN KEY(product_id) REFERENCES forte_products(product_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_forte_offers_product_active
ON forte_offers(product_id,active,is_own,price_kzt);

CREATE TABLE IF NOT EXISTS forte_price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    merchant_key TEXT NOT NULL,
    merchant_name TEXT NOT NULL DEFAULT '',
    price_kzt REAL,
    is_own INTEGER NOT NULL DEFAULT 0,
    captured_at TEXT NOT NULL,
    UNIQUE(run_id,product_id,merchant_key),
    FOREIGN KEY(product_id) REFERENCES forte_products(product_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_forte_price_history_product_time
ON forte_price_history(product_id,captured_at DESC);

CREATE TABLE IF NOT EXISTS forte_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    seller_name TEXT NOT NULL DEFAULT '',
    merchant_id TEXT NOT NULL DEFAULT '',
    city_id TEXT NOT NULL DEFAULT '',
    total_reported INTEGER NOT NULL DEFAULT 0,
    products_seen INTEGER NOT NULL DEFAULT 0,
    offers_seen INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_forte_sync_runs_time
ON forte_sync_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS subscription_features (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscription_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'KZT',
    term_days INTEGER NOT NULL DEFAULT 30,
    daily_operation_limit INTEGER,
    position_limit INTEGER,
    is_public INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    display_order INTEGER NOT NULL DEFAULT 100,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(created_by) REFERENCES app_users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS subscription_plan_features (
    plan_id INTEGER NOT NULL,
    feature_code TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    limit_value INTEGER,
    config_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(plan_id,feature_code),
    FOREIGN KEY(plan_id) REFERENCES subscription_plans(id) ON DELETE CASCADE,
    FOREIGN KEY(feature_code) REFERENCES subscription_features(code) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS subscription_plan_marketplace_limits (
    plan_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    position_limit INTEGER,
    daily_operation_limit INTEGER,
    PRIMARY KEY(plan_id,marketplace_code),
    FOREIGN KEY(plan_id) REFERENCES subscription_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS subscription_addons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    extra_positions INTEGER NOT NULL,
    price_amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'KZT',
    term_days INTEGER,
    is_public INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    display_order INTEGER NOT NULL DEFAULT 100,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(created_by) REFERENCES app_users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tenant_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_by INTEGER,
    requested_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    starts_at TEXT,
    ends_at TEXT,
    price_amount REAL NOT NULL,
    currency TEXT NOT NULL,
    term_days INTEGER NOT NULL,
    plan_snapshot_json TEXT NOT NULL DEFAULT '{}',
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(plan_id) REFERENCES subscription_plans(id),
    FOREIGN KEY(requested_by) REFERENCES app_users(id) ON DELETE SET NULL,
    FOREIGN KEY(reviewed_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_subscriptions_current
ON tenant_subscriptions(tenant_id,status,starts_at,ends_at);

CREATE TABLE IF NOT EXISTS subscription_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    subscription_id INTEGER NOT NULL UNIQUE,
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'KZT',
    status TEXT NOT NULL DEFAULT 'confirmed',
    paid_at TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    term_days INTEGER NOT NULL,
    months_count REAL NOT NULL DEFAULT 1,
    confirmed_by INTEGER,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(subscription_id) REFERENCES tenant_subscriptions(id) ON DELETE CASCADE,
    FOREIGN KEY(confirmed_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_subscription_payments_time
ON subscription_payments(paid_at DESC,tenant_id);

CREATE TABLE IF NOT EXISTS billing_sequences (
    sequence_year INTEGER PRIMARY KEY,
    last_value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subscription_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    subscription_id INTEGER NOT NULL,
    invoice_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'issued',
    months_count INTEGER NOT NULL,
    unit_price REAL NOT NULL DEFAULT 0,
    subtotal_amount REAL NOT NULL DEFAULT 0,
    vat_rate REAL NOT NULL DEFAULT 0,
    vat_amount REAL NOT NULL DEFAULT 0,
    total_amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'KZT',
    seller_snapshot_json TEXT NOT NULL DEFAULT '{}',
    buyer_snapshot_json TEXT NOT NULL DEFAULT '{}',
    line_items_json TEXT NOT NULL DEFAULT '[]',
    issued_at TEXT NOT NULL,
    due_at TEXT,
    pdf_path TEXT NOT NULL DEFAULT '',
    pdf_sha256 TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    cancelled_by INTEGER,
    cancelled_at TEXT,
    cancel_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id)
        REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(subscription_id)
        REFERENCES tenant_subscriptions(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by)
        REFERENCES app_users(id) ON DELETE SET NULL,
    FOREIGN KEY(cancelled_by)
        REFERENCES app_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_subscription_invoices_tenant
ON subscription_invoices(
    tenant_id,status,issued_at DESC
);

CREATE INDEX IF NOT EXISTS idx_subscription_invoices_subscription
ON subscription_invoices(
    subscription_id,issued_at DESC
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_invoices_open
ON subscription_invoices(subscription_id)
WHERE status<>'cancelled';

CREATE TABLE IF NOT EXISTS subscription_payment_proofs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    subscription_id INTEGER NOT NULL,
    invoice_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'under_review',
    original_filename TEXT NOT NULL DEFAULT '',
    stored_path TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    uploaded_by INTEGER,
    uploaded_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id)
        REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(subscription_id)
        REFERENCES tenant_subscriptions(id) ON DELETE CASCADE,
    FOREIGN KEY(invoice_id)
        REFERENCES subscription_invoices(id) ON DELETE CASCADE,
    FOREIGN KEY(uploaded_by)
        REFERENCES app_users(id) ON DELETE SET NULL,
    FOREIGN KEY(reviewed_by)
        REFERENCES app_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_subscription_payment_proofs_invoice
ON subscription_payment_proofs(
    invoice_id,uploaded_at DESC
);

CREATE INDEX IF NOT EXISTS idx_subscription_payment_proofs_tenant
ON subscription_payment_proofs(
    tenant_id,status,uploaded_at DESC
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_payment_proofs_review_open
ON subscription_payment_proofs(invoice_id)
WHERE status='under_review';

CREATE TABLE IF NOT EXISTS tenant_subscription_addons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    subscription_id INTEGER,
    addon_id INTEGER NOT NULL,
    marketplace_code TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    quantity INTEGER NOT NULL DEFAULT 1,
    requested_by INTEGER,
    requested_at TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    starts_at TEXT,
    ends_at TEXT,
    price_amount REAL NOT NULL,
    currency TEXT NOT NULL,
    extra_positions INTEGER NOT NULL,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY(subscription_id) REFERENCES tenant_subscriptions(id) ON DELETE SET NULL,
    FOREIGN KEY(addon_id) REFERENCES subscription_addons(id),
    FOREIGN KEY(requested_by) REFERENCES app_users(id) ON DELETE SET NULL,
    FOREIGN KEY(reviewed_by) REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_subscription_addons_current
ON tenant_subscription_addons(tenant_id,status,marketplace_code,starts_at,ends_at);

CREATE TABLE IF NOT EXISTS tenant_daily_usage (
    tenant_id INTEGER NOT NULL,
    usage_date TEXT NOT NULL,
    marketplace_code TEXT NOT NULL DEFAULT '',
    metric_code TEXT NOT NULL,
    used_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id,usage_date,marketplace_code,metric_code),
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_database(path: Path) -> None:
    settings = DatabaseSettings.from_environment()
    if settings.backend is DatabaseBackend.POSTGRESQL:
        settings.assert_runtime_ready()
        return
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
        if "workspace_profile_json" not in _columns(conn, "tenants"):
            conn.execute("ALTER TABLE tenants ADD COLUMN workspace_profile_json TEXT NOT NULL DEFAULT '{}'")
        if "workspace_profile_json" not in _columns(conn, "registration_requests"):
            conn.execute("ALTER TABLE registration_requests ADD COLUMN workspace_profile_json TEXT NOT NULL DEFAULT '{}'")
        if "legal_address" not in _columns(conn, "tenants"):
            conn.execute("ALTER TABLE tenants ADD COLUMN legal_address TEXT NOT NULL DEFAULT ''")
        if "actual_address" not in _columns(conn, "tenants"):
            conn.execute("ALTER TABLE tenants ADD COLUMN actual_address TEXT NOT NULL DEFAULT ''")
        if "legal_address" not in _columns(conn, "registration_requests"):
            conn.execute("ALTER TABLE registration_requests ADD COLUMN legal_address TEXT NOT NULL DEFAULT ''")
        if "actual_address" not in _columns(conn, "registration_requests"):
            conn.execute("ALTER TABLE registration_requests ADD COLUMN actual_address TEXT NOT NULL DEFAULT ''")
        if "run_date" not in _columns(conn, "operation_schedules"):
            conn.execute("ALTER TABLE operation_schedules ADD COLUMN run_date TEXT")
        if "tenant_seller_id" not in _columns(conn, "operation_schedules"):
            conn.execute("ALTER TABLE operation_schedules ADD COLUMN tenant_seller_id INTEGER")
        if "tenant_seller_id" not in _columns(conn, "schedule_runs"):
            conn.execute("ALTER TABLE schedule_runs ADD COLUMN tenant_seller_id INTEGER")
        if "platform_role" not in _columns(conn, "app_users"):
            conn.execute("ALTER TABLE app_users ADD COLUMN platform_role TEXT NOT NULL DEFAULT ''")
        user_columns = _columns(conn, "app_users")
        email_verified_column_added = "email_verified_at" not in user_columns
        if email_verified_column_added:
            conn.execute("ALTER TABLE app_users ADD COLUMN email_verified_at TEXT")
        if "session_version" not in user_columns:
            conn.execute("ALTER TABLE app_users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
        if email_verified_column_added:
            # Users that predate verified-email authentication retain access.
            # Newly created users are explicitly inserted as unverified.
            conn.execute(
                "UPDATE app_users SET email_verified_at=COALESCE(email_verified_at,created_at)"
            )
        if "tenant_id" not in _columns(conn, "app_events"):
            conn.execute("ALTER TABLE app_events ADD COLUMN tenant_id INTEGER")
        if "tenant_id" not in _columns(conn, "app_reports"):
            conn.execute("ALTER TABLE app_reports ADD COLUMN tenant_id INTEGER")
        if "platforms_json" not in _columns(conn, "app_reports"):
            conn.execute("ALTER TABLE app_reports ADD COLUMN platforms_json TEXT NOT NULL DEFAULT '[]'")
        if "tenant_id" not in _columns(conn, "app_product_state"):
            conn.execute("ALTER TABLE app_product_state ADD COLUMN tenant_id INTEGER")
        if "last_seen_at" not in _columns(conn, "product_attribute_definitions"):
            conn.execute("ALTER TABLE product_attribute_definitions ADD COLUMN last_seen_at TEXT")
        integration_columns = _columns(conn, "tenant_integrations")
        approval_column_added = "approval_status" not in integration_columns
        integration_migrations = (
            ("seller_name", "TEXT NOT NULL DEFAULT ''"),
            ("seller_identifier", "TEXT NOT NULL DEFAULT ''"),
            ("seller_url", "TEXT NOT NULL DEFAULT ''"),
            ("discovery_status", "TEXT NOT NULL DEFAULT 'idle'"),
            ("approval_status", "TEXT NOT NULL DEFAULT 'draft'"),
            ("discovery_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("submitted_by", "INTEGER"),
            ("submitted_at", "TEXT"),
            ("reviewed_by", "INTEGER"),
            ("reviewed_at", "TEXT"),
            ("review_note", "TEXT NOT NULL DEFAULT ''"),
        )
        for column, declaration in integration_migrations:
            if column not in integration_columns:
                conn.execute(
                    f"ALTER TABLE tenant_integrations ADD COLUMN {column} {declaration}"
                )
        seller_columns = _columns(conn, "tenant_marketplace_sellers")
        seller_migrations = (
            ("config_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("discovery_status", "TEXT NOT NULL DEFAULT 'idle'"),
            ("approval_status", "TEXT NOT NULL DEFAULT 'draft'"),
            ("discovery_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("submitted_by", "INTEGER"),
            ("submitted_at", "TEXT"),
            ("reviewed_by", "INTEGER"),
            ("reviewed_at", "TEXT"),
            ("review_note", "TEXT NOT NULL DEFAULT ''"),
            ("product_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_sync_at", "TEXT"),
            ("last_status", "TEXT"),
            ("last_error", "TEXT NOT NULL DEFAULT ''"),
        )
        for column, declaration in seller_migrations:
            if column not in seller_columns:
                conn.execute(
                    f"ALTER TABLE tenant_marketplace_sellers ADD COLUMN {column} {declaration}"
                )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_tenant_sellers_approval
               ON tenant_marketplace_sellers(
                   tenant_id,marketplace_code,approval_status,status
               )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_tenant_integrations_approval
               ON tenant_integrations(approval_status,submitted_at,tenant_id,integration_code)"""
        )

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

        for tenant_id, in conn.execute("SELECT id FROM tenants").fetchall():
            for role_code, label in ROLE_LABELS.items():
                conn.execute(
                    """
                    INSERT INTO tenant_roles(
                        tenant_id,role_code,display_name,is_system,created_at,updated_at
                    ) VALUES(?,?,?,1,?,?)
                    ON CONFLICT(tenant_id,role_code) DO NOTHING
                    """,
                    (int(tenant_id), role_code, label, stamp, stamp),
                )
                for permission_code in ROLE_DEFAULT_PERMISSIONS[role_code]:
                    conn.execute(
                        """
                        INSERT INTO tenant_role_permissions(
                            tenant_id,role_code,permission_code,is_enabled,created_at,updated_at
                        ) VALUES(?,?,?,1,?,?)
                        ON CONFLICT(tenant_id,role_code,permission_code) DO NOTHING
                        """,
                        (int(tenant_id), role_code, permission_code, stamp, stamp),
                    )
            for key, label, order in (
                ("title", "Название", 10),
                ("marketplace", "Marketplace", 20),
            ):
                conn.execute(
                    """
                    INSERT INTO tenant_catalog_filters(
                        tenant_id,attribute_key,display_name,is_enabled,display_order,
                        config_json,created_at,updated_at
                    ) VALUES(?,?,?,1,?,'{}',?,?)
                    ON CONFLICT(tenant_id,attribute_key) DO NOTHING
                    """,
                    (int(tenant_id), key, label, order, stamp, stamp),
                )

        legacy_default_states = {
            "kaspi": ("active", "approved"),
            "ozon": ("active", "approved"),
            "ozon_kz": ("disabled", "draft"),
            "forte_market": ("active", "approved"),
            "halyk_market": ("active", "approved"),
        }
        integrations = tuple(
            (
                definition.code,
                definition.label,
                *legacy_default_states.get(definition.code, ("disabled", "draft")),
            )
            for definition in MARKETPLACES
        )
        for code,title,status,approval_status in integrations:
            conn.execute(
                """
                INSERT INTO tenant_integrations(
                    tenant_id,integration_code,display_name,status,approval_status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id,integration_code) DO NOTHING
                """,
                (default_tenant_id,code,title,status,approval_status,stamp,stamp),
            )
        # Every company receives one configurable card per marketplace. Existing
        # rows are preserved; newly discovered marketplaces start disabled.
        for tenant_id, in conn.execute("SELECT id FROM tenants").fetchall():
            for code,title,_,_ in integrations:
                conn.execute(
                    """
                    INSERT INTO tenant_integrations(
                        tenant_id,integration_code,display_name,status,approval_status,created_at,updated_at
                    ) VALUES(?,?,?,'disabled','draft',?,?)
                    ON CONFLICT(tenant_id,integration_code) DO NOTHING
                    """,
                    (int(tenant_id),code,title,stamp,stamp),
                )
        conn.execute(
            """UPDATE tenant_integrations SET display_name='Ozon.ru'
               WHERE integration_code='ozon' AND display_name<>'Ozon.ru'"""
        )
        conn.execute(
            """UPDATE tenant_integrations SET display_name='Halyk Market'
               WHERE integration_code='halyk_market' AND display_name<>'Halyk Market'"""
        )
        conn.execute(
            """UPDATE tenant_integrations SET display_name='Forte Market'
               WHERE integration_code='forte_market' AND display_name<>'Forte Market'"""
        )
        # Keep the legacy approval column internally compatible for working
        # connections; the current product flow has no integration review step.
        if approval_column_added:
            conn.execute(
                """UPDATE tenant_integrations SET approval_status='approved'
                   WHERE approval_status='draft' AND status IN ('active','setup')
                     AND submitted_at IS NULL AND reviewed_at IS NULL"""
            )

        # Company grants and live connections are different concepts. Preserve
        # only proven legacy connections during the one-way compatibility
        # backfill; pending and newly created companies receive no grants.
        conn.execute(
            """
            INSERT INTO tenant_marketplace_access(
                tenant_id,marketplace_code,is_allowed,granted_at,updated_at
            )
            SELECT ti.tenant_id,ti.integration_code,1,?,?
            FROM tenant_integrations ti
            JOIN tenants t ON t.id=ti.tenant_id
            WHERE t.status IN ('active','approved','confirmed')
              AND ti.status IN ('active','setup')
              AND ti.approval_status='approved'
            ON CONFLICT(tenant_id,marketplace_code) DO NOTHING
            """,
            (stamp, stamp),
        )

        # Compatibility only: attach genuinely orphaned legacy users to the
        # default tenant. Never add a second tenant membership to modern users.
        users = conn.execute(
            """SELECT u.id,u.role FROM app_users u
               WHERE NOT EXISTS(
                   SELECT 1 FROM tenant_users tu WHERE tu.user_id=u.id
               )"""
        ).fetchall()
        for user_id,role in users:
            conn.execute(
                """
                INSERT INTO tenant_users(tenant_id,user_id,tenant_role,is_primary,is_active,created_at)
                VALUES(?,?,?,?,1,?)
                ON CONFLICT(tenant_id,user_id) DO NOTHING
                """,
                (default_tenant_id,int(user_id),str(role or 'viewer'),1,stamp),
            )
        # Older versions could accidentally attach every user to the default
        # tenant on startup. Disable only the later duplicate default
        # membership; rows are retained for audit and rollback.
        conn.execute(
            """UPDATE tenant_users
               SET is_primary=0,is_active=0
               WHERE tenant_id=?
                 AND EXISTS(
                     SELECT 1 FROM tenant_users other
                     WHERE other.user_id=tenant_users.user_id
                       AND other.tenant_id<>tenant_users.tenant_id
                       AND other.is_active=1
                       AND datetime(other.created_at)<=datetime(tenant_users.created_at)
                 )""",
            (default_tenant_id,),
        )
        conn.execute(
            "UPDATE tenant_integrations SET status='active',updated_at=? "
            "WHERE integration_code='halyk_market' AND status='coming_soon'",
            (stamp,),
        )
        conn.execute(
            "UPDATE tenant_integrations SET status='active',updated_at=? "
            "WHERE integration_code='forte_market' AND status='coming_soon'",
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
        conn.execute(
            """
            INSERT OR IGNORE INTO tenant_product_state(
                tenant_id,product_code,watched,priority,note,expected_monthly_units,
                updated_by,updated_at
            )
            SELECT COALESCE(tenant_id,?),product_code,watched,priority,note,
                   expected_monthly_units,updated_by,updated_at
            FROM app_product_state
            """,
            (default_tenant_id,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO tenant_catalog_products(
                tenant_id,marketplace_code,source_product_code,title,source_url,image_url,
                currency,active,first_seen_at,last_seen_at
            )
            SELECT ?, 'kaspi', product_code, COALESCE(title_catalog,''),
                   COALESCE(product_url,''), COALESCE(image_url,''), 'KZT',
                   1, COALESCE(collected_at,?), COALESCE(collected_at,?)
            FROM catalog_products
            """,
            (default_tenant_id, stamp, stamp),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO tenant_catalog_products(
                tenant_id,marketplace_code,source_product_code,title,brand,source_url,image_url,
                category_name,price_amount,currency,availability_status,attributes_json,
                active,first_seen_at,last_seen_at,source_updated_at
            )
            SELECT ?, 'halyk_market', product_id, COALESCE(name,''), COALESCE(brand,''),
                   COALESCE(product_url,''), COALESCE(image_url,''),
                   COALESCE(json_extract(categories_json,'$[0]'),''), price_kzt,
                   COALESCE(currency,'KZT'), '', COALESCE(specs_json,'[]'),
                   COALESCE(active,1), first_seen_at, last_seen_at, last_catalog_at
            FROM halyk_products
            """,
            (default_tenant_id,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO tenant_catalog_products(
                tenant_id,marketplace_code,source_product_code,tenant_seller_id,title,brand,
                source_url,image_url,category_name,price_amount,currency,availability_status,
                attributes_json,active,first_seen_at,last_seen_at,source_updated_at
            )
            SELECT ?, 'forte_market', product_id, NULL, COALESCE(name,''), COALESCE(brand,''),
                   COALESCE(product_url,''), COALESCE(image_url,''),
                   COALESCE(json_extract(categories_json,'$[0]'),''),
                   price_kzt, COALESCE(currency,'KZT'), '',
                   COALESCE(specs_json,'[]'), COALESCE(active,1), first_seen_at,last_seen_at,last_catalog_at
            FROM forte_products
            """,
            (default_tenant_id,),
        )

        # One-time, non-destructive multi-seller compatibility backfill.  The
        # historic marketplace integration remains as a summary card; every
        # concrete account and listing now receives an unambiguous seller key.
        migration_done = conn.execute(
            "SELECT 1 FROM metadata WHERE key='schema_multi_seller_v1_backfilled'"
        ).fetchone()
        if migration_done is None:
            conn.execute(
                """INSERT INTO tenant_marketplace_sellers(
                       tenant_id,marketplace_code,external_seller_id,display_name,source_url,
                       status,config_json,discovery_status,approval_status,discovery_json,
                       submitted_by,submitted_at,reviewed_by,reviewed_at,review_note,
                       product_count,last_sync_at,last_status,last_error,created_at,updated_at
                   )
                   SELECT ti.tenant_id,ti.integration_code,ti.seller_identifier,
                          ti.seller_name,ti.seller_url,
                          CASE WHEN ti.status='active' AND ti.approval_status='approved'
                               THEN 'active' ELSE ti.status END,
                          ti.config_json,ti.discovery_status,ti.approval_status,
                          ti.discovery_json,ti.submitted_by,ti.submitted_at,
                          ti.reviewed_by,ti.reviewed_at,ti.review_note,ti.product_count,
                          ti.last_sync_at,ti.last_status,COALESCE(ti.last_error,''),
                          ti.created_at,ti.updated_at
                   FROM tenant_integrations ti
                   WHERE TRIM(COALESCE(ti.seller_identifier,''))<>''
                     AND ti.seller_identifier NOT LIKE 'candidate:%'
                   ON CONFLICT(tenant_id,marketplace_code,external_seller_id) DO NOTHING"""
            )
            conn.execute(
                """UPDATE tenant_marketplace_sellers
                   SET config_json=COALESCE((
                           SELECT ti.config_json FROM tenant_integrations ti
                           WHERE ti.tenant_id=tenant_marketplace_sellers.tenant_id
                             AND ti.integration_code=tenant_marketplace_sellers.marketplace_code
                             AND ti.seller_identifier=tenant_marketplace_sellers.external_seller_id
                       ),config_json),
                       discovery_status=COALESCE((
                           SELECT ti.discovery_status FROM tenant_integrations ti
                           WHERE ti.tenant_id=tenant_marketplace_sellers.tenant_id
                             AND ti.integration_code=tenant_marketplace_sellers.marketplace_code
                             AND ti.seller_identifier=tenant_marketplace_sellers.external_seller_id
                       ),discovery_status),
                       approval_status=CASE
                           WHEN status='active' THEN 'approved'
                           WHEN status='pending' THEN 'pending'
                           WHEN status='rejected' THEN 'rejected'
                           ELSE approval_status END"""
            )

            product_columns = (
                "tenant_id", "marketplace_code", "tenant_seller_id",
                "source_product_code", "catalog_id", "seller_sku", "title",
                "brand", "model", "source_url", "image_url", "category_name",
                "price_amount", "currency", "availability_status",
                "attributes_json", "metadata_json", "active", "first_seen_at",
                "last_seen_at", "source_updated_at",
            )
            placeholders = ",".join("?" for _ in product_columns)
            legacy_product_columns = [
                str(item[1])
                for item in conn.execute(
                    "PRAGMA table_info(tenant_catalog_products)"
                ).fetchall()
            ]
            for raw in conn.execute("SELECT * FROM tenant_catalog_products").fetchall():
                row = dict(zip(legacy_product_columns, raw))
                seller_id = row.get("tenant_seller_id")
                sellers = conn.execute(
                    """SELECT s.id,s.external_seller_id,s.status,s.approval_status,
                              ti.seller_identifier AS canonical_identifier
                       FROM tenant_marketplace_sellers s
                       LEFT JOIN tenant_integrations ti
                         ON ti.tenant_id=s.tenant_id
                        AND ti.integration_code=s.marketplace_code
                       WHERE s.tenant_id=? AND s.marketplace_code=?
                       ORDER BY CASE
                           WHEN s.external_seller_id=ti.seller_identifier THEN 0
                           WHEN s.status='active' AND s.approval_status='approved' THEN 1
                           ELSE 2 END,s.id""",
                    (int(row["tenant_id"]), str(row["marketplace_code"])),
                ).fetchall()
                if seller_id is None and sellers:
                    canonical = [item for item in sellers if item[1] == item[4]]
                    active = [
                        item for item in sellers
                        if item[2] == "active" and item[3] == "approved"
                    ]
                    if len(canonical) == 1:
                        seller_id = int(canonical[0][0])
                    elif len(active) == 1:
                        seller_id = int(active[0][0])
                    elif len(sellers) == 1:
                        seller_id = int(sellers[0][0])
                if seller_id is None:
                    continue
                values = []
                for column in product_columns:
                    values.append(int(seller_id) if column == "tenant_seller_id" else row.get(column))
                conn.execute(
                    f"""INSERT OR IGNORE INTO tenant_seller_catalog_products(
                            {','.join(product_columns)}
                        ) VALUES({placeholders})""",
                    values,
                )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                ("schema_multi_seller_v1_backfilled", stamp),
            )
        conn.commit()
    finally:
        conn.close()
