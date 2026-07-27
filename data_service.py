from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from engine.kaspi_market_v9_1 import Database, enriched_comparison_rows, status_snapshot
from market_intelligence import (
    STATUS_INFO,
    Candidate,
    exact_offer_position,
    identity,
    normalize_specifications,
)
from schema import ensure_database

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


class DataService:
    def __init__(
        self,
        db_path: Path,
        seller_name: str,
        ozon_db_path: Path | None = None,
        seller_id: str = "",
    ):
        self.db_path = Path(db_path)
        self.seller_name = seller_name
        self.seller_id = seller_id
        self.ozon_db_path = Path(ozon_db_path) if ozon_db_path else None
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

    def preferences(self, user_id: int | None) -> dict[str, Any]:
        defaults = {
            "locale": "ru",
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
                "SELECT locale,display_currency,rub_to_kzt,usd_to_kzt,eur_to_kzt,default_monthly_units "
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
        locale = str(payload.get("locale") or "ru").lower()
        if locale not in {"ru", "kk", "en"}:
            raise ValueError("Поддерживаются языки ru, kk и en.")
        display_currency = str(payload.get("display_currency") or "KZT").upper()
        if display_currency not in {"KZT", "RUB", "USD", "EUR"}:
            raise ValueError("Некорректная валюта отображения.")
        values = {
            "locale": locale,
            "display_currency": display_currency,
            "rub_to_kzt": max(0.0001, float(payload.get("rub_to_kzt", 5.5))),
            "usd_to_kzt": max(0.0001, float(payload.get("usd_to_kzt", 520))),
            "eur_to_kzt": max(0.0001, float(payload.get("eur_to_kzt", 565))),
            "default_monthly_units": max(0, min(1_000_000, int(payload.get("default_monthly_units", 1)))),
        }
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO app_user_preferences(
                    user_id,locale,display_currency,rub_to_kzt,usd_to_kzt,eur_to_kzt,
                    default_monthly_units,updated_at
                ) VALUES(?,?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    locale=excluded.locale,display_currency=excluded.display_currency,
                    rub_to_kzt=excluded.rub_to_kzt,usd_to_kzt=excluded.usd_to_kzt,
                    eur_to_kzt=excluded.eur_to_kzt,
                    default_monthly_units=excluded.default_monthly_units,updated_at=excluded.updated_at
                """,
                (int(user_id), values["locale"], values["display_currency"], values["rub_to_kzt"],
                 values["usd_to_kzt"], values["eur_to_kzt"], values["default_monthly_units"]),
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
            expected_units = state.get("expected_monthly_units")
            row = {
                **base,
                **position,
                "product_code": code,
                "source_product_code": code,
                "platform": "kaspi",
                "platform_label": "Kaspi",
                "title": title,
                "brand": source_brand or ident.get("brand") or "",
                "model": ident.get("model") or "",
                "size": base.get("size") or self._identity_size(ident),
                "product_type": base.get("product_type") or ident.get("type") or "",
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
    def _ozon_product_type(title: Any) -> str:
        text = str(title or "").casefold()
        if "мотокамера" in text or "камера" in text and "мото" in text:
            return "motorcycle_tube"
        if "мотошин" in text or "мото шин" in text:
            return "motorcycle_tire"
        if "грузов" in text or "для груз" in text:
            return "truck_tire"
        if "шина" in text or "шины" in text:
            return "passenger_tire"
        return "other"

    def _ozon_rows(self) -> list[dict[str, Any]]:
        path = self.ozon_db_path
        if not path or not path.exists():
            return []
        try:
            conn = self._connect_path(path)
            rows = conn.execute(
                """
                SELECT p.*,o.seller_id,o.seller_name,o.seller_url,o.seller_rating,
                       o.card_price,o.catalog_price AS offer_catalog_price,o.regular_price,
                       o.original_price,o.currency,o.availability_status,o.location_city,
                       o.location_country,o.product_rating,o.review_count,o.last_checked_at
                FROM products p
                LEFT JOIN offers o ON o.article=p.article AND o.seller_key=(
                    SELECT o2.seller_key FROM offers o2
                    WHERE o2.article=p.article AND o2.active=1
                    ORDER BY CASE WHEN o2.card_price>0 THEN 0 ELSE 1 END, o2.card_price ASC, o2.last_checked_at DESC
                    LIMIT 1
                )
                WHERE p.active=1
                ORDER BY p.last_seen_at DESC
                """
            ).fetchall()
            result = []
            for record in rows:
                value = dict(record)
                article = str(value.get("article") or "")
                price_rub = value.get("card_price") or value.get("catalog_price") or 0
                status = "DATA_COLLECTED" if value.get("detail_status") == "COMPLETE" else (
                    "DATA_ERROR" if value.get("last_error") else "NOT_ANALYZED"
                )
                info = STATUS_INFO.get(status, STATUS_INFO["NOT_ANALYZED"])
                result.append({
                    "product_code": f"ozon:{article}",
                    "source_product_code": article,
                    "platform": "ozon",
                    "platform_label": "Ozon",
                    "title": value.get("title") or "",
                    "brand": value.get("brand") or "",
                    "model": value.get("model") or "",
                    "size": value.get("tire_size") or "",
                    "product_type": self._ozon_product_type(value.get("title")),
                    "strict_identity_eligible": bool(
                        self._ozon_product_type(value.get("title")) in {"passenger_tire", "truck_tire"}
                        and float(value.get("identity_completeness_percent") or 0) >= 75
                    ),
                    "product_url": value.get("canonical_url") or "",
                    "image_url": value.get("image_url") or "",
                    "own_price_kzt": None,
                    "market_price_kzt": None,
                    "price_original": price_rub,
                    "currency_original": value.get("currency") or "RUB",
                    "regular_price_original": value.get("regular_price") or 0,
                    "seller_id": value.get("seller_id") or "",
                    "seller_name": value.get("seller_name") or "",
                    "seller_url": value.get("seller_url") or "",
                    "seller_rating": value.get("seller_rating"),
                    "catalog_rating": value.get("product_rating"),
                    "catalog_reviews": value.get("review_count"),
                    "availability_status": value.get("availability_status") or "UNKNOWN",
                    "price_status": status,
                    "status_label": info["label"],
                    "status_tone": info["tone"],
                    "identity_completeness_percent": value.get("identity_completeness_percent") or 0,
                    "manufacturer_article": value.get("manufacturer_article") or "",
                    "load_index": value.get("load_index") or "",
                    "speed_index": value.get("speed_index") or "",
                    "season": value.get("season") or "UNKNOWN",
                    "studded": value.get("studded"),
                    "xl": bool(value.get("xl")),
                    "runflat": bool(value.get("runflat")),
                    "watched": False,
                    "priority": "normal",
                    "note": "",
                    "candidate_count": 0,
                    "_price_sort": float(price_rub or 0),
                    "_delta_sort": 0.0,
                    "_updated_sort": value.get("last_checked_at") or value.get("last_seen_at") or "",
                })
            return result
        except sqlite3.Error:
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

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
            self._rows_cache = self._kaspi_rows() + self._ozon_rows()
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
        else:
            item["price_kzt"] = item.get("own_price_kzt")
        units = item.get("expected_monthly_units")
        if units is None:
            units = int(preferences.get("default_monthly_units") or 0)
        item["expected_monthly_units"] = units
        potential = float(item.get("potential_margin_per_unit_kzt") or 0)
        item["potential_margin_monthly_kzt"] = round(potential * int(units or 0), 2)
        return item

    def overview(self, expected_count: int, assumed_workers: int = 2, user_id: int | None = None) -> dict[str, Any]:
        try:
            db = Database(self.db_path)
            snapshot = status_snapshot(db, assumed_workers)
            db.conn.close()
        except Exception:
            snapshot = {}
        preferences = self.preferences(user_id)
        rows = [self._apply_user_values(row, preferences) for row in self.rows()]
        kaspi_rows = [row for row in rows if row.get("platform") == "kaspi"]
        ozon_rows = [row for row in rows if row.get("platform") == "ozon"]
        counts = Counter(str(row.get("price_status") or "NOT_ANALYZED") for row in kaspi_rows)
        active_count = len(rows)
        priced_count = sum(1 for row in rows if row.get("price_kzt") is not None)
        analyzed_count = sum(1 for row in kaspi_rows if row.get("price_status") not in UNSCANNED_STATUSES)
        risk_count = sum(counts[key] for key in RISK_STATUSES)
        opportunity_count = sum(counts[key] for key in OPPORTUNITY_STATUSES)
        potential_monthly = sum(float(row.get("potential_margin_monthly_kzt") or 0) for row in kaspi_rows)
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
            "expected_count": effective_expected,
            "catalog_coverage_pct": round(len(kaspi_rows) / effective_expected * 100, 2) if effective_expected else None,
            "catalog_sync": latest_catalog_sync_value,
            "priced_count": priced_count,
            "price_coverage_pct": round(priced_count / active_count * 100, 2) if active_count else 0,
            "scanned_count": analyzed_count,
            "scan_coverage_pct": round(analyzed_count / len(kaspi_rows) * 100, 2) if kaspi_rows else 0,
            "watched_count": sum(1 for row in rows if row.get("watched")),
            "risk_count": risk_count,
            "favorable_count": opportunity_count,
            "opportunity_count": opportunity_count,
            "potential_margin_monthly_kzt": round(potential_monthly, 2),
            "status_distribution": status_distribution,
            "snapshot": snapshot,
            "last_own_price_at": last_own_price,
            "recent_events": recent_events,
            "preferences": preferences,
            "health": {
                "catalog": (
                    "ok" if kaspi_rows and (not effective_expected or len(kaspi_rows) >= effective_expected * 0.97)
                    else "warning" if kaspi_rows else "empty"
                ),
                "prices": "ok" if priced_count >= max(1, active_count * 0.9) else ("warning" if priced_count else "empty"),
                "market": "ok" if analyzed_count >= max(1, len(kaspi_rows) * 0.5) else ("warning" if analyzed_count else "empty"),
            },
        }

    def analytics_dashboard(self, user_id: int | None = None) -> dict[str, Any]:
        preferences = self.preferences(user_id)
        rows = [self._apply_user_values(row, preferences) for row in self.rows()]
        kaspi_rows = [row for row in rows if row.get("platform") == "kaspi"]
        ozon_rows = [row for row in rows if row.get("platform") == "ozon"]

        status_counts = Counter(str(row.get("price_status") or "NOT_ANALYZED") for row in kaspi_rows)
        analyzed_rows = [row for row in kaspi_rows if str(row.get("price_status") or "") not in UNSCANNED_STATUSES]
        risk_rows = [row for row in kaspi_rows if str(row.get("price_status") or "") in RISK_STATUSES]
        opportunity_rows = [row for row in kaspi_rows if str(row.get("price_status") or "") in OPPORTUNITY_STATUSES]
        review_rows = [row for row in kaspi_rows if str(row.get("price_status") or "") == "REVIEW_REQUIRED"]
        insufficient_rows = [row for row in kaspi_rows if str(row.get("price_status") or "") == "INSUFFICIENT_DATA"]

        valid_deltas = [float(row.get("difference_pct")) for row in analyzed_rows if row.get("difference_pct") is not None]
        average_delta = round(sum(valid_deltas) / len(valid_deltas), 2) if valid_deltas else 0.0
        potential_total = round(sum(float(row.get("potential_margin_monthly_kzt") or 0) for row in opportunity_rows), 2)

        brands: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "brand": "Без бренда", "total": 0, "risks": 0, "opportunities": 0,
            "review": 0, "potential_margin_monthly_kzt": 0.0,
        })
        for row in kaspi_rows:
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
        for name, values in (("Kaspi", kaspi_rows), ("Ozon", ozon_rows)):
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
        for row in kaspi_rows:
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
                "analyzed_count": len(analyzed_rows),
                "analysis_coverage_pct": round(len(analyzed_rows) / len(kaspi_rows) * 100, 2) if kaspi_rows else 0.0,
                "risk_count": len(risk_rows),
                "opportunity_count": len(opportunity_rows),
                "review_count": len(review_rows),
                "insufficient_count": len(insufficient_rows),
                "potential_margin_monthly_kzt": potential_total,
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
    def _matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
        query = str(filters.get("query") or "").strip().casefold()
        if query:
            haystack = " ".join(str(row.get(key) or "") for key in (
                "product_code", "source_product_code", "title", "brand", "model", "size", "seller_name"
            )).casefold()
            if query not in haystack:
                return False
        platform = str(filters.get("platform") or "").strip().casefold()
        if platform and str(row.get("platform") or "").casefold() != platform:
            return False
        brand = str(filters.get("brand") or "").strip().casefold()
        if brand and str(row.get("brand") or "").casefold() != brand:
            return False
        status = str(filters.get("status") or "").strip().upper()
        if status and str(row.get("price_status") or "").upper() != status:
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
            "brand", "model", "size", "product_type", "own_price_kzt", "market_price_kzt", "price_kzt",
            "price_original", "currency_original", "difference_kzt", "difference_pct", "price_status",
            "status_label", "status_tone", "reference_type", "reference_count", "market_min_price_kzt",
            "market_max_price_kzt", "market_median_price_kzt", "lowest_product_url", "highest_product_url",
            "price_rank", "price_rank_total", "potential_margin_per_unit_kzt", "potential_margin_monthly_kzt",
            "expected_monthly_units", "watched", "priority", "note", "catalog_rating", "catalog_reviews",
            "image_url", "seller_name", "seller_url", "identity_completeness_percent", "candidate_count",
            "match_method", "match_method_label", "exact_offer_status", "exact_offer_checked_at",
            "exact_offer_count", "competitor_seller_count", "legacy_candidate_count",
            "_updated_sort", "raw_price_status",
        }
        items = [{key: row.get(key) for key in fields} for row in rows[start:start + page_size]]
        return {"items": items, "page": page, "pages": pages, "page_size": page_size, "total": total}

    def product_codes(self, filters: dict[str, Any], limit: int = 10000) -> list[str]:
        return [str(row["product_code"]) for row in self.rows() if self._matches(row, filters)][:max(1, min(int(limit), 10000))]

    def filter_options(self) -> dict[str, Any]:
        rows = self.rows()
        brands = sorted({str(row.get("brand") or "").strip() for row in rows if str(row.get("brand") or "").strip()}, key=str.casefold)
        statuses = sorted({str(row.get("price_status") or "NOT_ANALYZED") for row in rows})
        return {
            "brands": brands,
            "platforms": [{"value": "kaspi", "label": "Kaspi"}, {"value": "ozon", "label": "Ozon"}],
            "statuses": [{"value": status, "label": STATUS_INFO.get(status, {}).get("label", status), "tone": STATUS_INFO.get(status, {}).get("tone", "neutral")} for status in statuses],
        }

    def product(self, code: str, user_id: int | None = None) -> dict[str, Any] | None:
        base = next((row for row in self.rows() if str(row.get("product_code")) == str(code)), None)
        if base is None:
            return None
        result = self._apply_user_values(base, self.preferences(user_id))
        result.pop("_price_sort", None); result.pop("_delta_sort", None); result.pop("_updated_sort", None)
        if result.get("platform") == "ozon":
            result["specifications"] = self._ozon_specifications(result)
            result["candidates"] = []
            result["offers"] = self._ozon_offers(str(result.get("source_product_code")))
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
                raw_code = code.removeprefix("kaspi:")
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

    def latest_events(self, limit: int = 40) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT e.id,e.event_type,e.entity_type,e.entity_id,e.details_json,e.created_at,u.display_name,u.email
                   FROM app_events e LEFT JOIN app_users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT ?""",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            result = []
            for row in rows:
                value = dict(row); value["details"] = self._json(value.pop("details_json", ""), {}); result.append(value)
            return result
        finally:
            conn.close()
