from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
import statistics
from typing import Any
from urllib.parse import urlencode, urljoin

from engine.kaspi_market_v9_1 import Database, enriched_comparison_rows, status_snapshot
from market_intelligence import (
    STATUS_INFO,
    Candidate,
    exact_offer_position,
    identity,
    normalize_specifications,
    canonical_season,
    canonical_studs,
)
from schema import ensure_database

HALYK_BASE_URL = "https://halykmarket.kz"

SORT_FIELDS = {
    "updated": "_updated_sort",
    "title": "title",
    "price": "_price_sort",
    "delta": "_delta_sort",
    "status": "price_status",
    "brand": "brand",
    "platform": "platform",
}

RISK_STATUSES = {"EXACT_ABOVE", "EXACT_HIGHEST", "DATA_ERROR"}
OPPORTUNITY_STATUSES = {"EXACT_LOWEST", "EXACT_BELOW"}
UNSCANNED_STATUSES = {"NOT_ANALYZED", "INSUFFICIENT_DATA", "REVIEW_REQUIRED"}
OZON_READY_STATUSES = {
    "DATA_COLLECTED", "NO_OTHER_SELLERS", "EXACT_LOWEST", "EXACT_BELOW",
    "EXACT_IN_MARKET", "EXACT_ABOVE", "EXACT_HIGHEST", "NO_OTHER_SELLERS",
    "COMPARABLE_LOWEST", "COMPARABLE_BELOW", "COMPARABLE_IN_MARKET",
    "COMPARABLE_ABOVE", "COMPARABLE_HIGHEST",
}

PRODUCT_TYPE_LABELS = {
    "passenger_tire": "Легковые шины",
    "commercial_tire": "Коммерческие шины",
    "truck_tire": "Грузовые шины",
    "motorcycle_tire": "Мотошины",
    "motorcycle_tube": "Мотокамеры",
    "tube": "Камеры",
    "rim_tape": "Ободные ленты",
    "chains": "Цепи",
    "other": "Другое",
}

SEASON_LABELS = {
    "SUMMER": "Летние",
    "WINTER": "Зимние",
    "ALL_SEASON": "Всесезонные",
    "UNKNOWN": "Сезон не определён",
}


