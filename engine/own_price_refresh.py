from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import ensure_database
from catalog_configuration_service import CatalogConfigurationService
from storage.postgres_compat import configure_connection, connect_database
try:
    from . import kaspi_search_compare_v8_2 as core
except ImportError:
    import kaspi_search_compare_v8_2 as core

OFFERS_URL = "https://kaspi.kz/yml/offer-view/offers"
HOME_URL = "https://kaspi.kz/shop/almaty/"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_json(text: Any, default: Any) -> Any:
    try:
        value = json.loads(text or "")
        return value
    except Exception:
        return default


def chunks(items: list[dict[str, Any]], size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def tenant_catalog_rows(
    conn: sqlite3.Connection, tenant_id: int, tenant_seller_id: int = 0
) -> list[dict[str, Any]]:
    """Return only Kaspi staging rows that belong to one company snapshot."""
    tenant_join = ""
    query_params: list[Any] = []
    if int(tenant_id or 0) > 0:
        membership_table = (
            "tenant_seller_catalog_products"
            if int(tenant_seller_id or 0) > 0 else "tenant_catalog_products"
        )
        seller_clause = " AND tcp.tenant_seller_id=?" if int(tenant_seller_id or 0) > 0 else ""
        tenant_join = f"""JOIN {membership_table} tcp
                              ON tcp.source_product_code=c.product_code
                             AND tcp.marketplace_code='kaspi'
                             AND tcp.tenant_id=? AND tcp.active=1{seller_clause}"""
        query_params.append(int(tenant_id))
        if int(tenant_seller_id or 0) > 0:
            query_params.append(int(tenant_seller_id))
    return [dict(row) for row in conn.execute(
        f"""
        SELECT c.product_code,c.title_catalog,c.catalog_price_kzt,
               m.brand,m.category_codes_json,m.base_product_codes_json,
               m.groups_json,m.has_variants
        FROM catalog_products c
        {tenant_join}
        LEFT JOIN catalog_product_meta m ON m.product_code=c.product_code
        WHERE COALESCE(m.active,1)=1
        ORDER BY c.page_number,c.position_on_page,c.product_code
        """,
        query_params,
    ).fetchall()]


async def post_batch(context: Any, page: Any, payload: dict[str, Any], retries: int, timeout: int) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            response = await context.request.post(
                OFFERS_URL,
                data=payload,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json;charset=UTF-8",
                    "Referer": page.url or HOME_URL,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=timeout * 1000,
            )
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"HTTP {response.status}: {body[:300]}")
            data = await response.json()
            if not isinstance(data, list):
                raise RuntimeError("неожиданный формат ответа offers")
            return [item for item in data if isinstance(item, dict)]
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(min(6.0, 1.3 * attempt))
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=timeout * 1000)
                except Exception:
                    pass
    raise RuntimeError(str(last_error or "offers request failed"))


