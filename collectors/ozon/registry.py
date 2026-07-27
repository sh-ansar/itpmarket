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
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.reset_stale_running_tasks()

    def close(self) -> None:
        self.conn.close()

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
        with self.conn:
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

    def select_articles(self, mode: str, limit: int, stale_days: int = 30, max_attempts: int = 3) -> list[str]:
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
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        return [str(row[0]) for row in self.conn.execute(sql, params).fetchall()]

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

    def counts(self) -> dict[str, int]:
        queries = {
            "products": "SELECT COUNT(*) FROM products",
            "active_products": "SELECT COUNT(*) FROM products WHERE active=1",
            "complete_products": "SELECT COUNT(*) FROM products WHERE detail_status='COMPLETE'",
            "offers": "SELECT COUNT(*) FROM offers WHERE active=1",
            "price_points": "SELECT COUNT(*) FROM price_history",
            "pending_tasks": "SELECT COUNT(*) FROM crawl_queue WHERE status IN ('PENDING','RUNNING')",
            "failed_tasks": "SELECT COUNT(*) FROM crawl_queue WHERE status IN ('FAILED','BLOCKED')",
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
