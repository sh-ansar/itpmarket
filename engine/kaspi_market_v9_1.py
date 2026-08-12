#!/usr/bin/env python3
"""
Kaspi Market V9 — инкрементальный мониторинг цен и аналогов.

V9 использует существующую SQLite-базу V7/V8 и разделяет работу на этапы:

* audit-catalog   — проверка полноты исходного каталога;
* merge-catalog   — объединение дополнительных CSV/JSON-выгрузок;
* discover        — поиск и проверка аналогов с общим кэшем;
* refresh-prices  — обновление цен без повторного поиска аналогов;
* retry-errors    — повтор только неуспешных позиций;
* dashboard       — локальная интерактивная панель;
* status          — краткий статус и оценка времени.

Скрипт не обходит CAPTCHA и ограничения Kaspi. При появлении проверки
работа приостанавливается до ручного подтверждения в открытом Chromium.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import html
import json
import math
import random
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

try:
    from . import kaspi_search_compare_v8_2 as core
except ImportError:
    import kaspi_search_compare_v8_2 as core


VERSION = "9.1"
DEFAULT_DB = r"data\kaspi_market.db"
DEFAULT_OUTPUT = "output"
DEFAULT_PROFILE = ".kaspi_profile"
DEFAULT_CITY_ID = core.DEFAULT_CITY_ID


# ---------------------------------------------------------------------------
# Базовые функции
# ---------------------------------------------------------------------------


def now_dt() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def parse_iso(value: Any) -> datetime | None:
    text = core.clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def is_fresh(value: Any, ttl_days: float) -> bool:
    stamp = parse_iso(value)
    if stamp is None:
        return False
    return stamp >= now_dt() - timedelta(days=max(0.0, ttl_days))


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-zА-Яа-я0-9_.-]+", "_", value).strip("_") or "file"


def format_seconds(value: float | int | None) -> str:
    if value is None:
        return "—"
    seconds = max(0, int(round(float(value))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {seconds} сек"
    return f"{seconds} сек"


def save_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})
    temporary.replace(path)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


# ---------------------------------------------------------------------------
# База данных и миграция
# ---------------------------------------------------------------------------


class Database(core.Database):
    """Расширяет существующую базу без удаления таблиц V7/V8."""

    def create_schema(self) -> None:
        super().create_schema()
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS v9_product_cache (
                product_code TEXT PRIMARY KEY,
                title TEXT,
                product_url TEXT,
                specifications_json TEXT,
                product_type TEXT,
                group_key TEXT,
                fetched_at TEXT,
                source TEXT,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS v9_group_search_cache (
                group_key TEXT PRIMARY KEY,
                query_text TEXT,
                cards_json TEXT,
                cards_count INTEGER,
                search_pages INTEGER,
                fetched_at TEXT,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS v9_discovery_state (
                source_product_code TEXT PRIMARY KEY,
                group_key TEXT,
                query_text TEXT,
                status TEXT,
                exact_status TEXT,
                candidates_found INTEGER,
                candidates_validated INTEGER,
                accepted_count INTEGER,
                review_count INTEGER,
                cache_hits INTEGER,
                cache_misses INTEGER,
                duration_seconds REAL,
                error TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS v9_price_state (
                candidate_product_code TEXT PRIMARY KEY,
                status TEXT,
                offers_count INTEGER,
                min_price_kzt REAL,
                duration_seconds REAL,
                error TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS v9_price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_product_code TEXT,
                candidate_product_code TEXT,
                merchant_id TEXT,
                merchant_name TEXT,
                merchant_sku TEXT,
                price_kzt REAL,
                merchant_rating REAL,
                merchant_reviews INTEGER,
                captured_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_v9_price_snapshots_code_time
            ON v9_price_snapshots(candidate_product_code, captured_at);

            CREATE TABLE IF NOT EXISTS v9_run_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT,
                item_key TEXT,
                status TEXT,
                duration_seconds REAL,
                cache_hits INTEGER,
                cache_misses INTEGER,
                attempts INTEGER,
                error TEXT,
                started_at TEXT,
                finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_v9_metrics_command_time
            ON v9_run_metrics(command, finished_at);

            CREATE TABLE IF NOT EXISTS v9_catalog_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT,
                rows_read INTEGER,
                rows_valid INTEGER,
                inserted_count INTEGER,
                updated_count INTEGER,
                imported_at TEXT
            );

            CREATE TABLE IF NOT EXISTS v9_catalog_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                catalog_count INTEGER,
                unique_count INTEGER,
                duplicate_count INTEGER,
                max_page INTEGER,
                typical_page_size INTEGER,
                last_page_size INTEGER,
                details_ok INTEGER,
                details_error INTEGER,
                details_missing INTEGER,
                expected_count INTEGER,
                coverage_pct REAL,
                suspected_truncation INTEGER,
                audit_json TEXT,
                audited_at TEXT
            );
            """
        )

        # Сохраняем уже выполненные результаты V8.2, чтобы V9 не начинала
        # обработку с нуля. При необходимости их можно пересчитать --refresh.
        self.conn.execute(
            """
            INSERT OR IGNORE INTO v9_discovery_state(
                source_product_code,
                group_key,
                query_text,
                status,
                exact_status,
                candidates_found,
                candidates_validated,
                accepted_count,
                review_count,
                cache_hits,
                cache_misses,
                duration_seconds,
                error,
                updated_at
            )
            SELECT
                source_product_code,
                '',
                query_text,
                CASE WHEN status='ok' THEN 'ok' ELSE 'error' END,
                CASE WHEN status='ok' THEN 'legacy' ELSE 'error' END,
                candidates_found,
                candidates_validated,
                accepted_count,
                review_count,
                0,
                0,
                NULL,
                error,
                COALESCE(finished_at, started_at)
            FROM market_search_runs
            """
        )
        self.conn.commit()

    def save_metric(
        self,
        command: str,
        item_key: str,
        status: str,
        duration_seconds: float,
        cache_hits: int = 0,
        cache_misses: int = 0,
        attempts: int = 1,
        error: str | None = None,
        started_at: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO v9_run_metrics(
                command, item_key, status, duration_seconds,
                cache_hits, cache_misses, attempts, error,
                started_at, finished_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                command,
                item_key,
                status,
                duration_seconds,
                cache_hits,
                cache_misses,
                attempts,
                error,
                started_at or now_iso(),
                now_iso(),
            ),
        )
        self.conn.commit()

    def discovery_jobs(
        self,
        limit: int,
        refresh: bool,
        only_errors: bool,
        codes: list[str],
    ) -> list[dict[str, Any]]:
        where = ["1=1"]
        params: list[Any] = []

        if only_errors:
            where.append(
                """
                EXISTS (
                    SELECT 1 FROM v9_discovery_state s
                    WHERE s.source_product_code=c.product_code
                      AND s.status IN ('error','partial')
                )
                """
            )
        elif not refresh:
            where.append(
                """
                NOT EXISTS (
                    SELECT 1 FROM v9_discovery_state s
                    WHERE s.source_product_code=c.product_code
                      AND s.status IN ('ok','partial')
                )
                """
            )

        if codes:
            placeholders = ",".join("?" for _ in codes)
            where.append(f"c.product_code IN ({placeholders})")
            params.extend(codes)

        sql = f"""
        SELECT
            c.product_code,
            c.page_number,
            c.position_on_page,
            c.title_catalog,
            c.catalog_price_kzt,
            c.catalog_rating,
            c.catalog_reviews,
            c.product_url,
            c.image_url,
            c.collected_at,
            d.title_detail,
            d.specifications_json,
            d.detail_status,
            d.detail_collected_at
        FROM catalog_products c
        LEFT JOIN product_details d ON d.product_code=c.product_code
        WHERE {' AND '.join(where)}
        ORDER BY
            COALESCE(c.page_number, 999999),
            COALESCE(c.position_on_page, 999999),
            c.product_code
        """
        rows = [dict(row) for row in self.conn.execute(sql, params).fetchall()]
        return rows[:limit] if limit > 0 else rows

    def get_product_cache(
        self,
        product_code: str,
        ttl_days: float,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM v9_product_cache WHERE product_code=?",
            (product_code,),
        ).fetchone()
        if not row or not is_fresh(row["fetched_at"], ttl_days):
            return None
        result = dict(row)
        result["specifications"] = core.clean_specs(
            result.get("specifications_json")
        )
        return result

    def save_product_cache(
        self,
        product_code: str,
        title: str,
        product_url: str,
        specifications: Any,
        source: str,
        group_key: str = "",
        last_error: str | None = None,
    ) -> None:
        product_type = core.product_type(title, core.specs_map(specifications))
        self.conn.execute(
            """
            INSERT INTO v9_product_cache(
                product_code, title, product_url, specifications_json,
                product_type, group_key, fetched_at, source, last_error
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(product_code) DO UPDATE SET
                title=CASE
                    WHEN excluded.title<>'' THEN excluded.title
                    ELSE v9_product_cache.title
                END,
                product_url=CASE
                    WHEN excluded.product_url<>'' THEN excluded.product_url
                    ELSE v9_product_cache.product_url
                END,
                specifications_json=CASE
                    WHEN excluded.specifications_json NOT IN ('', '[]')
                    THEN excluded.specifications_json
                    ELSE v9_product_cache.specifications_json
                END,
                product_type=CASE
                    WHEN excluded.product_type<>'other'
                    THEN excluded.product_type
                    ELSE v9_product_cache.product_type
                END,
                group_key=CASE
                    WHEN excluded.group_key<>'' THEN excluded.group_key
                    ELSE v9_product_cache.group_key
                END,
                fetched_at=excluded.fetched_at,
                source=excluded.source,
                last_error=excluded.last_error
            """,
            (
                product_code,
                title,
                product_url,
                json_dumps(core.clean_specs(specifications)),
                product_type,
                group_key,
                now_iso(),
                source,
                last_error,
            ),
        )
        self.conn.commit()

    def get_group_cache(
        self,
        group_key: str,
        ttl_days: float,
        required_pages: int,
    ) -> list[dict[str, Any]] | None:
        row = self.conn.execute(
            "SELECT * FROM v9_group_search_cache WHERE group_key=?",
            (group_key,),
        ).fetchone()
        if not row:
            return None
        if not is_fresh(row["fetched_at"], ttl_days):
            return None
        if int(row["search_pages"] or 0) < required_pages:
            return None
        cards = core.safe_json(row["cards_json"], [])
        return [dict(card) for card in cards if isinstance(card, dict)]

    def save_group_cache(
        self,
        group_key: str,
        query_text: str,
        cards: list[dict[str, Any]],
        search_pages: int,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO v9_group_search_cache(
                group_key, query_text, cards_json, cards_count,
                search_pages, fetched_at, last_error
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(group_key) DO UPDATE SET
                query_text=excluded.query_text,
                cards_json=excluded.cards_json,
                cards_count=excluded.cards_count,
                search_pages=excluded.search_pages,
                fetched_at=excluded.fetched_at,
                last_error=excluded.last_error
            """,
            (
                group_key,
                query_text,
                json_dumps(cards),
                len(cards),
                search_pages,
                now_iso(),
                error,
            ),
        )
        self.conn.commit()

    def save_discovery_state(
        self,
        code: str,
        group_key: str,
        query_text: str,
        status: str,
        exact_status: str,
        candidates_found: int,
        validated: int,
        accepted: int,
        review: int,
        cache_hits: int,
        cache_misses: int,
        duration_seconds: float,
        error: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO v9_discovery_state VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            ON CONFLICT(source_product_code) DO UPDATE SET
                group_key=excluded.group_key,
                query_text=excluded.query_text,
                status=excluded.status,
                exact_status=excluded.exact_status,
                candidates_found=excluded.candidates_found,
                candidates_validated=excluded.candidates_validated,
                accepted_count=excluded.accepted_count,
                review_count=excluded.review_count,
                cache_hits=excluded.cache_hits,
                cache_misses=excluded.cache_misses,
                duration_seconds=excluded.duration_seconds,
                error=excluded.error,
                updated_at=excluded.updated_at
            """,
            (
                code,
                group_key,
                query_text,
                status,
                exact_status,
                candidates_found,
                validated,
                accepted,
                review,
                cache_hits,
                cache_misses,
                duration_seconds,
                error,
                now_iso(),
            ),
        )
        self.conn.commit()


# ---------------------------------------------------------------------------
# Группировка товаров
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductGroup:
    key: str
    query: str
    product_type: str
    label: str


def _normalized_flag(value: str) -> str:
    normalized = core.normalize(value)
    if normalized in {"да", "есть", "с шипами", "шипованные"}:
        return "yes"
    if normalized in {"нет", "без шипов", "нешипованные"}:
        return "no"
    return normalized or "-"


def build_group(title: str, specifications: Any) -> ProductGroup:
    attrs = core.extract_attributes(title, specifications)
    product_type = attrs.get("type") or "other"

    if product_type in {"tire", "moto_tire"}:
        parts = [
            product_type,
            attrs.get("width") or "-",
            attrs.get("height") or "-",
            attrs.get("diameter") or "-",
            attrs.get("load") or "-",
            attrs.get("speed") or "-",
            attrs.get("season") or "-",
            _normalized_flag(attrs.get("studs") or ""),
            attrs.get("axis") or "-",
            attrs.get("runflat") or "-",
            attrs.get("tire_type") or "-",
            attrs.get("commercial") or "-",
            attrs.get("offroad_marking") or "-",
        ]
        key = "|".join(parts)

        query_parts: list[str] = []
        if attrs.get("width") and attrs.get("height") and attrs.get("diameter"):
            query_parts.append(
                f"{attrs['width']}/{attrs['height']} R{attrs['diameter']}"
            )
        if attrs.get("load") or attrs.get("speed"):
            query_parts.append(f"{attrs.get('load','')}{attrs.get('speed','')}")
        query_parts.append("мотошина" if product_type == "moto_tire" else "шина")
        if attrs.get("axis"):
            query_parts.append(attrs["axis"])
        if attrs.get("offroad_marking"):
            query_parts.append(attrs["offroad_marking"])
        if "да" in core.normalize(attrs.get("studs")):
            query_parts.append("с шипами")
        query = " ".join(part for part in query_parts if part).strip() or title
        label = query
        return ProductGroup(key, query, product_type, label)

    if product_type == "tube":
        parts = [
            product_type,
            attrs.get("diameter") or "-",
            attrs.get("valve") or "-",
            attrs.get("reinforced") or "-",
        ]
        key = "|".join(parts)
        query_parts = ["камера"]
        if attrs.get("diameter"):
            query_parts.append(f"D{attrs['diameter']}")
        if attrs.get("valve"):
            query_parts.append(attrs["valve"])
        query = " ".join(query_parts)
        return ProductGroup(key, query, product_type, query)

    normalized_title = core.normalize(title)
    return ProductGroup(
        f"other|{normalized_title}",
        title,
        product_type,
        title,
    )


# ---------------------------------------------------------------------------
# Каталог: аудит и объединение источников
# ---------------------------------------------------------------------------


def catalog_rows(db: Database) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.conn.execute(
            """
            SELECT
                c.*,
                d.title_detail,
                d.specifications_json,
                d.detail_status,
                d.detail_collected_at
            FROM catalog_products c
            LEFT JOIN product_details d ON d.product_code=c.product_code
            ORDER BY c.page_number, c.position_on_page, c.product_code
            """
        ).fetchall()
    ]


