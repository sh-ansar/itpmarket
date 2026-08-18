from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from marketplace_registry import parse_product_code
from storage.database_backend import DatabaseBackend, DatabaseSettings
from storage.postgres_compat import connect_database, database_error_types


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


class InventoryService:
    """Tenant master-product inventory and confirmed marketplace listings.

    Quantity and purchase cost belong to one physical inventory item. Marketplace
    listings only point to that item, so the same stock is never summed once per
    marketplace.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self):
        conn = connect_database(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _begin_write(conn) -> None:
        if DatabaseSettings.from_environment().backend is DatabaseBackend.SQLITE:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("BEGIN")

    @staticmethod
    def _listing_values(row: dict[str, Any]) -> tuple[str, int | None, str]:
        platform, seller_id, source_code = parse_product_code(row.get("product_code"))
        return platform, seller_id, source_code

    @staticmethod
    def _pair(left: str, right: str) -> tuple[str, str]:
        return tuple(sorted((str(left), str(right))))  # type: ignore[return-value]

    @staticmethod
    def _pricing(inventory: dict[str, Any], own_price: Any) -> dict[str, Any]:
        quantity = max(0, int(inventory.get("quantity_on_hand") or 0))
        purchase = inventory.get("purchase_price_kzt")
        purchase_value = float(purchase) if purchase not in (None, "") else None
        markup = max(0.0, float(inventory.get("target_markup_percent") or 0))
        price = float(own_price) if own_price not in (None, "") else None
        recommended = (
            round(purchase_value * (1 + markup / 100), 2)
            if purchase_value is not None else None
        )
        gross_per_unit = (
            round(price - purchase_value, 2)
            if price is not None and purchase_value is not None else None
        )
        if purchase_value is None or price is None:
            signal = "NO_COST" if purchase_value is None else "NO_PRICE"
        elif price < purchase_value:
            signal = "BELOW_COST"
        elif recommended is not None and price < recommended:
            signal = "BELOW_TARGET"
        else:
            signal = "TARGET_MET"
        return {
            "stock_value_kzt": (
                round(quantity * purchase_value, 2)
                if purchase_value is not None else None
            ),
            "recommended_min_price_kzt": recommended,
            "gross_profit_per_unit_kzt": gross_per_unit,
            "gross_profit_on_hand_kzt": (
                round(quantity * gross_per_unit, 2)
                if gross_per_unit is not None else None
            ),
            "pricing_signal": signal,
            "recommendation_basis": "PURCHASE_PRICE_PLUS_TARGET_MARKUP",
        }

    @staticmethod
    def _suggestion(source: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
        if str(source.get("platform")) == str(candidate.get("platform")):
            return None
        source_article = compact(source.get("manufacturer_article"))
        candidate_article = compact(candidate.get("manufacturer_article"))
        source_brand = compact(source.get("brand"))
        candidate_brand = compact(candidate.get("brand"))
        if (
            source_article and candidate_article and source_article == candidate_article
            and source_brand and source_brand == candidate_brand
        ):
            method, score, reason = (
                "MANUFACTURER_ARTICLE",
                100.0,
                "Совпали бренд и артикул производителя",
            )
        else:
            source_key = str(source.get("exact_characteristic_key") or "").strip()
            candidate_key = str(candidate.get("exact_characteristic_key") or "").strip()
            if source_key and source_key == candidate_key:
                method, score, reason = (
                    "STRICT_CHARACTERISTICS",
                    96.0,
                    "Совпали бренд, модель, размер и строгие характеристики",
                )
            else:
                same_base = bool(
                    source_brand
                    and source_brand == candidate_brand
                    and compact(source.get("size"))
                    and compact(source.get("size")) == compact(candidate.get("size"))
                    and str(source.get("product_type") or "")
                    == str(candidate.get("product_type") or "")
                )
                if not same_base:
                    return None
                left_model = compact(source.get("model"))
                right_model = compact(candidate.get("model"))
                similarity = (
                    SequenceMatcher(None, left_model, right_model).ratio()
                    if left_model and right_model else 0.0
                )
                if similarity < 0.82:
                    return None
                method = "BRAND_MODEL_SIZE_REVIEW"
                score = round(70 + similarity * 20, 2)
                reason = "Совпали бренд, размер и близкая модель; требуется подтверждение"
        return {
            "listing_code": str(candidate.get("product_code") or ""),
            "platform": str(candidate.get("platform") or ""),
            "platform_label": str(candidate.get("platform_label") or candidate.get("platform") or ""),
            "title": str(candidate.get("title") or ""),
            "seller_name": str(candidate.get("seller_name") or ""),
            "price_kzt": candidate.get("price_kzt") or candidate.get("own_price_kzt"),
            "match_method": method,
            "match_score": score,
            "match_reason": reason,
        }

    def context(
        self,
        tenant_id: int,
        listing_code: str,
        rows: list[dict[str, Any]],
        *,
        include_inventory: bool,
    ) -> dict[str, Any]:
        by_code = {str(row.get("product_code") or ""): row for row in rows}
        source = by_code.get(str(listing_code))
        if not source:
            raise PermissionError("Товар не принадлежит каталогу компании.")
        conn = self._connect()
        try:
            linked = conn.execute(
                """SELECT l.*,i.internal_sku,i.title AS inventory_title,
                          i.quantity_on_hand,i.purchase_price_kzt,
                          i.target_markup_percent,i.notes,i.updated_at AS inventory_updated_at
                   FROM tenant_product_listings l
                   JOIN tenant_inventory_products i
                     ON i.tenant_id=l.tenant_id AND i.id=l.inventory_product_id
                   WHERE l.tenant_id=? AND l.listing_code=?""",
                (int(tenant_id), str(listing_code)),
            ).fetchone()
            rejected_rows = conn.execute(
                """SELECT source_listing_code,candidate_listing_code
                   FROM tenant_product_match_decisions
                   WHERE tenant_id=? AND decision='rejected'
                     AND (source_listing_code=? OR candidate_listing_code=?)""",
                (int(tenant_id), str(listing_code), str(listing_code)),
            ).fetchall()
            rejected = {
                str(row["candidate_listing_code"])
                if str(row["source_listing_code"]) == str(listing_code)
                else str(row["source_listing_code"])
                for row in rejected_rows
            }
            link_rows = conn.execute(
                """SELECT listing_code,inventory_product_id
                   FROM tenant_product_listings WHERE tenant_id=?""",
                (int(tenant_id),),
            ).fetchall()
            link_map = {
                str(row["listing_code"]): int(row["inventory_product_id"])
                for row in link_rows
            }
            inventory: dict[str, Any] | None = None
            linked_listings: list[dict[str, Any]] = []
            if linked:
                inventory_id = int(linked["inventory_product_id"])
                for row in conn.execute(
                    """SELECT listing_code,marketplace_code,tenant_seller_id,
                              source_product_code,match_method,match_score,confirmed_at
                       FROM tenant_product_listings
                       WHERE tenant_id=? AND inventory_product_id=?
                       ORDER BY marketplace_code,listing_code""",
                    (int(tenant_id), inventory_id),
                ).fetchall():
                    value = dict(row)
                    catalog_row = by_code.get(str(value["listing_code"]), {})
                    value["title"] = str(catalog_row.get("title") or "")
                    value["platform_label"] = str(
                        catalog_row.get("platform_label") or value["marketplace_code"]
                    )
                    linked_listings.append(value)
                inventory = {
                    "id": inventory_id,
                    "internal_sku": str(linked["internal_sku"] or ""),
                    "title": str(linked["inventory_title"] or ""),
                    "quantity_on_hand": int(linked["quantity_on_hand"] or 0),
                    "target_markup_percent": float(linked["target_markup_percent"] or 0),
                    "notes": str(linked["notes"] or ""),
                    "updated_at": linked["inventory_updated_at"],
                    "linked_listings": linked_listings,
                }
                if include_inventory:
                    inventory["purchase_price_kzt"] = linked["purchase_price_kzt"]
                    inventory.update(self._pricing(inventory | {
                        "purchase_price_kzt": linked["purchase_price_kzt"]
                    }, source.get("price_kzt") or source.get("own_price_kzt")))
        finally:
            conn.close()

        # Matching is CPU work and can scan a large tenant catalogue. Never
        # keep one of the bounded PostgreSQL connections checked out here.
        suggestions: list[dict[str, Any]] = []
        for candidate_code, candidate in by_code.items():
            if candidate_code == str(listing_code) or candidate_code in rejected:
                continue
            suggestion = self._suggestion(source, candidate)
            if not suggestion:
                continue
            candidate_inventory_id = link_map.get(candidate_code)
            current_inventory_id = int(linked["inventory_product_id"]) if linked else None
            suggestion["status"] = (
                "confirmed"
                if current_inventory_id and candidate_inventory_id == current_inventory_id
                else "conflict"
                if current_inventory_id and candidate_inventory_id
                and candidate_inventory_id != current_inventory_id
                else "suggested"
            )
            suggestions.append(suggestion)
        suggestions.sort(
            key=lambda item: (
                item["status"] != "confirmed",
                -float(item.get("match_score") or 0),
                str(item.get("platform")),
            )
        )
        return {
            "can_view_inventory": bool(include_inventory),
            "inventory": inventory if include_inventory else None,
            "has_inventory_link": bool(linked),
            "matching": {
                "suggestions": suggestions[:30],
                "confirmed_listings": linked_listings,
                "dynamic": True,
            },
        }

    def save_inventory(
        self,
        tenant_id: int,
        listing_code: str,
        source_row: dict[str, Any],
        payload: dict[str, Any],
        actor_user_id: int,
    ) -> int:
        try:
            quantity = int(payload.get("quantity_on_hand") or 0)
            purchase_raw = payload.get("purchase_price_kzt")
            purchase = None if purchase_raw in (None, "") else round(float(purchase_raw), 2)
            markup = round(float(payload.get("target_markup_percent", 20)), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("Количество, закупочная цена или наценка заполнены неверно.") from exc
        if quantity < 0 or quantity > 1_000_000_000:
            raise ValueError("Количество должно быть от 0 до 1 000 000 000.")
        if purchase is not None and (purchase < 0 or purchase > 1_000_000_000_000):
            raise ValueError("Закупочная цена должна быть неотрицательной.")
        if markup < 0 or markup > 1000:
            raise ValueError("Целевая наценка должна быть от 0 до 1000%.")
        sku = str(payload.get("internal_sku") or "").strip()[:120]
        notes = str(payload.get("notes") or "").strip()[:2000]
        title = str(payload.get("title") or source_row.get("title") or "").strip()[:500]
        stamp = now_iso()
        platform, seller_id, source_code = self._listing_values(source_row)
        conn = self._connect()
        try:
            self._begin_write(conn)
            link = conn.execute(
                """SELECT inventory_product_id FROM tenant_product_listings
                   WHERE tenant_id=? AND listing_code=?""",
                (int(tenant_id), str(listing_code)),
            ).fetchone()
            before: dict[str, Any] = {}
            if link:
                inventory_id = int(link["inventory_product_id"])
                row = conn.execute(
                    "SELECT * FROM tenant_inventory_products WHERE tenant_id=? AND id=?",
                    (int(tenant_id), inventory_id),
                ).fetchone()
                before = dict(row) if row else {}
                conn.execute(
                    """UPDATE tenant_inventory_products
                       SET internal_sku=?,title=?,quantity_on_hand=?,purchase_price_kzt=?,
                           target_markup_percent=?,notes=?,updated_by=?,updated_at=?
                       WHERE tenant_id=? AND id=?""",
                    (
                        sku, title, quantity, purchase, markup, notes,
                        int(actor_user_id), stamp, int(tenant_id), inventory_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    """INSERT INTO tenant_inventory_products(
                           tenant_id,internal_sku,title,quantity_on_hand,
                           purchase_price_kzt,target_markup_percent,notes,
                           created_by,updated_by,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(tenant_id), sku, title, quantity, purchase, markup, notes,
                        int(actor_user_id), int(actor_user_id), stamp, stamp,
                    ),
                )
                inventory_id = int(cursor.lastrowid)
                conn.execute(
                    """INSERT INTO tenant_product_listings(
                           tenant_id,listing_code,inventory_product_id,marketplace_code,
                           tenant_seller_id,source_product_code,match_method,match_score,
                           confirmed_by,confirmed_at,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,'MANUAL_LISTING',100,?,?,?,?)""",
                    (
                        int(tenant_id), str(listing_code), inventory_id, platform,
                        seller_id, source_code, int(actor_user_id), stamp, stamp, stamp,
                    ),
                )
            after = {
                "internal_sku": sku,
                "title": title,
                "quantity_on_hand": quantity,
                "purchase_price_kzt": purchase,
                "target_markup_percent": markup,
                "notes": notes,
            }
            conn.execute(
                """INSERT INTO tenant_inventory_events(
                       tenant_id,inventory_product_id,event_type,details_json,
                       created_by,created_at
                   ) VALUES(?,?,'inventory_updated',?,?,?)""",
                (
                    int(tenant_id), inventory_id,
                    json.dumps({"before": before, "after": after}, ensure_ascii=False, default=str),
                    int(actor_user_id), stamp,
                ),
            )
            conn.commit()
            return inventory_id
        except database_error_types() as exc:
            conn.rollback()
            if "internal_sku" in str(exc).casefold() or "unique" in str(exc).casefold():
                raise ValueError("Такой внутренний SKU уже используется в этой компании.") from exc
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def decide_match(
        self,
        tenant_id: int,
        source: dict[str, Any],
        candidate: dict[str, Any],
        decision: str,
        actor_user_id: int,
        *,
        match_method: str = "MANUAL_CONFIRMATION",
        match_score: float | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        source_code = str(source.get("product_code") or "")
        candidate_code = str(candidate.get("product_code") or "")
        if not source_code or not candidate_code or source_code == candidate_code:
            raise ValueError("Выберите две разные товарные позиции.")
        if str(source.get("platform")) == str(candidate.get("platform")):
            raise ValueError("Объединение предназначено для листингов разных маркетплейсов.")
        normalized_decision = str(decision or "").strip().casefold()
        if normalized_decision not in {"confirmed", "rejected"}:
            raise ValueError("Неизвестное решение по сопоставлению.")
        pair_left, pair_right = self._pair(source_code, candidate_code)
        stamp = now_iso()
        conn = self._connect()
        try:
            self._begin_write(conn)
            existing_links = {
                str(row["listing_code"]): int(row["inventory_product_id"])
                for row in conn.execute(
                    """SELECT listing_code,inventory_product_id
                       FROM tenant_product_listings
                       WHERE tenant_id=? AND listing_code IN (?,?)""",
                    (int(tenant_id), source_code, candidate_code),
                ).fetchall()
            }
            if normalized_decision == "rejected":
                if (
                    source_code in existing_links
                    and candidate_code in existing_links
                    and existing_links[source_code] == existing_links[candidate_code]
                ):
                    raise ValueError(
                        "Карточки уже используют один складской товар. "
                        "Сначала нужно безопасно разделить остаток и закупочные данные."
                    )
                conn.execute(
                    """INSERT INTO tenant_product_match_decisions(
                           tenant_id,source_listing_code,candidate_listing_code,
                           decision,match_method,match_score,reason,updated_by,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(tenant_id,source_listing_code,candidate_listing_code)
                       DO UPDATE SET decision=excluded.decision,
                           match_method=excluded.match_method,match_score=excluded.match_score,
                           reason=excluded.reason,updated_by=excluded.updated_by,
                           updated_at=excluded.updated_at""",
                    (
                        int(tenant_id), pair_left, pair_right, normalized_decision,
                        match_method, match_score, str(reason or "")[:1000],
                        int(actor_user_id), stamp,
                    ),
                )
                conn.commit()
                return {"decision": "rejected", "inventory_product_id": None}

            links = existing_links
            inventory_ids = set(links.values())
            if len(inventory_ids) > 1:
                raise ValueError(
                    "Обе позиции уже относятся к разным складским товарам. "
                    "Автоматическое слияние закупочных данных запрещено."
                )
            if inventory_ids:
                inventory_id = next(iter(inventory_ids))
            else:
                cursor = conn.execute(
                    """INSERT INTO tenant_inventory_products(
                           tenant_id,title,quantity_on_hand,target_markup_percent,
                           created_by,updated_by,created_at,updated_at
                       ) VALUES(?,?,0,20,?,?,?,?)""",
                    (
                        int(tenant_id), str(source.get("title") or candidate.get("title") or "")[:500],
                        int(actor_user_id), int(actor_user_id), stamp, stamp,
                    ),
                )
                inventory_id = int(cursor.lastrowid)
            for listing in (source, candidate):
                code = str(listing.get("product_code") or "")
                platform, seller_id, raw_code = self._listing_values(listing)
                conn.execute(
                    """INSERT INTO tenant_product_listings(
                           tenant_id,listing_code,inventory_product_id,marketplace_code,
                           tenant_seller_id,source_product_code,match_method,match_score,
                           confirmed_by,confirmed_at,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(tenant_id,listing_code) DO UPDATE SET
                           inventory_product_id=excluded.inventory_product_id,
                           match_method=excluded.match_method,
                           match_score=excluded.match_score,
                           confirmed_by=excluded.confirmed_by,
                           confirmed_at=excluded.confirmed_at,
                           updated_at=excluded.updated_at""",
                    (
                        int(tenant_id), code, inventory_id, platform, seller_id,
                        raw_code, str(match_method or "MANUAL_CONFIRMATION")[:80],
                        match_score, int(actor_user_id), stamp, stamp, stamp,
                    ),
                )
            conn.execute(
                """INSERT INTO tenant_product_match_decisions(
                       tenant_id,source_listing_code,candidate_listing_code,
                       decision,match_method,match_score,reason,updated_by,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(tenant_id,source_listing_code,candidate_listing_code)
                   DO UPDATE SET decision=excluded.decision,
                       match_method=excluded.match_method,match_score=excluded.match_score,
                       reason=excluded.reason,updated_by=excluded.updated_by,
                       updated_at=excluded.updated_at""",
                (
                    int(tenant_id), pair_left, pair_right, "confirmed",
                    str(match_method or "MANUAL_CONFIRMATION")[:80], match_score,
                    str(reason or "")[:1000], int(actor_user_id), stamp,
                ),
            )
            conn.execute(
                """INSERT INTO tenant_inventory_events(
                       tenant_id,inventory_product_id,event_type,details_json,
                       created_by,created_at
                   ) VALUES(?,?,'match_confirmed',?,?,?)""",
                (
                    int(tenant_id), inventory_id,
                    json.dumps({"listings": [source_code, candidate_code], "method": match_method}, ensure_ascii=False),
                    int(actor_user_id), stamp,
                ),
            )
            conn.commit()
            return {"decision": "confirmed", "inventory_product_id": inventory_id}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def summary(self, tenant_id: int, visible_listing_codes: set[str]) -> dict[str, Any]:
        conn = self._connect()
        try:
            products = [dict(row) for row in conn.execute(
                """SELECT id,quantity_on_hand,purchase_price_kzt
                   FROM tenant_inventory_products WHERE tenant_id=?""",
                (int(tenant_id),),
            ).fetchall()]
            links = [dict(row) for row in conn.execute(
                """SELECT listing_code,inventory_product_id
                   FROM tenant_product_listings WHERE tenant_id=?""",
                (int(tenant_id),),
            ).fetchall()]
        finally:
            conn.close()
        visible_links = [
            row for row in links if str(row["listing_code"]) in visible_listing_codes
        ]
        visible_item_ids = {int(row["inventory_product_id"]) for row in visible_links}
        visible_products = [row for row in products if int(row["id"]) in visible_item_ids]
        unpriced_products = [
            row for row in visible_products
            if row.get("purchase_price_kzt") in (None, "")
        ]
        stock_value = sum(
            int(row.get("quantity_on_hand") or 0) * float(row.get("purchase_price_kzt") or 0)
            for row in visible_products
        )
        return {
            "inventory_products": len(visible_products),
            "linked_listings": len(visible_links),
            "unmatched_listings": max(0, len(visible_listing_codes) - len(visible_links)),
            "quantity_on_hand": sum(int(row.get("quantity_on_hand") or 0) for row in visible_products),
            "stock_value_kzt": round(stock_value, 2),
            "stock_value_complete": not unpriced_products,
            "unpriced_inventory_products": len(unpriced_products),
        }