class DataService:
    def __init__(
        self,
        db_path: Path,
        seller_name: str,
        ozon_db_path: Path | None = None,
        seller_id: str = "",
        halyk_seller_name: str = "Unityre",
    ):
        self.db_path = Path(db_path)
        self.seller_name = seller_name
        self.seller_id = seller_id
        self.ozon_db_path = Path(ozon_db_path) if ozon_db_path else None
        self.halyk_seller_name = halyk_seller_name
        self.lock = threading.RLock()
        self._rows_cache: list[dict[str, Any]] = []
        self._rows_cached_at = 0.0
        self._rows_signature: tuple[int, ...] | None = None
        ensure_database(self.db_path)
        db = Database(self.db_path)
        db.conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _connect_path(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def invalidate(self) -> None:
        with self.lock:
            self._rows_cached_at = 0.0
            self._rows_cache = []
            self._rows_signature = None

    @staticmethod
    def _json(value: Any, default: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value or "")
        except Exception:
            return default

    @staticmethod
    def _halyk_public_url(product_id: Any, title: Any = "", stored_url: Any = "", raw_json: Any = None) -> str:
        stored = str(stored_url or "").strip()
        if stored and "/search?" not in stored:
            return stored
        raw = DataService._json(raw_json, {}) if raw_json is not None else {}
        path = ""
        if isinstance(raw, dict):
            path = str(raw.get("url") or raw.get("url_handle") or raw.get("canonical_url") or "").strip()
        if path:
            if path.startswith("/category/"):
                return urljoin(HALYK_BASE_URL, path)
            if path.startswith("/"):
                return urljoin(HALYK_BASE_URL, f"/category{path}")
            return urljoin(HALYK_BASE_URL, path)
        query = str(product_id or "").strip() or str(title or "").strip()
        return f"{HALYK_BASE_URL}/search?{urlencode({'query': query})}" if query else ""

    def preferences(self, user_id: int | None) -> dict[str, Any]:
        defaults = {
            "locale": "ru",
            "theme": "system",
            "display_currency": "KZT",
            "rub_to_kzt": 5.5,
            "usd_to_kzt": 520.0,
            "eur_to_kzt": 565.0,
            "default_monthly_units": 1,
        }
        if not user_id:
            return defaults
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT locale,theme,display_currency,rub_to_kzt,usd_to_kzt,eur_to_kzt,default_monthly_units "
                "FROM app_user_preferences WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            if row:
                defaults.update(dict(row))
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO app_user_preferences(user_id) VALUES(?)", (int(user_id),)
                )
                conn.commit()
            return defaults
        finally:
            conn.close()

    def save_preferences(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.preferences(user_id)
        locale = str(payload.get("locale", current.get("locale", "ru")) or "ru").lower()
        if locale not in {"ru", "kk", "en"}:
            raise ValueError("Поддерживаются языки ru, kk и en.")
        theme = str(payload.get("theme", current.get("theme", "system")) or "system").lower()
        if theme not in {"system", "light", "dark"}:
            raise ValueError("Поддерживаются темы system, light и dark.")
        display_currency = str(
            payload.get("display_currency", current.get("display_currency", "KZT")) or "KZT"
        ).upper()
        if display_currency not in {"KZT", "RUB", "USD", "EUR"}:
            raise ValueError("Некорректная валюта отображения.")
        values = {
            "locale": locale,
            "theme": theme,
            "display_currency": display_currency,
            "rub_to_kzt": max(0.0001, float(payload.get("rub_to_kzt", current.get("rub_to_kzt", 5.5)))),
            "usd_to_kzt": max(0.0001, float(payload.get("usd_to_kzt", current.get("usd_to_kzt", 520)))),
            "eur_to_kzt": max(0.0001, float(payload.get("eur_to_kzt", current.get("eur_to_kzt", 565)))),
            "default_monthly_units": max(
                0,
                min(
                    1_000_000,
                    int(payload.get("default_monthly_units", current.get("default_monthly_units", 1))),
                ),
            ),
        }
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO app_user_preferences(
                    user_id,locale,theme,display_currency,rub_to_kzt,usd_to_kzt,eur_to_kzt,
                    default_monthly_units,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    locale=excluded.locale,theme=excluded.theme,
                    display_currency=excluded.display_currency,
                    rub_to_kzt=excluded.rub_to_kzt,usd_to_kzt=excluded.usd_to_kzt,
                    eur_to_kzt=excluded.eur_to_kzt,
                    default_monthly_units=excluded.default_monthly_units,
                    updated_at=excluded.updated_at
                """,
                (
                    int(user_id), values["locale"], values["theme"], values["display_currency"],
                    values["rub_to_kzt"], values["usd_to_kzt"], values["eur_to_kzt"],
                    values["default_monthly_units"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return values

    @staticmethod
    def _normalized_seller(value: Any) -> str:
        return " ".join(str(value or "").strip().casefold().split())

    def _is_own_exact_offer(self, row: dict[str, Any]) -> bool:
        merchant_id = self._normalized_seller(row.get("merchant_id"))
        merchant_name = self._normalized_seller(row.get("merchant_name"))
        seller_id = self._normalized_seller(self.seller_id)
        seller_name = self._normalized_seller(self.seller_name)
        return bool(
            (seller_id and merchant_id == seller_id)
            or (seller_name and merchant_name == seller_name)
        )

    def _load_kaspi_support_data(
        self,
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, list[dict[str, Any]]],
        dict[str, dict[str, Any]],
        dict[str, int],
    ]:
        conn = self._connect()
        try:
            states = {
                str(row["product_code"]): dict(row)
                for row in conn.execute(
                    "SELECT product_code,watched,priority,note,expected_monthly_units,updated_at FROM app_product_state"
                ).fetchall()
            }
            details = {
                str(row["product_code"]): dict(row)
                for row in conn.execute(
                    "SELECT product_code,title_detail,specifications_json,detail_status,detail_error,detail_collected_at FROM product_details"
                ).fetchall()
            }
            extras = {
                str(row["product_code"]): dict(row)
                for row in conn.execute(
                    """
                    SELECT c.product_code,c.image_url,m.stock,m.discount_percent,
                           m.price_before_discount_kzt,m.source_segment,m.active
                    FROM catalog_products c
                    LEFT JOIN catalog_product_meta m ON m.product_code=c.product_code
                    """
                ).fetchall()
            }
            exact_offers: dict[str, list[dict[str, Any]]] = defaultdict(list)
            try:
                for row in conn.execute(
                    """
                    SELECT source_product_code,candidate_product_code,merchant_id,merchant_name,
                           merchant_sku,price_kzt,merchant_rating,merchant_reviews,captured_at
                    FROM market_seller_offers
                    WHERE source_product_code=candidate_product_code AND price_kzt IS NOT NULL
                    ORDER BY source_product_code,price_kzt,merchant_name
                    """
                ).fetchall():
                    value = dict(row)
                    exact_offers[str(value["source_product_code"])].append(value)
            except sqlite3.OperationalError:
                pass
            exact_scans: dict[str, dict[str, Any]] = {}
            try:
                exact_scans = {
                    str(row["product_code"]): dict(row)
                    for row in conn.execute("SELECT * FROM exact_offer_scans").fetchall()
                }
            except sqlite3.OperationalError:
                pass
            legacy_counts: dict[str, int] = {}
            try:
                legacy_counts = {
                    str(row["source_product_code"]): int(row["count_value"] or 0)
                    for row in conn.execute(
                        """
                        SELECT source_product_code,COUNT(*) AS count_value
                        FROM market_candidates
                        WHERE candidate_product_code<>source_product_code
                        GROUP BY source_product_code
                        """
                    ).fetchall()
                }
            except sqlite3.OperationalError:
                pass
            return states, {"details": details, "extras": extras}, exact_offers, exact_scans, legacy_counts
        finally:
            conn.close()

    def _exact_offer_candidates(
        self,
        code: str,
        title: str,
        product_url: str,
        source_brand: str,
        offers: list[dict[str, Any]],
    ) -> list[Candidate]:
        selected: dict[str, dict[str, Any]] = {}
        for row in offers:
            if self._is_own_exact_offer(row):
                continue
            price = row.get("price_kzt")
            try:
                price_value = float(price)
            except (TypeError, ValueError):
                continue
            if price_value <= 0:
                continue
            merchant_key = str(row.get("merchant_id") or row.get("merchant_name") or "").strip()
            if not merchant_key:
                continue
            current = selected.get(merchant_key)
            if current is None or price_value < float(current.get("price_kzt") or 0):
                selected[merchant_key] = row
        result: list[Candidate] = []
        for merchant_key, row in selected.items():
            merchant_name = str(row.get("merchant_name") or merchant_key)
            result.append(Candidate(
                code=merchant_key,
                title=merchant_name,
                url=product_url,
                price=float(row.get("price_kzt") or 0),
                brand=source_brand,
                tier="SAME_PRODUCT_CARD",
                model=title,
                score=100.0,
                relation="KASPI_SAME_CARD",
                reasons=["same_product_code", f"product_code={code}", "different_seller"],
            ))
        result.sort(key=lambda item: (item.price, item.title.casefold()))
        return result

    def _kaspi_rows(self) -> list[dict[str, Any]]:
        db = Database(self.db_path)
        try:
            raw_rows = enriched_comparison_rows(db, self.seller_name)
        finally:
            db.conn.close()
        states, support, exact_offer_groups, exact_scans, legacy_counts = self._load_kaspi_support_data()
        details = support["details"]
        extras = support["extras"]
        result: list[dict[str, Any]] = []
        for raw in raw_rows:
            base = dict(raw)
            code = str(base.get("product_code") or "")
            extra = extras.get(code, {})
            if extra.get("active") is not None and int(extra.get("active") or 0) == 0:
                continue
            detail = details.get(code, {})
            specifications = self._json(detail.get("specifications_json"), [])
            source_brand = str(base.get("brand") or "")
            title = str(base.get("title") or detail.get("title_detail") or "")
            product_url = str(base.get("product_url") or "")
            exact_offers = exact_offer_groups.get(code, [])
            competitors = self._exact_offer_candidates(
                code, title, product_url, source_brand, exact_offers
            )
            scan = exact_scans.get(code, {})
            scan_status = str(scan.get("status") or ("ok" if exact_offers else ""))
            own_price = base.get("own_effective_price_kzt") or base.get("own_price_kzt")
            position = exact_offer_position(own_price, competitors, scan_status)
            status = str(position.get("price_status") or "NOT_ANALYZED")
            info = STATUS_INFO.get(status, STATUS_INFO["NOT_ANALYZED"])
            state = states.get(code, {})
            ident = identity(title, specifications, source_brand)
            characteristics = self._characteristics(
                title, specifications, source_brand,
                {
                    "size": base.get("size") or self._identity_size(ident),
                    "product_type": base.get("product_type") or ident.get("type"),
                    "model": ident.get("model"),
                },
            )
            expected_units = state.get("expected_monthly_units")
            row = {
                **base,
                **position,
                **characteristics,
                "product_code": code,
                "source_product_code": code,
                "platform": "kaspi",
                "platform_label": "Kaspi",
                "title": title,
                "brand": source_brand or ident.get("brand") or "",
                "model": ident.get("model") or "",
                "size": characteristics.get("size") or base.get("size") or self._identity_size(ident),
                "product_type": characteristics.get("product_type") or "other",
                "product_url": product_url,
                "seller_name": self.seller_name,
                "own_price_kzt": own_price,
                "price_original": own_price,
                "currency_original": "KZT",
                "price_status": status,
                "status_label": info["label"],
                "status_tone": info["tone"],
                "raw_price_status": base.get("price_status"),
                "match_method": "KASPI_PRODUCT_CODE",
                "match_method_label": "Та же карточка Kaspi",
                "exact_offer_status": scan_status or "not_checked",
                "exact_offer_checked_at": scan.get("checked_at"),
                "exact_offer_count": len(exact_offers),
                "competitor_seller_count": len(competitors),
                "watched": bool(state.get("watched")),
                "priority": state.get("priority") or "normal",
                "note": state.get("note") or "",
                "expected_monthly_units": expected_units,
                "user_state_updated_at": state.get("updated_at"),
                "image_url": extra.get("image_url"),
                "stock": extra.get("stock"),
                "discount_percent": extra.get("discount_percent"),
                "price_before_discount_kzt": extra.get("price_before_discount_kzt"),
                "source_segment": extra.get("source_segment"),
                "exact_candidates": [item.as_dict() for item in competitors],
                "segment_candidates": [],
                "review_candidates": [],
                "candidate_count": len(competitors),
                "legacy_candidate_count": int(legacy_counts.get(code, 0)),
                "legacy_candidates_used_in_analytics": False,
                "_price_sort": float(own_price) if own_price is not None else -1.0,
                "_delta_sort": float(position.get("difference_kzt") or 0),
                "_updated_sort": scan.get("checked_at") or base.get("last_price_update_at") or detail.get("detail_collected_at") or "",
            }
            result.append(row)
        return result

    @staticmethod
    def _identity_size(ident: dict[str, Any]) -> str:
        if ident.get("width") and ident.get("height") and ident.get("diameter"):
            return f"{ident['width']}/{ident['height']} R{ident['diameter']}"
        return ""

    @staticmethod
    def _canonical_product_type(value: Any, title: Any = "") -> str:
        raw = str(value or "").strip().casefold()
        aliases = {
            "tire": "passenger_tire",
            "moto_tire": "motorcycle_tire",
            "motorcycle_tire": "motorcycle_tire",
            "motorcycle_tube": "motorcycle_tube",
            "tube": "tube",
            "rim_tape": "rim_tape",
            "chain": "chains",
            "chains": "chains",
            "passenger_tire": "passenger_tire",
            "commercial_tire": "commercial_tire",
            "truck_tire": "truck_tire",
        }
        if raw in aliases:
            return aliases[raw]
        text = str(title or "").casefold()
        if "мотокам" in text or ("камера" in text and "мото" in text):
            return "motorcycle_tube"
        if "мотошин" in text or "мото шин" in text:
            return "motorcycle_tire"
        if "ободн" in text and "лент" in text:
            return "rim_tape"
        if "цеп" in text:
            return "chains"
        if "камер" in text:
            return "tube"
        if "груз" in text:
            return "truck_tire"
        if "коммерческ" in text or re.search(r"\br\d{2}c\b", text):
            return "commercial_tire"
        if "шин" in text or re.search(r"\br\d{2}\b", text):
            return "passenger_tire"
        return "other"

    @classmethod
    def _characteristics(
        cls,
        title: Any,
        specs: Any,
        brand: Any,
        explicit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        explicit = explicit or {}
        ident = identity(str(title or ""), specs, str(brand or ""))
        size_text = str(explicit.get("size") or "").strip()
        size_match = re.search(
            r"(?<!\d)(\d{2,3}(?:[.,]\d+)?)\s*/\s*(\d{1,3}(?:[.,]\d+)?)\s*(?:ZR|R|B|-)?\s*(\d{2})(?!\d)",
            size_text.upper(),
        )
        width = str(explicit.get("width") or ident.get("width") or (size_match.group(1) if size_match else "")).replace(",", ".")
        profile = str(explicit.get("profile") or explicit.get("height") or ident.get("height") or (size_match.group(2) if size_match else "")).replace(",", ".")
        diameter = str(explicit.get("diameter") or ident.get("diameter") or (size_match.group(3) if size_match else "")).replace(",", ".")
        normalized_size = f"{width}/{profile} R{diameter}" if width and profile and diameter else size_text
        product_type = cls._canonical_product_type(explicit.get("product_type") or ident.get("type"), title)
        season = canonical_season(explicit.get("season") or ident.get("season"))
        studs_value = explicit.get("studded")
        if studs_value is True or str(studs_value).strip().casefold() in {"1", "true", "yes", "да"}:
            studs = "YES"
        elif studs_value is False and "studded" in explicit:
            studs = "NO"
        else:
            studs = canonical_studs(explicit.get("studs") or ident.get("studs"))
        load_index = str(explicit.get("load_index") or ident.get("load") or "").strip()
        speed_index = str(explicit.get("speed_index") or ident.get("speed") or "").strip().upper()
        runflat_raw = explicit.get("runflat") if "runflat" in explicit else ident.get("runflat")
        runflat = bool(runflat_raw is True or str(runflat_raw or "").strip().casefold() in {"1", "true", "yes", "да", "runflat"})
        commercial_raw = explicit.get("commercial") if "commercial" in explicit else ident.get("commercial")
        commercial = bool(str(commercial_raw or "").strip().casefold() in {"1", "true", "yes", "да"} or product_type == "commercial_tire")
        # Cross-market group uses only fields available with high coverage on every
        # marketplace. Season, studs, load and speed remain report columns and are
        # part of the strict identity key below, but do not split the operational
        # group when a marketplace has not supplied them yet.
        base_parts = [product_type, normalized_size or "unknown"]
        group_key = "|".join(str(part or "unknown").casefold() for part in base_parts)
        group_label_parts = [normalized_size or "Размер не определён", PRODUCT_TYPE_LABELS.get(product_type, product_type)]
        exact_parts = [
            str(brand or ident.get("brand") or "").strip().casefold(),
            str(explicit.get("model") or ident.get("model") or "").strip().casefold(),
            normalized_size.casefold(),
            load_index.casefold(),
            speed_index.casefold(),
            season.casefold(),
            studs.casefold(),
        ]
        exact_key = "|".join(exact_parts) if all(exact_parts[:3]) else ""
        completeness_values = [product_type != "other", bool(normalized_size), season != "UNKNOWN", bool(load_index), bool(speed_index)]
        return {
            "product_type": product_type,
            "product_type_label": PRODUCT_TYPE_LABELS.get(product_type, product_type),
            "size": normalized_size,
            "tire_width": width,
            "tire_profile": profile,
            "tire_diameter": diameter,
            "load_index": load_index,
            "speed_index": speed_index,
            "season": season,
            "season_label": SEASON_LABELS.get(season, season),
            "studded": studs,
            "runflat": runflat,
            "commercial": commercial,
            "offroad": str(explicit.get("offroad") or ident.get("offroad") or "").strip().upper(),
            "characteristic_group": group_key,
            "characteristic_group_label": " · ".join(group_label_parts),
            "exact_characteristic_key": exact_key,
            "characteristic_completeness_percent": round(sum(completeness_values) / len(completeness_values) * 100),
        }

    @staticmethod
    def _ozon_product_type(title: Any) -> str:
        text = str(title or "").casefold()
        if "мотокамера" in text or ("камера" in text and "мото" in text):
            return "motorcycle_tube"
        if "мотошин" in text or "мото шин" in text:
            return "motorcycle_tire"
        if "коммерческ" in text or re.search(r"\br\d{2}c\b", text):
            return "commercial_tire"
        if "грузов" in text or "для груз" in text:
            return "truck_tire"
        if "шина" in text or "шины" in text:
            return "passenger_tire"
        return "other"

    @staticmethod
    def _normalized_ozon(value: Any) -> str:
        return re.sub(r"[^a-zа-я0-9]+", "", str(value or "").casefold())

    def _ozon_owner_config(self) -> tuple[str, set[str]]:
        expected_name = ""
        seller_ids: set[str] = set()
        if self.ozon_db_path:
            root = self.ozon_db_path.parent.parent
            expected_path = root / "EXPECTED_SELLER.txt"
            urls_path = root / "START_URLS.txt"
            try:
                for raw in expected_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                    value = raw.strip()
                    if value and not value.startswith("#"):
                        expected_name = self._normalized_ozon(value)
                        break
            except OSError:
                pass
            try:
                for raw in urls_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                    match = re.search(r"/seller/[^/?]*-(\d+)", raw, flags=re.I)
                    if match:
                        seller_ids.add(match.group(1))
            except OSError:
                pass
        return expected_name, seller_ids

    def _is_own_ozon_offer(
        self, row: dict[str, Any], expected_name: str, seller_ids: set[str]
    ) -> bool:
        seller_id = str(row.get("seller_id") or "").strip()
        seller_name = self._normalized_ozon(row.get("seller_name"))
        return bool(
            (seller_id and seller_id in seller_ids)
            or (expected_name and seller_name == expected_name)
        )

    @staticmethod
    def _ozon_source_type(url: Any) -> str:
        return "CLIENT_CATALOG" if "/seller/" in str(url or "").casefold() else "MARKET_CATEGORY"

    def _ensure_ozon_source_schema(
        self, conn: sqlite3.Connection, expected_name: str, seller_ids: set[str]
    ) -> None:
        conn.executescript(
            """
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
                PRIMARY KEY(article, source_url)
            );
            CREATE INDEX IF NOT EXISTS idx_product_sources_type
                ON product_sources(source_type, article);
            """
        )
        products = conn.execute(
            "SELECT article,discovery_url,first_seen_at,last_seen_at FROM products"
        ).fetchall()
        with conn:
            for product in products:
                source_url = str(product["discovery_url"] or "").strip()
                if not source_url:
                    continue
                source_type = self._ozon_source_type(source_url)
                first_seen = str(product["first_seen_at"] or datetime.now().isoformat(timespec="seconds"))
                last_seen = str(product["last_seen_at"] or first_seen)
                conn.execute(
                    """
                    INSERT INTO catalog_sources(
                        source_url,source_type,label,first_seen_at,last_seen_at,active
                    ) VALUES(?,?,?,?,?,1)
                    ON CONFLICT(source_url) DO UPDATE SET
                        source_type=excluded.source_type,
                        last_seen_at=MAX(catalog_sources.last_seen_at,excluded.last_seen_at),
                        active=1
                    """,
                    (source_url, source_type, "", first_seen, last_seen),
                )
                conn.execute(
                    """
                    INSERT INTO product_sources(
                        article,source_url,source_type,first_seen_at,last_seen_at,last_run_id,page_no
                    ) VALUES(?,?,?,?,?,'',0)
                    ON CONFLICT(article,source_url) DO UPDATE SET
                        source_type=excluded.source_type,
                        last_seen_at=MAX(product_sources.last_seen_at,excluded.last_seen_at)
                    """,
                    (str(product["article"]), source_url, source_type, first_seen, last_seen),
                )

            # A confirmed Alfa Tires offer is a safe ownership signal even when the
            # product was first discovered through a market category.
            offers = conn.execute(
                """
                SELECT article,seller_id,seller_name,seller_url,first_seen_at,last_seen_at
                FROM offers WHERE active=1
                """
            ).fetchall()
            for offer in offers:
                value = dict(offer)
                if not self._is_own_ozon_offer(value, expected_name, seller_ids):
                    continue
                source_url = str(value.get("seller_url") or "").strip()
                if not source_url:
                    source_url = f"seller://{value.get('seller_id') or expected_name or 'client'}"
                first_seen = str(value.get("first_seen_at") or datetime.now().isoformat(timespec="seconds"))
                last_seen = str(value.get("last_seen_at") or first_seen)
                conn.execute(
                    """
                    INSERT INTO catalog_sources(
                        source_url,source_type,label,first_seen_at,last_seen_at,active
                    ) VALUES(?,'CLIENT_CATALOG','',?,?,1)
                    ON CONFLICT(source_url) DO UPDATE SET
                        source_type='CLIENT_CATALOG',
                        last_seen_at=MAX(catalog_sources.last_seen_at,excluded.last_seen_at),
                        active=1
                    """,
                    (source_url, first_seen, last_seen),
                )
                conn.execute(
                    """
                    INSERT INTO product_sources(
                        article,source_url,source_type,first_seen_at,last_seen_at,last_run_id,page_no
                    ) VALUES(?,?,'CLIENT_CATALOG',?,?, '',0)
                    ON CONFLICT(article,source_url) DO UPDATE SET
                        source_type='CLIENT_CATALOG',
                        last_seen_at=MAX(product_sources.last_seen_at,excluded.last_seen_at)
                    """,
                    (str(value["article"]), source_url, first_seen, last_seen),
                )

    @staticmethod
    def _strict_ozon_keys(row: dict[str, Any]) -> list[tuple[Any, ...]]:
        normalize = DataService._normalized_ozon
        product_type = DataService._ozon_product_type(row.get("title"))
        brand = normalize(row.get("brand"))
        manufacturer_article = normalize(row.get("manufacturer_article"))
        result: list[tuple[Any, ...]] = []
        if manufacturer_article and brand:
            result.append(("MFR", product_type, brand, manufacturer_article))

        model = normalize(row.get("model"))
        size = normalize(row.get("tire_size"))
        load_index = normalize(row.get("load_index"))
        speed_index = normalize(row.get("speed_index"))
        season = normalize(row.get("season"))
        required = (brand, model, size, load_index, speed_index, season)
        if all(required):
            result.append((
                "FP", product_type, brand, model, size, load_index, speed_index, season,
                int(row.get("studded")) if row.get("studded") is not None else -1,
                int(bool(row.get("xl"))),
                int(bool(row.get("runflat"))),
            ))
        return result

    @staticmethod
    def _freshness(updated_at: Any) -> tuple[str, str]:
        text = str(updated_at or "").strip()
        if not text:
            return "never", "Не обновлялось"
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_hours = max(
                0.0,
                (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600,
            )
        except Exception:
            return "unknown", "Дата неизвестна"
        if age_hours <= 24:
            return "fresh", "Актуально"
        if age_hours <= 72:
            return "due", "Требует обновления"
        return "stale", "Устарело"


    def _ozon_rows(self) -> list[dict[str, Any]]:
        path = self.ozon_db_path
        if not path or not path.exists():
            return []
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect_path(path)
            expected_name, seller_ids = self._ozon_owner_config()
            self._ensure_ozon_source_schema(conn, expected_name, seller_ids)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS market_search_candidates (
                    client_article TEXT NOT NULL,candidate_article TEXT NOT NULL,
                    query_text TEXT NOT NULL DEFAULT '',query_url TEXT NOT NULL DEFAULT '',
                    catalog_rank INTEGER NOT NULL DEFAULT 0,match_level TEXT NOT NULL DEFAULT 'REJECTED',
                    match_score REAL NOT NULL DEFAULT 0,match_method TEXT NOT NULL DEFAULT '',
                    match_reason TEXT NOT NULL DEFAULT '',reasons_json TEXT NOT NULL DEFAULT '[]',
                    active INTEGER NOT NULL DEFAULT 1,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL,last_run_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(client_article,candidate_article)
                );
            """)
            products = {str(row["article"]): dict(row) for row in conn.execute("SELECT * FROM products WHERE active=1").fetchall()}
            memberships: dict[str,set[str]] = defaultdict(set)
            for row in conn.execute("SELECT article,source_type FROM product_sources").fetchall():
                memberships[str(row["article"])].add(str(row["source_type"]))
            offers_by_article: dict[str,list[dict[str,Any]]] = defaultdict(list)
            for row in conn.execute("SELECT * FROM offers WHERE active=1 ORDER BY article,CASE WHEN card_price>0 THEN 0 ELSE 1 END,card_price,last_checked_at DESC").fetchall():
                offers_by_article[str(row["article"])].append(dict(row))
            client_articles={article for article,types in memberships.items() if "CLIENT_CATALOG" in types}
            for article,offers in offers_by_article.items():
                if any(self._is_own_ozon_offer(o,expected_name,seller_ids) for o in offers): client_articles.add(article)
            search_rows: dict[str,list[dict[str,Any]]] = defaultdict(list)
            for row in conn.execute("SELECT * FROM market_search_candidates WHERE active=1 AND match_level IN ('EXACT','STRONG','COMPARABLE') ORDER BY match_score DESC,catalog_rank").fetchall():
                search_rows[str(row["client_article"])].append(dict(row))
            market_articles={article for article,types in memberships.items() if "MARKET_CATEGORY" in types or "MARKET_SEARCH" in types}
            key_index: dict[tuple[Any,...],list[str]] = defaultdict(list)
            for article in market_articles:
                product=products.get(article)
                if product:
                    for key in self._strict_ozon_keys(product): key_index[key].append(article)
            result=[]
            for article in sorted(client_articles,key=lambda x:str(products.get(x,{}).get("last_seen_at") or ""),reverse=True):
                value=products.get(article)
                if not value: continue
                article_offers=offers_by_article.get(article,[])
                own_offer=next((o for o in article_offers if self._is_own_ozon_offer(o,expected_name,seller_ids)),None)
                own_price=int((own_offer or {}).get("card_price") or value.get("catalog_price") or 0)
                accepted: dict[tuple[str,str],dict[str,Any]]={}
                def add_candidate(candidate_article:str,offer:dict[str,Any],method:str,label:str,level:str,score:float=100,reason:str=''):
                    if self._is_own_ozon_offer(offer,expected_name,seller_ids): return
                    price=int(offer.get("card_price") or offer.get("catalog_price") or 0)
                    if price<=0:return
                    seller_key=str(offer.get("seller_id") or offer.get("seller_name") or '').strip()
                    if not seller_key:return
                    key=(candidate_article,seller_key.casefold())
                    priority={'EXACT':3,'STRONG':2,'COMPARABLE':1}.get(level,0)
                    if key in accepted and accepted[key]['_priority']>=priority:return
                    product=products.get(candidate_article,{})
                    accepted[key]={
                        'article':candidate_article,'merchant_id':offer.get('seller_id') or '',
                        'merchant_name':offer.get('seller_name') or 'Продавец Ozon','merchant_rating':offer.get('seller_rating'),
                        'price_rub':price,'currency':offer.get('currency') or 'RUB','product_url':product.get('canonical_url') or '',
                        'product_title':product.get('title') or '', 'captured_at':offer.get('last_checked_at') or offer.get('last_seen_at'),
                        'match_method':method,'match_method_label':label,'match_level':level,'match_score':score,'match_reason':reason,
                        'is_own':False,'_priority':priority,
                    }
                for offer in article_offers:
                    add_candidate(article,offer,'OZON_SAME_ARTICLE','Та же карточка Ozon','EXACT',100,'Другой продавец той же карточки')
                matched_articles={}
                for key in self._strict_ozon_keys(value):
                    method='OZON_MANUFACTURER_ARTICLE' if key[0]=='MFR' else 'OZON_STRICT_FINGERPRINT'
                    label='Совпадение по артикулу производителя' if key[0]=='MFR' else 'Строгое совпадение характеристик'
                    for cand in key_index.get(key,[]):
                        if cand!=article: matched_articles.setdefault(cand,(method,label))
                for cand,(method,label) in matched_articles.items():
                    for offer in offers_by_article.get(cand,[]): add_candidate(cand,offer,method,label,'EXACT',95,'Совпадение из рыночной категории')
                for match in search_rows.get(article,[]):
                    cand=str(match.get('candidate_article') or '')
                    level=str(match.get('match_level') or 'COMPARABLE')
                    label={'EXACT':'Точный товар: бренд, модель и размер','STRONG':'Сильное совпадение: бренд, размер и близкая модель','COMPARABLE':'Сопоставимый товар: тот же бренд и размер'}.get(level,level)
                    for offer in offers_by_article.get(cand,[]):
                        add_candidate(cand,offer,'OZON_SEARCH_'+str(match.get('match_method') or ''),label,level,float(match.get('match_score') or 0),str(match.get('match_reason') or ''))
                exact_candidates=[{k:v for k,v in x.items() if k!='_priority'} for x in accepted.values() if x['match_level'] in {'EXACT','STRONG'}]
                comparable_candidates=[{k:v for k,v in x.items() if k!='_priority'} for x in accepted.values() if x['match_level']=='COMPARABLE']
                pool=exact_candidates if exact_candidates else comparable_candidates
                basis='EXACT' if exact_candidates else 'COMPARABLE' if comparable_candidates else 'NONE'
                prices=sorted(int(x['price_rub']) for x in pool if int(x.get('price_rub') or 0)>0)
                market_min=min(prices) if prices else None; market_max=max(prices) if prices else None
                market_median=float(statistics.median(prices)) if prices else None
                difference_pct=round((own_price-market_median)/market_median*100,2) if own_price and market_median else None
                rank_values=sorted([*prices,own_price]) if prices and own_price else []
                price_rank=rank_values.index(own_price)+1 if rank_values else None
                if value.get('detail_status')!='COMPLETE': status='DATA_ERROR' if value.get('last_error') else 'NOT_ANALYZED'
                elif not prices: status='NO_OTHER_SELLERS'
                elif basis=='EXACT':
                    status='EXACT_LOWEST' if own_price<=market_min else 'EXACT_HIGHEST' if own_price>=market_max else 'EXACT_IN_MARKET' if abs(float(difference_pct or 0))<=2 else 'EXACT_BELOW' if float(difference_pct or 0)<0 else 'EXACT_ABOVE'
                else:
                    status='COMPARABLE_LOWEST' if own_price<=market_min else 'COMPARABLE_HIGHEST' if own_price>=market_max else 'COMPARABLE_IN_MARKET' if abs(float(difference_pct or 0))<=2 else 'COMPARABLE_BELOW' if float(difference_pct or 0)<0 else 'COMPARABLE_ABOVE'
                info=STATUS_INFO.get(status,STATUS_INFO['NOT_ANALYZED'])
                own_updated=(own_offer or {}).get('last_checked_at') or value.get('last_price_at') or value.get('last_detail_at') or value.get('last_seen_at') or ''
                freshness_status,freshness_label=self._freshness(own_updated)
                primary=pool[0]['match_method'] if pool else ''
                primary_label=pool[0]['match_method_label'] if pool else ''
                characteristics=self._characteristics(
                    value.get('title') or '',
                    value.get('specs_json') or [],
                    value.get('brand') or '',
                    {
                        'size': value.get('tire_size') or '',
                        'product_type': self._ozon_product_type(value.get('title')),
                        'model': value.get('model') or '',
                        'load_index': value.get('load_index') or '',
                        'speed_index': value.get('speed_index') or '',
                        'season': value.get('season') or '',
                        'studded': value.get('studded'),
                        'runflat': value.get('runflat'),
                    },
                )
                result.append({
                    **characteristics,
                    'product_code':f'ozon:{article}','source_product_code':article,'platform':'ozon','platform_label':'Ozon','source_type':'CLIENT_CATALOG',
                    'title':value.get('title') or '','brand':value.get('brand') or '','model':value.get('model') or '','size':characteristics.get('size') or value.get('tire_size') or '',
                    'product_type':characteristics.get('product_type') or self._ozon_product_type(value.get('title')),'strict_identity_eligible':bool(value.get('brand') and value.get('tire_size')),
                    'product_url':value.get('canonical_url') or '','image_url':value.get('image_url') or '','own_price_kzt':None,'market_price_kzt':None,
                    'price_original':own_price,'currency_original':(own_offer or {}).get('currency') or 'RUB','regular_price_original':(own_offer or {}).get('regular_price') or 0,
                    'market_min_price_original':market_min,'market_max_price_original':market_max,'market_median_price_original':market_median,
                    'seller_id':(own_offer or {}).get('seller_id') or next(iter(seller_ids),''),'seller_name':(own_offer or {}).get('seller_name') or ('Alfa Tires' if expected_name else ''),
                    'seller_url':(own_offer or {}).get('seller_url') or '','seller_rating':(own_offer or {}).get('seller_rating'),'catalog_rating':(own_offer or {}).get('product_rating'),
                    'catalog_reviews':(own_offer or {}).get('review_count'),'availability_status':(own_offer or {}).get('availability_status') or 'UNKNOWN',
                    'price_status':status,'status_label':info['label'],'status_tone':info['tone'],'identity_completeness_percent':value.get('identity_completeness_percent') or 0,
                    'manufacturer_article':value.get('manufacturer_article') or '','load_index':characteristics.get('load_index') or value.get('load_index') or '','speed_index':characteristics.get('speed_index') or value.get('speed_index') or '',
                    'season':characteristics.get('season') or 'UNKNOWN','studded':characteristics.get('studded') or 'UNKNOWN','xl':bool(value.get('xl')),'runflat':bool(characteristics.get('runflat')),
                    'watched':False,'priority':'normal','note':'','candidate_count':len(pool),'reference_count':len(pool),'exact_candidate_count':len(exact_candidates),
                    'comparable_candidate_count':len(comparable_candidates),'reference_type':'OZON_EXACT_PRODUCT' if basis=='EXACT' else 'OZON_BRAND_SIZE' if basis=='COMPARABLE' else '',
                    'market_basis':basis,'match_method':primary,'match_method_label':primary_label,'exact_candidates':exact_candidates,'comparable_candidates':comparable_candidates,
                    'difference_pct':difference_pct,'price_rank':price_rank,'price_rank_total':len(rank_values) if rank_values else None,
                    'lowest_product_url':min(pool,key=lambda x:x['price_rub'])['product_url'] if pool else '',
                    'highest_product_url':max(pool,key=lambda x:x['price_rub'])['product_url'] if pool else '',
                    'updated_at':own_updated,'freshness_status':freshness_status,'freshness_label':freshness_label,
                    '_price_sort':float(own_price or 0),'_delta_sort':float(difference_pct or 0),'_updated_sort':own_updated,
                })
            return result
        except sqlite3.Error:
            return []
        finally:
            if conn is not None: conn.close()

    def _halyk_rows(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            tables = {
                str(row[0]) for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "halyk_products" not in tables:
                return []
            states = {
                str(row["product_code"]): dict(row)
                for row in conn.execute(
                    "SELECT product_code,watched,priority,note,expected_monthly_units,updated_at FROM app_product_state"
                ).fetchall()
            }
            offers_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
            if "halyk_offers" in tables:
                for row in conn.execute(
                    """
                    SELECT * FROM halyk_offers
                    WHERE active=1
                    ORDER BY product_id,is_own DESC,CASE WHEN price_kzt>0 THEN 0 ELSE 1 END,price_kzt,merchant_name
                    """
                ).fetchall():
                    offers_by_product[str(row["product_id"])].append(dict(row))
            products = conn.execute(
                "SELECT * FROM halyk_products WHERE active=1 ORDER BY last_seen_at DESC"
            ).fetchall()
        finally:
            conn.close()

        result: list[dict[str, Any]] = []
        for row in products:
            value = dict(row)
            product_id = str(value.get("product_id") or "")
            code = f"halyk:{product_id}"
            public_url = self._halyk_public_url(
                product_id,
                value.get("name"),
                value.get("product_url"),
                value.get("raw_json"),
            )
            specs = self._json(value.get("specs_json"), [])
            ident = identity(str(value.get("name") or ""), specs, str(value.get("brand") or ""))
            characteristics = self._characteristics(
                value.get("name") or "", specs, value.get("brand") or "",
                {
                    "size": self._identity_size(ident),
                    "product_type": ident.get("type"),
                    "model": ident.get("model"),
                },
            )
            offers = offers_by_product.get(product_id, [])
            own_offer = next((offer for offer in offers if int(offer.get("is_own") or 0) == 1), None)
            own_price = (
                own_offer.get("price_kzt")
                if own_offer and own_offer.get("price_kzt") is not None
                else value.get("price_kzt")
            )
            competitors = [
                Candidate(
                    code=str(offer.get("merchant_key") or offer.get("merchant_name") or ""),
                    title=str(offer.get("merchant_name") or "Продавец Halyk Market"),
                    url=public_url,
                    price=float(offer.get("price_kzt") or 0),
                    brand=str(value.get("brand") or ident.get("brand") or ""),
                    tier="SAME_PRODUCT_CARD",
                    model=str(value.get("name") or ""),
                    score=100.0,
                    relation="HALYK_SAME_CARD",
                    reasons=["same_halyk_product_id", f"product_id={product_id}", "different_seller"],
                )
                for offer in offers
                if int(offer.get("is_own") or 0) == 0 and float(offer.get("price_kzt") or 0) > 0
            ]
            scan_status = "ok" if value.get("last_market_at") else ""
            position = exact_offer_position(own_price, competitors, scan_status)
            position.update({
                "reference_type": "HALYK_SAME_CARD",
                "match_method": "HALYK_PRODUCT_ID",
            })
            status = str(position.get("price_status") or "NOT_ANALYZED")
            info = STATUS_INFO.get(status, STATUS_INFO["NOT_ANALYZED"])
            state = states.get(code, {})
            result.append({
                **position,
                **characteristics,
                "product_code": code,
                "source_product_code": product_id,
                "platform": "halyk_market",
                "platform_label": "Halyk Market",
                "source_type": "CLIENT_CATALOG",
                "title": value.get("name") or "",
                "brand": value.get("brand") or ident.get("brand") or "",
                "model": ident.get("model") or "",
                "size": characteristics.get("size") or self._identity_size(ident),
                "product_type": characteristics.get("product_type") or "other",
                "product_url": public_url,
                "image_url": value.get("image_url") or "",
                "price_kzt": own_price,
                "own_price_kzt": own_price,
                "market_price_kzt": position.get("market_price_kzt"),
                "price_original": own_price,
                "currency_original": "KZT",
                "seller_name": self.halyk_seller_name,
                "seller_url": "",
                "price_status": status,
                "status_label": info["label"],
                "status_tone": info["tone"],
                "match_method_label": "Та же карточка Halyk Market",
                "exact_offer_status": scan_status or "not_checked",
                "exact_offer_checked_at": value.get("last_market_at"),
                "exact_offer_count": len(offers),
                "competitor_seller_count": len(competitors),
                "candidate_count": len(competitors),
                "reference_count": position.get("reference_count") or len(competitors),
                "exact_candidates": [item.as_dict() for item in competitors],
                "segment_candidates": [],
                "review_candidates": [],
                "watched": bool(state.get("watched")),
                "priority": state.get("priority") or "normal",
                "note": state.get("note") or "",
                "expected_monthly_units": state.get("expected_monthly_units"),
                "identity_completeness_percent": 100 if product_id else 0,
                "catalog_rating": None,
                "catalog_reviews": None,
                "updated_at": value.get("last_market_at") or value.get("last_catalog_at") or value.get("last_seen_at"),
                "freshness_status": self._freshness(value.get("last_market_at") or value.get("last_catalog_at"))[0],
                "freshness_label": self._freshness(value.get("last_market_at") or value.get("last_catalog_at"))[1],
                "_price_sort": float(own_price or 0),
                "_delta_sort": float(position.get("difference_kzt") or 0),
                "_updated_sort": value.get("last_market_at") or value.get("last_catalog_at") or value.get("last_seen_at") or "",
                "raw_price_status": status,
            })
        return result

    def _source_signature(self) -> tuple[int, ...]:
        values: list[int] = []
        paths = [self.db_path]
        if self.ozon_db_path:
            paths.append(self.ozon_db_path)
        for path in paths:
            for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                try:
                    stat = candidate.stat()
                    values.extend((int(stat.st_mtime_ns), int(stat.st_size)))
                except OSError:
                    values.extend((0, 0))
        return tuple(values)

    def rows(self, ttl_seconds: float = 60.0) -> list[dict[str, Any]]:
        with self.lock:
            signature = self._source_signature()
            if self._rows_cache and self._rows_signature == signature:
                return self._rows_cache
            self._rows_cache = self._kaspi_rows() + self._ozon_rows() + self._halyk_rows()
            self._rows_cached_at = time.monotonic()
            self._rows_signature = self._source_signature()
            return self._rows_cache

    def _apply_user_values(self, row: dict[str, Any], preferences: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        rate = float(preferences.get("rub_to_kzt") or 5.5)
        if item.get("platform") == "ozon":
            original = float(item.get("price_original") or 0)
            item["price_kzt"] = round(original * rate, 2) if original else None
            item["market_price_kzt"] = item["price_kzt"]
            for source_key, target_key in (
                ("market_min_price_original", "market_min_price_kzt"),
                ("market_median_price_original", "market_median_price_kzt"),
                ("market_max_price_original", "market_max_price_kzt"),
            ):
                value = item.get(source_key)
                item[target_key] = round(float(value) * rate, 2) if value not in (None, "") else None
            median = item.get("market_median_price_kzt")
            item["difference_kzt"] = (
                round(float(item["price_kzt"]) - float(median), 2)
                if item.get("price_kzt") is not None and median is not None else None
            )
        else:
            item["price_kzt"] = item.get("own_price_kzt")
        units = item.get("expected_monthly_units")
        if units is None:
            units = int(preferences.get("default_monthly_units") or 0)
        item["expected_monthly_units"] = units
        potential = float(item.get("potential_margin_per_unit_kzt") or 0)
        item["potential_margin_monthly_kzt"] = round(potential * int(units or 0), 2)
        return item

    def overview(self, expected_count: int, assumed_workers: int = 2, user_id: int | None = None, allowed_platforms: set[str] | None = None) -> dict[str, Any]:
        try:
            db = Database(self.db_path)
            snapshot = status_snapshot(db, assumed_workers)
            db.conn.close()
        except Exception:
            snapshot = {}
        preferences = self.preferences(user_id)
        rows = [self._apply_user_values(row, preferences) for row in self.rows()]
        if allowed_platforms is not None:
            rows = [row for row in rows if str(row.get("platform")) in allowed_platforms]
        kaspi_rows = [row for row in rows if row.get("platform") == "kaspi"]
        ozon_rows = [row for row in rows if row.get("platform") == "ozon"]
        halyk_rows = [row for row in rows if row.get("platform") == "halyk_market"]
        exact_rows = kaspi_rows + halyk_rows
        counts = Counter(str(row.get("price_status") or "NOT_ANALYZED") for row in exact_rows)
        active_count = len(rows)
        priced_count = sum(1 for row in rows if row.get("price_kzt") is not None)
        kaspi_analyzed_rows = [
            row for row in kaspi_rows
            if str(row.get("price_status") or "NOT_ANALYZED") not in UNSCANNED_STATUSES
        ]
        ozon_ready_rows = [
            row for row in ozon_rows
            if str(row.get("price_status") or "NOT_ANALYZED") in OZON_READY_STATUSES
        ]
        halyk_analyzed_rows = [
            row for row in halyk_rows
            if str(row.get("price_status") or "NOT_ANALYZED") not in UNSCANNED_STATUSES
        ]
        data_ready_count = len(kaspi_analyzed_rows) + len(halyk_analyzed_rows) + len(ozon_ready_rows)
        risk_count = sum(counts[key] for key in RISK_STATUSES)
        opportunity_count = sum(counts[key] for key in OPPORTUNITY_STATUSES)
        potential_rows = [
            row for row in exact_rows
            if float(row.get("potential_margin_monthly_kzt") or 0) > 0
        ]
        potential_monthly = sum(
            float(row.get("potential_margin_monthly_kzt") or 0) for row in potential_rows
        )
        potential_units_total = sum(
            int(row.get("expected_monthly_units") or 0) for row in potential_rows
        )
        conn = self._connect()
        try:
            recent_events = [dict(row) for row in conn.execute(
                """SELECT e.event_type,e.entity_type,e.entity_id,e.created_at,u.display_name
                   FROM app_events e LEFT JOIN app_users u ON u.id=e.user_id
                   ORDER BY e.id DESC LIMIT 8""").fetchall()]
            last_own_price = conn.execute(
                "SELECT MAX(captured_at) FROM own_price_snapshots WHERE status='ok'"
            ).fetchone()[0]
            latest_catalog_sync = conn.execute(
                """
                SELECT status,reported_total,collected_unique,segments_total,segments_success,
                       warning,started_at,finished_at
                FROM catalog_sync_runs ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        latest_catalog_sync_value = dict(latest_catalog_sync) if latest_catalog_sync else {}
        reported_total = int(latest_catalog_sync_value.get("reported_total") or 0)
        effective_expected = reported_total or int(expected_count or 0)
        status_distribution = [{
            "status": key,
            "label": STATUS_INFO.get(key, {}).get("label", key),
            "tone": STATUS_INFO.get(key, {}).get("tone", "neutral"),
            "count": int(value),
        } for key, value in counts.most_common()]
        return {
            "catalog_count": active_count,
            "kaspi_count": len(kaspi_rows),
            "ozon_count": len(ozon_rows),
            "halyk_count": len(halyk_rows),
            "expected_count": effective_expected,
            "catalog_coverage_pct": round(len(kaspi_rows) / effective_expected * 100, 2) if effective_expected else None,
            "catalog_sync": latest_catalog_sync_value,
            "priced_count": priced_count,
            "price_coverage_pct": round(priced_count / active_count * 100, 2) if active_count else 0,
            # Backward-compatible aliases now represent combined data readiness.
            "scanned_count": data_ready_count,
            "scan_coverage_pct": round(data_ready_count / active_count * 100, 2) if active_count else 0,
            "data_ready_count": data_ready_count,
            "data_coverage_pct": round(data_ready_count / active_count * 100, 2) if active_count else 0,
            "kaspi_market_analyzed_count": len(kaspi_analyzed_rows),
            "kaspi_market_coverage_pct": round(len(kaspi_analyzed_rows) / len(kaspi_rows) * 100, 2) if kaspi_rows else 0,
            "ozon_data_ready_count": len(ozon_ready_rows),
            "ozon_data_coverage_pct": round(len(ozon_ready_rows) / len(ozon_rows) * 100, 2) if ozon_rows else 0,
            "halyk_market_analyzed_count": len(halyk_analyzed_rows),
            "halyk_market_coverage_pct": round(len(halyk_analyzed_rows) / len(halyk_rows) * 100, 2) if halyk_rows else 0,
            "watched_count": sum(1 for row in rows if row.get("watched")),
            "risk_count": risk_count,
            "favorable_count": opportunity_count,
            "opportunity_count": opportunity_count,
            "potential_position_count": len(potential_rows),
            "potential_units_total": potential_units_total,
            "price_potential_monthly_kzt": round(potential_monthly, 2),
            "potential_margin_monthly_kzt": round(potential_monthly, 2),
            "potential_basis": "KASPI_EXACT_Q1_MINUS_OWN_PRICE",
            "status_distribution": status_distribution,
            "snapshot": snapshot,
            "last_own_price_at": last_own_price,
            "recent_events": recent_events,
            "preferences": preferences,
            "health": {
                "catalog": (
                    "ok" if exact_rows and (not effective_expected or len(kaspi_rows) >= effective_expected * 0.97)
                    else "warning" if exact_rows else "empty"
                ),
                "prices": "ok" if priced_count >= max(1, active_count * 0.9) else ("warning" if priced_count else "empty"),
                "market": "ok" if data_ready_count >= max(1, active_count * 0.9) else ("warning" if data_ready_count else "empty"),
            },
        }

    def analytics_dashboard(
        self,
        user_id: int | None = None,
        allowed_platforms: set[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preferences = self.preferences(user_id)
        active_filters = filters or {}
        rows = [
            self._apply_user_values(row, preferences)
            for row in self.rows()
            if self._matches(row, active_filters)
        ]
        if allowed_platforms is not None:
            rows = [row for row in rows if str(row.get("platform")) in allowed_platforms]
        kaspi_rows = [row for row in rows if row.get("platform") == "kaspi"]
        ozon_rows = [row for row in rows if row.get("platform") == "ozon"]
        halyk_rows = [row for row in rows if row.get("platform") == "halyk_market"]
        exact_rows = kaspi_rows + halyk_rows

        status_counts = Counter(str(row.get("price_status") or "NOT_ANALYZED") for row in exact_rows)
        analyzed_rows = [row for row in exact_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]
        ozon_ready_rows = [
            row for row in ozon_rows
            if str(row.get("price_status") or "NOT_ANALYZED") in OZON_READY_STATUSES
        ]
        combined_ready_count = len(analyzed_rows) + len(ozon_ready_rows)
        risk_rows = [row for row in exact_rows if str(row.get("price_status") or "") in RISK_STATUSES]
        opportunity_rows = [row for row in exact_rows if str(row.get("price_status") or "") in OPPORTUNITY_STATUSES]
        review_rows = [row for row in exact_rows if str(row.get("price_status") or "") == "REVIEW_REQUIRED"]
        insufficient_rows = [row for row in exact_rows if str(row.get("price_status") or "") == "INSUFFICIENT_DATA"]

        valid_deltas = [float(row.get("difference_pct")) for row in analyzed_rows if row.get("difference_pct") is not None]
        average_delta = round(sum(valid_deltas) / len(valid_deltas), 2) if valid_deltas else 0.0
        potential_rows = [
            row for row in opportunity_rows
            if float(row.get("potential_margin_monthly_kzt") or 0) > 0
        ]
        potential_total = round(sum(float(row.get("potential_margin_monthly_kzt") or 0) for row in potential_rows), 2)
        potential_units_total = sum(int(row.get("expected_monthly_units") or 0) for row in potential_rows)

        brands: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "brand": "Без бренда", "total": 0, "risks": 0, "opportunities": 0,
            "review": 0, "potential_margin_monthly_kzt": 0.0,
        })
        for row in exact_rows:
            brand = str(row.get("brand") or "Без бренда").strip() or "Без бренда"
            item = brands[brand]
            item["brand"] = brand
            item["total"] += 1
            status = str(row.get("price_status") or "")
            if status in RISK_STATUSES:
                item["risks"] += 1
            if status in OPPORTUNITY_STATUSES:
                item["opportunities"] += 1
                item["potential_margin_monthly_kzt"] += float(row.get("potential_margin_monthly_kzt") or 0)
            if status in UNSCANNED_STATUSES:
                item["review"] += 1
        brand_rows = sorted(
            ({**value, "potential_margin_monthly_kzt": round(float(value["potential_margin_monthly_kzt"]), 2)} for value in brands.values()),
            key=lambda value: (value["risks"], value["review"], value["total"]),
            reverse=True,
        )[:12]

        def compact(row: dict[str, Any]) -> dict[str, Any]:
            return {
                "product_code": row.get("product_code"),
                "title": row.get("title"),
                "brand": row.get("brand"),
                "size": row.get("size"),
                "status": row.get("price_status"),
                "status_label": row.get("status_label"),
                "status_tone": row.get("status_tone"),
                "current_price_kzt": row.get("own_price_kzt") or row.get("price_kzt"),
                "market_median_price_kzt": row.get("market_median_price_kzt"),
                "difference_kzt": row.get("difference_kzt"),
                "difference_pct": row.get("difference_pct"),
                "potential_margin_per_unit_kzt": row.get("potential_margin_per_unit_kzt"),
                "potential_margin_monthly_kzt": row.get("potential_margin_monthly_kzt"),
                "product_url": row.get("product_url"),
                "image_url": row.get("image_url"),
            }

        top_risks = sorted(
            risk_rows,
            key=lambda row: (float(row.get("difference_pct") or 0), float(row.get("difference_kzt") or 0)),
            reverse=True,
        )[:10]
        top_opportunities = sorted(
            opportunity_rows,
            key=lambda row: (float(row.get("potential_margin_monthly_kzt") or 0), float(row.get("potential_margin_per_unit_kzt") or 0)),
            reverse=True,
        )[:10]

        platform_rows = []
        for name, values in (("Kaspi", kaspi_rows), ("Ozon", ozon_rows), ("Halyk Market", halyk_rows)):
            priced = sum(1 for row in values if row.get("price_kzt") is not None or row.get("own_price_kzt") is not None)
            platform_rows.append({
                "platform": name,
                "total": len(values),
                "priced": priced,
                "coverage_pct": round(priced / len(values) * 100, 2) if values else 0.0,
            })

        price_bands = [
            {"label": "до 50 000 ₸", "count": 0},
            {"label": "50–100 тыс. ₸", "count": 0},
            {"label": "100–150 тыс. ₸", "count": 0},
            {"label": "свыше 150 тыс. ₸", "count": 0},
        ]
        for row in exact_rows:
            price = float(row.get("own_price_kzt") or 0)
            if price <= 0:
                continue
            if price < 50_000:
                price_bands[0]["count"] += 1
            elif price < 100_000:
                price_bands[1]["count"] += 1
            elif price < 150_000:
                price_bands[2]["count"] += 1
            else:
                price_bands[3]["count"] += 1

        status_distribution = [
            {
                "status": status,
                "label": STATUS_INFO.get(status, {}).get("label", status),
                "tone": STATUS_INFO.get(status, {}).get("tone", "neutral"),
                "count": int(count),
            }
            for status, count in status_counts.most_common()
        ]

        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kpis": {
                "total_products": len(rows),
                "kaspi_products": len(kaspi_rows),
                "ozon_products": len(ozon_rows),
                "halyk_products": len(halyk_rows),
                "analyzed_count": combined_ready_count,
                "analysis_coverage_pct": round(combined_ready_count / len(rows) * 100, 2) if rows else 0.0,
                "data_ready_count": combined_ready_count,
                "data_coverage_pct": round(combined_ready_count / len(rows) * 100, 2) if rows else 0.0,
                "kaspi_market_analyzed_count": len(analyzed_rows),
                "kaspi_market_coverage_pct": round(len(analyzed_rows) / len(kaspi_rows) * 100, 2) if kaspi_rows else 0.0,
                "ozon_data_ready_count": len(ozon_ready_rows),
                "ozon_data_coverage_pct": round(len(ozon_ready_rows) / len(ozon_rows) * 100, 2) if ozon_rows else 0.0,
                "halyk_market_analyzed_count": len([row for row in halyk_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]),
                "halyk_market_coverage_pct": round(len([row for row in halyk_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]) / len(halyk_rows) * 100, 2) if halyk_rows else 0.0,
                "risk_count": len(risk_rows),
                "opportunity_count": len(opportunity_rows),
                "review_count": len(review_rows),
                "insufficient_count": len(insufficient_rows),
                "potential_margin_monthly_kzt": potential_total,
                "price_potential_monthly_kzt": potential_total,
                "potential_position_count": len(potential_rows),
                "potential_units_total": potential_units_total,
                "potential_basis": "KASPI_EXACT_Q1_MINUS_OWN_PRICE",
                "average_delta_pct": average_delta,
            },
            "status_distribution": status_distribution,
            "platforms": platform_rows,
            "brand_risks": brand_rows,
            "price_bands": price_bands,
            "top_risks": [compact(row) for row in top_risks],
            "top_opportunities": [compact(row) for row in top_opportunities],
        }

    @staticmethod
    def _filter_set(value: Any, *, upper: bool = False) -> set[str]:
        if isinstance(value, (list, tuple, set)):
            raw_values = value
        else:
            raw_values = str(value or "").split(",")
        result: set[str] = set()
        for raw_value in raw_values:
            item = str(raw_value or "").strip()
            if not item:
                continue
            result.add(item.upper() if upper else item.casefold())
        return result

    @staticmethod
    def _matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
        query = str(filters.get("query") or "").strip().casefold()
        if query:
            haystack = " ".join(str(row.get(key) or "") for key in (
                "product_code", "source_product_code", "title", "brand", "model", "size", "seller_name"
            )).casefold()
            if query not in haystack:
                return False
        platform = str(filters.get("platform") or "").strip().casefold()
        raw_platforms = filters.get("platforms")
        if isinstance(raw_platforms, (list, tuple, set)):
            platforms = {str(value or "").strip().casefold() for value in raw_platforms if str(value or "").strip()}
        else:
            platforms = {value.strip().casefold() for value in str(raw_platforms or "").split(",") if value.strip()}
        row_platform = str(row.get("platform") or "").casefold()
        if platform and row_platform != platform:
            return False
        if platforms and row_platform not in platforms:
            return False
        brands = DataService._filter_set(filters.get("brand"))
        if brands and str(row.get("brand") or "").casefold() not in brands:
            return False
        statuses = DataService._filter_set(filters.get("status"), upper=True)
        if statuses and str(row.get("price_status") or "").upper() not in statuses:
            return False
        freshness_values = DataService._filter_set(filters.get("freshness"))
        if freshness_values and str(row.get("freshness_status") or "never").casefold() not in freshness_values:
            return False
        product_types = DataService._filter_set(filters.get("product_type"))
        if product_types and str(row.get("product_type") or "other").casefold() not in product_types:
            return False
        sizes = DataService._filter_set(filters.get("size"))
        if sizes and str(row.get("size") or "").strip().casefold() not in sizes:
            return False
        seasons = DataService._filter_set(filters.get("season"), upper=True)
        if seasons and str(row.get("season") or "UNKNOWN").upper() not in seasons:
            return False
        characteristic_groups = DataService._filter_set(filters.get("characteristic_group"))
        if characteristic_groups and str(row.get("characteristic_group") or "").casefold() not in characteristic_groups:
            return False
        watched = str(filters.get("watched") or "").strip().casefold()
        if watched == "yes" and not row.get("watched"):
            return False
        if watched == "no" and row.get("watched"):
            return False
        scope = str(filters.get("scope") or "all").strip().casefold()
        status_value = str(row.get("price_status") or "")
        if scope == "risks" and status_value not in RISK_STATUSES:
            return False
        if scope == "unscanned" and status_value not in UNSCANNED_STATUSES:
            return False
        if scope == "opportunities" and status_value not in OPPORTUNITY_STATUSES:
            return False
        if scope == "watched" and not row.get("watched"):
            return False
        return True

    def products(self, page: int, page_size: int, filters: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
        preferences = self.preferences(user_id)
        rows = [self._apply_user_values(row, preferences) for row in self.rows() if self._matches(row, filters)]
        sort_name = str(filters.get("sort") or "updated")
        sort_field = SORT_FIELDS.get(sort_name, "_updated_sort")
        reverse = str(filters.get("direction") or "desc").casefold() != "asc"
        rows.sort(key=lambda row: (row.get(sort_field) is not None, row.get(sort_field) or "", row.get("title") or ""), reverse=reverse)
        total = len(rows)
        page_size = max(10, min(int(page_size), 200))
        pages = max(1, math.ceil(total / page_size))
        page = max(1, min(int(page), pages))
        start = (page - 1) * page_size
        fields = {
            "product_code", "source_product_code", "platform", "platform_label", "title", "product_url",
            "brand", "model", "size", "product_type", "product_type_label",
            "tire_width", "tire_profile", "tire_diameter", "load_index", "speed_index",
            "season", "season_label", "studded", "runflat", "commercial", "offroad",
            "characteristic_group", "characteristic_group_label", "exact_characteristic_key",
            "characteristic_completeness_percent",
            "own_price_kzt", "market_price_kzt", "price_kzt",
            "price_original", "currency_original", "difference_kzt", "difference_pct", "price_status",
            "status_label", "status_tone", "reference_type", "reference_count", "market_min_price_kzt",
            "market_max_price_kzt", "market_median_price_kzt", "market_min_price_original", "market_median_price_original", "market_max_price_original",
            "lowest_product_title", "lowest_product_price_kzt", "lowest_product_url",
            "highest_product_title", "highest_product_price_kzt", "highest_product_url",
            "price_rank", "price_rank_total", "potential_margin_per_unit_kzt", "potential_margin_monthly_kzt",
            "expected_monthly_units", "watched", "priority", "note", "catalog_rating", "catalog_reviews",
            "image_url", "seller_name", "seller_url", "identity_completeness_percent", "candidate_count",
            "match_method", "match_method_label", "exact_offer_status", "exact_offer_checked_at",
            "exact_offer_count", "competitor_seller_count", "legacy_candidate_count",
            "updated_at", "freshness_status", "freshness_label", "source_type",
            "_updated_sort", "raw_price_status",
        }
        items = [{key: row.get(key) for key in fields} for row in rows[start:start + page_size]]
        return {"items": items, "page": page, "pages": pages, "page_size": page_size, "total": total}

    def product_codes(self, filters: dict[str, Any], limit: int = 10000) -> list[str]:
        return [str(row["product_code"]) for row in self.rows() if self._matches(row, filters)][:max(1, min(int(limit), 10000))]

    def filter_options(self, allowed_platforms: set[str] | None = None) -> dict[str, Any]:
        rows = self.rows()
        if allowed_platforms is not None:
            rows = [row for row in rows if str(row.get("platform")) in allowed_platforms]
        brands = sorted({str(row.get("brand") or "").strip() for row in rows if str(row.get("brand") or "").strip()}, key=str.casefold)
        statuses = sorted({str(row.get("price_status") or "NOT_ANALYZED") for row in rows})
        sizes = sorted(
            {str(row.get("size") or "").strip() for row in rows if str(row.get("size") or "").strip()},
            key=lambda value: tuple(float(part) if part.replace(".", "", 1).isdigit() else part for part in re.split(r"[ /R]+", value)),
        )
        product_types = sorted(
            {str(row.get("product_type") or "other") for row in rows},
            key=lambda value: PRODUCT_TYPE_LABELS.get(value, value),
        )
        seasons = [
            value for value in ("SUMMER", "WINTER", "ALL_SEASON", "UNKNOWN")
            if any(str(row.get("season") or "UNKNOWN") == value for row in rows)
        ]
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("characteristic_group") or "").strip()
            if not key or not str(row.get("size") or "").strip():
                continue
            item = grouped.setdefault(key, {
                "value": key,
                "label": str(row.get("characteristic_group_label") or key),
                "count": 0,
                "platforms": set(),
            })
            item["count"] += 1
            item["platforms"].add(str(row.get("platform") or ""))
        characteristic_groups = []
        for item in grouped.values():
            platform_count = len(item["platforms"])
            if platform_count < 2:
                continue
            characteristic_groups.append({
                "value": item["value"],
                "label": item["label"],
                "count": item["count"],
                "platform_count": platform_count,
            })
        characteristic_groups.sort(key=lambda item: (-int(item["platform_count"]), -int(item["count"]), str(item["label"])))
        return {
            "brands": brands,
            "sizes": sizes,
            "product_types": [{"value": value, "label": PRODUCT_TYPE_LABELS.get(value, value)} for value in product_types],
            "seasons": [{"value": value, "label": SEASON_LABELS.get(value, value)} for value in seasons],
            "characteristic_groups": characteristic_groups[:400],
            "platforms": [
                item for item in [
                    {"value": "kaspi", "label": "Kaspi"},
                    {"value": "ozon", "label": "Ozon"},
                    {"value": "halyk_market", "label": "Halyk Market"},
                ] if allowed_platforms is None or item["value"] in allowed_platforms
            ],
            "statuses": [{"value": status, "label": STATUS_INFO.get(status, {}).get("label", status), "tone": STATUS_INFO.get(status, {}).get("tone", "neutral")} for status in statuses],
        }

    def product(self, code: str, user_id: int | None = None) -> dict[str, Any] | None:
        base = next((row for row in self.rows() if str(row.get("product_code")) == str(code)), None)
        if base is None:
            return None
        result = self._apply_user_values(base, self.preferences(user_id))
        result.pop("_price_sort", None); result.pop("_delta_sort", None); result.pop("_updated_sort", None)
        if result.get("platform") == "halyk_market":
            product_id = str(result.get("source_product_code") or "").removeprefix("halyk:")
            conn = self._connect()
            try:
                detail = conn.execute("SELECT * FROM halyk_products WHERE product_id=?", (product_id,)).fetchone()
                offers = conn.execute(
                    """
                    SELECT merchant_name,merchant_key,price_kzt,offer_type,is_own,last_checked_at
                    FROM halyk_offers
                    WHERE product_id=? AND active=1
                    ORDER BY CASE WHEN is_own=1 THEN 0 ELSE 1 END,price_kzt,merchant_name
                    LIMIT 100
                    """,
                    (product_id,),
                ).fetchall()
            finally:
                conn.close()
            result["specifications"] = normalize_specifications(detail["specs_json"] if detail else [])
            result["detail"] = dict(detail) if detail else None
            result["candidates"] = result.get("exact_candidates") or []
            result["offers"] = [
                {
                    **dict(row),
                    "merchant_id": row["merchant_key"],
                    "merchant_rating": None,
                    "captured_at": row["last_checked_at"],
                    "product_url": result.get("product_url") or "",
                    "match_method": "HALYK_PRODUCT_ID",
                    "match_method_label": "Та же карточка Halyk Market",
                }
                for row in offers
            ]
            result["history"] = self.price_history(code, user_id=user_id)
            return result
        if result.get("platform") == "ozon":
            result["specifications"] = self._ozon_specifications(result)
            rate = float(self.preferences(user_id).get("rub_to_kzt") or 5.5)
            offers = []
            for candidate in [*(result.get("exact_candidates") or []), *(result.get("comparable_candidates") or [])]:
                value = dict(candidate)
                value["price_kzt"] = round(float(value.get("price_rub") or 0) * rate, 2)
                offers.append(value)
            result["candidates"] = offers
            result["offers"] = offers
            result["history"] = self.price_history(code, user_id=user_id)
            return result
        raw_code = str(result.get("source_product_code") or code)
        conn = self._connect()
        try:
            detail = conn.execute("SELECT * FROM product_details WHERE product_code=?", (raw_code,)).fetchone()
            offers = []
            try:
                offers = conn.execute(
                    """SELECT candidate_product_code,merchant_name,merchant_id,merchant_sku,price_kzt,
                              merchant_rating,merchant_reviews,captured_at
                       FROM market_seller_offers
                       WHERE source_product_code=? AND candidate_product_code=?
                       ORDER BY price_kzt ASC,merchant_name ASC LIMIT 100""",
                    (raw_code, raw_code),
                ).fetchall()
            except sqlite3.OperationalError:
                pass
        finally:
            conn.close()
        result["specifications"] = normalize_specifications(detail["specifications_json"] if detail else [])
        result["detail"] = dict(detail) if detail else None
        result["candidates"] = result.get("exact_candidates") or []
        result["offers"] = [
            {
                **dict(row),
                "is_own": self._is_own_exact_offer(dict(row)),
                "product_url": result.get("product_url") or "",
                "match_method": "KASPI_PRODUCT_CODE",
            }
            for row in offers
        ]
        result["history"] = self.price_history(code, user_id=user_id)
        return result

    @staticmethod
    def _ozon_specifications(row: dict[str, Any]) -> list[dict[str, str]]:
        mapping = [
            ("Бренд", row.get("brand")), ("Модель", row.get("model")), ("Размер", row.get("size")),
            ("Артикул производителя", row.get("manufacturer_article")), ("Индекс нагрузки", row.get("load_index")),
            ("Индекс скорости", row.get("speed_index")), ("Сезон", row.get("season")),
            ("Шипы", "Да" if row.get("studded") == 1 else "Нет" if row.get("studded") == 0 else "Не указано"),
            ("XL", "Да" if row.get("xl") else "Нет"), ("RunFlat", "Да" if row.get("runflat") else "Нет"),
        ]
        return [{"section": "Основные", "name": name, "value": str(value)} for name, value in mapping if value not in (None, "")]

    def _ozon_offers(self, article: str) -> list[dict[str, Any]]:
        if not self.ozon_db_path or not self.ozon_db_path.exists():
            return []
        conn = self._connect_path(self.ozon_db_path)
        try:
            return [dict(row) for row in conn.execute("SELECT * FROM offers WHERE article=? ORDER BY card_price", (article,)).fetchall()]
        finally:
            conn.close()

    def price_history(self, code: str, limit: int = 120, user_id: int | None = None) -> list[dict[str, Any]]:
        if str(code).startswith("halyk:"):
            product_id = str(code).split(":", 1)[1]
            conn = self._connect()
            try:
                own = conn.execute(
                    """
                    SELECT captured_at AS at,price_kzt AS price,'own' AS series
                    FROM halyk_price_history
                    WHERE product_id=? AND is_own=1 AND price_kzt IS NOT NULL
                    ORDER BY id DESC LIMIT ?
                    """,
                    (product_id, int(limit)),
                ).fetchall()
                market = conn.execute(
                    """
                    SELECT captured_at AS at,MIN(price_kzt) AS price,'market' AS series
                    FROM halyk_price_history
                    WHERE product_id=? AND is_own=0 AND price_kzt IS NOT NULL
                    GROUP BY run_id,captured_at ORDER BY captured_at DESC LIMIT ?
                    """,
                    (product_id, int(limit)),
                ).fetchall()
                return sorted([dict(row) for row in own] + [dict(row) for row in market], key=lambda item: item.get("at") or "")
            finally:
                conn.close()
        if str(code).startswith("ozon:"):
            article = str(code).split(":", 1)[1]
            if not self.ozon_db_path or not self.ozon_db_path.exists():
                return []
            rate = float(self.preferences(user_id).get("rub_to_kzt") or 5.5)
            conn = self._connect_path(self.ozon_db_path)
            try:
                rows = conn.execute(
                    "SELECT collected_at AS at,card_price AS price,'ozon' AS series,currency FROM price_history WHERE article=? ORDER BY id DESC LIMIT ?",
                    (article, int(limit)),
                ).fetchall()
                return sorted([{**dict(row), "price_kzt": round(float(row["price"] or 0) * rate, 2)} for row in rows], key=lambda x: x.get("at") or "")
            finally:
                conn.close()
        raw_code = str(code).removeprefix("kaspi:")
        conn = self._connect()
        try:
            own = conn.execute(
                "SELECT captured_at AS at,price_kzt AS price,'own' AS series FROM own_price_snapshots "
                "WHERE product_code=? AND status='ok' AND price_kzt IS NOT NULL ORDER BY id DESC LIMIT ?",
                (raw_code, int(limit)),
            ).fetchall()
            market = []
            try:
                market = conn.execute(
                    "SELECT captured_at AS at,MIN(price_kzt) AS price,'market' AS series "
                    "FROM exact_offer_snapshots "
                    "WHERE product_code=? AND is_own=0 AND price_kzt IS NOT NULL "
                    "GROUP BY run_id,captured_at ORDER BY captured_at DESC LIMIT ?",
                    (raw_code, int(limit)),
                ).fetchall()
            except sqlite3.OperationalError:
                pass
            return sorted([dict(row) for row in own] + [dict(row) for row in market], key=lambda item: item.get("at") or "")
        finally:
            conn.close()

    def set_product_state(self, codes: list[str], watched: bool | None, priority: str | None, note: str | None, user_id: int, expected_monthly_units: int | None = None) -> int:
        clean_codes = sorted({str(code).strip() for code in codes if str(code).strip() and not str(code).startswith("ozon:")})
        if not clean_codes:
            return 0
        if priority is not None and priority not in {"low", "normal", "high", "critical"}:
            raise ValueError("Неизвестный приоритет.")
        conn = self._connect()
        try:
            for code in clean_codes:
                raw_code = code if code.startswith("halyk:") else code.removeprefix("kaspi:")
                current = conn.execute("SELECT watched,priority,note,expected_monthly_units FROM app_product_state WHERE product_code=?", (raw_code,)).fetchone()
                current_values = dict(current) if current else {"watched": 0, "priority": "normal", "note": "", "expected_monthly_units": None}
                units = current_values.get("expected_monthly_units") if expected_monthly_units is None else max(0, int(expected_monthly_units))
                conn.execute(
                    """INSERT INTO app_product_state(product_code,watched,priority,note,expected_monthly_units,updated_by,updated_at)
                       VALUES(?,?,?,?,?,?,datetime('now'))
                       ON CONFLICT(product_code) DO UPDATE SET watched=excluded.watched,priority=excluded.priority,
                           note=excluded.note,expected_monthly_units=excluded.expected_monthly_units,
                           updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (raw_code, int(watched if watched is not None else bool(current_values.get("watched"))),
                     priority if priority is not None else current_values.get("priority") or "normal",
                     note if note is not None else current_values.get("note") or "", units, int(user_id)),
                )
            conn.execute(
                "INSERT INTO app_events(user_id,event_type,entity_type,entity_id,details_json,created_at) VALUES(?,?,?,?,?,datetime('now'))",
                (int(user_id), "product_state_updated", "product_set", str(len(clean_codes)), json.dumps({"codes": clean_codes[:100], "watched": watched, "priority": priority, "expected_monthly_units": expected_monthly_units}, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()
        self.invalidate()
        return len(clean_codes)

    def latest_events(self, limit: int = 40, tenant_id: int | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            where = ""
            params: list[Any] = []
            if tenant_id is not None:
                where = (
                    "WHERE e.tenant_id=? OR "
                    "(e.tenant_id IS NULL AND ?=(SELECT id FROM tenants ORDER BY id LIMIT 1))"
                )
                params.extend([int(tenant_id), int(tenant_id)])
            params.append(max(1, min(int(limit), 200)))
            rows = conn.execute(
                f"""SELECT e.id,e.event_type,e.entity_type,e.entity_id,e.details_json,e.created_at,u.display_name,u.email
                   FROM app_events e LEFT JOIN app_users u ON u.id=e.user_id
                   {where}
                   ORDER BY e.id DESC LIMIT ?""",
                params,
            ).fetchall()
            result = []
            for row in rows:
                value = dict(row); value["details"] = self._json(value.pop("details_json", ""), {}); result.append(value)
            return result
        finally:
            conn.close()