def build_catalog_audit(db: Database, expected_count: int = 0) -> dict[str, Any]:
    rows = catalog_rows(db)
    codes = [core.clean_text(row.get("product_code")) for row in rows]
    codes = [code for code in codes if code]
    unique_codes = set(codes)

    page_counter: Counter[int] = Counter()
    brand_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    group_counter: Counter[str] = Counter()

    details_ok = 0
    details_error = 0
    details_missing = 0

    for row in rows:
        page = core.parse_int(row.get("page_number"))
        if page is not None:
            page_counter[page] += 1

        title = core.clean_text(row.get("title_detail") or row.get("title_catalog"))
        specifications = row.get("specifications_json") or "[]"
        brand = core.probable_brand(title) or "не определён"
        product_type = core.product_type(title, core.specs_map(specifications))
        group = build_group(title, specifications)
        brand_counter[brand] += 1
        type_counter[product_type] += 1
        group_counter[group.key] += 1

        status = core.clean_text(row.get("detail_status"))
        if status == "ok":
            details_ok += 1
        elif status:
            details_error += 1
        else:
            details_missing += 1

    page_sizes = list(page_counter.values())
    typical_page_size = (
        Counter(page_sizes).most_common(1)[0][0] if page_sizes else 0
    )
    max_page = max(page_counter, default=0)
    last_page_size = page_counter.get(max_page, 0)
    total = len(rows)
    unique_count = len(unique_codes)
    duplicate_count = max(0, len(codes) - unique_count)

    suspected_truncation = bool(
        max_page >= 20
        and typical_page_size > 0
        and last_page_size == typical_page_size
        and total >= max_page * typical_page_size
    )

    coverage_pct = (
        round(unique_count / expected_count * 100, 2)
        if expected_count > 0
        else None
    )

    details_coverage = round(details_ok / unique_count * 100, 2) if unique_count else 0

    return {
        "audited_at": now_iso(),
        "catalog_count": total,
        "unique_count": unique_count,
        "duplicate_count": duplicate_count,
        "expected_count": expected_count or None,
        "coverage_pct": coverage_pct,
        "max_page": max_page,
        "typical_page_size": typical_page_size,
        "last_page_size": last_page_size,
        "suspected_truncation": suspected_truncation,
        "details_ok": details_ok,
        "details_error": details_error,
        "details_missing": details_missing,
        "details_coverage_pct": details_coverage,
        "page_counts": [
            {"page": page, "products": count}
            for page, count in sorted(page_counter.items())
        ],
        "brands": [
            {"brand": brand, "products": count}
            for brand, count in brand_counter.most_common()
        ],
        "product_types": [
            {"type": product_type, "products": count}
            for product_type, count in type_counter.most_common()
        ],
        "technical_groups": len(group_counter),
        "largest_groups": [
            {"group_key": key, "products": count}
            for key, count in group_counter.most_common(30)
        ],
    }


