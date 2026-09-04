from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from marketplace_registry import MARKETPLACE_CODES
from subscription_service import SubscriptionLimitError, SubscriptionService
from storage.postgres_compat import configure_connection, connect_database
from storage.postgres_compat import PostgresConnection


ATTRIBUTE_ALIASES = {
    "brand": {"brand", "бренд", "производитель", "марка"},
    "model": {"model", "модель"},
    "season": {"season", "сезон", "сезонность", "тип сезона"},
    "size": {"size", "размер", "типоразмер"},
    "width": {"width", "ширина"},
    "height": {"height", "высота"},
    "diameter": {"diameter", "диаметр", "диагональ"},
    "memory": {"memory", "память", "объем памяти", "объём памяти"},
    "color": {"color", "цвет"},
    "category": {"category", "категория", "тип товара"},
}
ATTRIBUTE_LABELS = {
    "brand": "Бренд",
    "model": "Модель",
    "season": "Сезонность",
    "size": "Размер",
    "width": "Ширина",
    "height": "Высота",
    "diameter": "Диаметр / диагональ",
    "memory": "Объём памяти",
    "color": "Цвет",
    "category": "Категория",
}
PRODUCT_PREFIXES = {
    "kaspi": "",
    "ozon": "ozon:",
    "ozon_kz": "ozon_kz:",
    "halyk_market": "halyk:",
    "forte_market": "forte:",
    "wildberries": "wb:",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalized_source_name(value: Any) -> str:
    return re.sub(r"[^\w]+", " ", str(value or "").strip().casefold()).strip()


def normalized_attribute_key(source_name: Any) -> str:
    normalized = _normalized_source_name(source_name)
    for key, aliases in ATTRIBUTE_ALIASES.items():
        if normalized in aliases:
            return key
    ascii_key = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if ascii_key:
        return ascii_key[:80]
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"attr_{digest}"


def _attribute_items(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if isinstance(value, dict):
        if isinstance(value.get("attributes"), list):
            value = value["attributes"]
        else:
            value = [{"name": key, "value": item} for key, item in value.items()]
    if not isinstance(value, list):
        return []
    result: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw_name = (
            item.get("name") or item.get("attribute_name") or item.get("title")
            or item.get("key")
        )
        if raw_name in (None, "") and item.get("id") not in (None, ""):
            raw_name = f"attribute_{item['id']}"
        name = str(raw_name or "").strip()
        raw = item.get("value")
        if raw in (None, ""):
            raw = item.get("values")
        if isinstance(raw, list):
            values = []
            for child in raw:
                if isinstance(child, dict):
                    child = child.get("value") or child.get("dictionary_value") or child.get("name")
                if child not in (None, ""):
                    values.append(str(child).strip())
            rendered = ", ".join(value for value in values if value)
        elif isinstance(raw, dict):
            rendered = str(raw.get("value") or raw.get("name") or "").strip()
        else:
            rendered = str(raw or "").strip()
        if name and rendered:
            result.append((name[:240], rendered[:2000]))
    return result


class CatalogConfigurationService:
    def __init__(self, db_path: Path, ozon_db_path: Path | None = None):
        self.db_path = Path(db_path)
        self.ozon_db_path = Path(ozon_db_path) if ozon_db_path else None

    def _connect(self) -> Any:
        return configure_connection(
            connect_database(self.db_path, timeout=30), foreign_keys=True, busy_timeout=30000
        )

    def _position_entitlement(self, tenant_id: int, marketplace_code: str) -> dict[str, Any]:
        """Load the comparatively static plan limit before taking a catalog write lock.

        SubscriptionService performs idempotent seed maintenance when it is
        constructed.  Doing that through a second connection after SQLite's
        BEGIN IMMEDIATE would deadlock against our own transaction.  The
        mutable product count is still calculated and checked while the
        tenant write lock is held below.
        """
        value = SubscriptionService(self.db_path).entitlement(int(tenant_id))
        if not value["active"]:
            raise SubscriptionLimitError(
                "Каталог не сохранён: нет активного подтверждённого пакета."
            )
        marketplace = value["marketplaces"].get(str(marketplace_code), {})
        if not marketplace.get("enabled", False):
            raise SubscriptionLimitError("Площадка не включена в пакет компании.")
        return marketplace

    @staticmethod
    def _assert_position_entitlement(
        marketplace_code: str, marketplace: dict[str, Any], requested: int
    ) -> None:
        limit = marketplace.get("position_limit")
        if limit is not None and int(requested) > int(limit):
            missing = int(requested) - int(limit)
            raise SubscriptionLimitError(
                f"Недостаточно позиций для {marketplace_code}: доступно {limit}, "
                f"получено {requested}. Увеличьте лимит минимум на {missing} позиций."
            )

    def catalog_memberships(
        self,
        tenant_id: int,
        marketplaces: Iterable[str] | None = None,
        tenant_seller_id: int | None = None,
    ) -> set[tuple[str, str]]:
        allowed = {str(value) for value in (marketplaces or MARKETPLACE_CODES)} & set(MARKETPLACE_CODES)
        if not allowed:
            return set()
        placeholders = ",".join("?" for _ in allowed)
        conn = self._connect()
        try:
            if tenant_seller_id is not None:
                params: list[Any] = [int(tenant_id), int(tenant_seller_id)]
                params.extend(sorted(allowed))
                return {
                    (str(row["marketplace_code"]), str(row["source_product_code"]))
                    for row in conn.execute(
                        f"""SELECT marketplace_code,source_product_code
                            FROM tenant_seller_catalog_products
                            WHERE tenant_id=? AND tenant_seller_id=? AND active=1
                              AND marketplace_code IN ({placeholders})""",
                        params,
                    ).fetchall()
                }
            seller_rows_exist = conn.execute(
                """SELECT 1 FROM tenant_seller_catalog_products
                   WHERE tenant_id=? AND active=1 LIMIT 1""",
                (int(tenant_id),),
            ).fetchone()
            if seller_rows_exist:
                seller_where = "tenant_id=? AND active=1"
                params: list[Any] = [int(tenant_id)]
                seller_where += f" AND marketplace_code IN ({placeholders})"
                params.extend(sorted(allowed))
                return {
                    (str(row["marketplace_code"]), str(row["source_product_code"]))
                    for row in conn.execute(
                        f"""SELECT marketplace_code,source_product_code
                            FROM tenant_seller_catalog_products
                            WHERE {seller_where}""",
                        params,
                    ).fetchall()
                }
            return {
                (str(row["marketplace_code"]), str(row["source_product_code"]))
                for row in conn.execute(
                    f"""SELECT marketplace_code,source_product_code
                        FROM tenant_catalog_products
                        WHERE tenant_id=? AND active=1
                          AND marketplace_code IN ({placeholders})""",
                    [int(tenant_id), *sorted(allowed)],
                ).fetchall()
            }
        finally:
            conn.close()

    @staticmethod
    def _begin_tenant_write(conn: Any, tenant_id: int) -> None:
        """Serialize catalog capacity decisions for one tenant."""
        if isinstance(conn, PostgresConnection):
            conn.execute("SELECT id FROM tenants WHERE id=? FOR UPDATE", (int(tenant_id),))
        else:
            conn.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _validated_seller_id(
        conn: Any,
        tenant_id: int,
        marketplace_code: str,
        tenant_seller_id: int | None,
    ) -> int | None:
        if tenant_seller_id is not None:
            row = conn.execute(
                """SELECT id FROM tenant_marketplace_sellers
                   WHERE id=? AND tenant_id=? AND marketplace_code=?""",
                (int(tenant_seller_id), int(tenant_id), str(marketplace_code)),
            ).fetchone()
            if not row:
                raise PermissionError("Продавец не принадлежит компании или площадке.")
            return int(row[0])
        rows = conn.execute(
            """SELECT id FROM tenant_marketplace_sellers
               WHERE tenant_id=? AND marketplace_code=?
                 AND status='active' AND approval_status='approved'
               ORDER BY id""",
            (int(tenant_id), str(marketplace_code)),
        ).fetchall()
        if len(rows) == 1:
            return int(rows[0][0])
        if len(rows) > 1:
            raise ValueError("Выберите продавца для сохранения каталога.")
        return None

    @staticmethod
    def _active_position_count(
        conn: Any, tenant_id: int, marketplace_code: str
    ) -> int:
        seller_count = int(conn.execute(
            """SELECT COUNT(*) FROM tenant_seller_catalog_products
               WHERE tenant_id=? AND marketplace_code=? AND active=1""",
            (int(tenant_id), str(marketplace_code)),
        ).fetchone()[0])
        if seller_count:
            return seller_count
        return int(conn.execute(
            """SELECT COUNT(*) FROM tenant_catalog_products
               WHERE tenant_id=? AND marketplace_code=? AND active=1""",
            (int(tenant_id), str(marketplace_code)),
        ).fetchone()[0])

    @staticmethod
    def _seller_product_active(
        conn: Any,
        tenant_id: int,
        marketplace_code: str,
        tenant_seller_id: int | None,
        product_code: str,
    ) -> bool:
        if tenant_seller_id is None:
            return bool(conn.execute(
                """SELECT 1 FROM tenant_catalog_products
                   WHERE tenant_id=? AND marketplace_code=?
                     AND source_product_code=? AND active=1""",
                (int(tenant_id), str(marketplace_code), str(product_code)),
            ).fetchone())
        return bool(conn.execute(
            """SELECT 1 FROM tenant_seller_catalog_products
               WHERE tenant_id=? AND marketplace_code=? AND tenant_seller_id=?
                 AND source_product_code=? AND active=1""",
            (
                int(tenant_id), str(marketplace_code), int(tenant_seller_id),
                str(product_code),
            ),
        ).fetchone())

    def upsert_catalog_product(
        self,
        tenant_id: int,
        marketplace_code: str,
        product: dict[str, Any],
        *,
        catalog_id: int | None = None,
        tenant_seller_id: int | None = None,
        _conn: Any | None = None,
    ) -> None:
        platform = str(marketplace_code)
        if platform not in MARKETPLACE_CODES:
            raise ValueError("Неизвестная площадка.")
        product_code = str(
            product.get("source_product_code") or product.get("product_id")
            or product.get("sku") or product.get("offer_id") or ""
        ).strip()
        if not product_code:
            raise ValueError("У товара отсутствует marketplace product ID.")
        attributes = product.get("attributes") or product.get("specifications") or []
        stamp = now_iso()
        owns_connection = _conn is None
        position_entitlement = (
            self._position_entitlement(int(tenant_id), platform)
            if owns_connection else None
        )
        conn = _conn or self._connect()
        try:
            if owns_connection:
                self._begin_tenant_write(conn, int(tenant_id))
                tenant_seller_id = self._validated_seller_id(
                    conn, int(tenant_id), platform, tenant_seller_id
                )
                if not self._seller_product_active(
                    conn, int(tenant_id), platform, tenant_seller_id, product_code
                ):
                    current = self._active_position_count(conn, int(tenant_id), platform)
                    self._assert_position_entitlement(
                        platform, position_entitlement or {}, current + 1
                    )
            elif tenant_seller_id is not None:
                tenant_seller_id = self._validated_seller_id(
                    conn, int(tenant_id), platform, tenant_seller_id
                )
            values = (
                int(tenant_id), platform, product_code, catalog_id, tenant_seller_id,
                str(product.get("seller_sku") or product.get("offer_id") or "")[:240],
                str(product.get("title") or product.get("name") or "")[:2000],
                str(product.get("brand") or "")[:300],
                str(product.get("model") or "")[:300],
                str(product.get("url") or product.get("source_url") or "")[:3000],
                str(product.get("image_url") or "")[:3000],
                str(product.get("category") or product.get("category_name") or "")[:500],
                product.get("price"), str(product.get("currency") or "")[:12],
                str(product.get("availability") or product.get("visibility") or "")[:120],
                json.dumps(attributes, ensure_ascii=False, separators=(",", ":")),
                json.dumps(product.get("metadata") or {}, ensure_ascii=False, separators=(",", ":")),
                stamp, stamp, str(product.get("updated_at") or stamp),
            )
            if tenant_seller_id is not None:
                conn.execute(
                    """INSERT INTO tenant_seller_catalog_products(
                           tenant_id,marketplace_code,source_product_code,catalog_id,tenant_seller_id,
                           seller_sku,title,brand,model,source_url,image_url,category_name,
                           price_amount,currency,availability_status,attributes_json,metadata_json,
                           active,first_seen_at,last_seen_at,source_updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)
                       ON CONFLICT(
                           tenant_id,marketplace_code,tenant_seller_id,source_product_code
                       ) DO UPDATE SET
                           catalog_id=COALESCE(excluded.catalog_id,tenant_seller_catalog_products.catalog_id),
                           seller_sku=excluded.seller_sku,title=excluded.title,brand=excluded.brand,
                           model=excluded.model,source_url=excluded.source_url,image_url=excluded.image_url,
                           category_name=excluded.category_name,price_amount=excluded.price_amount,
                           currency=excluded.currency,availability_status=excluded.availability_status,
                           attributes_json=excluded.attributes_json,metadata_json=excluded.metadata_json,
                           active=1,last_seen_at=excluded.last_seen_at,
                           source_updated_at=excluded.source_updated_at""",
                    values,
                )
            conn.execute(
                """INSERT INTO tenant_catalog_products(
                       tenant_id,marketplace_code,source_product_code,catalog_id,tenant_seller_id,
                       seller_sku,title,brand,model,source_url,image_url,category_name,
                       price_amount,currency,availability_status,attributes_json,metadata_json,
                       active,first_seen_at,last_seen_at,source_updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)
                   ON CONFLICT(tenant_id,marketplace_code,source_product_code) DO UPDATE SET
                       catalog_id=COALESCE(excluded.catalog_id,tenant_catalog_products.catalog_id),
                       tenant_seller_id=COALESCE(excluded.tenant_seller_id,tenant_catalog_products.tenant_seller_id),
                       seller_sku=excluded.seller_sku,title=excluded.title,brand=excluded.brand,
                       model=excluded.model,source_url=excluded.source_url,image_url=excluded.image_url,
                       category_name=excluded.category_name,price_amount=excluded.price_amount,
                       currency=excluded.currency,availability_status=excluded.availability_status,
                       attributes_json=excluded.attributes_json,metadata_json=excluded.metadata_json,
                       active=1,last_seen_at=excluded.last_seen_at,
                       source_updated_at=excluded.source_updated_at""",
                values,
            )
            if not owns_connection:
                self.ingest_attributes(
                    tenant_id, platform, product_code, attributes, _conn=conn
                )
            if owns_connection:
                conn.commit()
        finally:
            if owns_connection:
                conn.close()
        if owns_connection:
            self.ingest_attributes(tenant_id, platform, product_code, attributes)

    def upsert_catalog_products(
        self,
        tenant_id: int,
        marketplace_code: str,
        products: Iterable[dict[str, Any]],
        *,
        catalog_id: int | None = None,
        tenant_seller_id: int | None = None,
    ) -> int:
        platform = str(marketplace_code)
        if platform not in MARKETPLACE_CODES:
            raise ValueError("Неизвестная площадка.")

        product_rows = list(products)
        position_entitlement = self._position_entitlement(int(tenant_id), platform)
        incoming_codes = {
            str(
                product.get("source_product_code")
                or product.get("product_id")
                or product.get("sku")
                or product.get("offer_id")
                or ""
            ).strip()
            for product in product_rows
        }
        incoming_codes.discard("")

        conn = self._connect()
        count = 0
        try:
            self._begin_tenant_write(conn, int(tenant_id))
            tenant_seller_id = self._validated_seller_id(
                conn, int(tenant_id), platform, tenant_seller_id
            )
            new_codes = {
                code for code in incoming_codes
                if not self._seller_product_active(
                    conn, int(tenant_id), platform, tenant_seller_id, code
                )
            }
            projected_count = self._active_position_count(
                conn, int(tenant_id), platform
            ) + len(new_codes)
            self._assert_position_entitlement(
                platform, position_entitlement, projected_count
            )
            for product in product_rows:
                self.upsert_catalog_product(
                    tenant_id,
                    platform,
                    product,
                    catalog_id=catalog_id,
                    tenant_seller_id=tenant_seller_id,
                    _conn=conn,
                )
                count += 1
            conn.commit()
            return count
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def replace_catalog_products(
        self,
        tenant_id: int,
        marketplace_code: str,
        products: Iterable[dict[str, Any]],
        *,
        catalog_id: int | None = None,
        tenant_seller_id: int | None = None,
    ) -> int:
        """Atomically replace one company's marketplace catalog snapshot."""
        platform = str(marketplace_code)
        if platform not in MARKETPLACE_CODES:
            raise ValueError("Неизвестная площадка.")
        product_rows = list(products)
        position_entitlement = self._position_entitlement(int(tenant_id), platform)
        conn = self._connect()
        count = 0
        try:
            self._begin_tenant_write(conn, int(tenant_id))
            tenant_seller_id = self._validated_seller_id(
                conn, int(tenant_id), platform, tenant_seller_id
            )
            incoming_codes = {
                str(
                    product.get("source_product_code")
                    or product.get("product_id")
                    or product.get("sku")
                    or product.get("offer_id")
                    or ""
                ).strip()
                for product in product_rows
            }
            incoming_codes.discard("")
            if tenant_seller_id is not None:
                other_count = int(conn.execute(
                    """SELECT COUNT(*) FROM tenant_seller_catalog_products
                       WHERE tenant_id=? AND marketplace_code=? AND active=1
                         AND tenant_seller_id<>?""",
                    (int(tenant_id), platform, int(tenant_seller_id)),
                ).fetchone()[0])
                projected_count = other_count + len(incoming_codes)
            else:
                projected_count = len(incoming_codes)
            self._assert_position_entitlement(
                platform, position_entitlement, projected_count
            )
            if tenant_seller_id is not None:
                conn.execute(
                    """UPDATE tenant_seller_catalog_products SET active=0
                       WHERE tenant_id=? AND marketplace_code=? AND tenant_seller_id=?""",
                    (int(tenant_id), platform, int(tenant_seller_id)),
                )
            else:
                conn.execute(
                    """UPDATE tenant_catalog_products SET active=0
                       WHERE tenant_id=? AND marketplace_code=?""",
                    (int(tenant_id), platform),
                )
            for product in product_rows:
                self.upsert_catalog_product(
                    tenant_id, platform, product,
                    catalog_id=catalog_id, tenant_seller_id=tenant_seller_id,
                    _conn=conn,
                )
                count += 1
            if tenant_seller_id is not None:
                conn.execute(
                    """UPDATE tenant_catalog_products AS tcp SET active=0
                       WHERE tcp.tenant_id=? AND tcp.marketplace_code=?
                         AND NOT EXISTS(
                             SELECT 1 FROM tenant_seller_catalog_products tsp
                             WHERE tsp.tenant_id=tcp.tenant_id
                               AND tsp.marketplace_code=tcp.marketplace_code
                               AND tsp.source_product_code=tcp.source_product_code
                               AND tsp.active=1
                         )""",
                    (int(tenant_id), platform),
                )
            stamp = now_iso()
            conn.execute(
                """UPDATE tenant_integrations SET product_count=?,last_sync_at=?,
                       last_status='completed',last_error='',updated_at=?
                   WHERE tenant_id=? AND integration_code=?""",
                (count,stamp,stamp,int(tenant_id),platform),
            )
            if tenant_seller_id is not None:
                seller_count = int(conn.execute(
                    """SELECT COUNT(*) FROM tenant_seller_catalog_products
                       WHERE tenant_id=? AND marketplace_code=?
                         AND tenant_seller_id=? AND active=1""",
                    (int(tenant_id), platform, int(tenant_seller_id)),
                ).fetchone()[0])
                conn.execute(
                    """UPDATE tenant_marketplace_sellers
                       SET product_count=?,last_sync_at=?,last_status='completed',
                           last_error='',updated_at=?
                       WHERE id=? AND tenant_id=?""",
                    (
                        seller_count, stamp, stamp, int(tenant_seller_id),
                        int(tenant_id),
                    ),
                )
            conn.commit()
            return count
        finally:
            conn.close()

    def materialize_legacy_kaspi_catalog(
        self,
        tenant_id: int,
        product_codes: Iterable[str] | None = None,
        *,
        replace: bool = False,
        tenant_seller_id: int | None = None,
        source_db_path: Path | None = None,
    ) -> int:
        """Copy the just-collected legacy Kaspi rows into one tenant snapshot.

        The browser collectors still use the historic raw tables as a staging
        area.  Tenant-facing reads never depend on those shared rows: after an
        operation, this method copies only its product set to the requesting
        company's catalog.
        """
        # ``None`` means the intentional legacy-owner fallback (all active
        # staging rows).  An explicitly supplied empty iterable means that the
        # current collection has not found any products and must never expand
        # to the shared legacy catalogue.
        codes_were_supplied = product_codes is not None
        codes = [str(value).strip() for value in (product_codes or []) if str(value).strip()]
        source_path = Path(source_db_path) if source_db_path else self.db_path
        conn = connect_database(source_path, timeout=30)
        configure_connection(conn, foreign_keys=True, busy_timeout=30000)
        try:
            if codes_were_supplied and not codes:
                rows = []
            else:
                where = "WHERE COALESCE(m.active,1)=1"
                params: list[Any] = []
                if codes:
                    where += f" AND c.product_code IN ({','.join('?' for _ in codes)})"
                    params.extend(codes)
                rows = conn.execute(
                    f"""SELECT c.product_code,c.title_catalog,c.catalog_price_kzt,
                               c.catalog_rating,c.catalog_reviews,c.product_url,c.image_url,
                               c.collected_at,m.brand,m.category_id,m.stock,m.own_offer_active,
                               m.last_seen_at,d.title_detail,d.price_kzt AS detail_price_kzt,
                               d.specifications_json,d.detail_collected_at
                        FROM catalog_products c
                        LEFT JOIN catalog_product_meta m ON m.product_code=c.product_code
                        LEFT JOIN product_details d ON d.product_code=c.product_code
                        {where}
                        ORDER BY c.product_code""",
                    params,
                ).fetchall()
        finally:
            conn.close()

        products: list[dict[str, Any]] = []
        for raw in rows:
            value = dict(raw)
            try:
                attributes = json.loads(str(value.get("specifications_json") or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                attributes = []
            brand = str(value.get("brand") or "").strip()
            if brand.casefold() in {"", "all"}:
                for source_name, source_value in _attribute_items(attributes):
                    if normalized_attribute_key(source_name) == "brand":
                        brand = str(source_value or "").strip()
                        if brand:
                            break
            stock = value.get("stock")
            own_offer = value.get("own_offer_active")
            availability = (
                "in_stock" if own_offer == 1 or (stock is not None and int(stock) > 0)
                else "out_of_stock" if own_offer == 0 or stock == 0
                else ""
            )
            products.append({
                "product_id": value["product_code"],
                "title": value.get("title_detail") or value.get("title_catalog") or "",
                "brand": brand,
                "url": value.get("product_url") or "",
                "image_url": value.get("image_url") or "",
                "category": value.get("category_id") or "",
                "price": (
                    value.get("detail_price_kzt")
                    if value.get("detail_price_kzt") is not None
                    else value.get("catalog_price_kzt")
                ),
                "currency": "KZT",
                "availability": availability,
                "attributes": attributes,
                "updated_at": (
                    value.get("detail_collected_at") or value.get("last_seen_at")
                    or value.get("collected_at")
                ),
                "metadata": {
                    "rating": value.get("catalog_rating"),
                    "reviews": value.get("catalog_reviews"),
                    "stock": stock,
                },
            })
        if replace:
            return self.replace_catalog_products(
                int(tenant_id), "kaspi", products,
                tenant_seller_id=tenant_seller_id,
            )
        count = self.upsert_catalog_products(
            int(tenant_id), "kaspi", products,
            tenant_seller_id=tenant_seller_id,
        )
        stamp = now_iso()
        conn = self._connect()
        try:
            active_count = self._active_position_count(
                conn, int(tenant_id), "kaspi"
            )
            conn.execute(
                """UPDATE tenant_integrations SET product_count=?,last_sync_at=?,
                          last_status='completed',last_error='',updated_at=?
                   WHERE tenant_id=? AND integration_code='kaspi'""",
                (active_count, stamp, stamp, int(tenant_id)),
            )
            conn.commit()
        finally:
            conn.close()
        return count

    def ingest_attributes(
        self,
        tenant_id: int,
        marketplace_code: str,
        source_product_code: str,
        raw_attributes: Any,
        *,
        _conn: Any | None = None,
    ) -> int:
        items = _attribute_items(raw_attributes)
        if not items:
            return 0
        stamp = now_iso()
        owns_connection = _conn is None
        conn = _conn or self._connect()
        try:
            for source_name, raw_value in items:
                key = normalized_attribute_key(source_name)
                label = ATTRIBUTE_LABELS.get(key, source_name)
                conn.execute(
                    """INSERT INTO product_attribute_definitions(
                           tenant_id,product_type,attribute_key,display_name,data_type,
                           is_identity,is_required,display_order,created_at,updated_at,last_seen_at
                       ) VALUES(?,'*',?,?,'text',0,0,0,?,?,?)
                       ON CONFLICT(tenant_id,product_type,attribute_key) DO UPDATE SET
                           last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
                    (int(tenant_id), key, label, stamp, stamp, stamp),
                )
                definition_id = int(conn.execute(
                    """SELECT id FROM product_attribute_definitions
                       WHERE tenant_id=? AND product_type='*' AND attribute_key=?""",
                    (int(tenant_id), key),
                ).fetchone()[0])
                conn.execute(
                    """INSERT INTO product_attribute_sources(
                           tenant_id,definition_id,marketplace_code,source_attribute,
                           sample_values_json,first_seen_at,last_seen_at
                       ) VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(tenant_id,definition_id,marketplace_code,source_attribute)
                       DO UPDATE SET sample_values_json=excluded.sample_values_json,
                                     last_seen_at=excluded.last_seen_at""",
                    (
                        int(tenant_id), definition_id, str(marketplace_code), source_name,
                        json.dumps([raw_value], ensure_ascii=False), stamp, stamp,
                    ),
                )
                conn.execute(
                    """INSERT INTO product_attribute_values(
                           tenant_id,platform,source_product_code,definition_id,raw_value,
                           normalized_text,source,collected_at
                       ) VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(tenant_id,platform,source_product_code,definition_id)
                       DO UPDATE SET raw_value=excluded.raw_value,
                                     normalized_text=excluded.normalized_text,
                                     source=excluded.source,collected_at=excluded.collected_at""",
                    (
                        int(tenant_id), str(marketplace_code), str(source_product_code),
                        definition_id, raw_value, raw_value.casefold(), source_name, stamp,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_catalog_filters(
                           tenant_id,attribute_key,display_name,is_enabled,display_order,
                           config_json,created_at,updated_at
                       ) VALUES(?,?,?,0,100,'{}',?,?)
                       ON CONFLICT(tenant_id,attribute_key) DO UPDATE SET
                           display_name=excluded.display_name,updated_at=excluded.updated_at""",
                    (int(tenant_id), key, label, stamp, stamp),
                )
            if owns_connection:
                conn.commit()
            return len(items)
        finally:
            if owns_connection:
                conn.close()

    def refresh_registry(
        self, tenant_id: int, allowed_marketplaces: set[str]
    ) -> dict[str, int]:
        allowed = set(allowed_marketplaces) & set(MARKETPLACE_CODES)
        if not allowed:
            return {"products": 0, "attributes": 0}
        placeholders = ",".join("?" for _ in allowed)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""SELECT marketplace_code,source_product_code,attributes_json
                    FROM tenant_catalog_products
                    WHERE tenant_id=? AND active=1
                      AND marketplace_code IN ({placeholders})""",
                [int(tenant_id), *sorted(allowed)],
            ).fetchall()
            products = 0
            attributes = 0
            for row in rows:
                count = self.ingest_attributes(
                    tenant_id, str(row["marketplace_code"]),
                    str(row["source_product_code"]), row["attributes_json"],
                    _conn=conn,
                )
                products += 1
                attributes += count
            conn.commit()
            return {"products": products, "attributes": attributes}
        finally:
            conn.close()

    def filter_configuration(
        self, tenant_id: int, allowed_marketplaces: set[str]
    ) -> dict[str, Any]:
        allowed = set(allowed_marketplaces) & set(MARKETPLACE_CODES)
        if not allowed:
            return {"filters": [], "attributes": []}
        conn = self._connect()
        try:
            filters = [dict(row) for row in conn.execute(
                """SELECT attribute_key,display_name,is_enabled,display_order,config_json
                   FROM tenant_catalog_filters WHERE tenant_id=?
                   ORDER BY display_order,display_name""",
                (int(tenant_id),),
            ).fetchall()]
            definitions = []
            for row in conn.execute(
                """SELECT d.id,d.attribute_key,d.display_name,d.data_type,
                          COUNT(DISTINCT v.source_product_code) product_count,
                          COUNT(DISTINCT s.marketplace_code) marketplace_count
                   FROM product_attribute_definitions d
                   LEFT JOIN product_attribute_values v
                     ON v.tenant_id=d.tenant_id AND v.definition_id=d.id
                   LEFT JOIN product_attribute_sources s
                     ON s.tenant_id=d.tenant_id AND s.definition_id=d.id
                   WHERE d.tenant_id=? AND d.product_type='*'
                   GROUP BY d.id ORDER BY d.display_name""",
                (int(tenant_id),),
            ).fetchall():
                item = dict(row)
                source_rows = conn.execute(
                        """SELECT marketplace_code,source_attribute,sample_values_json
                           FROM product_attribute_sources
                           WHERE tenant_id=? AND definition_id=?""",
                        (int(tenant_id), int(row["id"])),
                    ).fetchall()
                item["sources"] = [
                    dict(source) for source in source_rows
                    if str(source["marketplace_code"]) in allowed
                ]
                allowed_placeholders = ",".join("?" for _ in allowed)
                scoped_count = conn.execute(
                        f"""SELECT COUNT(DISTINCT platform || ':' || source_product_code)
                            FROM product_attribute_values
                            WHERE tenant_id=? AND definition_id=?
                              AND platform IN ({allowed_placeholders})""",
                        [int(tenant_id), int(row["id"]), *sorted(allowed)],
                    ).fetchone()[0]
                item["product_count"] = int(scoped_count or 0)
                item["marketplace_count"] = len({
                    str(source["marketplace_code"]) for source in item["sources"]
                })
                values = conn.execute(
                        """SELECT DISTINCT raw_value FROM product_attribute_values
                           WHERE tenant_id=? AND definition_id=? AND platform IN ({})
                           ORDER BY raw_value LIMIT 250""".format(
                            allowed_placeholders
                        ),
                        [int(tenant_id), int(row["id"]), *sorted(allowed)],
                    ).fetchall()
                item["values"] = [str(value[0]) for value in values if value[0] not in (None, "")]
                if not item["sources"]:
                    continue
                definitions.append(item)
        finally:
            conn.close()
        for item in filters:
            try:
                item["config"] = json.loads(item.pop("config_json") or "{}")
            except json.JSONDecodeError:
                item["config"] = {}
        visible_filter_keys = {"title", "marketplace"} | {
            str(item["attribute_key"]) for item in definitions
        }
        filters = [
            item for item in filters
            if str(item.get("attribute_key") or "") in visible_filter_keys
        ]
        return {"filters": filters, "attributes": definitions}

    def update_filters(
        self, tenant_id: int, filters: list[dict[str, Any]], actor_user_id: int
    ) -> list[dict[str, Any]]:
        stamp = now_iso()
        conn = self._connect()
        try:
            known = {
                str(row[0]) for row in conn.execute(
                    "SELECT attribute_key FROM tenant_catalog_filters WHERE tenant_id=?",
                    (int(tenant_id),),
                ).fetchall()
            }
            for order, item in enumerate(filters[:500], 1):
                key = str(item.get("attribute_key") or "").strip()
                if key not in known:
                    raise ValueError(f"Неизвестная характеристика: {key}")
                conn.execute(
                    """UPDATE tenant_catalog_filters
                       SET is_enabled=?,display_order=?,updated_at=?,created_by=?
                       WHERE tenant_id=? AND attribute_key=?""",
                    (
                        1 if key in {"title", "marketplace"} or bool(item.get("is_enabled")) else 0,
                        order * 10, stamp, int(actor_user_id), int(tenant_id), key,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return self.filter_configuration(tenant_id, set(MARKETPLACE_CODES))["filters"]

    def matching_product_codes(
        self,
        tenant_id: int,
        allowed_marketplaces: set[str],
        selections: dict[str, list[str]],
    ) -> set[str] | None:
        active = {
            str(key): {str(value).strip().casefold() for value in values if str(value).strip()}
            for key, values in selections.items()
            if values
        }
        if not active:
            return None
        allowed = set(allowed_marketplaces) & set(MARKETPLACE_CODES)
        if not allowed:
            return set()
        conn = self._connect()
        try:
            matched: set[tuple[str, str]] | None = None
            for key, values in active.items():
                definition = conn.execute(
                    """SELECT d.id FROM product_attribute_definitions d
                       JOIN tenant_catalog_filters f
                         ON f.tenant_id=d.tenant_id
                        AND f.attribute_key=d.attribute_key
                        AND f.is_enabled=1
                       WHERE d.tenant_id=? AND d.product_type='*'
                         AND d.attribute_key=?""",
                    (int(tenant_id), key),
                ).fetchone()
                if not definition:
                    return set()
                rows = conn.execute(
                    """SELECT platform,source_product_code,normalized_text
                       FROM product_attribute_values
                       WHERE tenant_id=? AND definition_id=?""",
                    (int(tenant_id), int(definition["id"])),
                ).fetchall()
                current = {
                    (str(row["platform"]), str(row["source_product_code"]))
                    for row in rows
                    if str(row["platform"]) in allowed
                    and str(row["normalized_text"] or "").casefold() in values
                }
                matched = current if matched is None else matched & current
            return {
                PRODUCT_PREFIXES[platform] + code
                for platform, code in (matched or set())
            }
        finally:
            conn.close()