async def run(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    ensure_database(db_path)
    conn = configure_connection(connect_database(db_path, timeout=60), journal_mode="WAL", busy_timeout=60000)
    rows = tenant_catalog_rows(
        conn, int(args.tenant_id or 0), int(args.tenant_seller_id or 0)
    )
    selected_codes = [core.clean_text(value) for value in str(args.codes or "").split(",") if core.clean_text(value)]
    if selected_codes:
        selected_set = set(selected_codes)
        by_code = {str(row.get("product_code")): row for row in rows}
        rows = [by_code[code] for code in selected_codes if code in by_code and code in selected_set]
    if args.limit > 0:
        rows = rows[:args.limit]
    if not rows:
        print("[Быстрые цены] Каталог пуст.")
        conn.close()
        return 1

    await core.ensure_playwright()
    total_batches = (len(rows) + args.batch_size - 1) // args.batch_size
    updated = 0
    missing = 0
    errors = 0
    print(f"[Быстрые цены] Товаров: {len(rows)}; пакетов: {total_batches}")

    async with core.async_playwright() as playwright:
        context = await core.launch_context(playwright, Path(args.profile), args.headless)
        page = await context.new_page()
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=args.timeout * 1000)
            await core.close_city_modal(page)
            controller = core.BlockController()
            await controller.handle(page)

            for batch_no, batch in enumerate(chunks(rows, args.batch_size), start=1):
                entries = []
                for row in batch:
                    entries.append({
                        "sku": str(row["product_code"]),
                        "hasVariants": bool(row.get("has_variants")),
                        "merchantId": args.seller_id,
                        "product": {
                            "brand": row.get("brand") or "",
                            "categoryCodes": safe_json(row.get("category_codes_json"), []),
                            "baseProductCodes": safe_json(row.get("base_product_codes_json"), []),
                            "groups": safe_json(row.get("groups_json"), []),
                        },
                    })
                payload = {
                    "options": ["PRICE"],
                    "cityId": args.city_id,
                    "installationId": "-1",
                    "entries": entries,
                    "zoneId": [args.zone_id] if args.zone_id else [],
                }
                stamp = now_iso()
                batch_codes = {str(row["product_code"]) for row in batch}
                try:
                    offers = await post_batch(context, page, payload, args.retries, args.timeout)
                    by_code = {
                        str(offer.get("masterSku")): offer
                        for offer in offers
                        if str(offer.get("merchantId")) == args.seller_id
                    }
                    for row in batch:
                        code = str(row["product_code"])
                        offer = by_code.get(code)
                        if offer is None:
                            missing += 1
                            conn.execute(
                                "UPDATE catalog_product_meta SET own_offer_active=0,last_seen_at=? WHERE product_code=?",
                                (stamp, code),
                            )
                            conn.execute(
                                """
                                INSERT INTO own_price_snapshots(
                                    product_code,merchant_id,status,error,captured_at
                                ) VALUES(?,?,?, ?,?)
                                """,
                                (code, args.seller_id, "missing", "оффер Unityre не найден в batch response", stamp),
                            )
                            if int(args.tenant_seller_id or 0) > 0:
                                conn.execute(
                                    """UPDATE tenant_seller_catalog_products
                                       SET availability_status='out_of_stock',last_seen_at=?
                                       WHERE tenant_id=? AND marketplace_code='kaspi'
                                         AND tenant_seller_id=? AND source_product_code=?""",
                                    (
                                        stamp, int(args.tenant_id),
                                        int(args.tenant_seller_id), code,
                                    ),
                                )
                                conn.execute(
                                    """INSERT INTO tenant_seller_price_snapshots(
                                           tenant_id,marketplace_code,tenant_seller_id,
                                           source_product_code,status,error,currency,captured_at
                                       ) VALUES(?,'kaspi',?,?,? ,?,'KZT',?)""",
                                    (
                                        int(args.tenant_id), int(args.tenant_seller_id),
                                        code, "missing", "own offer not found", stamp,
                                    ),
                                )
                            continue
                        price = core.parse_float(offer.get("price"))
                        before = core.parse_float(offer.get("priceBeforeDiscount"))
                        discount = core.parse_float(offer.get("discount"))
                        merchant_sku = core.clean_text(offer.get("merchantSku"))
                        conn.execute(
                            "UPDATE catalog_products SET catalog_price_kzt=?,collected_at=? WHERE product_code=?",
                            (core.parse_int(price), stamp, code),
                        )
                        conn.execute(
                            """
                            UPDATE catalog_product_meta SET own_offer_active=1,
                                price_before_discount_kzt=?,discount_percent=?,last_seen_at=?
                            WHERE product_code=?
                            """,
                            (before, discount, stamp, code),
                        )
                        conn.execute(
                            """
                            INSERT INTO own_price_snapshots(
                                product_code,merchant_id,merchant_sku,price_kzt,
                                price_before_discount_kzt,discount_percent,status,captured_at
                            ) VALUES(?,?,?,?,?,?,?,?)
                            """,
                            (code, args.seller_id, merchant_sku, price, before, discount, "ok", stamp),
                        )
                        if int(args.tenant_seller_id or 0) > 0:
                            conn.execute(
                                """UPDATE tenant_seller_catalog_products
                                   SET price_amount=?,seller_sku=?,availability_status='in_stock',
                                       last_seen_at=?,source_updated_at=?
                                   WHERE tenant_id=? AND marketplace_code='kaspi'
                                     AND tenant_seller_id=? AND source_product_code=?""",
                                (
                                    price, merchant_sku, stamp, stamp,
                                    int(args.tenant_id), int(args.tenant_seller_id), code,
                                ),
                            )
                            conn.execute(
                                """INSERT INTO tenant_seller_price_snapshots(
                                       tenant_id,marketplace_code,tenant_seller_id,
                                       source_product_code,seller_sku,price_amount,
                                       price_before_discount,discount_percent,currency,
                                       status,error,captured_at
                                   ) VALUES(?,'kaspi',?,?,?,?,?,?,?,'ok','',?)""",
                                (
                                    int(args.tenant_id), int(args.tenant_seller_id), code,
                                    merchant_sku, price, before, discount, "KZT", stamp,
                                ),
                            )
                        updated += 1
                    conn.commit()
                    print(
                        f"[Быстрые цены] {batch_no}/{total_batches}; "
                        f"ответов={len(offers)}; обновлено={updated}; нет оффера={missing}"
                    )
                except Exception as exc:
                    errors += len(batch)
                    for code in batch_codes:
                        conn.execute(
                            """
                            INSERT INTO own_price_snapshots(
                                product_code,merchant_id,status,error,captured_at
                            ) VALUES(?,?,?,?,?)
                            """,
                            (code, args.seller_id, "error", str(exc), stamp),
                        )
                        if int(args.tenant_seller_id or 0) > 0:
                            conn.execute(
                                """INSERT INTO tenant_seller_price_snapshots(
                                       tenant_id,marketplace_code,tenant_seller_id,
                                       source_product_code,status,error,currency,captured_at
                                   ) VALUES(?,'kaspi',?,?,? ,?,'KZT',?)""",
                                (
                                    int(args.tenant_id), int(args.tenant_seller_id),
                                    code, "error", str(exc)[:1500], stamp,
                                ),
                            )
                    conn.commit()
                    print(f"[Быстрые цены] ОШИБКА пакета {batch_no}: {exc}")
                await asyncio.sleep(random.uniform(args.min_delay, args.max_delay))
        finally:
            await page.close()
            await context.close()
            conn.close()

    if int(args.tenant_id or 0) > 0 and int(args.tenant_seller_id or 0) <= 0:
        refreshed_codes = [str(row["product_code"]) for row in rows]
        CatalogConfigurationService(db_path).materialize_legacy_kaspi_catalog(
            int(args.tenant_id), refreshed_codes, replace=False
        )
    print(f"[Быстрые цены] Завершено: обновлено={updated}; missing={missing}; errors={errors}")
    return 0 if errors == 0 else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Быстрое обновление цен Unityre пакетным offers-запросом")
    parser.add_argument("--db", default="data/kaspi_market.db")
    parser.add_argument("--profile", default=".kaspi_profile")
    parser.add_argument("--seller-id", default="Unityre")
    parser.add_argument("--tenant-id", type=int, default=0)
    parser.add_argument("--tenant-seller-id", type=int, default=0)
    parser.add_argument("--city-id", default="750000000")
    parser.add_argument("--zone-id", default="Magnum_ZONE1")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--codes", default="", help="SKU через запятую; пусто — весь активный каталог")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--min-delay", type=float, default=0.35)
    parser.add_argument("--max-delay", type=float, default=0.8)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(parse_args())))
    except KeyboardInterrupt:
        print("\n[Быстрые цены] Остановлено. Уже сохранённые пакеты не потеряны.")
        raise SystemExit(130)
