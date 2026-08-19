#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from storage.postgres_compat import connect_database


SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS products (
    article TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    manufacturer_article TEXT NOT NULL DEFAULT '',
    tire_size TEXT NOT NULL DEFAULT '',
    width_mm TEXT NOT NULL DEFAULT '',
    profile_percent TEXT NOT NULL DEFAULT '',
    diameter_inch TEXT NOT NULL DEFAULT '',
    load_index TEXT NOT NULL DEFAULT '',
    speed_index TEXT NOT NULL DEFAULT '',
    season TEXT NOT NULL DEFAULT 'UNKNOWN',
    studded INTEGER,
    xl INTEGER NOT NULL DEFAULT 0,
    runflat INTEGER NOT NULL DEFAULT 0,
    image_url TEXT NOT NULL DEFAULT '',
    product_identity_key TEXT NOT NULL DEFAULT '',
    identity_completeness_percent REAL NOT NULL DEFAULT 0,
    discovery_url TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_catalog_at TEXT,
    last_detail_at TEXT,
    last_price_at TEXT,
    catalog_price INTEGER NOT NULL DEFAULT 0,
    detail_status TEXT NOT NULL DEFAULT 'NEW',
    active INTEGER NOT NULL DEFAULT 1,
    details_hash TEXT NOT NULL DEFAULT '',
    raw_json_path TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_products_active ON products(active);
CREATE INDEX IF NOT EXISTS idx_products_detail_status ON products(detail_status);
CREATE INDEX IF NOT EXISTS idx_products_last_detail ON products(last_detail_at);
CREATE INDEX IF NOT EXISTS idx_products_identity ON products(product_identity_key);

CREATE TABLE IF NOT EXISTS offers (
    article TEXT NOT NULL,
    seller_key TEXT NOT NULL,
    seller_id TEXT NOT NULL DEFAULT '',
    seller_name TEXT NOT NULL DEFAULT '',
    seller_url TEXT NOT NULL DEFAULT '',
    seller_rating REAL,
    card_price INTEGER NOT NULL DEFAULT 0,
    catalog_price INTEGER NOT NULL DEFAULT 0,
    regular_price INTEGER NOT NULL DEFAULT 0,
    original_price INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'RUB',
    availability_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    location_city TEXT NOT NULL DEFAULT '',
    location_country TEXT NOT NULL DEFAULT '',
    product_rating REAL,
    review_count INTEGER,
    price_hash TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(article, seller_key),
    FOREIGN KEY(article) REFERENCES products(article) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_offers_seller ON offers(seller_id, seller_name);
CREATE INDEX IF NOT EXISTS idx_offers_checked ON offers(last_checked_at);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    article TEXT NOT NULL,
    seller_key TEXT NOT NULL,
    card_price INTEGER NOT NULL DEFAULT 0,
    catalog_price INTEGER NOT NULL DEFAULT 0,
    regular_price INTEGER NOT NULL DEFAULT 0,
    original_price INTEGER NOT NULL DEFAULT 0,
    availability_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    currency TEXT NOT NULL DEFAULT 'RUB',
    collected_at TEXT NOT NULL,
    price_hash TEXT NOT NULL DEFAULT '',
    UNIQUE(run_id, article, seller_key),
    FOREIGN KEY(article) REFERENCES products(article) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_price_history_article_time ON price_history(article, collected_at DESC);

CREATE TABLE IF NOT EXISTS catalog_snapshots (
    run_id TEXT NOT NULL,
    article TEXT NOT NULL,
    page_no INTEGER NOT NULL,
    catalog_price INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    canonical_url TEXT NOT NULL DEFAULT '',
    collected_at TEXT NOT NULL,
    PRIMARY KEY(run_id, article)
);


CREATE TABLE IF NOT EXISTS catalog_sources (
    source_url TEXT PRIMARY KEY,
    source_type TEXT NOT NULL DEFAULT 'MARKET_CATEGORY',
    label TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_catalog_sources_type ON catalog_sources(source_type, active);

CREATE TABLE IF NOT EXISTS product_sources (
    article TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'MARKET_CATEGORY',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL DEFAULT '',
    page_no INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(article, source_url),
    FOREIGN KEY(article) REFERENCES products(article) ON DELETE CASCADE,
    FOREIGN KEY(source_url) REFERENCES catalog_sources(source_url) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_product_sources_type ON product_sources(source_type, article);


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
    last_run_id TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(client_article) REFERENCES products(article) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_market_search_jobs_status ON market_search_jobs(status,last_search_at);

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
    PRIMARY KEY(client_article,candidate_article),
    FOREIGN KEY(client_article) REFERENCES products(article) ON DELETE CASCADE,
    FOREIGN KEY(candidate_article) REFERENCES products(article) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_market_search_candidates_level
ON market_search_candidates(client_article,active,match_level,match_score DESC);

CREATE TABLE IF NOT EXISTS crawl_queue (
    article TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    priority INTEGER NOT NULL DEFAULT 50,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_attempt_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(article, task_type),
    FOREIGN KEY(article) REFERENCES products(article) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_queue_status_priority ON crawl_queue(status, priority DESC, updated_at);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    start_url TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    pages_loaded INTEGER NOT NULL DEFAULT 0,
    items_total INTEGER NOT NULL DEFAULT 0,
    items_success INTEGER NOT NULL DEFAULT 0,
    items_failed INTEGER NOT NULL DEFAULT 0,
    items_blocked INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")



def canonical_source_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split("#", 1)[0]


def source_type_for_url(value: Any) -> str:
    text = canonical_source_url(value).casefold()
    return "CLIENT_CATALOG" if "/seller/" in text else "MARKET_SEARCH" if "/search/" in text else "MARKET_CATEGORY"


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def seller_key(item: dict[str, Any]) -> str:
    seller_id = str(item.get("seller_id") or "").strip()
    if seller_id:
        return seller_id
    name = str(item.get("seller_name") or "UNKNOWN").strip().casefold()
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect_database(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.backfill_source_memberships()
        self.reset_stale_running_tasks()

    def close(self) -> None:
        self.conn.close()


    def backfill_source_memberships(self) -> None:
        """Create source memberships for registries produced before version 3.2.4."""
        timestamp = now_iso()
        rows = self.conn.execute(
            "SELECT article,discovery_url,first_seen_at,last_seen_at FROM products"
        ).fetchall()
        with self.conn:
            for row in rows:
                source_url = canonical_source_url(row["discovery_url"])
                if not source_url:
                    continue
                source_type = source_type_for_url(source_url)
                first_seen = str(row["first_seen_at"] or timestamp)
                last_seen = str(row["last_seen_at"] or timestamp)
                self.conn.execute(
                    """
                    INSERT INTO catalog_sources(source_url,source_type,label,first_seen_at,last_seen_at,active)
                    VALUES(?,?,?,?,?,1)
                    ON CONFLICT(source_url) DO UPDATE SET
                        source_type=excluded.source_type,
                        last_seen_at=MAX(catalog_sources.last_seen_at, excluded.last_seen_at),
                        active=1
                    """,
                    (source_url, source_type, "", first_seen, last_seen),
                )
                self.conn.execute(
                    """
                    INSERT INTO product_sources(
                        article,source_url,source_type,first_seen_at,last_seen_at,last_run_id,page_no
                    ) VALUES(?,?,?,?,?,'',0)
                    ON CONFLICT(article,source_url) DO UPDATE SET
                        source_type=excluded.source_type,
                        last_seen_at=MAX(product_sources.last_seen_at, excluded.last_seen_at)
                    """,
                    (str(row["article"]), source_url, source_type, first_seen, last_seen),
                )

    def reset_stale_running_tasks(self) -> None:
        cutoff = (datetime.now() - timedelta(hours=12)).isoformat(timespec="seconds")
        with self.conn:
            self.conn.execute(
                """
                UPDATE crawl_queue
                SET status='PENDING', updated_at=?
                WHERE status='RUNNING' AND COALESCE(last_attempt_at, '') < ?
                """,
                (now_iso(), cutoff),
            )

    def begin_run(self, run_id: str, mode: str, start_url: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO runs(run_id,mode,start_url,started_at,status) VALUES(?,?,?,?,?)",
                (run_id, mode, start_url, now_iso(), "RUNNING"),
            )

    def finish_run(self, run_id: str, status: str, metrics: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE runs SET finished_at=?, status=?, pages_loaded=?, items_total=?,
                    items_success=?, items_failed=?, items_blocked=?, duration_seconds=?, notes=?
                WHERE run_id=?
                """,
                (
                    now_iso(),
                    status,
                    int(metrics.get("pages_loaded") or 0),
                    int(metrics.get("items_total") or 0),
                    int(metrics.get("items_success") or 0),
                    int(metrics.get("items_failed") or 0),
                    int(metrics.get("items_blocked") or 0),
                    float(metrics.get("duration_seconds") or 0),
                    str(metrics.get("notes") or ""),
                    run_id,
                ),
            )

    def queue_task(self, article: str, task_type: str, priority: int = 50) -> None:
        timestamp = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO crawl_queue(article,task_type,status,priority,attempts,updated_at)
                VALUES(?,?,?,?,0,?)
                ON CONFLICT(article,task_type) DO UPDATE SET
                    priority=MAX(crawl_queue.priority, excluded.priority),
                    status=CASE WHEN crawl_queue.status='DONE' THEN 'PENDING' ELSE crawl_queue.status END,
                    updated_at=excluded.updated_at
                """,
                (article, task_type, "PENDING", priority, timestamp),
            )

    def upsert_catalog_product(
        self,
        item: dict[str, Any],
        discovery_url: str,
        run_id: str,
        page_no: int,
        collected_at: str,
    ) -> tuple[bool, bool]:
        article = str(item.get("article") or "").strip()
        if not article:
            return False, False
        current = self.conn.execute(
            "SELECT catalog_price, detail_status FROM products WHERE article=?", (article,)
        ).fetchone()
        is_new = current is None
        old_price = int(current["catalog_price"] or 0) if current else 0
        new_price = int(item.get("catalog_card_price") or 0)
        price_changed = bool(current and new_price and old_price and new_price != old_price)
        timestamp = collected_at
        source_url = canonical_source_url(discovery_url)
        source_type = source_type_for_url(source_url)
        with self.conn:
            if source_url:
                self.conn.execute(
                    """
                    INSERT INTO catalog_sources(source_url,source_type,label,first_seen_at,last_seen_at,active)
                    VALUES(?,?,?,?,?,1)
                    ON CONFLICT(source_url) DO UPDATE SET
                        source_type=excluded.source_type,
                        last_seen_at=excluded.last_seen_at,
                        active=1
                    """,
                    (source_url, source_type, "", timestamp, timestamp),
                )
            self.conn.execute(
                """
                INSERT INTO products(
                    article,canonical_url,title,image_url,discovery_url,first_seen_at,last_seen_at,
                    last_catalog_at,catalog_price,detail_status,active
                ) VALUES(?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(article) DO UPDATE SET
                    canonical_url=CASE WHEN excluded.canonical_url<>'' THEN excluded.canonical_url ELSE products.canonical_url END,
                    title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE products.title END,
                    image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE products.image_url END,
                    discovery_url=excluded.discovery_url,
                    last_seen_at=excluded.last_seen_at,
                    last_catalog_at=excluded.last_catalog_at,
                    catalog_price=CASE WHEN excluded.catalog_price>0 THEN excluded.catalog_price ELSE products.catalog_price END,
                    active=1
                """,
                (
                    article,
                    str(item.get("url") or ""),
                    str(item.get("name") or ""),
                    str(item.get("image_url") or ""),
                    discovery_url,
                    timestamp,
                    timestamp,
                    timestamp,
                    new_price,
                    "NEW",
                ),
            )
            if source_url:
                self.conn.execute(
                    """
                    INSERT INTO product_sources(
                        article,source_url,source_type,first_seen_at,last_seen_at,last_run_id,page_no
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(article,source_url) DO UPDATE SET
                        source_type=excluded.source_type,
                        last_seen_at=excluded.last_seen_at,
                        last_run_id=excluded.last_run_id,
                        page_no=excluded.page_no
                    """,
                    (article, source_url, source_type, timestamp, timestamp, run_id, int(page_no)),
                )
            self.conn.execute(
                """
                INSERT OR REPLACE INTO catalog_snapshots(
                    run_id,article,page_no,catalog_price,title,canonical_url,collected_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    article,
                    page_no,
                    new_price,
                    str(item.get("name") or ""),
                    str(item.get("url") or ""),
                    timestamp,
                ),
            )
        if is_new or not current or str(current["detail_status"] or "") != "COMPLETE":
            self.queue_task(article, "ENRICH", 100 if is_new else 80)
        elif price_changed:
            self.queue_task(article, "REFRESH_PRICE", 90)
        return is_new, price_changed

    def get_product(self, article: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM products WHERE article=?", (article,)).fetchone()
        return dict(row) if row else {}

    def catalog_product_for_parser(self, article: str) -> dict[str, Any]:
        row = self.get_product(article)
        return {
            "article": article,
            "name": row.get("title", ""),
            "catalog_card_price": row.get("catalog_price", 0),
            "image_url": row.get("image_url", ""),
            "url": row.get("canonical_url", ""),
        }

    def articles_for_sources(self, source_urls: Iterable[Any]) -> set[str]:
        sources = {
            canonical_source_url(value)
            for value in source_urls
            if canonical_source_url(value)
        }
        if not sources:
            return set()
        placeholders = ",".join("?" for _ in sources)
        return {
            str(row[0])
            for row in self.conn.execute(
                f"""SELECT DISTINCT article
                    FROM product_sources
                    WHERE source_url IN ({placeholders})""",
                sorted(sources),
            ).fetchall()
        }

    def select_articles(
        self, mode: str, limit: int, stale_days: int = 30, max_attempts: int = 3,
        allowed_articles: set[str] | None = None,
    ) -> list[str]:
        params: list[Any] = []
        if mode == "enrich-new":
            sql = """
                SELECT p.article FROM products p
                LEFT JOIN crawl_queue q ON q.article=p.article AND q.task_type='ENRICH'
                WHERE p.active=1 AND (
                    p.detail_status IN ('NEW','ERROR','BLOCKED','CATALOG_ONLY') OR p.last_detail_at IS NULL
                ) AND COALESCE(q.attempts,0) < ?
                ORDER BY COALESCE(q.priority,50) DESC, p.first_seen_at
            """
            params.append(max_attempts)
        elif mode == "refresh-prices":
            sql = """
                SELECT article FROM products WHERE active=1
                ORDER BY COALESCE(last_price_at,'') ASC, article
            """
        elif mode == "refresh-stale":
            cutoff = (datetime.now() - timedelta(days=stale_days)).isoformat(timespec="seconds")
            sql = """
                SELECT article FROM products WHERE active=1
                AND (last_detail_at IS NULL OR last_detail_at < ?)
                ORDER BY COALESCE(last_detail_at,'') ASC
            """
            params.append(cutoff)
        elif mode == "retry-failed":
            sql = """
                SELECT DISTINCT p.article FROM products p
                JOIN crawl_queue q ON q.article=p.article
                WHERE p.active=1 AND q.status IN ('FAILED','BLOCKED') AND q.attempts < ?
                  AND (q.next_attempt_at IS NULL OR q.next_attempt_at <= ?)
                ORDER BY q.priority DESC, q.updated_at
            """
            params.extend([max_attempts, now_iso()])
        elif mode == "stress-test":
            sql = """
                SELECT article FROM products WHERE active=1
                ORDER BY COALESCE(last_price_at,'') ASC, article
            """
        else:
            raise ValueError(f"Unknown mode: {mode}")
        values = [str(row[0]) for row in self.conn.execute(sql, params).fetchall()]
        if allowed_articles is not None:
            values = [article for article in values if article in allowed_articles]
        if limit > 0:
            values = values[:limit]
        return values

    def claim_task(self, article: str, task_type: str) -> None:
        timestamp = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO crawl_queue(article,task_type,status,priority,attempts,last_attempt_at,updated_at)
                VALUES(?,?,?,?,1,?,?)
                ON CONFLICT(article,task_type) DO UPDATE SET
                    status='RUNNING', attempts=crawl_queue.attempts+1,
                    last_attempt_at=excluded.last_attempt_at, updated_at=excluded.updated_at
                """,
                (article, task_type, "RUNNING", 50, timestamp, timestamp),
            )

    def complete_task(self, article: str, task_type: str) -> None:
        with self.conn:
            if task_type == "RETRY":
                self.conn.execute(
                    """
                    UPDATE crawl_queue SET status='DONE', last_error='', next_attempt_at=NULL, updated_at=?
                    WHERE article=?
                    """,
                    (now_iso(), article),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE crawl_queue SET status='DONE', last_error='', next_attempt_at=NULL, updated_at=?
                    WHERE article=? AND task_type=?
                    """,
                    (now_iso(), article, task_type),
                )

    def fail_task(self, article: str, task_type: str, status: str, error: str, backoff_seconds: int) -> None:
        next_at = (datetime.now() + timedelta(seconds=backoff_seconds)).isoformat(timespec="seconds")
        product_status = "BLOCKED" if status == "BLOCKED" else "ERROR"
        with self.conn:
            self.conn.execute(
                """
                UPDATE crawl_queue SET status=?, last_error=?, next_attempt_at=?, updated_at=?
                WHERE article=? AND task_type=?
                """,
                (status, error[:2000], next_at, now_iso(), article, task_type),
            )
            self.conn.execute(
                "UPDATE products SET detail_status=?, last_error=? WHERE article=?",
                (product_status, error[:2000], article),
            )

    def update_from_detail(
        self,
        item: dict[str, Any],
        normalized: dict[str, Any],
        run_id: str,
        collected_at: str,
        raw_json_path: str = "",
    ) -> None:
        article = str(item.get("article") or "")
        key = seller_key(item)
        details_payload = {
            name: normalized.get(name)
            for name in (
                "brand", "model", "manufacturer_article", "tire_size", "width_mm",
                "profile_percent", "diameter_inch", "load_index", "speed_index",
                "season", "studded", "xl", "runflat", "product_identity_key"
            )
        }
        details_digest = stable_hash(details_payload)
        price_payload = {
            "seller_key": key,
            "card_price": int(normalized.get("price") or 0),
            "catalog_price": int(normalized.get("catalog_price") or 0),
            "regular_price": int(normalized.get("regular_price") or 0),
            "original_price": int(normalized.get("original_price") or 0),
            "availability": str(normalized.get("availability_status") or "UNKNOWN"),
        }
        price_digest = stable_hash(price_payload)
        studded = normalized.get("studded")
        studded_db = None if studded is None else int(bool(studded))
        with self.conn:
            self.conn.execute(
                """
                UPDATE products SET
                    canonical_url=CASE WHEN ?<>'' THEN ? ELSE canonical_url END,
                    title=CASE WHEN ?<>'' THEN ? ELSE title END,
                    brand=?, model=?, manufacturer_article=?, tire_size=?, width_mm=?,
                    profile_percent=?, diameter_inch=?, load_index=?, speed_index=?, season=?,
                    studded=?, xl=?, runflat=?, image_url=CASE WHEN ?<>'' THEN ? ELSE image_url END,
                    product_identity_key=?, identity_completeness_percent=?, last_detail_at=?,
                    last_price_at=?, detail_status='COMPLETE', details_hash=?, raw_json_path=CASE WHEN ?<>'' THEN ? ELSE raw_json_path END,
                    last_error='', active=1
                WHERE article=?
                """,
                (
                    str(normalized.get("source_url") or ""), str(normalized.get("source_url") or ""),
                    str(normalized.get("title") or ""), str(normalized.get("title") or ""),
                    str(normalized.get("brand") or ""), str(normalized.get("model") or ""),
                    str(normalized.get("manufacturer_article") or ""), str(normalized.get("tire_size") or ""),
                    str(normalized.get("width_mm") or ""), str(normalized.get("profile_percent") or ""),
                    str(normalized.get("diameter_inch") or ""), str(normalized.get("load_index") or ""),
                    str(normalized.get("speed_index") or ""), str(normalized.get("season") or "UNKNOWN"),
                    studded_db, int(bool(normalized.get("xl"))), int(bool(normalized.get("runflat"))),
                    str(normalized.get("image_url") or ""), str(normalized.get("image_url") or ""),
                    str(normalized.get("product_identity_key") or ""),
                    float(normalized.get("identity_completeness_percent") or 0),
                    collected_at, collected_at, details_digest,
                    raw_json_path, raw_json_path, article,
                ),
            )
            existing = self.conn.execute(
                "SELECT first_seen_at FROM offers WHERE article=? AND seller_key=?", (article, key)
            ).fetchone()
            first_seen = str(existing[0]) if existing else collected_at
            self.conn.execute(
                """
                INSERT INTO offers(
                    article,seller_key,seller_id,seller_name,seller_url,seller_rating,
                    card_price,catalog_price,regular_price,original_price,currency,
                    availability_status,location_city,location_country,product_rating,
                    review_count,price_hash,first_seen_at,last_seen_at,last_checked_at,active
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(article,seller_key) DO UPDATE SET
                    seller_id=excluded.seller_id, seller_name=excluded.seller_name,
                    seller_url=excluded.seller_url, seller_rating=excluded.seller_rating,
                    card_price=excluded.card_price, catalog_price=excluded.catalog_price,
                    regular_price=excluded.regular_price, original_price=excluded.original_price,
                    availability_status=excluded.availability_status,
                    location_city=excluded.location_city, location_country=excluded.location_country,
                    product_rating=excluded.product_rating, review_count=excluded.review_count,
                    price_hash=excluded.price_hash, last_seen_at=excluded.last_seen_at,
                    last_checked_at=excluded.last_checked_at, active=1
                """,
                (
                    article, key, str(item.get("seller_id") or ""), str(item.get("seller_name") or ""),
                    str(item.get("seller_link") or ""), item.get("seller_rating"),
                    int(normalized.get("price") or 0), int(normalized.get("catalog_price") or 0),
                    int(normalized.get("regular_price") or 0), int(normalized.get("original_price") or 0),
                    str(normalized.get("currency") or "RUB"), str(normalized.get("availability_status") or "UNKNOWN"),
                    str(normalized.get("location_city") or ""), str(normalized.get("location_country") or ""),
                    normalized.get("product_rating"), normalized.get("review_count"), price_digest,
                    first_seen, collected_at, collected_at,
                ),
            )
            self.conn.execute(
                """
                INSERT OR IGNORE INTO price_history(
                    run_id,article,seller_key,card_price,catalog_price,regular_price,
                    original_price,availability_status,currency,collected_at,price_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, article, key, int(normalized.get("price") or 0),
                    int(normalized.get("catalog_price") or 0), int(normalized.get("regular_price") or 0),
                    int(normalized.get("original_price") or 0), str(normalized.get("availability_status") or "UNKNOWN"),
                    str(normalized.get("currency") or "RUB"), collected_at, price_digest,
                ),
            )


    def client_products_for_market_search(
        self, limit: int = 0, allowed_articles: set[str] | None = None
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT p.*,j.last_search_at,j.exact_found,j.comparable_found
            FROM products p
            LEFT JOIN market_search_jobs j ON j.client_article=p.article
            WHERE p.active=1 AND p.detail_status='COMPLETE'
              AND TRIM(p.brand)<>'' AND TRIM(p.tire_size)<>''
              AND EXISTS(
                  SELECT 1 FROM product_sources ps
                  WHERE ps.article=p.article AND ps.source_type='CLIENT_CATALOG'
              )
            ORDER BY CASE WHEN j.last_search_at IS NULL THEN 0 ELSE 1 END,
                     COALESCE(j.exact_found,0),COALESCE(j.comparable_found,0),
                     COALESCE(j.last_search_at,''),p.article
        """
        values = [dict(row) for row in self.conn.execute(sql).fetchall()]
        if allowed_articles is not None:
            values = [row for row in values if str(row.get("article") or "") in allowed_articles]
        if limit > 0:
            values = values[: int(limit)]
        return values

    def primary_offer(self, article: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT * FROM offers WHERE article=? AND active=1
            ORDER BY CASE WHEN card_price>0 THEN 0 ELSE 1 END,last_checked_at DESC LIMIT 1
            """, (str(article),)
        ).fetchone()
        return dict(row) if row else {}

    def begin_market_search(self, client_article: str, run_id: str) -> None:
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO market_search_jobs(client_article,status,attempts,last_search_at,last_run_id)
                VALUES(?,'RUNNING',1,?,?)
                ON CONFLICT(client_article) DO UPDATE SET
                    status='RUNNING',attempts=market_search_jobs.attempts+1,
                    last_search_at=excluded.last_search_at,last_run_id=excluded.last_run_id,
                    last_error=''
                """, (str(client_article), stamp, run_id)
            )
            self.conn.execute(
                "UPDATE market_search_candidates SET active=0 WHERE client_article=?",
                (str(client_article),)
            )

    def save_market_candidate(
        self, client_article: str, candidate_article: str, query_text: str,
        query_url: str, catalog_rank: int, match: dict[str, Any], run_id: str,
    ) -> None:
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO market_search_candidates(
                    client_article,candidate_article,query_text,query_url,catalog_rank,
                    match_level,match_score,match_method,match_reason,reasons_json,
                    active,first_seen_at,last_seen_at,last_checked_at,last_run_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)
                ON CONFLICT(client_article,candidate_article) DO UPDATE SET
                    query_text=excluded.query_text,query_url=excluded.query_url,
                    catalog_rank=MIN(market_search_candidates.catalog_rank,excluded.catalog_rank),
                    match_level=CASE WHEN excluded.match_score>=market_search_candidates.match_score
                        THEN excluded.match_level ELSE market_search_candidates.match_level END,
                    match_score=MAX(market_search_candidates.match_score,excluded.match_score),
                    match_method=CASE WHEN excluded.match_score>=market_search_candidates.match_score
                        THEN excluded.match_method ELSE market_search_candidates.match_method END,
                    match_reason=CASE WHEN excluded.match_score>=market_search_candidates.match_score
                        THEN excluded.match_reason ELSE market_search_candidates.match_reason END,
                    reasons_json=CASE WHEN excluded.match_score>=market_search_candidates.match_score
                        THEN excluded.reasons_json ELSE market_search_candidates.reasons_json END,
                    active=1,last_seen_at=excluded.last_seen_at,last_checked_at=excluded.last_checked_at,
                    last_run_id=excluded.last_run_id
                """,
                (
                    str(client_article), str(candidate_article), query_text, query_url,
                    int(catalog_rank), str(match.get('level') or 'REJECTED'),
                    float(match.get('score') or 0), str(match.get('method') or ''),
                    str(match.get('reason') or ''), json.dumps(match.get('reasons') or [], ensure_ascii=False),
                    stamp, stamp, stamp, run_id,
                )
            )

    def finish_market_search(
        self, client_article: str, query_text: str, query_url: str,
        status: str, candidates: int, exact: int, comparable: int,
        run_id: str, error: str = '',
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO market_search_jobs(
                    client_article,query_text,query_url,status,attempts,candidates_found,
                    exact_found,comparable_found,last_search_at,last_error,last_run_id
                ) VALUES(?,?,?,?,1,?,?,?,?,?,?)
                ON CONFLICT(client_article) DO UPDATE SET
                    query_text=excluded.query_text,query_url=excluded.query_url,status=excluded.status,
                    candidates_found=excluded.candidates_found,exact_found=excluded.exact_found,
                    comparable_found=excluded.comparable_found,last_search_at=excluded.last_search_at,
                    last_error=excluded.last_error,last_run_id=excluded.last_run_id
                """,
                (str(client_article),query_text,query_url,status,int(candidates),int(exact),
                 int(comparable),now_iso(),str(error)[:2000],run_id)
            )

    def counts(self) -> dict[str, int]:
        queries = {
            "products": "SELECT COUNT(*) FROM products",
            "active_products": "SELECT COUNT(*) FROM products WHERE active=1",
            "complete_products": "SELECT COUNT(*) FROM products WHERE detail_status='COMPLETE'",
            "offers": "SELECT COUNT(*) FROM offers WHERE active=1",
            "price_points": "SELECT COUNT(*) FROM price_history",
            "pending_tasks": "SELECT COUNT(*) FROM crawl_queue WHERE status IN ('PENDING','RUNNING')",
            "failed_tasks": "SELECT COUNT(*) FROM crawl_queue WHERE status IN ('FAILED','BLOCKED')",
            "market_search_products": "SELECT COUNT(*) FROM market_search_jobs",
            "market_exact_matches": "SELECT COUNT(*) FROM market_search_candidates WHERE active=1 AND match_level IN ('EXACT','STRONG')",
            "market_comparable_matches": "SELECT COUNT(*) FROM market_search_candidates WHERE active=1 AND match_level='COMPARABLE'",
        }
        return {name: int(self.conn.execute(sql).fetchone()[0]) for name, sql in queries.items()}

    def export_current(self, json_path: Path, csv_path: Path) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT
                    'ozon_ru' AS source,
                    p.article AS source_product_id,
                    o.seller_id AS source_seller_id,
                    o.seller_name AS source_seller_name,
                    p.canonical_url AS source_url,
                    p.title, p.brand, p.model, p.manufacturer_article, p.tire_size,
                    p.width_mm, p.profile_percent, p.diameter_inch, p.load_index,
                    p.speed_index, p.season, p.studded, p.xl, p.runflat,
                    p.product_identity_key, p.identity_completeness_percent,
                    o.card_price AS price, o.catalog_price, o.regular_price,
                    o.original_price, o.currency, o.availability_status,
                    o.location_city, o.location_country, o.seller_rating,
                    o.product_rating, o.review_count, o.last_checked_at AS collected_at,
                    p.detail_status, p.active
                FROM products p
                LEFT JOIN offers o ON o.article=p.article
                AND o.last_checked_at=(SELECT MAX(o2.last_checked_at) FROM offers o2 WHERE o2.article=p.article)
                WHERE p.active=1
                ORDER BY p.article
                """
            ).fetchall()
        ]
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        fields: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fields.append(key)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return rows
