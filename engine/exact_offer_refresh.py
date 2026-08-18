from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import ensure_database
from catalog_configuration_service import CatalogConfigurationService
from storage.postgres_compat import connect_database
try:
    from . import kaspi_search_compare_v8_2 as core
    from .kaspi_market_v9_1 import Database, capture_with_retries
except ImportError:
    import kaspi_search_compare_v8_2 as core
    from kaspi_market_v9_1 import Database, capture_with_retries


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalized(value: Any) -> str:
    return core.normalize(core.clean_text(value))


def result_exit_code(stats: dict[str, int]) -> int:
    completed = int(stats.get("ok") or 0) + int(stats.get("no_competitors") or 0)
    return 0 if int(stats.get("error") or 0) == 0 and completed > 0 else 2


def is_own_offer(offer: dict[str, Any], seller_id: str, seller_name: str) -> bool:
    merchant_id = normalized(offer.get("merchantId"))
    merchant_name = normalized(offer.get("merchantName"))
    wanted_id = normalized(seller_id)
    wanted_name = normalized(seller_name)
    return bool((wanted_id and merchant_id == wanted_id) or (wanted_name and merchant_name == wanted_name))


def get_jobs(
    db_path: Path,
    *,
    tenant_id: int = 0,
    tenant_seller_id: int = 0,
    codes: list[str],
    limit: int,
    refresh: bool,
    only_errors: bool,
    stale_hours: float,
) -> list[dict[str, Any]]:
    conn = connect_database(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    where = ["COALESCE(m.active,1)=1", "c.product_url IS NOT NULL", "TRIM(c.product_url)<>''"]
    params: list[Any] = []
    tenant_join = ""
    scan_join = "LEFT JOIN exact_offer_scans s ON s.product_code=c.product_code"
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
        params.append(int(tenant_id))
        if int(tenant_seller_id or 0) > 0:
            params.append(int(tenant_seller_id))
            scan_join = """LEFT JOIN tenant_seller_offer_scans s
                                  ON s.source_product_code=c.product_code
                                 AND s.marketplace_code='kaspi'
                                 AND s.tenant_id=? AND s.tenant_seller_id=?"""
            params.extend((int(tenant_id), int(tenant_seller_id)))
    if codes:
        placeholders = ",".join("?" for _ in codes)
        where.append(f"c.product_code IN ({placeholders})")
        params.extend(codes)
    if only_errors:
        where.append("s.status='error'")
    elif not refresh:
        threshold = (datetime.now(timezone.utc) - timedelta(hours=max(0.0, stale_hours))).isoformat(timespec="seconds")
        where.append("(s.product_code IS NULL OR s.status NOT IN ('ok','no_competitors') OR datetime(s.checked_at)<datetime(?))")
        params.append(threshold)
    sql = f"""
        SELECT c.product_code,
               COALESCE(NULLIF(d.title_detail,''),c.title_catalog,'') AS title,
               COALESCE(NULLIF(d.product_url,''),c.product_url,'') AS product_url,
               c.catalog_price_kzt,c.catalog_rating,c.catalog_reviews,
               d.specifications_json,d.detail_status,
               s.status AS exact_status,s.checked_at AS exact_checked_at
        FROM catalog_products c
        {tenant_join}
        LEFT JOIN catalog_product_meta m ON m.product_code=c.product_code
        LEFT JOIN product_details d ON d.product_code=c.product_code
        {scan_join}
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(s.checked_at,''),c.page_number,c.position_on_page,c.product_code
    """
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows[:limit] if limit > 0 else rows


def save_success(
    db: Database,
    *,
    run_id: str,
    item: dict[str, Any],
    detail: dict[str, Any],
    offers: list[dict[str, Any]],
    seller_id: str,
    seller_name: str,
    duration: float,
    tenant_id: int = 0,
    tenant_seller_id: int = 0,
) -> tuple[int, int, float | None, float | None]:
    code = core.clean_text(item.get("product_code"))
    stamp = now_iso()
    title = core.clean_text(detail.get("candidate_title_detail") or item.get("title"))
    specs = core.clean_specs(detail.get("specifications") or item.get("specifications_json") or [])

    valid_offers: list[dict[str, Any]] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        if core.parse_float(offer.get("price")) is None or not core.clean_text(offer.get("merchantId")):
            continue
        valid_offers.append(offer)

    db.conn.execute(
        "DELETE FROM market_seller_offers WHERE source_product_code=? AND candidate_product_code=?",
        (code, code),
    )
    db.save_offers(code, code, valid_offers)

    prices = [float(core.parse_float(offer.get("price")) or 0) for offer in valid_offers]
    prices = [value for value in prices if value > 0]
    own_price = next((
        core.parse_float(offer.get("price")) for offer in valid_offers
        if is_own_offer(offer, seller_id, seller_name)
    ), None)
    competitors = [offer for offer in valid_offers if not is_own_offer(offer, seller_id, seller_name)]
    competitor_prices = [float(core.parse_float(offer.get("price")) or 0) for offer in competitors]
    competitor_prices = [value for value in competitor_prices if value > 0]

    exact_candidate = {
        "candidate_product_code": code,
        "search_page": 0,
        "position": 0,
        "candidate_title": title,
        "candidate_url": core.clean_text(item.get("product_url")),
        "candidate_price_kzt": min(prices) if prices else item.get("catalog_price_kzt"),
        "candidate_rating": item.get("catalog_rating"),
        "candidate_reviews": item.get("catalog_reviews"),
        "fast_score": 100,
        "fast_decision": "accepted",
        "fast_reason": "та же карточка Kaspi и тот же product_code",
        "candidate_title_detail": title,
        "candidate_specs": specs,
        "detail_score": 100,
        "final_decision": "accepted",
        "detail_reason": "точное совпадение по product_code; предложения разных продавцов",
    }
    db.save_candidate(code, exact_candidate)

    effective_price = own_price if own_price is not None else item.get("catalog_price_kzt")
    db.conn.execute(
        """
        INSERT INTO product_details(
            product_code,product_url,title_detail,price_kzt,product_rating,product_reviews,
            specifications_json,detail_status,detail_error,detail_collected_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(product_code) DO UPDATE SET
            product_url=excluded.product_url,title_detail=excluded.title_detail,
            price_kzt=COALESCE(excluded.price_kzt,product_details.price_kzt),
            product_rating=COALESCE(excluded.product_rating,product_details.product_rating),
            product_reviews=COALESCE(excluded.product_reviews,product_details.product_reviews),
            specifications_json=excluded.specifications_json,detail_status='ok',
            detail_error=NULL,detail_collected_at=excluded.detail_collected_at
        """,
        (
            code, core.clean_text(item.get("product_url")), title, effective_price,
            core.parse_float(item.get("catalog_rating")), core.parse_int(item.get("catalog_reviews")),
            json.dumps(specs, ensure_ascii=False), "ok", None, stamp,
        ),
    )

    for offer in valid_offers:
        db.conn.execute(
            """
            INSERT INTO exact_offer_snapshots(
                run_id,product_code,merchant_id,merchant_name,merchant_sku,price_kzt,
                merchant_rating,merchant_reviews,is_own,captured_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, code, core.clean_text(offer.get("merchantId")),
                core.clean_text(offer.get("merchantName")), core.clean_text(offer.get("merchantSku")),
                core.parse_float(offer.get("price")), core.parse_float(offer.get("merchantRating")),
                core.parse_int(offer.get("merchantReviewsQuantity")),
                1 if is_own_offer(offer, seller_id, seller_name) else 0, stamp,
            ),
        )
        if int(tenant_id or 0) > 0 and int(tenant_seller_id or 0) > 0:
            db.conn.execute(
                """INSERT INTO tenant_seller_offer_snapshots(
                       run_id,tenant_id,marketplace_code,tenant_seller_id,
                       source_product_code,merchant_id,merchant_name,merchant_sku,
                       price_amount,currency,merchant_rating,merchant_reviews,is_own,captured_at
                   ) VALUES(?,?,'kaspi',?,?,?,?,?,?,'KZT',?,?,?,?)""",
                (
                    run_id, int(tenant_id), int(tenant_seller_id), code,
                    core.clean_text(offer.get("merchantId")),
                    core.clean_text(offer.get("merchantName")),
                    core.clean_text(offer.get("merchantSku")),
                    core.parse_float(offer.get("price")),
                    core.parse_float(offer.get("merchantRating")),
                    core.parse_int(offer.get("merchantReviewsQuantity")),
                    1 if is_own_offer(offer, seller_id, seller_name) else 0,
                    stamp,
                ),
            )

    status = "ok" if competitors else "no_competitors"
    db.conn.execute(
        """
        INSERT INTO exact_offer_scans(
            product_code,status,offers_count,competitor_count,min_price_kzt,max_price_kzt,
            duration_seconds,error,checked_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(product_code) DO UPDATE SET
            status=excluded.status,offers_count=excluded.offers_count,
            competitor_count=excluded.competitor_count,min_price_kzt=excluded.min_price_kzt,
            max_price_kzt=excluded.max_price_kzt,duration_seconds=excluded.duration_seconds,
            error=NULL,checked_at=excluded.checked_at
        """,
        (
            code, status, len(valid_offers), len(competitors),
            min(competitor_prices) if competitor_prices else None,
            max(competitor_prices) if competitor_prices else None,
            round(duration, 3), None, stamp,
        ),
    )
    if int(tenant_id or 0) > 0 and int(tenant_seller_id or 0) > 0:
        db.conn.execute(
            """INSERT INTO tenant_seller_offer_scans(
                   tenant_id,marketplace_code,tenant_seller_id,source_product_code,
                   status,offers_count,competitor_count,min_price,max_price,
                   duration_seconds,error,checked_at
               ) VALUES(?,'kaspi',?,?,?,?,?,?,?,?,'',?)
               ON CONFLICT(
                   tenant_id,marketplace_code,tenant_seller_id,source_product_code
               ) DO UPDATE SET
                   status=excluded.status,offers_count=excluded.offers_count,
                   competitor_count=excluded.competitor_count,
                   min_price=excluded.min_price,max_price=excluded.max_price,
                   duration_seconds=excluded.duration_seconds,error='',
                   checked_at=excluded.checked_at""",
            (
                int(tenant_id), int(tenant_seller_id), code, status,
                len(valid_offers), len(competitors),
                min(competitor_prices) if competitor_prices else None,
                max(competitor_prices) if competitor_prices else None,
                round(duration, 3), stamp,
            ),
        )
        if own_price is not None:
            db.conn.execute(
                """UPDATE tenant_seller_catalog_products
                   SET price_amount=?,currency='KZT',last_seen_at=?,source_updated_at=?
                   WHERE tenant_id=? AND marketplace_code='kaspi'
                     AND tenant_seller_id=? AND source_product_code=?""",
                (
                    own_price, stamp, stamp, int(tenant_id),
                    int(tenant_seller_id), code,
                ),
            )
    db.conn.commit()
    return len(valid_offers), len(competitors), (
        min(competitor_prices) if competitor_prices else None
    ), (max(competitor_prices) if competitor_prices else None)


def save_error(
    db: Database,
    code: str,
    error: str,
    duration: float,
    *,
    tenant_id: int = 0,
    tenant_seller_id: int = 0,
) -> None:
    db.conn.execute(
        """
        INSERT INTO exact_offer_scans(
            product_code,status,offers_count,competitor_count,duration_seconds,error,checked_at
        ) VALUES(?,'error',0,0,?,?,?)
        ON CONFLICT(product_code) DO UPDATE SET
            status='error',duration_seconds=excluded.duration_seconds,
            error=excluded.error,checked_at=excluded.checked_at
        """,
        (code, round(duration, 3), error[:1500], now_iso()),
    )
    if int(tenant_id or 0) > 0 and int(tenant_seller_id or 0) > 0:
        db.conn.execute(
            """INSERT INTO tenant_seller_offer_scans(
                   tenant_id,marketplace_code,tenant_seller_id,source_product_code,
                   status,offers_count,competitor_count,duration_seconds,error,checked_at
               ) VALUES(?,'kaspi',?,?,'error',0,0,?,?,?)
               ON CONFLICT(
                   tenant_id,marketplace_code,tenant_seller_id,source_product_code
               ) DO UPDATE SET status='error',offers_count=0,competitor_count=0,
                   duration_seconds=excluded.duration_seconds,error=excluded.error,
                   checked_at=excluded.checked_at""",
            (
                int(tenant_id), int(tenant_seller_id), code,
                round(duration, 3), error[:1500], now_iso(),
            ),
        )
    db.conn.execute(
        "INSERT INTO errors(stage,product_code,message,created_at) VALUES('exact_offer_refresh',?,?,?)",
        (code, error[:1500], now_iso()),
    )
    db.conn.commit()


async def run(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    ensure_database(db_path)
    codes = [core.clean_text(value) for value in str(args.codes or "").split(",") if core.clean_text(value)]
    jobs = get_jobs(
        db_path,
        tenant_id=int(args.tenant_id or 0),
        tenant_seller_id=int(args.tenant_seller_id or 0),
        codes=codes,
        limit=max(0, int(args.limit)),
        refresh=bool(args.refresh),
        only_errors=bool(args.only_errors),
        stale_hours=max(0.0, float(args.stale_hours)),
    )
    if not jobs:
        print("[ТОЧНЫЕ ПРЕДЛОЖЕНИЯ] Нет товаров для обновления.")
        return 0

    workers = max(1, min(int(args.workers), 3))
    print("=" * 78)
    print("KASPI EXACT OFFERS 3.1 — ТОЛЬКО ОДНА И ТА ЖЕ КАРТОЧКА ТОВАРА")
    print("Сравнение выполняется по product_code внутри карточки Kaspi.")
    print("Поиск аналогов других брендов и моделей не выполняется.")
    print("=" * 78)
    print(f"Товаров: {len(jobs)}; воркеров: {workers}; checkpoint после каждой карточки.")

    queue: asyncio.Queue[tuple[int, dict[str, Any]] | None] = asyncio.Queue()
    for index, item in enumerate(jobs, start=1):
        queue.put_nowait((index, item))
    for _ in range(workers):
        queue.put_nowait(None)

    db = Database(db_path)
    db_lock = asyncio.Lock()
    controller = core.BlockController()
    run_id = f"exact_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    stats = {"ok": 0, "no_competitors": 0, "error": 0}

    await core.ensure_playwright()
    async with core.async_playwright() as playwright:
        context = await core.launch_context(playwright, Path(args.profile), bool(args.headless))
        try:
            async def worker(worker_id: int) -> None:
                page = await context.new_page()
                await page.route("**/*", core.light_route)
                try:
                    while True:
                        job = await queue.get()
                        if job is None:
                            queue.task_done()
                            break
                        index, item = job
                        code = core.clean_text(item.get("product_code"))
                        title = core.clean_text(item.get("title"))
                        url = core.clean_text(item.get("product_url"))
                        started = time.monotonic()
                        print(f"\n[ТОЧНЫЙ ТОВАР {index}/{len(jobs)}] {code} — {title}")
                        try:
                            detail, offers, attempts = await capture_with_retries(
                                page, url, int(args.timeout), str(args.city_id), controller, int(args.retries)
                            )
                            duration = time.monotonic() - started
                            async with db_lock:
                                total, competitors, minimum, maximum = save_success(
                                    db,
                                    run_id=run_id,
                                    item=item,
                                    detail=detail,
                                    offers=offers,
                                    seller_id=str(args.seller_id),
                                    seller_name=str(args.seller_name),
                                    duration=duration,
                                    tenant_id=int(args.tenant_id or 0),
                                    tenant_seller_id=int(args.tenant_seller_id or 0),
                                )
                            status = "ok" if competitors else "no_competitors"
                            stats[status] += 1
                            min_text = f"{minimum:.0f}" if minimum is not None else "—"
                            max_text = f"{maximum:.0f}" if maximum is not None else "—"
                            print(
                                f"COMPLETE | предложений={total} | конкурентов={competitors} | "
                                f"мин={min_text} | макс={max_text} | попыток={attempts} | {duration:.1f} сек."
                            )
                        except Exception as exc:
                            duration = time.monotonic() - started
                            async with db_lock:
                                save_error(
                                    db, code, str(exc), duration,
                                    tenant_id=int(args.tenant_id or 0),
                                    tenant_seller_id=int(args.tenant_seller_id or 0),
                                )
                            stats["error"] += 1
                            print(f"ERROR | {exc} | {duration:.1f} сек.")
                        finally:
                            queue.task_done()
                        await asyncio.sleep(random.uniform(float(args.min_delay), float(args.max_delay)))
                finally:
                    await page.close()

            tasks = [asyncio.create_task(worker(index + 1)) for index in range(workers)]
            await queue.join()
            await asyncio.gather(*tasks)
        finally:
            await context.close()
            db.conn.close()

    print("\n" + "=" * 78)
    print(
        f"Готово: успешно={stats['ok']}; без других продавцов={stats['no_competitors']}; "
        f"ошибок={stats['error']}; всего={len(jobs)}"
    )
    print("Старые похожие карточки сохранены в базе как архив и не участвуют в аналитике.")
    print("=" * 78)
    if int(args.tenant_id or 0) > 0 and int(args.tenant_seller_id or 0) <= 0:
        CatalogConfigurationService(db_path).materialize_legacy_kaspi_catalog(
            int(args.tenant_id), [str(item["product_code"]) for item in jobs], replace=False
        )
    return result_exit_code(stats)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Обновляет предложения продавцов только внутри точной карточки Kaspi."
    )
    parser.add_argument("--db", default="data/unityre_kaspi.db")
    parser.add_argument("--profile", default=".kaspi_profile")
    parser.add_argument("--seller-id", default="Unityre")
    parser.add_argument("--tenant-id", type=int, default=0)
    parser.add_argument("--tenant-seller-id", type=int, default=0)
    parser.add_argument("--seller-name", default="Unityre")
    parser.add_argument("--city-id", default="750000000")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--codes", default="")
    parser.add_argument("--timeout", type=int, default=50)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--min-delay", type=float, default=0.6)
    parser.add_argument("--max-delay", type=float, default=1.4)
    parser.add_argument("--stale-hours", type=float, default=24)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--only-errors", action="store_true")
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(parse_args())))
    except KeyboardInterrupt:
        print("\nОстановлено. Уже обработанные точные карточки сохранены.")
        raise SystemExit(130)