def write_catalog_audit(
    db: Database,
    output: Path,
    expected_count: int,
) -> dict[str, Any]:
    audit = build_catalog_audit(db, expected_count)
    output.mkdir(parents=True, exist_ok=True)
    save_json(output / "catalog_audit.json", audit)
    save_csv(
        output / "catalog_pages.csv",
        audit["page_counts"],
        ["page", "products"],
    )
    save_csv(
        output / "catalog_brands.csv",
        audit["brands"],
        ["brand", "products"],
    )

    db.conn.execute(
        """
        INSERT INTO v9_catalog_audits(
            catalog_count, unique_count, duplicate_count, max_page,
            typical_page_size, last_page_size, details_ok, details_error,
            details_missing, expected_count, coverage_pct,
            suspected_truncation, audit_json, audited_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            audit["catalog_count"],
            audit["unique_count"],
            audit["duplicate_count"],
            audit["max_page"],
            audit["typical_page_size"],
            audit["last_page_size"],
            audit["details_ok"],
            audit["details_error"],
            audit["details_missing"],
            expected_count,
            audit["coverage_pct"],
            int(audit["suspected_truncation"]),
            json_dumps(audit),
            audit["audited_at"],
        ),
    )
    db.conn.commit()

    warning = (
        "Возможное ограничение выдачи: последняя страница заполнена полностью."
        if audit["suspected_truncation"]
        else "Явного признака обрезания по заполненной последней странице нет."
    )

    brand_rows = "".join(
        f"<tr><td>{html.escape(item['brand'])}</td><td>{item['products']}</td></tr>"
        for item in audit["brands"][:30]
    )
    page_rows = "".join(
        f"<tr><td>{item['page']}</td><td>{item['products']}</td></tr>"
        for item in audit["page_counts"]
    )

    document = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Аудит каталога Unityre</title>
<style>
body{{font-family:Arial,sans-serif;margin:24px;color:#202124;background:#f7f8fa}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.card,section{{background:white;border:1px solid #dfe3e8;border-radius:10px;padding:16px}}
.warn{{background:{'#fff0f0' if audit['suspected_truncation'] else '#eef8ef'};margin:16px 0}}
table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left}}
.columns{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}@media(max-width:800px){{.columns{{grid-template-columns:1fr}}}}
</style></head><body>
<h1>Аудит исходного каталога</h1>
<p>Сформировано: {html.escape(audit['audited_at'])}</p>
<div class="cards">
<div class="card"><b>Уникальных товаров</b><br>{audit['unique_count']}</div>
<div class="card"><b>Страниц</b><br>{audit['max_page']}</div>
<div class="card"><b>Последняя страница</b><br>{audit['last_page_size']} товаров</div>
<div class="card"><b>Карточки с характеристиками</b><br>{audit['details_ok']} ({audit['details_coverage_pct']}%)</div>
<div class="card"><b>Технических групп</b><br>{audit['technical_groups']}</div>
<div class="card"><b>Ожидаемое количество</b><br>{audit['expected_count'] or 'не задано'}</div>
</div>
<section class="warn"><b>Вывод:</b> {html.escape(warning)}</section>
<div class="columns">
<section><h2>Страницы</h2><table><tr><th>Страница</th><th>Товаров</th></tr>{page_rows}</table></section>
<section><h2>Бренды</h2><table><tr><th>Бренд</th><th>Товаров</th></tr>{brand_rows}</table></section>
</div></body></html>"""
    (output / "catalog_audit.html").write_text(document, encoding="utf-8")
    return audit


def _first_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    lowered = {str(key).casefold(): value for key, value in row.items()}
    for alias in aliases:
        value = lowered.get(alias.casefold())
        if value not in (None, ""):
            return value
    return None


def load_catalog_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]

    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("products", "cards", "data", "items", "results"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [dict(row) for row in nested if isinstance(row, dict)]
    raise RuntimeError(f"Не найден список товаров в {path}")


def canonical_catalog_row(row: dict[str, Any]) -> dict[str, Any] | None:
    code = core.clean_text(
        _first_value(
            row,
            (
                "product_code", "productCode", "masterSku", "master_sku",
                "sku", "id", "code",
            ),
        )
    )
    if not code:
        url = core.clean_text(_first_value(row, ("product_url", "url", "link")))
        match = re.search(r"-(\d{5,})/?(?:\?|$)", url)
        code = match.group(1) if match else ""
    if not code:
        return None

    title = core.clean_text(
        _first_value(row, ("title_catalog", "title", "name", "product_name"))
    )
    url = core.clean_text(_first_value(row, ("product_url", "url", "link")))
    price = core.parse_int(
        _first_value(row, ("catalog_price_kzt", "price_kzt", "price", "minPrice"))
    )
    return {
        "product_code": code,
        "page_number": core.parse_int(_first_value(row, ("page_number", "page"))),
        "position_on_page": core.parse_int(
            _first_value(row, ("position_on_page", "position", "index"))
        ),
        "title_catalog": title,
        "catalog_price_kzt": price,
        "catalog_rating": core.parse_float(
            _first_value(row, ("catalog_rating", "rating"))
        ),
        "catalog_reviews": core.parse_int(
            _first_value(row, ("catalog_reviews", "reviews", "reviews_count"))
        ),
        "product_url": url,
        "image_url": core.clean_text(_first_value(row, ("image_url", "image"))),
        "catalog_page_url": core.clean_text(
            _first_value(row, ("catalog_page_url", "source_url"))
        ),
        "collected_at": core.clean_text(
            _first_value(row, ("collected_at", "updated_at"))
        ) or now_iso(),
    }


def merge_catalog_command(args: argparse.Namespace) -> None:
    db = Database(Path(args.db))
    try:
        paths: list[Path] = []
        for pattern in args.input:
            matched = [Path(value) for value in glob.glob(pattern)] if any(x in pattern for x in "*?[") else [Path(pattern)]
            paths.extend(path for path in matched if path.exists())
        if not paths:
            raise RuntimeError("Не найдено ни одного входного CSV/JSON-файла")

        for path in paths:
            raw_rows = load_catalog_file(path)
            valid_rows = [canonical_catalog_row(row) for row in raw_rows]
            valid_rows = [row for row in valid_rows if row]
            inserted = 0
            updated = 0

            for row in valid_rows:
                exists = db.conn.execute(
                    "SELECT 1 FROM catalog_products WHERE product_code=?",
                    (row["product_code"],),
                ).fetchone()
                inserted += int(not bool(exists))
                updated += int(bool(exists))

                db.conn.execute(
                    """
                    INSERT INTO catalog_products(
                        product_code, page_number, position_on_page,
                        title_catalog, catalog_price_kzt, catalog_rating,
                        catalog_reviews, product_url, image_url,
                        catalog_page_url, collected_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(product_code) DO UPDATE SET
                        page_number=COALESCE(excluded.page_number, catalog_products.page_number),
                        position_on_page=COALESCE(excluded.position_on_page, catalog_products.position_on_page),
                        title_catalog=CASE WHEN excluded.title_catalog<>'' THEN excluded.title_catalog ELSE catalog_products.title_catalog END,
                        catalog_price_kzt=COALESCE(excluded.catalog_price_kzt, catalog_products.catalog_price_kzt),
                        catalog_rating=COALESCE(excluded.catalog_rating, catalog_products.catalog_rating),
                        catalog_reviews=COALESCE(excluded.catalog_reviews, catalog_products.catalog_reviews),
                        product_url=CASE WHEN excluded.product_url<>'' THEN excluded.product_url ELSE catalog_products.product_url END,
                        image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE catalog_products.image_url END,
                        catalog_page_url=CASE WHEN excluded.catalog_page_url<>'' THEN excluded.catalog_page_url ELSE catalog_products.catalog_page_url END,
                        collected_at=excluded.collected_at
                    """,
                    tuple(row[field] for field in (
                        "product_code", "page_number", "position_on_page",
                        "title_catalog", "catalog_price_kzt", "catalog_rating",
                        "catalog_reviews", "product_url", "image_url",
                        "catalog_page_url", "collected_at",
                    )),
                )

            db.conn.execute(
                """
                INSERT INTO v9_catalog_imports(
                    source_file, rows_read, rows_valid,
                    inserted_count, updated_count, imported_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    str(path.resolve()),
                    len(raw_rows),
                    len(valid_rows),
                    inserted,
                    updated,
                    now_iso(),
                ),
            )
            db.conn.commit()
            print(
                f"[Каталог] {path}: строк={len(raw_rows)}, "
                f"валидных={len(valid_rows)}, новых={inserted}, обновлено={updated}"
            )

        audit = write_catalog_audit(db, Path(args.output), args.expected_count)
        print(f"[Каталог] Уникальных товаров после объединения: {audit['unique_count']}")
    finally:
        db.conn.close()


# ---------------------------------------------------------------------------
# Сетевые операции с повторными попытками
# ---------------------------------------------------------------------------


async def search_with_retries(
    page: Any,
    query: str,
    pages: int,
    timeout_seconds: int,
    controller: core.BlockController,
    retries: int,
) -> tuple[list[dict[str, Any]], int]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            if attempt > 1:
                await page.goto(
                    core.HOME_URL,
                    wait_until="domcontentloaded",
                    timeout=timeout_seconds * 1000,
                )
                await core.close_city_modal(page)
                await asyncio.sleep(min(6.0, 1.2 * attempt))
            await core.submit_search(
                page,
                query,
                timeout_seconds * 1000,
                controller,
            )
            cards = await core.collect_search_cards(
                page,
                pages,
                timeout_seconds * 1000,
                controller,
            )
            if cards:
                return cards, attempt
            raise RuntimeError("поиск не вернул карточек")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "поиск завершился ошибкой"))


async def capture_with_retries(
    page: Any,
    url: str,
    timeout_seconds: int,
    city_id: str,
    controller: core.BlockController,
    retries: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            detail, offers = await core.capture_offers_response(
                page,
                url,
                timeout_seconds * 1000,
                city_id,
                controller,
            )
            title = core.clean_text(detail.get("candidate_title_detail"))
            specs = core.clean_specs(detail.get("specifications"))
            if title or specs or offers:
                return detail, offers, attempt
            raise RuntimeError("карточка не вернула название, характеристики и предложения")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(min(5.0, 1.0 * attempt))
    raise RuntimeError(str(last_error or "карточка завершилась ошибкой"))


# ---------------------------------------------------------------------------
# Поиск аналогов с кэшем
# ---------------------------------------------------------------------------


async def discover_command(args: argparse.Namespace) -> None:
    await core.ensure_playwright()
    workers = max(1, min(args.workers, 4))
    db = Database(Path(args.db))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    codes = [core.clean_text(value) for value in args.codes.split(",") if core.clean_text(value)]
    jobs = db.discovery_jobs(
        limit=args.limit,
        refresh=args.refresh,
        only_errors=args.only_errors,
        codes=codes,
    )
    if not jobs:
        print("[V9] Нет товаров для поиска аналогов.")
        build_dashboard(db, output, args.seller_name, workers)
        db.conn.close()
        return

    print(
        f"[V9] Товаров: {len(jobs)}; воркеров: {workers}; "
        f"кэш поиска: {args.search_cache_days} дн.; "
        f"кэш характеристик: {args.detail_cache_days} дн."
    )

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    for item in jobs:
        queue.put_nowait(item)
    for _ in range(workers):
        queue.put_nowait(None)

    controller = core.BlockController()
    db_lock = asyncio.Lock()
    tasks: list[asyncio.Task[Any]] = []
    progress = {"done": 0, "ok": 0, "partial": 0, "error": 0}
    debug_dir = output / "debug"

    async with core.async_playwright() as playwright:
        context = await core.launch_context(
            playwright,
            Path(args.profile),
            args.headless,
        )
        try:
            async def worker(worker_id: int) -> None:
                search_page = await context.new_page()
                detail_page = await context.new_page()
                await search_page.route("**/*", core.light_route)
                await detail_page.route("**/*", core.light_route)
                try:
                    await search_page.goto(
                        core.HOME_URL,
                        wait_until="domcontentloaded",
                        timeout=args.timeout * 1000,
                    )
                    await core.close_city_modal(search_page)
                    await controller.handle(search_page)

                    while True:
                        item = await queue.get()
                        if item is None:
                            queue.task_done()
                            break

                        started_monotonic = time.monotonic()
                        started_at = now_iso()
                        source_code = core.clean_text(item.get("product_code"))
                        source_title = core.clean_text(
                            item.get("title_detail") or item.get("title_catalog")
                        )
                        source_specs = item.get("specifications_json") or "[]"
                        source_url = core.clean_text(item.get("product_url"))
                        source_price = core.parse_int(item.get("catalog_price_kzt"))
                        group = build_group(source_title, source_specs)

                        cache_hits = 0
                        cache_misses = 0
                        attempts_total = 0
                        candidates_found = 0
                        validated = 0
                        accepted = 0
                        reviews = 0
                        exact_status = "not_started"
                        errors: list[str] = []

                        async with db_lock:
                            db.begin_run(source_code, group.query)

                        # 1. Точная карточка: прямой URL без поисковой выдачи.
                        try:
                            detail, exact_offers, attempts = await capture_with_retries(
                                detail_page,
                                source_url,
                                args.timeout,
                                args.city_id,
                                controller,
                                args.retries,
                            )
                            attempts_total += attempts
                            exact_title = core.clean_text(
                                detail.get("candidate_title_detail") or source_title
                            )
                            exact_specs = core.clean_specs(
                                detail.get("specifications") or source_specs
                            )
                            exact_prices = [
                                core.parse_float(offer.get("price"))
                                for offer in exact_offers
                                if isinstance(offer, dict)
                            ]
                            exact_prices = [value for value in exact_prices if value is not None]
                            exact_min_price = min(exact_prices) if exact_prices else source_price

                            # V9.1: свежая база может не иметь product_details.
                            # После открытия точной карточки используем реальные
                            # характеристики как источник для технической группы
                            # и дальнейшего сопоставления кандидатов.
                            source_title = exact_title or source_title
                            source_specs = json_dumps(exact_specs)
                            group = build_group(source_title, source_specs)
                            own_offer_price = next((
                                core.parse_float(offer.get("price"))
                                for offer in exact_offers
                                if isinstance(offer, dict)
                                and core.normalize(offer.get("merchantId")) == core.normalize("Unityre")
                            ), None)
                            if own_offer_price is not None:
                                source_price = core.parse_int(own_offer_price)

                            exact_candidate = {
                                "candidate_product_code": source_code,
                                "search_page": 0,
                                "position": 0,
                                "candidate_title": exact_title,
                                "candidate_url": source_url,
                                "candidate_price_kzt": exact_min_price,
                                "candidate_rating": item.get("catalog_rating"),
                                "candidate_reviews": item.get("catalog_reviews"),
                                "fast_score": 100,
                                "fast_decision": "accepted",
                                "fast_reason": "прямая карточка исходного товара",
                                "candidate_title_detail": exact_title,
                                "candidate_specs": exact_specs,
                                "detail_score": 100,
                                "final_decision": "accepted",
                                "detail_reason": "совпадает код исходного товара",
                            }
                            async with db_lock:
                                db.save_candidate(source_code, exact_candidate)
                                db.conn.execute(
                                    "DELETE FROM market_seller_offers WHERE source_product_code=? AND candidate_product_code=?",
                                    (source_code, source_code),
                                )
                                db.save_offers(source_code, source_code, exact_offers)
                                db.save_product_cache(
                                    source_code,
                                    exact_title,
                                    source_url,
                                    exact_specs,
                                    "exact_page",
                                    group.key,
                                )
                                db.conn.execute(
                                    """
                                    INSERT INTO product_details(
                                        product_code, product_url, title_detail, price_kzt,
                                        product_rating, product_reviews, specifications_json,
                                        detail_status, detail_error, detail_collected_at
                                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                                    ON CONFLICT(product_code) DO UPDATE SET
                                        product_url=excluded.product_url,
                                        title_detail=excluded.title_detail,
                                        price_kzt=excluded.price_kzt,
                                        product_rating=excluded.product_rating,
                                        product_reviews=excluded.product_reviews,
                                        specifications_json=excluded.specifications_json,
                                        detail_status='ok',
                                        detail_error=NULL,
                                        detail_collected_at=excluded.detail_collected_at
                                    """,
                                    (
                                        source_code, source_url, exact_title, source_price,
                                        core.parse_float(item.get("catalog_rating")),
                                        core.parse_int(item.get("catalog_reviews")),
                                        json_dumps(exact_specs), "ok", None, now_iso(),
                                    ),
                                )
                                db.begin_run(source_code, group.query)
                                db.conn.commit()
                            exact_status = "ok" if exact_offers else "no_offers_captured"
                            accepted += 1
                        except Exception as exc:
                            exact_status = "error"
                            errors.append(f"точная карточка: {exc}")
                            await core.save_debug(
                                detail_page,
                                debug_dir,
                                source_code,
                                "exact",
                                str(exc),
                            )

                        # 2. Поисковая выдача технической группы — один раз на группу.
                        cards: list[dict[str, Any]] = []
                        try:
                            async with db_lock:
                                cards = None if args.refresh_search else db.get_group_cache(
                                    group.key,
                                    args.search_cache_days,
                                    args.search_pages,
                                )
                            if cards is not None:
                                cache_hits += 1
                            else:
                                cache_misses += 1
                                cards, attempts = await search_with_retries(
                                    search_page,
                                    group.query,
                                    args.search_pages,
                                    args.timeout,
                                    controller,
                                    args.retries,
                                )
                                attempts_total += attempts
                                async with db_lock:
                                    db.save_group_cache(
                                        group.key,
                                        group.query,
                                        cards,
                                        args.search_pages,
                                    )
                            candidates_found = len(cards)
                        except Exception as exc:
                            errors.append(f"поиск группы: {exc}")
                            await core.save_debug(
                                search_page,
                                debug_dir,
                                source_code,
                                "group_search",
                                str(exc),
                            )
                            cards = []

                        # 3. Быстрый отбор и общий кэш характеристик кандидатов.
                        selected: list[dict[str, Any]] = []
                        if cards:
                            selected = core.choose_candidates(
                                source_code,
                                source_title,
                                source_specs,
                                cards,
                                args.validate_top + 1,
                            )
                            selected = [
                                row for row in selected
                                if core.clean_text(row.get("candidate_product_code")) != source_code
                            ][: args.validate_top]
                            selected_codes = {
                                core.clean_text(row.get("candidate_product_code"))
                                for row in selected
                            }

                            for card in cards:
                                candidate_code = core.clean_text(card.get("candidate_product_code"))
                                if not candidate_code or candidate_code == source_code:
                                    continue
                                match = core.fast_match(
                                    source_code,
                                    source_title,
                                    source_specs,
                                    candidate_code,
                                    core.clean_text(card.get("candidate_title")),
                                )
                                candidate = dict(card)
                                candidate["fast_score"] = round(match.score, 2)
                                candidate["fast_decision"] = match.decision
                                candidate["fast_reason"] = match.reason
                                candidate["candidate_specs"] = []
                                candidate["final_decision"] = (
                                    "pending" if candidate_code in selected_codes else "not_validated"
                                )
                                async with db_lock:
                                    db.save_candidate(source_code, candidate)
                                    db.conn.commit()

                        for candidate in selected:
                            candidate_code = core.clean_text(candidate.get("candidate_product_code"))
                            candidate_url = core.clean_text(candidate.get("candidate_url"))
                            if not candidate_code or not candidate_url:
                                continue

                            detail: dict[str, Any]
                            offers: list[dict[str, Any]] = []
                            fetched_from_network = False
                            try:
                                async with db_lock:
                                    cached = None if args.refresh_details else db.get_product_cache(
                                        candidate_code,
                                        args.detail_cache_days,
                                    )
                                if cached:
                                    cache_hits += 1
                                    detail = {
                                        "candidate_title_detail": cached.get("title"),
                                        "specifications": cached.get("specifications", []),
                                    }
                                else:
                                    cache_misses += 1
                                    fetched_from_network = True
                                    detail, offers, attempts = await capture_with_retries(
                                        detail_page,
                                        candidate_url,
                                        args.timeout,
                                        args.city_id,
                                        controller,
                                        args.retries,
                                    )
                                    attempts_total += attempts
                                    async with db_lock:
                                        db.save_product_cache(
                                            candidate_code,
                                            core.clean_text(
                                                detail.get("candidate_title_detail")
                                                or candidate.get("candidate_title")
                                            ),
                                            candidate_url,
                                            detail.get("specifications") or [],
                                            "candidate_page",
                                        )

                                candidate_title = core.clean_text(
                                    detail.get("candidate_title_detail")
                                    or candidate.get("candidate_title")
                                )
                                candidate_specs = core.clean_specs(
                                    detail.get("specifications") or []
                                )
                                result = core.detail_match(
                                    source_title,
                                    source_specs,
                                    candidate_title,
                                    candidate_specs,
                                    float(candidate.get("fast_score") or 0),
                                )
                                candidate["candidate_title_detail"] = candidate_title
                                candidate["candidate_specs"] = candidate_specs
                                candidate["detail_score"] = round(result.score, 2)
                                candidate["final_decision"] = result.decision
                                candidate["detail_reason"] = result.reason
                                validated += 1
                                accepted += int(result.decision == "accepted")
                                reviews += int(result.decision == "review")

                                async with db_lock:
                                    db.save_candidate(source_code, candidate)
                                    if fetched_from_network and result.decision in {"accepted", "review"}:
                                        db.conn.execute(
                                            "DELETE FROM market_seller_offers WHERE source_product_code=? AND candidate_product_code=?",
                                            (source_code, candidate_code),
                                        )
                                        db.save_offers(source_code, candidate_code, offers)
                                    db.conn.commit()
                            except Exception as exc:
                                errors.append(f"кандидат {candidate_code}: {exc}")
                                async with db_lock:
                                    db.add_error(source_code, "v9_candidate", str(exc))

                            await asyncio.sleep(
                                random.uniform(
                                    max(0.15, args.min_delay / 3),
                                    max(0.35, args.max_delay / 3),
                                )
                            )

                        if exact_status == "ok" and cards:
                            status = "ok"
                        elif exact_status != "error" or cards:
                            status = "partial"
                        else:
                            status = "error"

                        error_text = " | ".join(errors) if errors else None
                        duration = time.monotonic() - started_monotonic

                        async with db_lock:
                            # Для совместимости с отчетами V8 статус partial
                            # считается обработанным, но V9 хранит его отдельно.
                            db.finish_run(
                                source_code,
                                "ok" if status in {"ok", "partial"} else "error",
                                candidates_found,
                                validated,
                                accepted,
                                reviews,
                                error_text,
                            )
                            db.save_discovery_state(
                                source_code,
                                group.key,
                                group.query,
                                status,
                                exact_status,
                                candidates_found,
                                validated,
                                accepted,
                                reviews,
                                cache_hits,
                                cache_misses,
                                duration,
                                error_text,
                            )
                            db.save_metric(
                                "discover",
                                source_code,
                                status,
                                duration,
                                cache_hits,
                                cache_misses,
                                attempts_total,
                                error_text,
                                started_at,
                            )
                            progress["done"] += 1
                            progress[status] += 1
                            recent = db.conn.execute(
                                """
                                SELECT AVG(duration_seconds)
                                FROM (
                                    SELECT duration_seconds
                                    FROM v9_run_metrics
                                    WHERE command='discover'
                                      AND status IN ('ok','partial')
                                    ORDER BY id DESC LIMIT 50
                                )
                                """
                            ).fetchone()[0]
                            remaining = len(jobs) - progress["done"]
                            eta = (float(recent or duration) * remaining / workers)
                            print(
                                f"[W{worker_id}] {progress['done']}/{len(jobs)} "
                                f"{source_code} — {status}; exact={exact_status}; "
                                f"cards={candidates_found}; checked={validated}; "
                                f"accepted={accepted}; review={reviews}; "
                                f"cache={cache_hits}/{cache_misses}; "
                                f"{duration:.1f}s; ETA {format_seconds(eta)}"
                            )

                        await asyncio.sleep(
                            random.uniform(args.min_delay, args.max_delay)
                        )
                        queue.task_done()
                finally:
                    await search_page.close()
                    await detail_page.close()

            tasks = [asyncio.create_task(worker(index + 1)) for index in range(workers)]
            await queue.join()
            await asyncio.gather(*tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await context.close()

    if not getattr(args, "no_dashboard", False):
        build_dashboard(db, output, args.seller_name, workers)
    db.conn.close()


# ---------------------------------------------------------------------------
# Обновление цен без повторного поиска
# ---------------------------------------------------------------------------


def price_targets(
    db: Database,
    include_analogs: bool,
    limit: int,
    codes: list[str],
) -> list[dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}

    catalog = db.conn.execute(
        "SELECT product_code, product_url FROM catalog_products"
    ).fetchall()
    for row in catalog:
        code = core.clean_text(row["product_code"])
        if not code:
            continue
        targets[code] = {
            "candidate_product_code": code,
            "candidate_url": core.clean_text(row["product_url"]),
            "references": {code},
            "exact_sources": {code},
        }

    if include_analogs:
        rows = db.conn.execute(
            """
            SELECT source_product_code, candidate_product_code, candidate_url
            FROM market_candidates
            WHERE final_decision IN ('accepted','review')
            """
        ).fetchall()
        for row in rows:
            candidate_code = core.clean_text(row["candidate_product_code"])
            source_code = core.clean_text(row["source_product_code"])
            if not candidate_code:
                continue
            target = targets.setdefault(
                candidate_code,
                {
                    "candidate_product_code": candidate_code,
                    "candidate_url": core.clean_text(row["candidate_url"]),
                    "references": set(),
                    "exact_sources": set(),
                },
            )
            target["references"].add(source_code)
            if source_code == candidate_code:
                target["exact_sources"].add(source_code)

    result = list(targets.values())
    if codes:
        code_set = set(codes)
        result = [row for row in result if row["candidate_product_code"] in code_set]
    result.sort(key=lambda row: row["candidate_product_code"])
    return result[:limit] if limit > 0 else result


async def refresh_prices_command(args: argparse.Namespace) -> None:
    await core.ensure_playwright()
    workers = max(1, min(args.workers, 4))
    db = Database(Path(args.db))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    codes = [core.clean_text(value) for value in args.codes.split(",") if core.clean_text(value)]
    targets = price_targets(db, args.include_analogs, args.limit, codes)
    if not targets:
        print("[Цены] Нет карточек для обновления.")
        db.conn.close()
        return

    print(
        f"[Цены] Уникальных карточек: {len(targets)}; воркеров: {workers}; "
        f"аналоги={'да' if args.include_analogs else 'нет'}"
    )

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    for target in targets:
        queue.put_nowait(target)
    for _ in range(workers):
        queue.put_nowait(None)

    controller = core.BlockController()
    db_lock = asyncio.Lock()
    progress = {"done": 0, "ok": 0, "error": 0}
    tasks: list[asyncio.Task[Any]] = []
    debug_dir = output / "debug_prices"

    async with core.async_playwright() as playwright:
        context = await core.launch_context(playwright, Path(args.profile), args.headless)
        try:
            async def worker(worker_id: int) -> None:
                page = await context.new_page()
                await page.route("**/*", core.light_route)
                try:
                    while True:
                        target = await queue.get()
                        if target is None:
                            queue.task_done()
                            break

                        started = time.monotonic()
                        started_at = now_iso()
                        code = target["candidate_product_code"]
                        url = target["candidate_url"]
                        status = "error"
                        error: str | None = None
                        attempts = 0
                        offers: list[dict[str, Any]] = []

                        try:
                            detail, offers, attempts = await capture_with_retries(
                                page,
                                url,
                                args.timeout,
                                args.city_id,
                                controller,
                                args.retries,
                            )
                            prices = [
                                core.parse_float(offer.get("price"))
                                for offer in offers
                                if isinstance(offer, dict)
                            ]
                            prices = [price for price in prices if price is not None]
                            min_price = min(prices) if prices else None
                            title = core.clean_text(detail.get("candidate_title_detail"))
                            specs = core.clean_specs(detail.get("specifications"))

                            async with db_lock:
                                db.save_product_cache(
                                    code,
                                    title,
                                    url,
                                    specs,
                                    "price_refresh",
                                )

                                # Одна карточка загружается один раз, после чего
                                # результат распространяется на все исходные товары,
                                # где она является точной или аналоговой.
                                references = set(target["references"])
                                references.update(target["exact_sources"])
                                for source_code in references:
                                    db.conn.execute(
                                        "DELETE FROM market_seller_offers WHERE source_product_code=? AND candidate_product_code=?",
                                        (source_code, code),
                                    )
                                    db.save_offers(source_code, code, offers)

                                if min_price is not None:
                                    db.conn.execute(
                                        """
                                        UPDATE market_candidates
                                        SET candidate_price_kzt=?, checked_at=?
                                        WHERE candidate_product_code=?
                                          AND final_decision IN ('accepted','review')
                                        """,
                                        (min_price, now_iso(), code),
                                    )

                                captured_at = now_iso()
                                for source_code in references or {code}:
                                    for offer in offers:
                                        price = core.parse_float(offer.get("price"))
                                        if price is None:
                                            continue
                                        db.conn.execute(
                                            """
                                            INSERT INTO v9_price_snapshots(
                                                source_product_code,
                                                candidate_product_code,
                                                merchant_id,
                                                merchant_name,
                                                merchant_sku,
                                                price_kzt,
                                                merchant_rating,
                                                merchant_reviews,
                                                captured_at
                                            ) VALUES(?,?,?,?,?,?,?,?,?)
                                            """,
                                            (
                                                source_code,
                                                code,
                                                core.clean_text(offer.get("merchantId")),
                                                core.clean_text(offer.get("merchantName")),
                                                core.clean_text(offer.get("merchantSku")),
                                                price,
                                                core.parse_float(offer.get("merchantRating")),
                                                core.parse_int(offer.get("merchantReviewsQuantity")),
                                                captured_at,
                                            ),
                                        )

                                duration = time.monotonic() - started
                                db.conn.execute(
                                    """
                                    INSERT INTO v9_price_state VALUES(?,?,?,?,?,?,?)
                                    ON CONFLICT(candidate_product_code) DO UPDATE SET
                                        status=excluded.status,
                                        offers_count=excluded.offers_count,
                                        min_price_kzt=excluded.min_price_kzt,
                                        duration_seconds=excluded.duration_seconds,
                                        error=excluded.error,
                                        updated_at=excluded.updated_at
                                    """,
                                    (code, "ok", len(offers), min_price, duration, None, now_iso()),
                                )
                                db.save_metric(
                                    "refresh_prices",
                                    code,
                                    "ok",
                                    duration,
                                    attempts=attempts,
                                    started_at=started_at,
                                )
                                db.conn.commit()
                            status = "ok"
                        except Exception as exc:
                            error = str(exc)
                            duration = time.monotonic() - started
                            await core.save_debug(page, debug_dir, code, "price", error)
                            async with db_lock:
                                db.conn.execute(
                                    """
                                    INSERT INTO v9_price_state VALUES(?,?,?,?,?,?,?)
                                    ON CONFLICT(candidate_product_code) DO UPDATE SET
                                        status=excluded.status,
                                        offers_count=excluded.offers_count,
                                        min_price_kzt=excluded.min_price_kzt,
                                        duration_seconds=excluded.duration_seconds,
                                        error=excluded.error,
                                        updated_at=excluded.updated_at
                                    """,
                                    (code, "error", 0, None, duration, error, now_iso()),
                                )
                                db.save_metric(
                                    "refresh_prices",
                                    code,
                                    "error",
                                    duration,
                                    attempts=attempts,
                                    error=error,
                                    started_at=started_at,
                                )
                                db.conn.commit()

                        async with db_lock:
                            progress["done"] += 1
                            progress[status] += 1
                            avg = db.conn.execute(
                                """
                                SELECT AVG(duration_seconds) FROM (
                                    SELECT duration_seconds FROM v9_run_metrics
                                    WHERE command='refresh_prices' AND status='ok'
                                    ORDER BY id DESC LIMIT 100
                                )
                                """
                            ).fetchone()[0]
                            remaining = len(targets) - progress["done"]
                            eta = float(avg or duration) * remaining / workers
                            print(
                                f"[P{worker_id}] {progress['done']}/{len(targets)} "
                                f"{code} — {status}; offers={len(offers)}; "
                                f"{duration:.1f}s; ETA {format_seconds(eta)}"
                                + (f"; {error}" if error else "")
                            )

                        await asyncio.sleep(random.uniform(args.min_delay, args.max_delay))
                        queue.task_done()
                finally:
                    await page.close()

            tasks = [asyncio.create_task(worker(index + 1)) for index in range(workers)]
            await queue.join()
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await context.close()

    if not getattr(args, "no_dashboard", False):
        build_dashboard(db, output, args.seller_name, workers)
    db.conn.close()


# ---------------------------------------------------------------------------
# Отчёт и панель управления
# ---------------------------------------------------------------------------


def _table_exists(db: Database, table: str) -> bool:
    return bool(
        db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def status_snapshot(db: Database, assumed_workers: int = 2) -> dict[str, Any]:
    total = db.conn.execute("SELECT COUNT(*) FROM catalog_products").fetchone()[0]
    unique_count = db.conn.execute(
        "SELECT COUNT(DISTINCT product_code) FROM catalog_products"
    ).fetchone()[0]
    details_ok = db.conn.execute(
        "SELECT COUNT(*) FROM product_details WHERE detail_status='ok'"
    ).fetchone()[0]
    completed = db.conn.execute(
        "SELECT COUNT(*) FROM v9_discovery_state WHERE status IN ('ok','partial')"
    ).fetchone()[0]
    success = db.conn.execute(
        "SELECT COUNT(*) FROM v9_discovery_state WHERE status='ok'"
    ).fetchone()[0]
    partial = db.conn.execute(
        "SELECT COUNT(*) FROM v9_discovery_state WHERE status='partial'"
    ).fetchone()[0]
    errors = db.conn.execute(
        "SELECT COUNT(*) FROM v9_discovery_state WHERE status='error'"
    ).fetchone()[0]
    review = db.conn.execute(
        "SELECT COUNT(*) FROM market_candidates WHERE final_decision='review'"
    ).fetchone()[0]
    group_cache = db.conn.execute(
        "SELECT COUNT(*) FROM v9_group_search_cache"
    ).fetchone()[0]
    product_cache = db.conn.execute(
        "SELECT COUNT(*) FROM v9_product_cache"
    ).fetchone()[0]
    price_ok = db.conn.execute(
        "SELECT COUNT(*) FROM v9_price_state WHERE status='ok'"
    ).fetchone()[0]

    durations = [
        float(row[0])
        for row in db.conn.execute(
            """
            SELECT duration_seconds FROM v9_run_metrics
            WHERE command='discover' AND status IN ('ok','partial')
              AND duration_seconds IS NOT NULL
            ORDER BY id DESC LIMIT 100
            """
        ).fetchall()
    ]
    avg_duration = statistics.mean(durations) if durations else None
    median_duration = statistics.median(durations) if durations else None
    remaining = max(0, unique_count - completed)
    eta_seconds = (
        avg_duration * remaining / max(1, assumed_workers)
        if avg_duration is not None
        else None
    )

    last_discovery = db.conn.execute(
        "SELECT MAX(updated_at) FROM v9_discovery_state"
    ).fetchone()[0]
    last_price = db.conn.execute(
        "SELECT MAX(updated_at) FROM v9_price_state"
    ).fetchone()[0]

    return {
        "generated_at": now_iso(),
        "catalog_rows": total,
        "catalog_unique": unique_count,
        "details_ok": details_ok,
        "details_coverage_pct": round(details_ok / unique_count * 100, 2) if unique_count else 0,
        "discovery_completed": completed,
        "discovery_success": success,
        "discovery_partial": partial,
        "discovery_errors": errors,
        "discovery_remaining": remaining,
        "discovery_progress_pct": round(completed / unique_count * 100, 2) if unique_count else 0,
        "review_candidates": review,
        "group_cache_entries": group_cache,
        "product_cache_entries": product_cache,
        "price_cards_updated": price_ok,
        "average_seconds_per_item": round(avg_duration, 2) if avg_duration is not None else None,
        "median_seconds_per_item": round(median_duration, 2) if median_duration is not None else None,
        "eta_seconds": round(eta_seconds, 2) if eta_seconds is not None else None,
        "eta_text": format_seconds(eta_seconds),
        "last_discovery_at": last_discovery,
        "last_price_update_at": last_price,
        "assumed_workers": assumed_workers,
    }


def enriched_comparison_rows(
    db: Database,
    seller_name: str,
    product_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows = core.report_rows(db, seller_name, product_codes)
    requested_codes = [core.clean_text(value) for value in (product_codes or []) if core.clean_text(value)]
    code_filter = ""
    params: list[Any] = []
    if product_codes is not None:
        if not requested_codes:
            return []
        placeholders = ",".join("?" for _ in requested_codes)
        code_filter = f" WHERE source_product_code IN ({placeholders})"
        params = requested_codes
    state_rows = {
        row["source_product_code"]: dict(row)
        for row in db.conn.execute(f"SELECT * FROM v9_discovery_state{code_filter}", params).fetchall()
    }
    product_filter = code_filter.replace("source_product_code", "candidate_product_code")
    price_times = {
        row["candidate_product_code"]: row["updated_at"]
        for row in db.conn.execute(
            f"SELECT candidate_product_code, updated_at FROM v9_price_state{product_filter}",
            params,
        ).fetchall()
    }
    detail_filter = code_filter.replace("source_product_code", "product_code")
    details = {
        row["product_code"]: dict(row)
        for row in db.conn.execute(
            f"SELECT product_code, specifications_json FROM product_details{detail_filter}",
            params,
        ).fetchall()
    }

    result: list[dict[str, Any]] = []
    for row in rows:
        code = core.clean_text(row.get("product_code"))
        state = state_rows.get(code, {})
        title = core.clean_text(row.get("title"))
        specs = details.get(code, {}).get("specifications_json") or "[]"
        attrs = core.extract_attributes(title, specs)
        brand = core.probable_brand(title)
        row.update(
            {
                "v9_status": state.get("status") or (
                    "legacy" if row.get("scan_status") else "not_scanned"
                ),
                "v9_exact_status": state.get("exact_status"),
                "v9_group_key": state.get("group_key"),
                "v9_query_text": state.get("query_text"),
                "v9_cache_hits": state.get("cache_hits"),
                "v9_cache_misses": state.get("cache_misses"),
                "v9_duration_seconds": state.get("duration_seconds"),
                "v9_updated_at": state.get("updated_at"),
                "last_price_update_at": price_times.get(code),
                "brand": brand,
                "product_type": attrs.get("type"),
                "size": "/".join(
                    value for value in (
                        attrs.get("width"), attrs.get("height")
                    ) if value
                ) + (f" R{attrs.get('diameter')}" if attrs.get("diameter") else ""),
            }
        )
        result.append(row)
    return result


def build_dashboard(
    db: Database,
    output: Path,
    seller_name: str,
    assumed_workers: int = 2,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    # Сохраняем совместимые CSV V8.2, но заменяем HTML более удобной панелью.
    core.export_reports(db, output, seller_name)
    snapshot = status_snapshot(db, assumed_workers)
    rows = enriched_comparison_rows(db, seller_name)

    fields = [
        "product_code", "title", "brand", "product_type", "size",
        "own_effective_price_kzt", "exact_competitor_min_price_kzt",
        "exact_competitor_name", "exact_difference_kzt", "exact_difference_pct",
        "analog_min_price_kzt", "cheapest_analog_title",
        "analog_difference_kzt", "analog_difference_pct",
        "review_candidate_title", "review_min_price_kzt", "review_candidate_score",
        "price_status", "v9_status", "v9_exact_status", "v9_cache_hits",
        "v9_cache_misses", "v9_duration_seconds", "v9_updated_at",
        "last_price_update_at", "product_url",
    ]
    save_csv(output / "price_comparison_v9.csv", rows, fields)
    save_json(output / "price_comparison_v9.json", rows)
    save_json(output / "status.json", snapshot)

    status_counts = Counter(core.clean_text(row.get("price_status") or "NOT_SCANNED") for row in rows)
    successful_rows = [
        row for row in rows
        if core.clean_text(row.get("scan_status"))
        or core.clean_text(row.get("v9_status")) in {"ok", "partial", "legacy"}
    ]

    options_status = "".join(
        f"<option value='{html.escape(status)}'>{html.escape(status)} ({count})</option>"
        for status, count in sorted(status_counts.items())
    )
    brands = sorted({core.clean_text(row.get("brand")) for row in rows if core.clean_text(row.get("brand"))})
    options_brand = "".join(
        f"<option value='{html.escape(brand)}'>{html.escape(brand)}</option>"
        for brand in brands
    )
    types = sorted({core.clean_text(row.get("product_type")) for row in rows if core.clean_text(row.get("product_type"))})
    options_type = "".join(
        f"<option value='{html.escape(value)}'>{html.escape(value)}</option>"
        for value in types
    )

    def money(value: Any) -> str:
        number = core.parse_float(value)
        return f"{number:,.0f}".replace(",", " ") if number is not None else ""

    table_rows = []
    for row in rows:
        code = core.clean_text(row.get("product_code"))
        title = core.clean_text(row.get("title"))
        status = core.clean_text(row.get("price_status") or "NOT_SCANNED")
        v9_status = core.clean_text(row.get("v9_status"))
        brand = core.clean_text(row.get("brand"))
        product_type = core.clean_text(row.get("product_type"))
        product_url = core.clean_text(row.get("product_url"))
        link_title = (
            f"<a href='{html.escape(product_url)}' target='_blank'>{html.escape(title)}</a>"
            if product_url else html.escape(title)
        )
        table_rows.append(
            "<tr "
            f"data-status='{html.escape(status)}' "
            f"data-v9='{html.escape(v9_status)}' "
            f"data-brand='{html.escape(brand)}' "
            f"data-type='{html.escape(product_type)}' "
            f"data-search='{html.escape((code + ' ' + title + ' ' + brand).casefold())}'>"
            f"<td>{html.escape(code)}</td>"
            f"<td>{link_title}<div class='muted'>{html.escape(core.clean_text(row.get('size')))}</div></td>"
            f"<td>{html.escape(brand)}</td>"
            f"<td>{html.escape(product_type)}</td>"
            f"<td class='money'>{money(row.get('own_effective_price_kzt'))}</td>"
            f"<td class='money'>{money(row.get('exact_competitor_min_price_kzt'))}</td>"
            f"<td>{html.escape(core.clean_text(row.get('exact_competitor_name')))}</td>"
            f"<td>{html.escape(core.clean_text(row.get('exact_difference_pct')))}</td>"
            f"<td class='money'>{money(row.get('analog_min_price_kzt'))}</td>"
            f"<td>{html.escape(core.clean_text(row.get('cheapest_analog_title')))}</td>"
            f"<td>{html.escape(core.clean_text(row.get('analog_difference_pct')))}</td>"
            f"<td>{html.escape(core.clean_text(row.get('review_candidate_title')))}</td>"
            f"<td><span class='badge {html.escape(status)}'>{html.escape(status)}</span>"
            f"<div class='muted'>{html.escape(v9_status)}</div></td>"
            f"<td>{html.escape(core.clean_text(row.get('v9_duration_seconds')))}</td>"
            f"<td>{html.escape(core.clean_text(row.get('v9_updated_at') or row.get('finished_at')))}</td>"
            "</tr>"
        )

    progress = snapshot["discovery_progress_pct"]
    dashboard = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kaspi Market V9</title>
<style>
:root{{--bg:#f5f7fa;--panel:#fff;--line:#dfe4ea;--text:#1f2937;--muted:#6b7280;--accent:#2563eb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:Arial,sans-serif}}
header{{background:#111827;color:white;padding:22px 28px}}header h1{{margin:0 0 6px;font-size:24px}}header p{{margin:0;color:#cbd5e1}}
main{{padding:22px;max-width:1800px;margin:auto}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px}}
.card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px}}.number{{font-size:26px;font-weight:700;margin-top:7px}}
.muted{{color:var(--muted);font-size:12px;margin-top:4px}}.progress{{height:14px;background:#e5e7eb;border-radius:10px;overflow:hidden;margin-top:10px}}.progress span{{display:block;height:100%;background:var(--accent);width:{progress}%}}
.panel{{margin-top:14px}}.filters{{display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:10px}}input,select,button{{border:1px solid #cfd6de;border-radius:8px;padding:9px;background:white}}button{{cursor:pointer}}
.table-wrap{{overflow:auto;max-height:70vh}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border-bottom:1px solid #e5e7eb;padding:8px;vertical-align:top;text-align:left}}th{{background:#f3f4f6;position:sticky;top:0;z-index:2;white-space:nowrap}}td.money{{white-space:nowrap;text-align:right}}a{{color:#1d4ed8;text-decoration:none}}a:hover{{text-decoration:underline}}
.badge{{display:inline-block;padding:4px 7px;border-radius:999px;background:#e5e7eb;font-size:11px;font-weight:700}}.EXACT_WIN,.EXACT_TIE,.ANALOG_WIN,.ANALOG_TIE{{background:#d8f3dc}}.EXACT_LOSE,.ANALOG_LOSE,.SCAN_ERROR{{background:#ffd9d9}}.EXACT_NEAR,.ANALOG_NEAR{{background:#fff1b8}}.ONLY_OWN_OFFER,.NOT_SCANNED,.NO_ACCEPTED_MATCH{{background:#e5e7eb}}
.tabs{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}}.tabs button.active{{background:#111827;color:white}}
@media(max-width:900px){{.filters{{grid-template-columns:1fr 1fr}}}}
</style></head><body>
<header><h1>Kaspi Market V9</h1><p>Инкрементальный мониторинг Unityre · {html.escape(snapshot['generated_at'])}</p></header>
<main>
<div class="grid">
<div class="card"><b>Каталог</b><div class="number">{snapshot['catalog_unique']}</div><div class="muted">уникальных кодов</div></div>
<div class="card"><b>Обработано</b><div class="number">{snapshot['discovery_completed']}</div><div class="muted">{snapshot['discovery_progress_pct']}%</div></div>
<div class="card"><b>Осталось</b><div class="number">{snapshot['discovery_remaining']}</div><div class="muted">ETA: {html.escape(snapshot['eta_text'])}</div></div>
<div class="card"><b>Среднее время</b><div class="number">{html.escape(format_seconds(snapshot['average_seconds_per_item']))}</div><div class="muted">на товар одним воркером</div></div>
<div class="card"><b>Ошибки / partial</b><div class="number">{snapshot['discovery_errors']} / {snapshot['discovery_partial']}</div><div class="muted">нужен retry</div></div>
<div class="card"><b>Кэш</b><div class="number">{snapshot['product_cache_entries']}</div><div class="muted">карточек · {snapshot['group_cache_entries']} групп</div></div>
<div class="card"><b>Цены обновлены</b><div class="number">{snapshot['price_cards_updated']}</div><div class="muted">{html.escape(core.clean_text(snapshot['last_price_update_at']))}</div></div>
<div class="card"><b>На проверку</b><div class="number">{snapshot['review_candidates']}</div><div class="muted">кандидатов review</div></div>
</div>
<div class="panel"><b>Прогресс поиска аналогов</b><div class="progress"><span></span></div></div>
<div class="panel">
<div class="tabs">
<button class="active" data-tab="all">Все</button>
<button data-tab="EXACT_LOSE">Точный проигрыш</button>
<button data-tab="EXACT_WIN">Точный выигрыш</button>
<button data-tab="ONLY_OWN_OFFER">Только Unityre</button>
<button data-tab="ANALOG_LOSE">Дешевле аналоги</button>
<button data-tab="SCAN_ERROR">Ошибки</button>
<button data-tab="NOT_SCANNED">Не обработано</button>
</div>
<div class="filters">
<input id="search" placeholder="Код, название или бренд">
<select id="status"><option value="">Все статусы</option>{options_status}</select>
<select id="brand"><option value="">Все бренды</option>{options_brand}</select>
<select id="type"><option value="">Все типы</option>{options_type}</select>
<button id="reset">Сбросить</button>
</div>
<p class="muted">Показано: <span id="visibleCount">0</span> из {len(rows)}. Точный конкурент имеет приоритет над аналогом.</p>
<div class="table-wrap"><table id="products"><thead><tr>
<th>Код</th><th>Товар</th><th>Бренд</th><th>Тип</th><th>Unityre</th>
<th>Точный минимум</th><th>Продавец</th><th>Разница, %</th>
<th>Аналог минимум</th><th>Аналог</th><th>Разница, %</th>
<th>Review</th><th>Статус</th><th>Сек.</th><th>Обновлено</th>
</tr></thead><tbody>{''.join(table_rows)}</tbody></table></div>
</div>
</main>
<script>
const rows=[...document.querySelectorAll('#products tbody tr')];
const search=document.getElementById('search'),status=document.getElementById('status'),brand=document.getElementById('brand'),type=document.getElementById('type');
let tab='all';
function apply(){{let visible=0;const q=search.value.trim().toLowerCase();for(const row of rows){{const okQ=!q||row.dataset.search.includes(q);const okS=!status.value||row.dataset.status===status.value;const okB=!brand.value||row.dataset.brand===brand.value;const okT=!type.value||row.dataset.type===type.value;const okTab=tab==='all'||row.dataset.status===tab;const show=okQ&&okS&&okB&&okT&&okTab;row.style.display=show?'':'none';if(show)visible++;}}document.getElementById('visibleCount').textContent=visible;}}
for(const element of [search,status,brand,type])element.addEventListener('input',apply);
document.getElementById('reset').onclick=()=>{{search.value='';status.value='';brand.value='';type.value='';tab='all';document.querySelectorAll('.tabs button').forEach((b,i)=>b.classList.toggle('active',i===0));apply();}};
document.querySelectorAll('.tabs button').forEach(button=>button.onclick=()=>{{tab=button.dataset.tab;document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('active'));button.classList.add('active');apply();}});apply();
</script></body></html>"""
    (output / "dashboard.html").write_text(dashboard, encoding="utf-8")
    print(f"[Панель] {output / 'dashboard.html'}")
    print(f"[Отчёт] {output / 'price_comparison_v9.csv'}")


# ---------------------------------------------------------------------------
# Команды без браузера
# ---------------------------------------------------------------------------


def audit_command(args: argparse.Namespace) -> None:
    db = Database(Path(args.db))
    try:
        audit = write_catalog_audit(db, Path(args.output), args.expected_count)
        print(f"Каталог: {audit['unique_count']} уникальных товаров")
        print(f"Страниц: {audit['max_page']}; последняя: {audit['last_page_size']}")
        print(f"Характеристики: {audit['details_ok']} ({audit['details_coverage_pct']}%)")
        if audit["suspected_truncation"]:
            print("ВНИМАНИЕ: вероятно, каталог ограничен глубиной выдачи.")
        print(f"Отчёт: {Path(args.output) / 'catalog_audit.html'}")
    finally:
        db.conn.close()


def dashboard_command(args: argparse.Namespace) -> None:
    db = Database(Path(args.db))
    try:
        build_dashboard(db, Path(args.output), args.seller_name, args.assumed_workers)
    finally:
        db.conn.close()


def status_command(args: argparse.Namespace) -> None:
    db = Database(Path(args.db))
    try:
        snapshot = status_snapshot(db, args.assumed_workers)
        print(f"Kaspi Market V{VERSION}")
        print(f"Каталог: {snapshot['catalog_unique']}")
        print(f"Карточки с характеристиками: {snapshot['details_ok']} ({snapshot['details_coverage_pct']}%)")
        print(f"Обработано: {snapshot['discovery_completed']} ({snapshot['discovery_progress_pct']}%)")
        print(f"Успешно: {snapshot['discovery_success']}; partial: {snapshot['discovery_partial']}; ошибки: {snapshot['discovery_errors']}")
        print(f"Осталось: {snapshot['discovery_remaining']}")
        print(f"Среднее время: {format_seconds(snapshot['average_seconds_per_item'])}")
        print(f"Оценка завершения для {snapshot['assumed_workers']} воркеров: {snapshot['eta_text']}")
        print(f"Кэш карточек: {snapshot['product_cache_entries']}; кэш групп: {snapshot['group_cache_entries']}")
        print(f"Обновлено ценовых карточек: {snapshot['price_cards_updated']}")
    finally:
        db.conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_browser_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--codes", default="")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--city-id", default=DEFAULT_CITY_ID)
    parser.add_argument("--min-delay", type=float, default=1.0)
    parser.add_argument("--max-delay", type=float, default=2.2)
    parser.add_argument("--headless", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kaspi Market V9: кэш, раздельное обновление цен и панель контроля."
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--seller-name", default="Unityre")

    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit-catalog", help="Проверить полноту каталога")
    audit.add_argument("--expected-count", type=int, default=0)

    merge = sub.add_parser("merge-catalog", help="Объединить дополнительные CSV/JSON")
    merge.add_argument("--input", action="append", required=True)
    merge.add_argument("--expected-count", type=int, default=0)

    discover = sub.add_parser("discover", help="Найти и проверить аналоги")
    add_browser_arguments(discover)
    discover.add_argument("--search-pages", type=int, default=2)
    discover.add_argument("--validate-top", type=int, default=5)
    discover.add_argument("--search-cache-days", type=float, default=14)
    discover.add_argument("--detail-cache-days", type=float, default=30)
    discover.add_argument("--refresh", action="store_true")
    discover.add_argument("--refresh-search", action="store_true")
    discover.add_argument("--refresh-details", action="store_true")
    discover.add_argument("--only-errors", action="store_true")
    discover.add_argument("--no-dashboard", action="store_true")

    refresh = sub.add_parser("refresh-prices", help="Обновить только цены и продавцов")
    add_browser_arguments(refresh)
    refresh.add_argument("--include-analogs", action="store_true")
    refresh.add_argument("--no-dashboard", action="store_true")

    retry = sub.add_parser("retry-errors", help="Повторить ошибки поиска аналогов")
    add_browser_arguments(retry)
    retry.add_argument("--search-pages", type=int, default=2)
    retry.add_argument("--validate-top", type=int, default=5)
    retry.add_argument("--search-cache-days", type=float, default=14)
    retry.add_argument("--detail-cache-days", type=float, default=30)
    retry.add_argument("--no-dashboard", action="store_true")
    retry.set_defaults(
        only_errors=True,
        refresh=False,
        refresh_search=False,
        refresh_details=False,
        no_dashboard=False,
    )

    dashboard = sub.add_parser("dashboard", help="Перестроить панель")
    dashboard.add_argument("--assumed-workers", type=int, default=2)

    status = sub.add_parser("status", help="Показать прогресс и ETA")
    status.add_argument("--assumed-workers", type=int, default=2)

    return parser


async def async_main(args: argparse.Namespace) -> None:
    if args.command == "discover":
        await discover_command(args)
    elif args.command == "retry-errors":
        await discover_command(args)
    elif args.command == "refresh-prices":
        await refresh_prices_command(args)
    elif args.command == "audit-catalog":
        audit_command(args)
    elif args.command == "merge-catalog":
        merge_catalog_command(args)
    elif args.command == "dashboard":
        dashboard_command(args)
    elif args.command == "status":
        status_command(args)


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(async_main(args))
        return 0
    except KeyboardInterrupt:
        print("\nОстановлено. Завершённые позиции сохранены в SQLite.")
        return 130
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
