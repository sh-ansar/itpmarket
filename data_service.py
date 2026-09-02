from __future__ import annotations

import json
import math
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
import statistics
from typing import Any
from urllib.parse import urlencode, urljoin
from storage.database_backend import DatabaseBackend, DatabaseSettings
from storage.postgres_compat import PostgresConnection, configure_connection, connect_database, database_error_types, table_exists

from engine.kaspi_market_v9_1 import Database, enriched_comparison_rows, status_snapshot
from collectors.wildberries.wildberries_collector import image_url_for_article
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
from marketplace_registry import seller_scoped_product_code, parse_product_code

HALYK_BASE_URL = "https://halykmarket.kz"
FORTE_BASE_URL = "https://market.forte.kz"

SORT_FIELDS = {
    "updated": "_updated_sort",
    "title": "title",
    "price": "_price_sort",
    "delta": "_delta_sort",
    "status": "price_status",
    "brand": "brand",
    "platform": "platform",
}

RISK_STATUSES = {
    "EXACT_ABOVE", "EXACT_HIGHEST", "EXACT_TIED_HIGHEST",
    "EXACT_BELOW", "DATA_ERROR",
}
OPPORTUNITY_STATUSES = {"EXACT_LOWEST", "EXACT_TIED_LOWEST"}
UNSCANNED_STATUSES = {"NOT_ANALYZED", "INSUFFICIENT_DATA", "REVIEW_REQUIRED"}
TENANT_SNAPSHOT_CACHE_TTL_SECONDS = 2.0
TENANT_SNAPSHOT_CACHE_MAX_KEYS = 128
OZON_READY_STATUSES = {
    "DATA_COLLECTED", "NO_OTHER_SELLERS", "EXACT_LOWEST", "EXACT_BELOW",
    "EXACT_TIED_LOWEST", "EXACT_IN_MARKET", "EXACT_ABOVE", "EXACT_HIGHEST",
    "EXACT_TIED_HIGHEST", "NO_OTHER_SELLERS",
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
        forte_seller_name: str = "Unityre",
        ozon_kz_db_path: Path | None = None,
    ):
        self.db_path = Path(db_path)
        self.seller_name = seller_name
        self.seller_id = seller_id
        self.ozon_db_path = Path(ozon_db_path) if ozon_db_path else None
        self.halyk_seller_name = halyk_seller_name
        self.forte_seller_name = forte_seller_name
        self.ozon_kz_db_path = Path(ozon_kz_db_path) if ozon_kz_db_path else None
        self.lock = threading.RLock()
        self._rows_cache: list[dict[str, Any]] = []
        self._rows_cached_at = 0.0
        self._rows_signature: tuple[int, ...] | None = None
        self._rows_refreshing = False
        self._cache_generation = 0
        self._tenant_snapshot_cache: dict[
            tuple[int, tuple[str, ...]], tuple[float, list[dict[str, Any]]]
        ] = {}
        self._tenant_snapshot_locks: dict[
            tuple[int, tuple[str, ...]], threading.Lock
        ] = {}
        ensure_database(self.db_path)
        # The legacy Kaspi Database wrapper validates a local SQLite file.
        # PostgreSQL deployments deliberately have no such file in a clean
        # Git clone, and all runtime access is routed through connect_database.
        if DatabaseSettings.from_environment().backend is DatabaseBackend.SQLITE:
            db = Database(self.db_path)
            db.conn.close()

    def _connect(self) -> Any:
        conn = connect_database(self.db_path, timeout=30)
        return configure_connection(conn, foreign_keys=True, busy_timeout=30000)

    @staticmethod
    def _connect_path(path: Path) -> Any:
        conn = connect_database(path, timeout=30)
        return configure_connection(conn, busy_timeout=30000)

    def invalidate(self) -> None:
        with self.lock:
            self._cache_generation += 1
            self._rows_cached_at = 0.0
            self._rows_cache = []
            self._rows_signature = None
            self._rows_refreshing = False
            self._tenant_snapshot_cache.clear()
            self._tenant_snapshot_locks.clear()

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

    @staticmethod
    def _forte_public_url(product_id: Any, slug: Any = "", stored_url: Any = "") -> str:
        stored = str(stored_url or "").strip()
        if stored:
            return urljoin(FORTE_BASE_URL, stored)
        route = str(slug or "").strip() or str(product_id or "").strip()
        return f"{FORTE_BASE_URL}/items/{route}" if route else ""

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
                    "INSERT OR IGNORE INTO app_user_preferences(user_id,updated_at) VALUES(?,datetime('now'))", (int(user_id),)
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

    def _is_own_exact_offer(
        self,
        row: dict[str, Any],
        seller_id: str | None = None,
        seller_name: str | None = None,
    ) -> bool:
        merchant_id = self._normalized_seller(row.get("merchant_id"))
        merchant_name = self._normalized_seller(row.get("merchant_name"))
        seller_id = self._normalized_seller(
            self.seller_id if seller_id is None else seller_id
        )
        seller_name = self._normalized_seller(
            self.seller_name if seller_name is None else seller_name
        )
        return bool(
            (seller_id and merchant_id == seller_id)
            or (seller_name and merchant_name == seller_name)
        )

    def _load_kaspi_support_data(
        self,
        product_codes: list[str] | None = None,
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, list[dict[str, Any]]],
        dict[str, dict[str, Any]],
        dict[str, int],
    ]:
        requested_codes = [str(value).strip() for value in (product_codes or []) if str(value).strip()]
        if product_codes is not None and not requested_codes:
            return {}, {"details": {}, "extras": {}}, defaultdict(list), {}, {}
        placeholders = ",".join("?" for _ in requested_codes)
        params: list[Any] = requested_codes
        product_where = f" WHERE product_code IN ({placeholders})" if product_codes is not None else ""
        conn = self._connect()
        try:
            # Tenant-specific state is overlaid after the shared row cache is
            # built. Caching it here would leak notes between organizations.
            states: dict[str, dict[str, Any]] = {}
            details = {
                str(row["product_code"]): dict(row)
                for row in conn.execute(
                    f"SELECT product_code,title_detail,specifications_json,detail_status,detail_error,detail_collected_at FROM product_details{product_where}",
                    params,
                ).fetchall()
            }
            extras = {
                str(row["product_code"]): dict(row)
                for row in conn.execute(
                    f"""
                    SELECT c.product_code,c.image_url,m.stock,m.discount_percent,
                           m.price_before_discount_kzt,m.source_segment,m.active
                    FROM catalog_products c
                    LEFT JOIN catalog_product_meta m ON m.product_code=c.product_code
                    {product_where.replace('product_code', 'c.product_code')}
                    """,
                    params,
                ).fetchall()
            }
            exact_offers: dict[str, list[dict[str, Any]]] = defaultdict(list)
            try:
                for row in conn.execute(
                    f"""
                    SELECT source_product_code,candidate_product_code,merchant_id,merchant_name,
                           merchant_sku,price_kzt,merchant_rating,merchant_reviews,captured_at
                    FROM market_seller_offers
                    WHERE source_product_code=candidate_product_code AND price_kzt IS NOT NULL
                    {('AND source_product_code IN (' + placeholders + ')') if product_codes is not None else ''}
                    ORDER BY source_product_code,price_kzt,merchant_name
                    """,
                    params,
                ).fetchall():
                    value = dict(row)
                    exact_offers[str(value["source_product_code"])].append(value)
            except database_error_types():
                pass
            exact_scans: dict[str, dict[str, Any]] = {}
            try:
                exact_scans = {
                    str(row["product_code"]): dict(row)
                    for row in conn.execute(
                        f"SELECT * FROM exact_offer_scans{product_where}", params
                    ).fetchall()
                }
            except database_error_types():
                pass
            legacy_counts: dict[str, int] = {}
            try:
                legacy_counts = {
                    str(row["source_product_code"]): int(row["count_value"] or 0)
                    for row in conn.execute(
                        f"""
                        SELECT source_product_code,COUNT(*) AS count_value
                        FROM market_candidates
                        WHERE candidate_product_code<>source_product_code
                        {('AND source_product_code IN (' + placeholders + ')') if product_codes is not None else ''}
                        GROUP BY source_product_code
                        """,
                        params,
                    ).fetchall()
                }
            except database_error_types():
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
        seller_id: str | None = None,
        seller_name: str | None = None,
    ) -> list[Candidate]:
        selected: dict[str, dict[str, Any]] = {}
        for row in offers:
            if self._is_own_exact_offer(row, seller_id, seller_name):
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

    def _kaspi_rows(
        self,
        product_codes: list[str] | None = None,
        *,
        seller_id: str | None = None,
        seller_name: str | None = None,
        seller_url: str = "",
    ) -> list[dict[str, Any]]:
        own_seller_id = self.seller_id if seller_id is None else str(seller_id or "")
        own_seller_name = self.seller_name if seller_name is None else str(seller_name or "")
        seller_match = own_seller_id or own_seller_name
        db = Database(self.db_path)
        try:
            raw_rows = enriched_comparison_rows(db, seller_match, product_codes)
        finally:
            db.conn.close()
        states, support, exact_offer_groups, exact_scans, legacy_counts = self._load_kaspi_support_data(
            product_codes
        )
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
            own_exact_offer = next((
                offer for offer in exact_offers
                if self._is_own_exact_offer(offer, own_seller_id, own_seller_name)
            ), None)
            competitors = self._exact_offer_candidates(
                code, title, product_url, source_brand, exact_offers,
                own_seller_id, own_seller_name,
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
                "seller_id": own_seller_id,
                "seller_name": str(
                    (own_exact_offer or {}).get("merchant_name")
                    or own_seller_name or own_seller_id
                ),
                "seller_url": seller_url,
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

    @staticmethod
    def _ozon_effective_price(offer: dict[str, Any] | None, fallback: Any = 0) -> int:
        value = offer or {}
        current_prices: list[int] = []
        for key in ("card_price", "regular_price"):
            try:
                price = int(value.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if price > 0:
                current_prices.append(price)
        if current_prices:
            return min(current_prices)
        for candidate in (value.get("catalog_price"), fallback):
            try:
                price = int(candidate or 0)
            except (TypeError, ValueError):
                continue
            if price > 0:
                return price
        return 0

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
        self, conn: Any, expected_name: str, seller_ids: set[str]
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


    def _current_ozon_rows(
        self, conn: Any, expected_name: str, seller_ids: set[str]
    ) -> list[dict[str, Any]]:
        """Build Ozon analytics from one published market snapshot only."""
        catalog = conn.execute(
            """SELECT cp.catalog_run_id AS run_id FROM catalog_publications cp
               JOIN runs r ON r.run_id=cp.catalog_run_id
               WHERE r.mode='discover' AND r.status='PASSED'
                 AND EXISTS(SELECT 1 FROM catalog_snapshots cs WHERE cs.run_id=cp.catalog_run_id)
               ORDER BY cp.published_at DESC LIMIT 1"""
        ).fetchone()
        if not catalog:
            return []
        catalog_run_id = str(catalog["run_id"])
        current = conn.execute(
            """SELECT mac.market_run_id FROM market_analysis_current mac
               JOIN market_analysis_runs mar ON mar.run_id=mac.market_run_id
               WHERE mac.catalog_run_id=? AND mar.status='PASSED'""",
            (catalog_run_id,),
        ).fetchone()
        market_run_id = str(current["market_run_id"]) if current else ""
        products = {
            str(row["article"]): dict(row)
            for row in conn.execute(
                """SELECT p.* FROM catalog_snapshots cs JOIN products p ON p.article=cs.article
                   WHERE cs.run_id=? ORDER BY p.article""",
                (catalog_run_id,),
            ).fetchall()
        }
        offers_by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in conn.execute(
            """SELECT * FROM offers WHERE active=1
               ORDER BY article,CASE WHEN card_price>0 THEN 0 ELSE 1 END,card_price,last_checked_at DESC"""
        ).fetchall():
            offers_by_article[str(row["article"])].append(dict(row))
        product_state: dict[str, dict[str, Any]] = {}
        candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if market_run_id:
            product_state = {
                str(row["client_article"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM market_analysis_products WHERE market_run_id=?",
                    (market_run_id,),
                ).fetchall()
            }
            for row in conn.execute(
                """SELECT * FROM market_analysis_candidates
                   WHERE market_run_id=? AND match_level IN ('EXACT','STRONG','COMPARABLE')
                   ORDER BY match_score DESC,catalog_rank""",
                (market_run_id,),
            ).fetchall():
                candidates[str(row["client_article"])].append(dict(row))

        result: list[dict[str, Any]] = []
        for article, value in products.items():
            article_offers = offers_by_article.get(article, [])
            own_offer = next((
                offer for offer in article_offers
                if self._is_own_ozon_offer(offer, expected_name, seller_ids)
            ), None)
            own_price = self._ozon_effective_price(own_offer, value.get("catalog_price"))
            accepted: dict[tuple[str, str], dict[str, Any]] = {}
            for row in candidates.get(article, []):
                offer = dict(row)
                if self._is_own_ozon_offer(offer, expected_name, seller_ids):
                    continue
                price = self._ozon_effective_price(offer)
                seller_key = str(offer.get("seller_id") or offer.get("seller_name") or "").strip()
                if price <= 0 or not seller_key:
                    continue
                level = str(offer.get("match_level") or "")
                priority = {"EXACT": 3, "STRONG": 2, "COMPARABLE": 1}.get(level, 0)
                key = (str(offer.get("candidate_article") or ""), seller_key.casefold())
                if key in accepted and accepted[key]["_priority"] >= priority:
                    continue
                label = {
                    "OZON_SAME_ARTICLE": "Та же карточка Ozon.ru",
                    "OZON_MANUFACTURER_ARTICLE": "Совпадение по артикулу производителя",
                    "OZON_STRICT_FINGERPRINT": "Строгое совпадение характеристик",
                }.get(str(offer.get("match_method") or ""), "Результат поиска Ozon.ru")
                accepted[key] = {
                    "article": str(offer.get("candidate_article") or ""),
                    "merchant_id": offer.get("seller_id") or "",
                    "merchant_name": offer.get("seller_name") or "Продавец Ozon.ru",
                    "merchant_rating": offer.get("seller_rating"),
                    "price_rub": price,
                    "currency": offer.get("currency") or "RUB",
                    "product_url": offer.get("product_url") or "",
                    "product_title": offer.get("product_title") or "",
                    "captured_at": offer.get("collected_at") or "",
                    "match_method": offer.get("match_method") or "",
                    "match_method_label": label,
                    "match_level": level,
                    "match_score": float(offer.get("match_score") or 0),
                    "match_reason": offer.get("match_reason") or "",
                    "is_own": False,
                    "_priority": priority,
                }
            exact_candidates = [
                {key: value for key, value in item.items() if key != "_priority"}
                for item in accepted.values() if item["match_level"] in {"EXACT", "STRONG"}
            ]
            comparable_candidates = [
                {key: value for key, value in item.items() if key != "_priority"}
                for item in accepted.values() if item["match_level"] == "COMPARABLE"
            ]
            pool = exact_candidates if exact_candidates else comparable_candidates
            basis = "EXACT" if exact_candidates else "COMPARABLE" if comparable_candidates else "NONE"
            prices = sorted(int(item["price_rub"]) for item in pool if int(item.get("price_rub") or 0) > 0)
            market_min = min(prices) if prices else None
            market_max = max(prices) if prices else None
            market_median = float(statistics.median(prices)) if prices else None
            difference_pct = round((own_price - market_median) / market_median * 100, 2) if own_price and market_median else None
            state = product_state.get(article, {})
            state_status = str(state.get("status") or "")
            if not market_run_id or state_status == "NO_SAFE_IDENTITY":
                status = "NOT_ANALYZED"
            elif value.get("detail_status") != "COMPLETE":
                status = "DATA_ERROR" if value.get("last_error") else "NOT_ANALYZED"
            elif not prices:
                status = "NO_OTHER_SELLERS"
            elif basis == "EXACT":
                status = "EXACT_LOWEST" if own_price < market_min else "EXACT_TIED_LOWEST" if own_price == market_min else "EXACT_HIGHEST" if own_price > market_max else "EXACT_TIED_HIGHEST" if own_price == market_max else "EXACT_IN_MARKET" if abs(float(difference_pct or 0)) <= 2 else "EXACT_BELOW" if float(difference_pct or 0) < 0 else "EXACT_ABOVE"
            else:
                status = "COMPARABLE_LOWEST" if own_price <= market_min else "COMPARABLE_HIGHEST" if own_price >= market_max else "COMPARABLE_IN_MARKET" if abs(float(difference_pct or 0)) <= 2 else "COMPARABLE_BELOW" if float(difference_pct or 0) < 0 else "COMPARABLE_ABOVE"
            info = STATUS_INFO.get(status, STATUS_INFO["NOT_ANALYZED"])
            own_updated = (own_offer or {}).get("last_checked_at") or value.get("last_price_at") or value.get("last_detail_at") or value.get("last_seen_at") or ""
            freshness_status, freshness_label = self._freshness(own_updated)
            characteristics = self._characteristics(
                value.get("title") or "", value.get("specs_json") or [], value.get("brand") or "",
                {
                    "size": value.get("tire_size") or "", "product_type": self._ozon_product_type(value.get("title")),
                    "model": value.get("model") or "", "load_index": value.get("load_index") or "",
                    "speed_index": value.get("speed_index") or "", "season": value.get("season") or "",
                    "studded": value.get("studded"), "runflat": value.get("runflat"),
                },
            )
            result.append({
                **characteristics,
                "product_code": f"ozon:{article}", "source_product_code": article,
                "platform": "ozon", "platform_label": "Ozon.ru", "source_type": "CURRENT_CATALOG",
                "title": value.get("title") or "", "brand": value.get("brand") or "", "model": value.get("model") or "",
                "size": characteristics.get("size") or value.get("tire_size") or "",
                "product_type": characteristics.get("product_type") or self._ozon_product_type(value.get("title")),
                "strict_identity_eligible": bool(value.get("brand") and value.get("tire_size")),
                "product_url": value.get("canonical_url") or "", "image_url": value.get("image_url") or "",
                "own_price_kzt": None, "market_price_kzt": None, "price_original": own_price,
                "currency_original": (own_offer or {}).get("currency") or "RUB",
                "regular_price_original": (own_offer or {}).get("regular_price") or 0,
                "market_min_price_original": market_min, "market_max_price_original": market_max,
                "market_median_price_original": market_median,
                "seller_id": (own_offer or {}).get("seller_id") or next(iter(seller_ids), ""),
                "seller_name": (own_offer or {}).get("seller_name") or ("Alfa Tires" if expected_name else ""),
                "seller_url": (own_offer or {}).get("seller_url") or "", "seller_rating": (own_offer or {}).get("seller_rating"),
                "catalog_rating": (own_offer or {}).get("product_rating"), "catalog_reviews": (own_offer or {}).get("review_count"),
                "availability_status": (own_offer or {}).get("availability_status") or "UNKNOWN",
                "price_status": status, "status_label": info["label"], "status_tone": info["tone"],
                "identity_completeness_percent": value.get("identity_completeness_percent") or 0,
                "manufacturer_article": value.get("manufacturer_article") or "", "load_index": characteristics.get("load_index") or value.get("load_index") or "",
                "speed_index": characteristics.get("speed_index") or value.get("speed_index") or "", "season": characteristics.get("season") or "UNKNOWN",
                "studded": characteristics.get("studded") or "UNKNOWN", "xl": bool(value.get("xl")), "runflat": bool(characteristics.get("runflat")),
                "watched": False, "priority": "normal", "note": "", "candidate_count": len(pool), "reference_count": len(pool),
                "exact_candidate_count": len(exact_candidates), "comparable_candidate_count": len(comparable_candidates),
                "reference_type": "OZON_EXACT_PRODUCT" if basis == "EXACT" else "OZON_BRAND_SIZE" if basis == "COMPARABLE" else "",
                "market_basis": basis, "match_method": pool[0]["match_method"] if pool else "",
                "match_method_label": pool[0]["match_method_label"] if pool else "", "exact_candidates": exact_candidates,
                "comparable_candidates": comparable_candidates, "difference_pct": difference_pct,
                "price_rank": (1 + sum(price < own_price for price in prices)) if prices and own_price else None,
                "price_rank_total": len(prices) + 1 if prices and own_price else None,
                "price_rank_tie_count": (1 + sum(price == own_price for price in prices)) if prices and own_price else 0,
                "is_lowest": status in {"EXACT_LOWEST", "EXACT_TIED_LOWEST", "COMPARABLE_LOWEST"}, "is_unique_lowest": status == "EXACT_LOWEST",
                "is_highest": status in {"EXACT_HIGHEST", "EXACT_TIED_HIGHEST", "COMPARABLE_HIGHEST"}, "is_unique_highest": status == "EXACT_HIGHEST",
                "lowest_product_url": min(pool, key=lambda item: item["price_rub"])["product_url"] if pool else "",
                "highest_product_url": max(pool, key=lambda item: item["price_rub"])["product_url"] if pool else "",
                "updated_at": own_updated, "freshness_status": freshness_status, "freshness_label": freshness_label,
                "market_run_id": market_run_id, "_price_sort": float(own_price or 0),
                "_delta_sort": float(difference_pct or 0), "_updated_sort": own_updated,
            })
        return result


    def _ozon_rows(self) -> list[dict[str, Any]]:
        path = self.ozon_db_path
        if not path or not path.exists():
            return []
        conn: Any | None = None
        try:
            conn = self._connect_path(path)
            expected_name, seller_ids = self._ozon_owner_config()
            if not all(table_exists(conn, table) for table in (
                "catalog_snapshots", "market_analysis_runs",
                "market_analysis_products", "market_analysis_candidates",
                "market_analysis_current", "catalog_publications",
            )):
                # A legacy active-row registry cannot prove one completed,
                # catalog-bound market state.  Hiding it is safer than
                # presenting a mixed partial run as current analytics.
                return []
            return self._current_ozon_rows(conn, expected_name, seller_ids)
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
                own_price=self._ozon_effective_price(own_offer, value.get("catalog_price"))
                accepted: dict[tuple[str,str],dict[str,Any]]={}
                def add_candidate(candidate_article:str,offer:dict[str,Any],method:str,label:str,level:str,score:float=100,reason:str=''):
                    if self._is_own_ozon_offer(offer,expected_name,seller_ids): return
                    price=self._ozon_effective_price(offer)
                    if price<=0:return
                    seller_key=str(offer.get("seller_id") or offer.get("seller_name") or '').strip()
                    if not seller_key:return
                    key=(candidate_article,seller_key.casefold())
                    priority={'EXACT':3,'STRONG':2,'COMPARABLE':1}.get(level,0)
                    if key in accepted and accepted[key]['_priority']>=priority:return
                    product=products.get(candidate_article,{})
                    accepted[key]={
                        'article':candidate_article,'merchant_id':offer.get('seller_id') or '',
                        'merchant_name':offer.get('seller_name') or 'Продавец Ozon.ru','merchant_rating':offer.get('seller_rating'),
                        'price_rub':price,'currency':offer.get('currency') or 'RUB','product_url':product.get('canonical_url') or '',
                        'product_title':product.get('title') or '', 'captured_at':offer.get('last_checked_at') or offer.get('last_seen_at'),
                        'match_method':method,'match_method_label':label,'match_level':level,'match_score':score,'match_reason':reason,
                        'is_own':False,'_priority':priority,
                    }
                for offer in article_offers:
                    add_candidate(article,offer,'OZON_SAME_ARTICLE','Та же карточка Ozon.ru','EXACT',100,'Другой продавец той же карточки')
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
                price_rank=(1+sum(price<own_price for price in prices)) if rank_values else None
                price_rank_tie_count=(1+sum(price==own_price for price in prices)) if rank_values else 0
                if value.get('detail_status')!='COMPLETE': status='DATA_ERROR' if value.get('last_error') else 'NOT_ANALYZED'
                elif not prices: status='NO_OTHER_SELLERS'
                elif basis=='EXACT':
                    status='EXACT_LOWEST' if own_price<market_min else 'EXACT_TIED_LOWEST' if own_price==market_min else 'EXACT_HIGHEST' if own_price>market_max else 'EXACT_TIED_HIGHEST' if own_price==market_max else 'EXACT_IN_MARKET' if abs(float(difference_pct or 0))<=2 else 'EXACT_BELOW' if float(difference_pct or 0)<0 else 'EXACT_ABOVE'
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
                    'product_code':f'ozon:{article}','source_product_code':article,'platform':'ozon','platform_label':'Ozon.ru','source_type':'CLIENT_CATALOG',
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
                    'price_rank_tie_count':price_rank_tie_count,
                    'lowest_tie_count':price_rank_tie_count if prices and own_price==market_min else 1 if prices and own_price<market_min else 0,
                    'highest_tie_count':price_rank_tie_count if prices and own_price==market_max else 1 if prices and own_price>market_max else 0,
                    'is_lowest':status in {'EXACT_LOWEST','EXACT_TIED_LOWEST','COMPARABLE_LOWEST'},
                    'is_unique_lowest':status=='EXACT_LOWEST',
                    'is_highest':status in {'EXACT_HIGHEST','EXACT_TIED_HIGHEST','COMPARABLE_HIGHEST'},
                    'is_unique_highest':status=='EXACT_HIGHEST',
                    'lowest_product_url':min(pool,key=lambda x:x['price_rub'])['product_url'] if pool else '',
                    'highest_product_url':max(pool,key=lambda x:x['price_rub'])['product_url'] if pool else '',
                    'updated_at':own_updated,'freshness_status':freshness_status,'freshness_label':freshness_label,
                    '_price_sort':float(own_price or 0),'_delta_sort':float(difference_pct or 0),'_updated_sort':own_updated,
                })
            return result
        except database_error_types():
            return []
        finally:
            if conn is not None: conn.close()

    def _ozon_kz_rows(self) -> list[dict[str, Any]]:
        if not self.ozon_kz_db_path or not self.ozon_kz_db_path.exists():
            return []
        conn = self._connect_path(self.ozon_kz_db_path)
        try:
            if not all(
                table_exists(conn, table)
                for table in ("ozon_kz_products", "ozon_kz_offers")
            ):
                return []
            offers_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in conn.execute(
                """SELECT * FROM ozon_kz_offers WHERE active=1
                   ORDER BY product_id,is_own DESC,price_kzt,seller_name"""
            ).fetchall():
                offers_by_product[str(row["product_id"])].append(dict(row))
            products = conn.execute(
                "SELECT * FROM ozon_kz_products WHERE active=1 ORDER BY last_seen_at DESC"
            ).fetchall()
        except database_error_types():
            return []
        finally:
            conn.close()

        result: list[dict[str, Any]] = []
        for row in products:
            value = dict(row)
            product_id = str(value.get("product_id") or "")
            code = f"ozon_kz:{product_id}"
            specs = self._json(value.get("specifications_json"), [])
            ident = identity(
                str(value.get("title") or ""), specs, str(value.get("brand") or "")
            )
            characteristics = self._characteristics(
                value.get("title") or "", specs, value.get("brand") or "",
                {
                    "size": self._identity_size(ident),
                    "product_type": ident.get("type"),
                    "model": value.get("model") or ident.get("model"),
                },
            )
            offers = offers_by_product.get(product_id, [])
            own_offer = next((item for item in offers if int(item.get("is_own") or 0)), None)
            own_price = (
                own_offer.get("price_kzt") if own_offer and own_offer.get("price_kzt") is not None
                else value.get("own_price_kzt")
            )
            competitors = [
                Candidate(
                    code=str(item.get("seller_id") or ""),
                    title=str(item.get("seller_name") or "Продавец Ozon.kz"),
                    url=str(value.get("canonical_url") or ""),
                    price=float(item.get("price_kzt") or 0),
                    brand=str(value.get("brand") or ident.get("brand") or ""),
                    tier="SAME_PRODUCT_CARD",
                    model=str(value.get("title") or ""),
                    score=100.0,
                    relation="OZON_KZ_SAME_CARD",
                    reasons=["same_ozon_kz_product_id", f"product_id={product_id}"],
                )
                for item in offers
                if not int(item.get("is_own") or 0) and float(item.get("price_kzt") or 0) > 0
            ]
            position = exact_offer_position(
                own_price, competitors, "ok" if value.get("last_seen_at") else ""
            )
            status_code = str(position.get("price_status") or "NOT_ANALYZED")
            status_info = STATUS_INFO.get(status_code, STATUS_INFO["NOT_ANALYZED"])
            result.append({
                **position,
                **characteristics,
                "product_code": code,
                "source_product_code": product_id,
                "platform": "ozon_kz",
                "platform_label": "Ozon.kz",
                "source_type": "SEPARATE_KZ_CONNECTOR",
                "title": value.get("title") or "",
                "brand": value.get("brand") or ident.get("brand") or "",
                "model": value.get("model") or ident.get("model") or "",
                "size": characteristics.get("size") or self._identity_size(ident),
                "product_type": characteristics.get("product_type") or "other",
                "product_url": value.get("canonical_url") or "",
                "image_url": value.get("image_url") or "",
                "price_kzt": own_price,
                "own_price_kzt": own_price,
                "price_original": own_price,
                "currency_original": "KZT",
                "availability_status": (
                    own_offer.get("availability_status") if own_offer
                    else value.get("availability_status") or "UNKNOWN"
                ),
                "seller_id": own_offer.get("seller_id") if own_offer else "",
                "seller_name": own_offer.get("seller_name") if own_offer else "",
                "seller_url": own_offer.get("seller_url") if own_offer else "",
                "price_status": status_code,
                "status_label": status_info["label"],
                "status_tone": status_info["tone"],
                "reference_type": "OZON_KZ_SAME_CARD",
                "match_method": "OZON_KZ_PRODUCT_ID",
                "match_method_label": "Та же карточка Ozon.kz",
                "exact_offer_status": "ok" if value.get("last_seen_at") else "not_checked",
                "exact_offer_checked_at": value.get("last_seen_at"),
                "exact_offer_count": len(offers),
                "competitor_seller_count": len(competitors),
                "candidate_count": len(competitors),
                "reference_count": position.get("reference_count") or len(competitors),
                "exact_candidates": [item.as_dict() for item in competitors],
                "watched": False,
                "priority": "normal",
                "note": "",
                "expected_monthly_units": None,
                "updated_at": value.get("last_seen_at"),
                "freshness_status": self._freshness(value.get("last_seen_at"))[0],
                "freshness_label": self._freshness(value.get("last_seen_at"))[1],
                "_price_sort": float(own_price or 0),
                "_delta_sort": float(position.get("difference_kzt") or 0),
                "_updated_sort": value.get("last_seen_at") or "",
                "raw_price_status": status_code,
                "_ozon_kz_specifications": specs,
            })
        return result

    def _halyk_rows(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if not table_exists(conn, "halyk_products"):
                return []
            states: dict[str, dict[str, Any]] = {}
            offers_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
            if table_exists(conn, "halyk_offers"):
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

    def _forte_rows(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if not table_exists(conn, "forte_products"):
                return []
            # Tenant-specific state is overlaid after the shared row cache is
            # built. Caching it here would leak notes between organizations.
            states: dict[str, dict[str, Any]] = {}
            offers_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
            if table_exists(conn, "forte_offers"):
                for row in conn.execute(
                    """
                    SELECT * FROM forte_offers
                    WHERE active=1
                    ORDER BY product_id,is_own DESC,CASE WHEN price_kzt>0 THEN 0 ELSE 1 END,price_kzt,merchant_name
                    """
                ).fetchall():
                    offers_by_product[str(row["product_id"])].append(dict(row))
            products = conn.execute(
                "SELECT * FROM forte_products WHERE active=1 ORDER BY last_seen_at DESC"
            ).fetchall()
        finally:
            conn.close()

        result: list[dict[str, Any]] = []
        for row in products:
            value = dict(row)
            product_id = str(value.get("product_id") or "")
            code = f"forte:{product_id}"
            public_url = self._forte_public_url(
                product_id, value.get("slug"), value.get("product_url")
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
                    code=str(offer.get("merchant_id") or offer.get("merchant_key") or ""),
                    title=str(offer.get("merchant_name") or "Продавец Forte Market"),
                    url=public_url,
                    price=float(offer.get("price_kzt") or 0),
                    brand=str(value.get("brand") or ident.get("brand") or ""),
                    tier="SAME_PRODUCT_CARD",
                    model=str(value.get("name") or ""),
                    score=100.0,
                    relation="FORTE_SAME_CARD",
                    reasons=["same_forte_product_id", f"product_id={product_id}", "different_seller"],
                )
                for offer in offers
                if int(offer.get("is_own") or 0) == 0 and float(offer.get("price_kzt") or 0) > 0
            ]
            scan_status = "ok" if value.get("last_market_at") else ""
            position = exact_offer_position(own_price, competitors, scan_status)
            position.update({
                "reference_type": "FORTE_SAME_CARD",
                "match_method": "FORTE_PRODUCT_ID",
            })
            status = str(position.get("price_status") or "NOT_ANALYZED")
            info = STATUS_INFO.get(status, STATUS_INFO["NOT_ANALYZED"])
            state = states.get(code, {})
            result.append({
                **position,
                **characteristics,
                "product_code": code,
                "source_product_code": product_id,
                "platform": "forte_market",
                "platform_label": "Forte Market",
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
                "seller_id": value.get("merchant_id") or "",
                "seller_name": self.forte_seller_name,
                "seller_url": f"{FORTE_BASE_URL}/merchant-products/{value.get('merchant_id')}" if value.get("merchant_id") else "",
                "price_status": status,
                "status_label": info["label"],
                "status_tone": info["tone"],
                "match_method_label": "Та же карточка Forte Market",
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
                "catalog_rating": value.get("catalog_rating"),
                "catalog_reviews": value.get("catalog_reviews"),
                "updated_at": value.get("last_market_at") or value.get("last_catalog_at") or value.get("last_seen_at"),
                "freshness_status": self._freshness(value.get("last_market_at") or value.get("last_catalog_at"))[0],
                "freshness_label": self._freshness(value.get("last_market_at") or value.get("last_catalog_at"))[1],
                "_price_sort": float(own_price or 0),
                "_delta_sort": float(position.get("difference_kzt") or 0),
                "_updated_sort": value.get("last_market_at") or value.get("last_catalog_at") or value.get("last_seen_at") or "",
                "raw_price_status": status,
            })
        return result

    def _tenant_id_for_user(self, user_id: int | None) -> int | None:
        if not user_id:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT tenant_id FROM tenant_users
                   WHERE user_id=? AND is_active=1
                   ORDER BY is_primary DESC,tenant_id LIMIT 1""",
                (int(user_id),),
            ).fetchone()
            return int(row["tenant_id"]) if row else None
        finally:
            conn.close()

    def _tenant_states(self, user_id: int | None) -> dict[str, dict[str, Any]]:
        tenant_id = self._tenant_id_for_user(user_id)
        if tenant_id is None:
            return {}
        conn = self._connect()
        try:
            return {
                str(row["product_code"]): dict(row)
                for row in conn.execute(
                    """SELECT product_code,watched,priority,note,
                              expected_monthly_units,updated_at
                       FROM tenant_product_state WHERE tenant_id=?""",
                    (tenant_id,),
                ).fetchall()
            }
        finally:
            conn.close()

    def _with_tenant_state(
        self, rows: list[dict[str, Any]], user_id: int | None
    ) -> list[dict[str, Any]]:
        states = self._tenant_states(user_id)
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            state = states.get(str(item.get("product_code") or ""), {})
            item["watched"] = bool(state.get("watched"))
            item["priority"] = str(state.get("priority") or "normal")
            item["note"] = str(state.get("note") or "")
            item["expected_monthly_units"] = state.get("expected_monthly_units")
            item["user_state_updated_at"] = state.get("updated_at")
            result.append(item)
        return result

    def user_owns_shared_catalog(self, user_id: int | None) -> bool:
        if not user_id:
            return True
        tenant_id = self._tenant_id_for_user(user_id)
        conn = self._connect()
        try:
            owner = conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()
            owner_id = int(owner["id"]) if owner else None
        finally:
            conn.close()
        return tenant_id is not None and tenant_id == owner_id

    @staticmethod
    def _catalog_product_code(platform: str, source_product_code: str) -> str:
        prefixes = {
            "kaspi": "",
            "ozon": "ozon:",
            "ozon_kz": "ozon_kz:",
            "halyk_market": "halyk:",
            "forte_market": "forte:",
            "wildberries": "wb:",
        }
        prefix = prefixes.get(str(platform), "")
        value = str(source_product_code or "")
        return value if prefix and value.startswith(prefix) else prefix + value

    def _tenant_catalog_snapshot(
        self, tenant_id: int, marketplaces: set[str] | None = None
    ) -> list[dict[str, Any]]:
        requested = tuple(sorted({
            str(value).strip() for value in (marketplaces or set())
            if str(value).strip()
        }))
        key = (int(tenant_id), requested)
        now = time.monotonic()
        with self.lock:
            generation = self._cache_generation
            cached = self._tenant_snapshot_cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
            key_lock = self._tenant_snapshot_locks.setdefault(key, threading.Lock())

        # Only identical tenant/scope loads wait for each other. Different
        # companies and marketplace scopes remain fully concurrent.
        with key_lock:
            now = time.monotonic()
            with self.lock:
                cached = self._tenant_snapshot_cache.get(key)
                if cached and cached[0] > now:
                    return cached[1]
            result = self._load_tenant_catalog_snapshot(
                int(tenant_id), set(requested) or None
            )
            with self.lock:
                # A collector or settings update may invalidate the catalogue
                # while this query is in flight. Its response is still valid
                # for the caller, but it must not repopulate a cleared cache.
                if generation != self._cache_generation:
                    return result
                expires_at = time.monotonic() + TENANT_SNAPSHOT_CACHE_TTL_SECONDS
                self._tenant_snapshot_cache[key] = (expires_at, result)
                if len(self._tenant_snapshot_cache) > TENANT_SNAPSHOT_CACHE_MAX_KEYS:
                    removable = sorted(
                        (
                            (cache_key, value[0])
                            for cache_key, value in self._tenant_snapshot_cache.items()
                            if cache_key != key
                        ),
                        key=lambda item: item[1],
                    )
                    while (
                        len(self._tenant_snapshot_cache) > TENANT_SNAPSHOT_CACHE_MAX_KEYS
                        and removable
                    ):
                        stale_key, _ = removable.pop(0)
                        self._tenant_snapshot_cache.pop(stale_key, None)
                        self._tenant_snapshot_locks.pop(stale_key, None)
            return result

    def _load_tenant_catalog_snapshot(
        self, tenant_id: int, marketplaces: set[str] | None = None,
        source_product_code: str | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            requested = {
                str(value).strip() for value in (marketplaces or set())
                if str(value).strip()
            }
            where = "tenant_id=? AND active=1"
            params: list[Any] = [int(tenant_id)]
            if requested:
                where += f" AND marketplace_code IN ({','.join('?' for _ in requested)})"
                params.extend(sorted(requested))
            if source_product_code:
                where += " AND source_product_code=?"
                params.append(str(source_product_code))
            seller_counts = {
                str(row["marketplace_code"]): int(row["seller_count"])
                for row in conn.execute(
                    """SELECT marketplace_code,COUNT(DISTINCT tenant_seller_id) AS seller_count
                       FROM tenant_seller_catalog_products
                       WHERE tenant_id=? AND active=1
                       GROUP BY marketplace_code""",
                    (int(tenant_id),),
                ).fetchall()
            }
            # Product identity must become seller-scoped as soon as multiple
            # accounts are active, not only after the second account has
            # completed its first catalog sync.  This keeps bookmarks/state
            # stable and prevents unsafe legacy enrichment during that window.
            for row in conn.execute(
                """SELECT marketplace_code,COUNT(*) AS seller_count
                   FROM tenant_marketplace_sellers
                   WHERE tenant_id=? AND status='active'
                     AND approval_status='approved'
                   GROUP BY marketplace_code""",
                (int(tenant_id),),
            ).fetchall():
                code = str(row["marketplace_code"])
                seller_counts[code] = max(
                    seller_counts.get(code, 0), int(row["seller_count"])
                )
            if seller_counts:
                seller_where = "tsp.tenant_id=? AND tsp.active=1"
                seller_params: list[Any] = [int(tenant_id)]
                if requested:
                    seller_where += (
                        f" AND tsp.marketplace_code IN ("
                        f"{','.join('?' for _ in requested)})"
                    )
                    seller_params.extend(sorted(requested))
                if source_product_code:
                    seller_where += " AND tsp.source_product_code=?"
                    seller_params.append(str(source_product_code))
                rows = conn.execute(
                    f"""SELECT tsp.*,s.external_seller_id,s.display_name AS seller_name,
                               s.source_url AS seller_url
                        FROM tenant_seller_catalog_products tsp
                        JOIN tenant_marketplace_sellers s
                          ON s.id=tsp.tenant_seller_id AND s.tenant_id=tsp.tenant_id
                        WHERE {seller_where}
                        ORDER BY tsp.marketplace_code,tsp.tenant_seller_id,
                                 tsp.source_product_code""",
                    seller_params,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT tcp.*,NULL AS external_seller_id,
                               NULL AS seller_name,NULL AS seller_url
                        FROM tenant_catalog_products tcp
                        WHERE {where}
                        ORDER BY marketplace_code,source_product_code""",
                    params,
                ).fetchall()
            integrations = {
                str(row["integration_code"]): dict(row)
                for row in conn.execute(
                    """SELECT integration_code,seller_identifier,seller_name,seller_url
                       FROM tenant_integrations WHERE tenant_id=?""",
                    (int(tenant_id),),
                ).fetchall()
            }
            tenant = conn.execute(
                "SELECT name FROM tenants WHERE id=?", (int(tenant_id),)
            ).fetchone()
        finally:
            conn.close()
        tenant_name = str(tenant["name"] if tenant else "").strip()
        legacy_seller_names = {
            self._normalized_seller(self.seller_name),
            self._normalized_seller(self.halyk_seller_name),
            self._normalized_seller(self.forte_seller_name),
            self._normalized_seller("Unityre"),
        }
        labels = {
            "kaspi": "Kaspi",
            "ozon": "Ozon.ru",
            "ozon_kz": "Ozon.kz",
            "halyk_market": "Halyk Market",
            "forte_market": "Forte Market",
            "wildberries": "Wildberries",
        }
        result: list[dict[str, Any]] = []
        for raw in rows:
            value = dict(raw)
            platform = str(value.get("marketplace_code") or "")
            integration = integrations.get(platform, {})
            integration_seller = str(
                value.get("seller_name") or integration.get("seller_name") or ""
            ).strip()
            if (
                not integration_seller
                or self._normalized_seller(integration_seller) in legacy_seller_names
            ):
                integration_seller = tenant_name
            source_code = str(value.get("source_product_code") or "")
            tenant_seller_id = int(value.get("tenant_seller_id") or 0)
            public_code = self._catalog_product_code(platform, source_code)
            if tenant_seller_id and seller_counts.get(platform, 0) > 1:
                public_code = seller_scoped_product_code(
                    platform, tenant_seller_id, source_code
                )
            amount = value.get("price_amount")
            is_rub = str(value.get("currency") or "").upper() == "RUB"
            try:
                attributes = json.loads(str(value.get("attributes_json") or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                attributes = []
            updated = value.get("source_updated_at") or value.get("last_seen_at")
            result.append({
                "product_code": public_code,
                "source_product_code": source_code,
                "tenant_seller_id": tenant_seller_id or None,
                "platform": platform,
                "platform_label": labels.get(platform, platform),
                "title": value.get("title") or source_code,
                "product_url": value.get("source_url") or "",
                "image_url": value.get("image_url") or (
                    image_url_for_article(source_code) if platform == "wildberries" else ""
                ),
                "brand": value.get("brand") or "",
                "model": value.get("model") or "",
                "size": "",
                "product_type": "other",
                "product_type_label": PRODUCT_TYPE_LABELS.get("other", "Other"),
                "season": "UNKNOWN",
                "season_label": SEASON_LABELS.get("UNKNOWN", "Unknown"),
                "characteristic_group": "",
                "characteristic_group_label": "",
                "own_price_kzt": None if is_rub else amount,
                "price_original": amount if is_rub else None,
                "currency_original": value.get("currency") or "",
                "market_price_kzt": None,
                "difference_kzt": None,
                "difference_pct": None,
                "price_status": "NOT_ANALYZED",
                "status_label": STATUS_INFO.get("NOT_ANALYZED", {}).get("label", "Not analyzed"),
                "status_tone": STATUS_INFO.get("NOT_ANALYZED", {}).get("tone", "neutral"),
                "reference_count": 0,
                "candidate_count": 0,
                "seller_id": value.get("external_seller_id") or integration.get("seller_identifier") or "",
                "seller_name": integration_seller or value.get("external_seller_id") or integration.get("seller_identifier") or tenant_name,
                "seller_url": value.get("seller_url") or integration.get("seller_url") or "",
                "updated_at": updated,
                "freshness_status": self._freshness(updated)[0],
                "freshness_label": self._freshness(updated)[1],
                "source_type": "TENANT_CATALOG",
                "raw_price_status": "NOT_ANALYZED",
                "_updated_sort": updated or "",
                "_price_sort": float(amount or 0),
                "_delta_sort": 0.0,
                "_tenant_attributes": attributes,
                "_tenant_catalog_only": True,
                "_multi_seller_marketplace": seller_counts.get(platform, 0) > 1,
            })
        return result

    def rows_for_user(
        self, user_id: int | None, marketplaces: set[str] | None = None
    ) -> list[dict[str, Any]]:
        if not user_id:
            return self.rows()
        tenant_id = self._tenant_id_for_user(user_id)
        if tenant_id is None:
            return []
        requested = {
            str(value).strip() for value in (marketplaces or set())
            if str(value).strip()
        }
        snapshot = self._tenant_catalog_snapshot(tenant_id, requested or None)
        # Wildberries is stored exclusively in the tenant catalog. Avoid the
        # expensive legacy Kaspi/Forte/Halyk materialization for a WB-only view,
        # including the normal unfiltered catalogue request.
        snapshot_platforms = {
            str(row.get("platform") or "") for row in snapshot
            if str(row.get("platform") or "")
        }
        sellers_by_platform: dict[str, set[int]] = defaultdict(set)
        for row in snapshot:
            seller_id = int(row.get("tenant_seller_id") or 0)
            if seller_id:
                sellers_by_platform[str(row.get("platform") or "")].add(seller_id)
        # Legacy collector tables are product-keyed and cannot safely enrich two
        # accounts that sell the same SKU. Seller-scoped snapshots are the
        # authoritative response until every legacy analytics table is retired.
        if (
            any(len(values) > 1 for values in sellers_by_platform.values())
            or any(bool(row.get("_multi_seller_marketplace")) for row in snapshot)
        ):
            return self._with_tenant_state(snapshot, user_id)
        if requested == {"wildberries"} or (
            snapshot and snapshot_platforms == {"wildberries"}
        ):
            return self._with_tenant_state(snapshot, user_id)
        snapshot_by_key = {
            (str(row.get("platform") or ""), str(row.get("source_product_code") or "")): row
            for row in snapshot
        }
        memberships = {
            (str(row.get("platform") or ""), str(row.get("source_product_code") or ""))
            for row in snapshot
        }
        legacy_owner = self.user_owns_shared_catalog(user_id)
        # A newly approved non-owner tenant has no shared-catalog fallback. Do
        # not materialize every legacy marketplace merely to prove its own
        # tenant snapshot is empty; on a production-sized database that turns a
        # zero-row page into a multi-second request.
        if not memberships and not legacy_owner:
            return self._with_tenant_state(snapshot, user_id)
        platforms_with_membership = {platform for platform, _ in memberships}
        if legacy_owner:
            shared_rows = [
                row for row in self.rows()
                if not requested or str(row.get("platform") or "") in requested
            ]
        else:
            shared_platforms = platforms_with_membership - {"wildberries"}
            kaspi_codes = sorted(
                source_code for platform, source_code in memberships
                if platform == "kaspi"
            )
            conn = self._connect()
            try:
                integration = conn.execute(
                    """SELECT seller_identifier,seller_name,seller_url
                       FROM tenant_integrations
                       WHERE tenant_id=? AND integration_code='kaspi'""",
                    (int(tenant_id),),
                ).fetchone()
            finally:
                conn.close()
            integration_value = dict(integration) if integration else {}
            tenant_kaspi_rows = self._kaspi_rows(
                kaspi_codes,
                seller_id=str(integration_value.get("seller_identifier") or ""),
                seller_name=str(integration_value.get("seller_name") or ""),
                seller_url=str(integration_value.get("seller_url") or ""),
            ) if kaspi_codes else []
            if shared_platforms <= {"kaspi"}:
                shared_rows = tenant_kaspi_rows
            else:
                # The remaining legacy connectors still use their established
                # shared loaders. Tenant membership is applied below before any
                # row is returned.
                shared_rows = [
                    row for row in self.rows()
                    if str(row.get("platform") or "") != "kaspi"
                ] + tenant_kaspi_rows
        visible: list[dict[str, Any]] = []
        represented: set[tuple[str, str]] = set()
        for row in shared_rows:
            key = (str(row.get("platform") or ""), str(row.get("source_product_code") or ""))
            # Explicit tenant catalogue membership is authoritative and safe to
            # enrich with the shared collector row for that exact product. The
            # tenant snapshot still owns its public metadata and own price, while
            # exact-card offers/specifications come from the collector tables.
            if key in memberships:
                item = dict(row)
                tenant_row = snapshot_by_key[key]
                for field in (
                    "title", "product_url", "image_url", "brand", "model",
                    "currency_original", "updated_at", "freshness_status",
                    "freshness_label", "seller_id", "seller_url",
                ):
                    if tenant_row.get(field) not in (None, ""):
                        item[field] = tenant_row[field]
                # The exact-card offer carries the human-readable seller name
                # (for example, LICK), while the integration may only know the
                # numeric merchant id. Never replace it with a global seller.
                if not str(item.get("seller_name") or "").strip():
                    item["seller_name"] = tenant_row.get("seller_name") or ""
                elif self._normalized_seller(item.get("seller_name")) in {
                    self._normalized_seller(self.seller_name),
                    self._normalized_seller(self.halyk_seller_name),
                    self._normalized_seller(self.forte_seller_name),
                } and str(tenant_row.get("seller_name") or "").strip():
                    item["seller_name"] = tenant_row["seller_name"]
                tenant_price = tenant_row.get("own_price_kzt")
                if key[0] != "ozon" and tenant_price not in (None, ""):
                    item["own_price_kzt"] = tenant_price
                    item["price_kzt"] = tenant_price
                    item["price_original"] = tenant_price
                item["_tenant_catalog_only"] = False
                visible.append(item)
                represented.add(key)
            # Compatibility for the original workspace: old collector tables have
            # no tenant_id. Platforms without explicit membership retain the
            # legacy fallback only for the original owning workspace.
            elif (
                legacy_owner
                and key[0] not in platforms_with_membership
                and (not requested or key[0] in requested)
            ):
                visible.append(row)
                represented.add(key)
        visible.extend(row for row in snapshot if (
            str(row.get("platform") or ""), str(row.get("source_product_code") or "")
        ) not in represented)
        return self._with_tenant_state(visible, user_id)

    def _source_signature(self) -> tuple[int, ...]:
        values: list[int] = []
        paths = [self.db_path]
        if self.ozon_db_path:
            paths.append(self.ozon_db_path)
        if self.ozon_kz_db_path:
            paths.append(self.ozon_kz_db_path)
        for path in paths:
            for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                try:
                    stat = candidate.stat()
                    values.extend((int(stat.st_mtime_ns), int(stat.st_size)))
                except OSError:
                    values.extend((0, 0))
        return tuple(values)

    def _materialize_shared_rows(self) -> list[dict[str, Any]]:
        return (
            self._kaspi_rows()
            + self._ozon_rows()
            + self._ozon_kz_rows()
            + self._halyk_rows()
            + self._forte_rows()
        )

    def _refresh_shared_rows(self, generation: int) -> None:
        try:
            rows = self._materialize_shared_rows()
            signature = self._source_signature()
            with self.lock:
                if generation == self._cache_generation:
                    self._rows_cache = rows
                    self._rows_cached_at = time.monotonic()
                    self._rows_signature = signature
        finally:
            with self.lock:
                if generation == self._cache_generation:
                    self._rows_refreshing = False

    def rows(self, ttl_seconds: float = 60.0) -> list[dict[str, Any]]:
        with self.lock:
            signature = self._source_signature()
            cache_age = time.monotonic() - self._rows_cached_at
            ttl = max(0.0, float(ttl_seconds))
            if self._rows_cache and self._rows_signature == signature:
                if cache_age < ttl:
                    return self._rows_cache
                if ttl > 0:
                    # A legacy refresh can take many seconds on a production
                    # catalogue. Serve the last complete snapshot and rebuild
                    # it in the background instead of blocking an HTTP thread.
                    if not self._rows_refreshing:
                        self._rows_refreshing = True
                        threading.Thread(
                            target=self._refresh_shared_rows,
                            args=(self._cache_generation,),
                            name="spyon-shared-catalog-refresh",
                            daemon=True,
                        ).start()
                    return self._rows_cache
            self._rows_cache = self._materialize_shared_rows()
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
        rows = [
            self._apply_user_values(row, preferences)
            for row in self.rows_for_user(user_id, allowed_platforms)
        ]
        if allowed_platforms is not None:
            rows = [row for row in rows if str(row.get("platform")) in allowed_platforms]
        kaspi_rows = [row for row in rows if row.get("platform") == "kaspi"]
        ozon_rows = [row for row in rows if row.get("platform") == "ozon"]
        ozon_kz_rows = [row for row in rows if row.get("platform") == "ozon_kz"]
        halyk_rows = [row for row in rows if row.get("platform") == "halyk_market"]
        forte_rows = [row for row in rows if row.get("platform") == "forte_market"]
        wildberries_rows = [row for row in rows if row.get("platform") == "wildberries"]
        exact_rows = kaspi_rows + ozon_kz_rows + halyk_rows + forte_rows
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
        ozon_kz_ready_rows = [
            row for row in ozon_kz_rows
            if str(row.get("price_status") or "NOT_ANALYZED") not in UNSCANNED_STATUSES
        ]
        halyk_analyzed_rows = [
            row for row in halyk_rows
            if str(row.get("price_status") or "NOT_ANALYZED") not in UNSCANNED_STATUSES
        ]
        forte_analyzed_rows = [
            row for row in forte_rows
            if str(row.get("price_status") or "NOT_ANALYZED") not in UNSCANNED_STATUSES
        ]
        wildberries_ready_rows = [
            row for row in wildberries_rows if row.get("own_price_kzt") is not None
        ]
        data_ready_count = (
            len(kaspi_analyzed_rows) + len(ozon_ready_rows) + len(ozon_kz_ready_rows)
            + len(halyk_analyzed_rows) + len(forte_analyzed_rows)
            + len(wildberries_ready_rows)
        )
        risk_count = sum(counts[key] for key in RISK_STATUSES)
        opportunity_count = sum(1 for row in exact_rows if self._is_opportunity(row))
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
            tenant_id = self._tenant_id_for_user(user_id)
            recent_events = [dict(row) for row in conn.execute(
                """SELECT e.event_type,e.entity_type,e.entity_id,e.created_at,u.display_name
                   FROM app_events e LEFT JOIN app_users u ON u.id=e.user_id
                   WHERE e.tenant_id=?
                   ORDER BY e.id DESC LIMIT 8""",
                (tenant_id,),
            ).fetchall()] if tenant_id is not None else []
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
            "ozon_kz_count": len(ozon_kz_rows),
            "halyk_count": len(halyk_rows),
            "forte_count": len(forte_rows),
            "wildberries_count": len(wildberries_rows),
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
            "ozon_kz_data_ready_count": len(ozon_kz_ready_rows),
            "ozon_kz_data_coverage_pct": round(len(ozon_kz_ready_rows) / len(ozon_kz_rows) * 100, 2) if ozon_kz_rows else 0,
            "halyk_market_analyzed_count": len(halyk_analyzed_rows),
            "halyk_market_coverage_pct": round(len(halyk_analyzed_rows) / len(halyk_rows) * 100, 2) if halyk_rows else 0,
            "forte_market_analyzed_count": len(forte_analyzed_rows),
            "forte_market_coverage_pct": round(len(forte_analyzed_rows) / len(forte_rows) * 100, 2) if forte_rows else 0,
            "wildberries_data_ready_count": len(wildberries_ready_rows),
            "wildberries_data_coverage_pct": round(len(wildberries_ready_rows) / len(wildberries_rows) * 100, 2) if wildberries_rows else 0,
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
            for row in self.rows_for_user(user_id, allowed_platforms)
            if self._matches(row, active_filters)
        ]
        if allowed_platforms is not None:
            rows = [row for row in rows if str(row.get("platform")) in allowed_platforms]
        kaspi_rows = [row for row in rows if row.get("platform") == "kaspi"]
        ozon_rows = [row for row in rows if row.get("platform") == "ozon"]
        ozon_kz_rows = [row for row in rows if row.get("platform") == "ozon_kz"]
        halyk_rows = [row for row in rows if row.get("platform") == "halyk_market"]
        forte_rows = [row for row in rows if row.get("platform") == "forte_market"]
        wildberries_rows = [row for row in rows if row.get("platform") == "wildberries"]
        exact_rows = kaspi_rows + ozon_kz_rows + halyk_rows + forte_rows

        status_counts = Counter(str(row.get("price_status") or "NOT_ANALYZED") for row in exact_rows)
        analyzed_rows = [row for row in exact_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]
        ozon_ready_rows = [
            row for row in ozon_rows
            if str(row.get("price_status") or "NOT_ANALYZED") in OZON_READY_STATUSES
        ]
        wildberries_ready_rows = [
            row for row in wildberries_rows if row.get("own_price_kzt") is not None
        ]
        combined_ready_count = len(analyzed_rows) + len(ozon_ready_rows) + len(wildberries_ready_rows)
        risk_rows = [row for row in exact_rows if str(row.get("price_status") or "") in RISK_STATUSES]
        opportunity_rows = [row for row in exact_rows if self._is_opportunity(row)]
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
            if self._is_opportunity(row):
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
        for name, values in (("Kaspi", kaspi_rows), ("Ozon.ru", ozon_rows), ("Ozon.kz", ozon_kz_rows), ("Halyk Market", halyk_rows), ("Forte Market", forte_rows), ("Wildberries", wildberries_rows)):
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
                "ozon_kz_products": len(ozon_kz_rows),
                "halyk_products": len(halyk_rows),
                "forte_products": len(forte_rows),
                "wildberries_products": len(wildberries_rows),
                "analyzed_count": combined_ready_count,
                "analysis_coverage_pct": round(combined_ready_count / len(rows) * 100, 2) if rows else 0.0,
                "data_ready_count": combined_ready_count,
                "data_coverage_pct": round(combined_ready_count / len(rows) * 100, 2) if rows else 0.0,
                "kaspi_market_analyzed_count": len([row for row in kaspi_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]),
                "kaspi_market_coverage_pct": round(len([row for row in kaspi_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]) / len(kaspi_rows) * 100, 2) if kaspi_rows else 0.0,
                "ozon_data_ready_count": len(ozon_ready_rows),
                "ozon_data_coverage_pct": round(len(ozon_ready_rows) / len(ozon_rows) * 100, 2) if ozon_rows else 0.0,
                "ozon_kz_data_ready_count": len([row for row in ozon_kz_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]),
                "ozon_kz_data_coverage_pct": round(len([row for row in ozon_kz_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]) / len(ozon_kz_rows) * 100, 2) if ozon_kz_rows else 0.0,
                "halyk_market_analyzed_count": len([row for row in halyk_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]),
                "halyk_market_coverage_pct": round(len([row for row in halyk_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]) / len(halyk_rows) * 100, 2) if halyk_rows else 0.0,
                "forte_market_analyzed_count": len([row for row in forte_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]),
                "forte_market_coverage_pct": round(len([row for row in forte_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]) / len(forte_rows) * 100, 2) if forte_rows else 0.0,
                "wildberries_data_ready_count": len(wildberries_ready_rows),
                "wildberries_data_coverage_pct": round(len(wildberries_ready_rows) / len(wildberries_rows) * 100, 2) if wildberries_rows else 0.0,
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
    def _is_opportunity(row: dict[str, Any]) -> bool:
        """Return only price increases proven safe by the exact-price model."""
        status = str(row.get("price_status") or "")
        return (
            status in OPPORTUNITY_STATUSES
            and float(row.get("potential_margin_per_unit_kzt") or 0) > 0
        )

    @staticmethod
    def _business_priority(row: dict[str, Any]) -> tuple[int, float, str]:
        """Shared ordering for the default All view and derived surfaces."""
        status = str(row.get("price_status") or "NOT_ANALYZED")
        risk_order = {
            "EXACT_HIGHEST": 0, "EXACT_TIED_HIGHEST": 1,
            "EXACT_ABOVE": 2, "EXACT_BELOW": 3, "DATA_ERROR": 4,
        }
        if status in risk_order:
            magnitude = abs(float(row.get("difference_kzt") or 0))
            return (risk_order[status], -magnitude, str(row.get("title") or ""))
        if DataService._is_opportunity(row):
            return (10, -float(row.get("potential_margin_per_unit_kzt") or 0), str(row.get("title") or ""))
        if status in {"EXACT_IN_MARKET", "NO_OTHER_SELLERS"}:
            return (20, 0.0, str(row.get("title") or ""))
        if status.startswith("COMPARABLE_"):
            return (30, 0.0, str(row.get("title") or ""))
        return (40, 0.0, str(row.get("title") or ""))

    @staticmethod
    def _matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
        attribute_codes = filters.get("attribute_product_codes")
        if attribute_codes is not None and str(row.get("product_code") or "") not in set(attribute_codes):
            return False
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
        if scope == "opportunities" and not DataService._is_opportunity(row):
            return False
        if scope == "watched" and not row.get("watched"):
            return False
        return True

    def products(self, page: int, page_size: int, filters: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
        projected = self._tenant_catalog_page(page, page_size, filters, user_id)
        if projected is not None:
            return projected
        preferences = self.preferences(user_id)
        marketplaces = self._filter_set(filters.get("platforms"))
        platform = str(filters.get("platform") or "").strip().casefold()
        if platform:
            marketplaces.add(platform)
        rows = [
            self._apply_user_values(row, preferences)
            for row in self.rows_for_user(user_id, marketplaces or None)
            if self._matches(row, filters)
        ]
        sort_name = str(filters.get("sort") or "updated")
        sort_field = SORT_FIELDS.get(sort_name, "_updated_sort")
        reverse = str(filters.get("direction") or "desc").casefold() != "asc"
        if sort_name == "updated" and str(filters.get("scope") or "all").casefold() == "all":
            rows.sort(key=self._business_priority)
        else:
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
            "price_rank", "price_rank_total", "price_rank_tie_count", "lowest_tie_count",
            "highest_tie_count", "is_lowest", "is_unique_lowest", "is_highest",
            "is_unique_highest", "potential_margin_per_unit_kzt", "potential_margin_monthly_kzt",
            "expected_monthly_units", "watched", "priority", "note", "catalog_rating", "catalog_reviews",
            "image_url", "seller_name", "seller_url", "identity_completeness_percent", "candidate_count",
            "match_method", "match_method_label", "exact_offer_status", "exact_offer_checked_at",
            "exact_offer_count", "competitor_seller_count", "legacy_candidate_count",
            "updated_at", "freshness_status", "freshness_label", "source_type",
            "_updated_sort", "raw_price_status",
        }
        items = [{key: row.get(key) for key in fields} for row in rows[start:start + page_size]]
        return {"items": items, "page": page, "pages": pages, "page_size": page_size, "total": total}

    def _tenant_catalog_page(
        self, page: int, page_size: int, filters: dict[str, Any], user_id: int | None,
    ) -> dict[str, Any] | None:
        """Paginate the tenant catalogue in SQL without collector enrichment.

        This projection is authoritative for catalogue metadata and own price.
        Complex legacy-analytics filters retain the established compatibility
        path below; the normal catalogue request is COUNT + ORDER/LIMIT/OFFSET.
        """
        tenant_id = self._tenant_id_for_user(user_id)
        if tenant_id is None:
            return None
        # The PostgreSQL tenant projection has the bounded, analytics-aware
        # path.  SQLite remains a fixture backend and retains the portable
        # catalogue query below; production never falls back to rows_for_user.
        probe = self._connect()
        try:
            if isinstance(probe, PostgresConnection):
                return self._postgres_tenant_catalog_page(
                    probe, int(tenant_id), page, page_size, filters,
                )
        finally:
            probe.close()
        unsupported = {
            "attribute_product_codes", "freshness", "product_type", "size",
            "season", "characteristic_group", "watched",
        }
        if any(filters.get(name) not in (None, "", [], (), set()) for name in unsupported):
            return None
        statuses = self._filter_set(filters.get("status"), upper=True)
        scope = str(filters.get("scope") or "all").strip().casefold()
        # A raw tenant catalogue has not yet been exact-offer enriched.
        if (statuses and "NOT_ANALYZED" not in statuses) or scope in {
            "risks", "opportunities", "watched",
        }:
            return {"items": [], "page": 1, "pages": 1, "page_size": max(10, min(int(page_size), 200)), "total": 0,
                    "lookup_strategy": "sql_projection"}

        marketplaces = self._filter_set(filters.get("platforms"))
        platform = str(filters.get("platform") or "").strip().casefold()
        if platform:
            marketplaces.add(platform)
        query = str(filters.get("query") or "").strip().casefold()
        brands = self._filter_set(filters.get("brand"))
        conn = self._connect()
        try:
            seller_counts = {
                str(row["marketplace_code"]): int(row["seller_count"])
                for row in conn.execute(
                    """SELECT marketplace_code,COUNT(DISTINCT tenant_seller_id) AS seller_count
                       FROM tenant_seller_catalog_products
                       WHERE tenant_id=? AND active=1 GROUP BY marketplace_code""",
                    (int(tenant_id),),
                ).fetchall()
            }
            for row in conn.execute(
                """SELECT marketplace_code,COUNT(*) AS seller_count
                   FROM tenant_marketplace_sellers
                   WHERE tenant_id=? AND status='active' AND approval_status='approved'
                   GROUP BY marketplace_code""",
                (int(tenant_id),),
            ).fetchall():
                code = str(row["marketplace_code"])
                seller_counts[code] = max(seller_counts.get(code, 0), int(row["seller_count"]))
            seller_source = bool(seller_counts)
            alias = "tsp" if seller_source else "tcp"
            table = "tenant_seller_catalog_products" if seller_source else "tenant_catalog_products"
            where = [f"{alias}.tenant_id=?", f"{alias}.active=1"]
            params: list[Any] = [int(tenant_id)]
            if marketplaces:
                where.append(f"{alias}.marketplace_code IN ({','.join('?' for _ in marketplaces)})")
                params.extend(sorted(marketplaces))
            if query:
                where.append(
                    "(" + " OR ".join(
                        f"LOWER(COALESCE({alias}.{field},'')) LIKE ?"
                        for field in ("source_product_code", "title", "brand", "model", "seller_sku")
                    ) + ")"
                )
                params.extend([f"%{query}%"] * 5)
            if brands:
                where.append(f"LOWER(COALESCE({alias}.brand,'')) IN ({','.join('?' for _ in brands)})")
                params.extend(sorted(brands))
            clause = " AND ".join(where)
            total = int(conn.execute(f"SELECT COUNT(*) AS count FROM {table} {alias} WHERE {clause}", params).fetchone()["count"])
            size = max(10, min(int(page_size), 200))
            pages = max(1, math.ceil(total / size))
            current = max(1, min(int(page), pages))
            sort_name = str(filters.get("sort") or "updated")
            sort_columns = {
                "updated": f"COALESCE({alias}.source_updated_at,{alias}.last_seen_at)",
                "title": f"{alias}.title", "price": f"{alias}.price_amount",
                "delta": f"{alias}.price_amount", "status": f"{alias}.availability_status",
                "brand": f"{alias}.brand", "platform": f"{alias}.marketplace_code",
            }
            order = sort_columns.get(sort_name, sort_columns["updated"])
            direction = "ASC" if str(filters.get("direction") or "desc").casefold() == "asc" else "DESC"
            join = (
                "JOIN tenant_marketplace_sellers s ON s.id=tsp.tenant_seller_id AND s.tenant_id=tsp.tenant_id"
                if seller_source else ""
            )
            seller_fields = "s.external_seller_id,s.display_name AS seller_name,s.source_url AS seller_url" if seller_source else "NULL AS external_seller_id,NULL AS seller_name,NULL AS seller_url"
            rows = conn.execute(
                f"""SELECT {alias}.*,{seller_fields} FROM {table} {alias} {join}
                    WHERE {clause} ORDER BY {order} {direction},{alias}.source_product_code ASC
                    LIMIT ? OFFSET ?""",
                [*params, size, (current - 1) * size],
            ).fetchall()
            tenant = conn.execute("SELECT name FROM tenants WHERE id=?", (int(tenant_id),)).fetchone()
            integrations = {
                str(row["integration_code"]): dict(row)
                for row in conn.execute(
                    """SELECT integration_code,seller_identifier,seller_name,seller_url
                       FROM tenant_integrations WHERE tenant_id=?""", (int(tenant_id),)
                ).fetchall()
            }
        finally:
            conn.close()
        labels = {"kaspi": "Kaspi", "ozon": "Ozon.ru", "ozon_kz": "Ozon.kz", "halyk_market": "Halyk Market", "forte_market": "Forte Market", "wildberries": "Wildberries"}
        tenant_name = str(tenant["name"] if tenant else "")
        items: list[dict[str, Any]] = []
        for raw in rows:
            value = dict(raw); platform_value = str(value.get("marketplace_code") or "")
            source_code = str(value.get("source_product_code") or "")
            seller_id = int(value.get("tenant_seller_id") or 0)
            product_code = self._catalog_product_code(platform_value, source_code)
            if seller_id and seller_counts.get(platform_value, 0) > 1:
                product_code = seller_scoped_product_code(platform_value, seller_id, source_code)
            amount = value.get("price_amount")
            is_rub = str(value.get("currency") or "").upper() == "RUB"
            integration = integrations.get(platform_value, {})
            updated = value.get("source_updated_at") or value.get("last_seen_at")
            items.append({
                "product_code": product_code, "source_product_code": source_code,
                "platform": platform_value, "platform_label": labels.get(platform_value, platform_value),
                "title": value.get("title") or source_code, "product_url": value.get("source_url") or "",
                "image_url": value.get("image_url") or (image_url_for_article(source_code) if platform_value == "wildberries" else ""),
                "brand": value.get("brand") or "", "model": value.get("model") or "", "size": "",
                "product_type": "other", "product_type_label": PRODUCT_TYPE_LABELS.get("other", "Other"),
                "season": "UNKNOWN", "season_label": SEASON_LABELS.get("UNKNOWN", "Unknown"),
                "own_price_kzt": None if is_rub else amount, "price_original": amount if is_rub else None,
                "currency_original": value.get("currency") or "", "price_kzt": None if is_rub else amount,
                "market_price_kzt": None, "difference_kzt": None, "difference_pct": None,
                "price_status": "NOT_ANALYZED", "status_label": STATUS_INFO.get("NOT_ANALYZED", {}).get("label", "Not analyzed"),
                "status_tone": STATUS_INFO.get("NOT_ANALYZED", {}).get("tone", "neutral"),
                "reference_count": 0, "candidate_count": 0, "seller_name": value.get("seller_name") or integration.get("seller_name") or tenant_name,
                "seller_url": value.get("seller_url") or integration.get("seller_url") or "", "updated_at": updated,
                "freshness_status": self._freshness(updated)[0], "freshness_label": self._freshness(updated)[1],
                "source_type": "TENANT_CATALOG", "watched": False, "priority": "normal", "note": "",
            })
        return {"items": items, "page": current, "pages": pages, "page_size": size, "total": total,
                "lookup_strategy": "sql_projection"}

    def _postgres_tenant_catalog_page(
        self, conn: Any, tenant_id: int, page: int, page_size: int,
        filters: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Bounded tenant SQL projection with exact-offer analytics.

        The latest seller scan identifies the immutable snapshot run.  The
        aggregate CTE therefore never enriches or materialises a catalogue in
        Python; it calculates status/potential only for rows selected by SQL.
        """
        unsupported = {"attribute_product_codes", "freshness", "product_type", "size", "season", "characteristic_group", "watched"}
        if any(filters.get(name) not in (None, "", [], (), set()) for name in unsupported):
            return None
        scope = str(filters.get("scope") or "all").strip().casefold()
        if scope == "watched":
            return None
        marketplaces = self._filter_set(filters.get("platforms"))
        platform = str(filters.get("platform") or "").strip().casefold()
        if platform:
            marketplaces.add(platform)
        query = str(filters.get("query") or "").strip().casefold()
        brands = self._filter_set(filters.get("brand"))
        statuses = self._filter_set(filters.get("status"), upper=True)
        where = ["p.tenant_id=?", "p.active=1"]
        params: list[Any] = [tenant_id]
        if marketplaces:
            where.append("p.marketplace_code IN (" + ",".join("?" for _ in marketplaces) + ")")
            params.extend(sorted(marketplaces))
        if query:
            where.append("(" + " OR ".join(f"LOWER(COALESCE(p.{field},'')) LIKE ?" for field in ("source_product_code", "title", "brand", "model", "seller_sku")) + ")")
            params.extend([f"%{query}%"] * 5)
        if brands:
            where.append("LOWER(COALESCE(p.brand,'')) IN (" + ",".join("?" for _ in brands) + ")")
            params.extend(sorted(brands))
        base_where = " AND ".join(where)
        cte = f"""
            WITH base AS (
              SELECT p.*, s.display_name AS seller_name, s.source_url AS seller_url,
                     sc.status AS scan_status, sc.offers_count, sc.competitor_count,
                     sc.checked_at, sc.error AS scan_error
              FROM tenant_seller_catalog_products p
              JOIN tenant_marketplace_sellers s ON s.id=p.tenant_seller_id
              LEFT JOIN tenant_seller_offer_scans sc
                ON sc.tenant_id=p.tenant_id AND sc.marketplace_code=p.marketplace_code
               AND sc.tenant_seller_id=p.tenant_seller_id AND sc.source_product_code=p.source_product_code
              WHERE {base_where}
            ), aggregate_offers AS (
              SELECT b.tenant_id,b.marketplace_code,b.tenant_seller_id,b.source_product_code,
                     COUNT(o.id) FILTER (WHERE o.is_own=0 AND o.price_amount>0) AS references,
                     MIN(o.price_amount) FILTER (WHERE o.is_own=0 AND o.price_amount>0) AS market_min,
                     MAX(o.price_amount) FILTER (WHERE o.is_own=0 AND o.price_amount>0) AS market_max,
                     percentile_cont(0.25) WITHIN GROUP (ORDER BY o.price_amount) FILTER (WHERE o.is_own=0 AND o.price_amount>0) AS market_q1,
                     percentile_cont(0.5) WITHIN GROUP (ORDER BY o.price_amount) FILTER (WHERE o.is_own=0 AND o.price_amount>0) AS market_median,
                     COUNT(o.id) FILTER (WHERE o.is_own=0 AND o.price_amount>0 AND o.price_amount=(SELECT MIN(x.price_amount) FROM tenant_seller_offer_snapshots x WHERE x.tenant_id=b.tenant_id AND x.marketplace_code=b.marketplace_code AND x.tenant_seller_id=b.tenant_seller_id AND x.source_product_code=b.source_product_code AND x.captured_at=b.checked_at AND x.is_own=0 AND x.price_amount>0)) AS min_ties,
                     COUNT(o.id) FILTER (WHERE o.is_own=0 AND o.price_amount>0 AND o.price_amount=(SELECT MAX(x.price_amount) FROM tenant_seller_offer_snapshots x WHERE x.tenant_id=b.tenant_id AND x.marketplace_code=b.marketplace_code AND x.tenant_seller_id=b.tenant_seller_id AND x.source_product_code=b.source_product_code AND x.captured_at=b.checked_at AND x.is_own=0 AND x.price_amount>0)) AS max_ties
              FROM base b LEFT JOIN tenant_seller_offer_snapshots o
                ON o.tenant_id=b.tenant_id AND o.marketplace_code=b.marketplace_code
               AND o.tenant_seller_id=b.tenant_seller_id AND o.source_product_code=b.source_product_code
               AND o.captured_at=b.checked_at
              GROUP BY b.tenant_id,b.marketplace_code,b.tenant_seller_id,b.source_product_code
            ), analytics AS (
              SELECT b.*, a.references,a.market_min,a.market_max,a.market_q1,a.market_median,a.min_ties,a.max_ties,
                CASE
                  WHEN b.scan_status='error' THEN 'DATA_ERROR'
                  WHEN COALESCE(a.references,0)=0 AND b.scan_status IN ('ok','no_competitors') THEN 'NO_OTHER_SELLERS'
                  WHEN b.scan_status IS NULL THEN 'NOT_ANALYZED'
                  WHEN b.scan_status NOT IN ('ok','no_competitors','error') THEN 'REVIEW_REQUIRED'
                  WHEN b.price_amount>a.market_max THEN 'EXACT_HIGHEST'
                  WHEN b.price_amount=a.market_max THEN 'EXACT_TIED_HIGHEST'
                  WHEN b.price_amount<a.market_min THEN 'EXACT_LOWEST'
                  WHEN b.price_amount=a.market_min THEN 'EXACT_TIED_LOWEST'
                  WHEN b.price_amount>a.market_median THEN 'EXACT_ABOVE'
                  WHEN b.price_amount<a.market_median THEN 'EXACT_BELOW'
                  ELSE 'EXACT_IN_MARKET'
                END AS price_status,
                GREATEST(0, COALESCE(a.market_q1,0)-COALESCE(b.price_amount,0)) AS safe_potential
              FROM base b LEFT JOIN aggregate_offers a USING(tenant_id,marketplace_code,tenant_seller_id,source_product_code)
            )
        """
        conditions: list[str] = []
        extra: list[Any] = []
        if statuses:
            conditions.append("price_status IN (" + ",".join("?" for _ in statuses) + ")")
            extra.extend(sorted(statuses))
        if scope == "risks":
            conditions.append("price_status IN ('EXACT_HIGHEST','EXACT_TIED_HIGHEST','EXACT_ABOVE','EXACT_BELOW','DATA_ERROR')")
        elif scope == "opportunities":
            conditions.append("price_status IN ('EXACT_LOWEST','EXACT_TIED_LOWEST') AND safe_potential>0")
        elif scope in {"unscanned", "review"}:
            conditions.append("price_status IN ('NOT_ANALYZED','INSUFFICIENT_DATA','REVIEW_REQUIRED')")
        filtered = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = int(conn.execute(cte + " SELECT COUNT(*) AS count FROM analytics" + filtered, [*params, *extra]).fetchone()["count"])
        size = max(10, min(int(page_size), 200)); pages = max(1, math.ceil(total / size)); current = max(1, min(int(page), pages))
        sort_name = str(filters.get("sort") or "updated")
        if sort_name == "updated" and scope == "all":
            order = """CASE price_status WHEN 'EXACT_HIGHEST' THEN 0 WHEN 'EXACT_TIED_HIGHEST' THEN 1 WHEN 'EXACT_ABOVE' THEN 2 WHEN 'EXACT_BELOW' THEN 3 WHEN 'DATA_ERROR' THEN 4 WHEN 'EXACT_LOWEST' THEN 10 WHEN 'EXACT_TIED_LOWEST' THEN 11 WHEN 'EXACT_IN_MARKET' THEN 20 WHEN 'NO_OTHER_SELLERS' THEN 21 WHEN 'NOT_ANALYZED' THEN 40 WHEN 'INSUFFICIENT_DATA' THEN 41 WHEN 'REVIEW_REQUIRED' THEN 42 ELSE 30 END, CASE WHEN price_status IN ('EXACT_LOWEST','EXACT_TIED_LOWEST') THEN safe_potential ELSE ABS(COALESCE(price_amount-market_median,0)) END DESC, title ASC"""
        else:
            columns = {"title":"title", "price":"price_amount", "delta":"price_amount-market_median", "status":"price_status", "brand":"brand", "platform":"marketplace_code", "updated":"COALESCE(source_updated_at,last_seen_at)"}
            direction = "ASC" if str(filters.get("direction") or "desc").casefold()=="asc" else "DESC"
            order = f"{columns.get(sort_name, columns['updated'])} {direction}, source_product_code ASC"
        rows = conn.execute(cte + f" SELECT * FROM analytics{filtered} ORDER BY {order} LIMIT ? OFFSET ?", [*params, *extra, size, (current-1)*size]).fetchall()
        items: list[dict[str, Any]] = []
        labels = {"kaspi":"Kaspi","ozon":"Ozon.ru","ozon_kz":"Ozon.kz","halyk_market":"Halyk Market","forte_market":"Forte Market","wildberries":"Wildberries"}
        for raw in rows:
            value = dict(raw); status = str(value["price_status"]); info = STATUS_INFO.get(status, STATUS_INFO["NOT_ANALYZED"])
            amount = value.get("price_amount"); median = value.get("market_median")
            difference = (float(amount)-float(median)) if amount is not None and median is not None else None
            percentage = round(difference / float(median) * 100, 2) if difference is not None and float(median) else None
            code = self._catalog_product_code(str(value["marketplace_code"]), str(value["source_product_code"]))
            items.append({"product_code":code,"source_product_code":value["source_product_code"],"platform":value["marketplace_code"],"platform_label":labels.get(str(value["marketplace_code"]),str(value["marketplace_code"])),"title":value.get("title") or value["source_product_code"],"product_url":value.get("source_url") or "","brand":value.get("brand") or "","model":value.get("model") or "","image_url":value.get("image_url") or "","own_price_kzt":amount,"price_kzt":amount,"price_original":amount,"currency_original":value.get("currency") or "","market_price_kzt":median,"market_median_price_kzt":median,"market_min_price_kzt":value.get("market_min"),"market_max_price_kzt":value.get("market_max"),"difference_kzt":difference,"difference_pct":percentage,"price_status":status,"status_label":info["label"],"status_tone":info["tone"],"reference_count":int(value.get("references") or 0),"candidate_count":int(value.get("references") or 0),"safe_potential":float(value.get("safe_potential") or 0),"potential_margin_per_unit_kzt":float(value.get("safe_potential") or 0),"analytics_updated_at":value.get("checked_at"),"exact_offer_checked_at":value.get("checked_at"),"exact_offer_status":value.get("scan_status") or "not_checked","seller_name":value.get("seller_name") or "","seller_url":value.get("seller_url") or "","updated_at":value.get("source_updated_at") or value.get("last_seen_at"),"source_type":"TENANT_CATALOG"})
        return {"items":items,"page":current,"pages":pages,"page_size":size,"total":total,"lookup_strategy":"sql_analytics_projection"}

    def product_codes(
        self, filters: dict[str, Any], limit: int = 10000, user_id: int | None = None
    ) -> list[str]:
        marketplaces = self._filter_set(filters.get("platforms"))
        platform = str(filters.get("platform") or "").strip().casefold()
        if platform:
            marketplaces.add(platform)
        return [
            str(row["product_code"])
            for row in self.rows_for_user(user_id, marketplaces or None)
            if self._matches(row, filters)
        ][:max(1, min(int(limit), 10000))]

    def filter_options(
        self,
        allowed_platforms: set[str] | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        rows = self.rows_for_user(user_id, allowed_platforms)
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
                    {"value": "ozon", "label": "Ozon.ru"},
                    {"value": "ozon_kz", "label": "Ozon.kz"},
                    {"value": "halyk_market", "label": "Halyk Market"},
                    {"value": "forte_market", "label": "Forte Market"},
                    {"value": "wildberries", "label": "Wildberries"},
                ] if allowed_platforms is None or item["value"] in allowed_platforms
            ],
            "statuses": [{"value": status, "label": STATUS_INFO.get(status, {}).get("label", status), "tone": STATUS_INFO.get(status, {}).get("tone", "neutral")} for status in statuses],
        }

    def product(
        self,
        code: str,
        user_id: int | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        marketplace_scope = {"wildberries"} if str(code).startswith("wb:") else None
        visible_rows = rows if rows is not None else self.rows_for_user(
            user_id, marketplace_scope
        )
        base = next((
            row for row in visible_rows
            if str(row.get("product_code")) == str(code)
        ), None)
        if base is None:
            return None
        result = self._apply_user_values(base, self.preferences(user_id))
        result.pop("_price_sort", None); result.pop("_delta_sort", None); result.pop("_updated_sort", None)
        if result.pop("_tenant_catalog_only", False):
            result["specifications"] = normalize_specifications(
                result.pop("_tenant_attributes", [])
            )
            result["detail"] = None
            result["candidates"] = []
            result["offers"] = []
            result["history"] = []
            return result
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
        if result.get("platform") == "forte_market":
            product_id = str(result.get("source_product_code") or "").removeprefix("forte:")
            conn = self._connect()
            try:
                detail = conn.execute("SELECT * FROM forte_products WHERE product_id=?", (product_id,)).fetchone()
                offers = conn.execute(
                    """
                    SELECT merchant_name,merchant_key,merchant_id,price_kzt,merchant_rating,merchant_reviews,
                           offer_type,availability_status,is_own,last_checked_at
                    FROM forte_offers
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
                    "merchant_rating": row["merchant_rating"],
                    "merchant_reviews": row["merchant_reviews"],
                    "captured_at": row["last_checked_at"],
                    "product_url": result.get("product_url") or "",
                    "match_method": "FORTE_PRODUCT_ID",
                    "match_method_label": "Та же карточка Forte Market",
                }
                for row in offers
            ]
            result["history"] = self.price_history(code, user_id=user_id)
            return result
        if result.get("platform") == "ozon_kz":
            product_id = str(result.get("source_product_code") or "")
            detail = None
            offers: list[dict[str, Any]] = []
            if self.ozon_kz_db_path and self.ozon_kz_db_path.exists():
                conn = self._connect_path(self.ozon_kz_db_path)
                try:
                    detail_row = conn.execute(
                        "SELECT * FROM ozon_kz_products WHERE product_id=?",
                        (product_id,),
                    ).fetchone()
                    detail = dict(detail_row) if detail_row else None
                    offers = [dict(row) for row in conn.execute(
                        """SELECT * FROM ozon_kz_offers
                           WHERE product_id=? AND active=1
                           ORDER BY is_own DESC,price_kzt,seller_name LIMIT 100""",
                        (product_id,),
                    ).fetchall()]
                finally:
                    conn.close()
            result["specifications"] = normalize_specifications(
                (detail or {}).get("specifications_json") or []
            )
            result["detail"] = detail
            result["candidates"] = result.get("exact_candidates") or []
            result["offers"] = offers
            result["history"] = self.price_history(code, user_id=user_id)
            result.pop("_ozon_kz_specifications", None)
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
            except database_error_types():
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

    def targeted_product(
        self, code: str, user_id: int,
    ) -> dict[str, Any] | None:
        """Read one tenant catalogue entry without materializing its catalogue.

        Seller-scoped catalogues are authoritative and already contain the
        current own price, attributes and source metadata.  Legacy shared
        analytics still use the established fallback in ``product`` so their
        exact-offer behavior is not changed by this performance fix.
        """
        tenant_id = self._tenant_id_for_user(user_id)
        if tenant_id is None:
            return None
        platform, _, source_code = parse_product_code(code)
        snapshot = self._load_tenant_catalog_snapshot(
            tenant_id, {platform}, source_product_code=source_code,
        )
        base = next(
            (row for row in snapshot if str(row.get("product_code") or "") == str(code)),
            None,
        )
        if base is not None:
            result = self.product(code, user_id, rows=[base])
            if result is not None:
                result["lookup_strategy"] = "targeted"
            return result
        # A populated tenant projection is authoritative for its marketplace.
        # Do not turn a normal detail miss into a full shared-catalog scan.
        conn = self._connect()
        try:
            exists = conn.execute(
                """SELECT 1 FROM tenant_seller_catalog_products
                   WHERE tenant_id=? AND marketplace_code=? AND active=1 LIMIT 1""",
                (int(tenant_id), platform),
            ).fetchone() or conn.execute(
                """SELECT 1 FROM tenant_catalog_products
                   WHERE tenant_id=? AND marketplace_code=? AND active=1 LIMIT 1""",
                (int(tenant_id), platform),
            ).fetchone()
        finally:
            conn.close()
        if exists:
            return None
        # Preserve collector-only details solely for an unmigrated legacy
        # marketplace which has no tenant projection at all.
        result = self.product(code, user_id)
        if result is not None:
            result["lookup_strategy"] = "legacy_fallback"
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
        parsed_platform, parsed_seller_id, parsed_source_code = parse_product_code(code)
        if parsed_seller_id and parsed_platform != "kaspi":
            # Seller-scoped collector registries are intentionally not merged
            # into the old global registry. Returning no history is safer than
            # exposing another account's price series.
            return []
        if str(code).startswith("ozon_kz:"):
            product_id = parsed_source_code
            if not self.ozon_kz_db_path or not self.ozon_kz_db_path.exists():
                return []
            conn = self._connect_path(self.ozon_kz_db_path)
            try:
                return [dict(row) for row in conn.execute(
                    """SELECT captured_at AS at,price_kzt AS price,
                              CASE WHEN seller_id='' THEN 'own' ELSE seller_id END AS series,
                              availability_status
                       FROM ozon_kz_price_history
                       WHERE product_id=? ORDER BY captured_at DESC LIMIT ?""",
                    (product_id, int(limit)),
                ).fetchall()]
            except database_error_types():
                return []
            finally:
                conn.close()
        if str(code).startswith("forte:"):
            product_id = parsed_source_code
            conn = self._connect()
            try:
                own = conn.execute(
                    """
                    SELECT captured_at AS at,price_kzt AS price,'own' AS series
                    FROM forte_price_history
                    WHERE product_id=? AND is_own=1 AND price_kzt IS NOT NULL
                    ORDER BY id DESC LIMIT ?
                    """,
                    (product_id, int(limit)),
                ).fetchall()
                market = conn.execute(
                    """
                    SELECT captured_at AS at,MIN(price_kzt) AS price,'market' AS series
                    FROM forte_price_history
                    WHERE product_id=? AND is_own=0 AND price_kzt IS NOT NULL
                    GROUP BY run_id,captured_at ORDER BY captured_at DESC LIMIT ?
                    """,
                    (product_id, int(limit)),
                ).fetchall()
                return sorted([dict(row) for row in own] + [dict(row) for row in market], key=lambda item: item.get("at") or "")
            finally:
                conn.close()
        if str(code).startswith("halyk:"):
            product_id = parsed_source_code
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
            article = parsed_source_code
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
        tenant_seller_id, raw_code = parsed_seller_id, parsed_source_code
        conn = self._connect()
        try:
            if tenant_seller_id:
                own = conn.execute(
                    """SELECT captured_at AS at,price_amount AS price,'own' AS series
                       FROM tenant_seller_price_snapshots
                       WHERE tenant_seller_id=? AND marketplace_code='kaspi'
                         AND source_product_code=? AND status='ok'
                         AND price_amount IS NOT NULL
                       ORDER BY id DESC LIMIT ?""",
                    (int(tenant_seller_id), raw_code, int(limit)),
                ).fetchall()
                market = conn.execute(
                    """SELECT captured_at AS at,MIN(price_amount) AS price,
                              'market' AS series
                       FROM tenant_seller_offer_snapshots
                       WHERE tenant_seller_id=? AND marketplace_code='kaspi'
                         AND source_product_code=? AND is_own=0
                         AND price_amount IS NOT NULL
                       GROUP BY run_id,captured_at ORDER BY captured_at DESC LIMIT ?""",
                    (int(tenant_seller_id), raw_code, int(limit)),
                ).fetchall()
                return sorted(
                    [dict(row) for row in own] + [dict(row) for row in market],
                    key=lambda item: item.get("at") or "",
                )
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
            except database_error_types():
                pass
            return sorted([dict(row) for row in own] + [dict(row) for row in market], key=lambda item: item.get("at") or "")
        finally:
            conn.close()

    def set_product_state(self, codes: list[str], watched: bool | None, priority: str | None, note: str | None, user_id: int, expected_monthly_units: int | None = None) -> int:
        clean_codes = sorted({str(code).strip() for code in codes if str(code).strip()})
        if not clean_codes:
            return 0
        visible_codes = {
            str(row.get("product_code") or "") for row in self.rows_for_user(user_id)
        }
        if set(clean_codes) - visible_codes:
            raise PermissionError("Один или несколько товаров не принадлежат каталогу компании.")
        if priority is not None and priority not in {"low", "normal", "high", "critical"}:
            raise ValueError("Неизвестный приоритет.")
        tenant_id = self._tenant_id_for_user(user_id)
        if tenant_id is None:
            raise PermissionError("Пользователь не привязан к активной организации.")
        conn = self._connect()
        try:
            for code in clean_codes:
                platform, seller_id, source_code = parse_product_code(code)
                raw_code = (
                    code if seller_id or platform != "kaspi" else source_code
                )
                current = conn.execute(
                    """SELECT watched,priority,note,expected_monthly_units
                       FROM tenant_product_state WHERE tenant_id=? AND product_code=?""",
                    (tenant_id, raw_code),
                ).fetchone()
                current_values = dict(current) if current else {"watched": 0, "priority": "normal", "note": "", "expected_monthly_units": None}
                units = current_values.get("expected_monthly_units") if expected_monthly_units is None else max(0, int(expected_monthly_units))
                conn.execute(
                    """INSERT INTO tenant_product_state(
                           tenant_id,product_code,watched,priority,note,
                           expected_monthly_units,updated_by,updated_at
                       ) VALUES(?,?,?,?,?,?,?,datetime('now'))
                       ON CONFLICT(tenant_id,product_code) DO UPDATE SET
                           watched=excluded.watched,priority=excluded.priority,
                           note=excluded.note,expected_monthly_units=excluded.expected_monthly_units,
                           updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (tenant_id, raw_code, int(watched if watched is not None else bool(current_values.get("watched"))),
                      priority if priority is not None else current_values.get("priority") or "normal",
                      note if note is not None else current_values.get("note") or "", units, int(user_id)),
                )
            conn.execute(
                """INSERT INTO app_events(
                       user_id,tenant_id,event_type,entity_type,entity_id,details_json,created_at
                   ) VALUES(?,?,?,?,?,?,datetime('now'))""",
                (int(user_id), tenant_id, "product_state_updated", "product_set", str(len(clean_codes)), json.dumps({"codes": clean_codes[:100], "watched": watched, "priority": priority, "expected_monthly_units": expected_monthly_units}, ensure_ascii=False)),
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
