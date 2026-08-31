from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

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

BASE_Q = ":listingType:merchantListing:allMerchants:{seller_id}"
API_TEMPLATE = (
    "https://kaspi.kz/yml/product-view/pl/filters?"
    "page=0&ui=d&q={q}&filteredByCategory=false&i=-1&c={city_id}"
)
SELLER_URL = "https://kaspi.kz/shop/m/{seller_id}/products/?c={city_id}"
FILTER_PATH = "/yml/product-view/pl/filters"
CARD_SELECTOR = ".item-card[data-product-id]"
CARD_LINK_SELECTOR = ".item-card[data-product-id] .item-card__name-link"

DOM_CARD_JS = r"""
(cards) => cards.map((card, index) => {
  const clean = (value) => (value || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const link = card.querySelector('.item-card__name-link') || card.querySelector('a[href*="/shop/p/"]');
  const image = card.querySelector('.item-card__image') || card.querySelector('img');
  const priceNode = card.querySelector('.item-card__debet .item-card__prices-price') ||
                    card.querySelector('.item-card__prices-price');
  const ratingNode = card.querySelector('.item-card__rating .rating') ||
                     card.querySelector('[class*="rating"]');
  let rating = null;
  if (ratingNode) {
    const direct = clean(ratingNode.textContent).replace(',', '.').match(/\b([0-5](?:\.\d)?)\b/);
    if (direct) rating = Number(direct[1]);
    if (rating === null) {
      const match = String(ratingNode.className).match(/(?:^|\s)_(\d{2})(?:\s|$)/);
      if (match) rating = Number(match[1]) / 10;
    }
  }
  const reviewsNode = card.querySelector('.item-card__rating a') || card.querySelector('a[href*="tab=reviews"]');
  const href = link ? link.getAttribute('href') : '';
  return {
    position_on_page: index + 1,
    id: card.getAttribute('data-product-id') || '',
    title: clean(link?.textContent),
    unitSalePrice: clean(priceNode?.textContent),
    rating,
    reviewsQuantity: clean(reviewsNode?.textContent),
    shopLink: href ? new URL(href, location.origin).href : '',
    previewImages: [{large: image?.getAttribute('src') || image?.getAttribute('data-src') || ''}],
  };
})
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean(value: Any) -> str:
    return core.clean_text(value)


def json_text(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, separators=(",", ":"))


def root_api_url(seller_id: str, city_id: str) -> str:
    q = BASE_Q.format(seller_id=seller_id)
    return API_TEMPLATE.format(q=quote(q, safe=""), city_id=quote(city_id, safe=""))


def complete_seller_snapshot(reported_total: int, collected_unique: int) -> bool:
    """Only an exact, positive seller total may replace an active snapshot."""
    return int(reported_total) > 0 and int(collected_unique) == int(reported_total)


def materialize_verified_tenant_snapshot(
    db_path: Path,
    args: argparse.Namespace,
    products: dict[str, dict[str, Any]],
    product_codes: set[str],
    *,
    is_complete: bool,
) -> int:
    """Publish a seller snapshot only after its authoritative contract passed.

    The collector's SQLite tables are staging. A partial, unverified or empty
    seller response must never merge arbitrary rows into a tenant catalogue.
    """
    if not is_complete or int(args.tenant_id or 0) <= 0:
        return 0

    service = CatalogConfigurationService(db_path)
    if int(args.tenant_seller_id or 0) > 0:
        return service.replace_catalog_products(
            int(args.tenant_id),
            "kaspi",
            products.values(),
            tenant_seller_id=int(args.tenant_seller_id),
        )
    return service.materialize_legacy_kaspi_catalog(
        int(args.tenant_id), product_codes, replace=True
    )


async def fetch_root_payload(page: Any, seller_url: str, retries: int, timeout: int) -> dict[str, Any]:
    """Получает только первую страницу каталога и список фильтров.

    В отличие от сломанной версии 2.0.1, этот запрос не модифицируется для брендов.
    Все брендовые страницы далее открываются только реальными кликами в интерфейсе Kaspi.
    """
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            async with page.expect_response(
                lambda response: (
                    FILTER_PATH in response.url
                    and int(response.status) == 200
                ),
                timeout=timeout * 1000,
            ) as response_info:
                await page.goto(
                    seller_url,
                    wait_until="domcontentloaded",
                    timeout=timeout * 1000,
                )
            response = await response_info.value
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"HTTP {response.status}: {body[:300]}")
            payload = await response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise RuntimeError("Kaspi вернул неожиданный JSON")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(min(8.0, 1.4 * attempt + random.random()))
    raise RuntimeError(str(last_error or "не удалось получить первую страницу каталога"))


def brand_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for item in payload.get("data", {}).get("filters") or []:
        if clean(item.get("id")) == "manufacturerName":
            rows = []
            for row in item.get("rows") or []:
                title = clean(row.get("title"))
                count = core.parse_int(row.get("count")) or 0
                if title and count > 0:
                    rows.append({"name": title, "expected": count})
            return rows
    return []


def infer_card_brand(title: Any, available_brands: list[str]) -> str:
    """Infer a card brand only from brands advertised by this exact seller page."""
    normalized_title = re.sub(r"\s+", " ", clean(title)).casefold()
    matches = [
        clean(brand) for brand in available_brands
        if clean(brand) and clean(brand).casefold() in normalized_title
    ]
    return max(matches, key=len) if matches else ""


async def wait_catalog(page: Any, timeout_seconds: int) -> None:
    await page.wait_for_selector(CARD_LINK_SELECTOR, state="attached", timeout=timeout_seconds * 1000)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(500)
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(250)


async def catalog_signature(page: Any) -> tuple[str, ...]:
    try:
        values = await page.locator(CARD_SELECTOR).evaluate_all(
            "cards => cards.map(card => card.getAttribute('data-product-id') || '')"
        )
    except Exception:
        return ()
    return tuple(clean(value) for value in values if clean(value))


async def active_page_number(page: Any) -> int | None:
    try:
        text = clean(await page.locator(".pagination__el._active").first.inner_text(timeout=1500))
    except Exception:
        return None
    return core.parse_int(text)


async def wait_signature_change(
    page: Any,
    previous: tuple[str, ...],
    timeout_seconds: int,
    expected_page: int | None = None,
) -> tuple[str, ...]:
    deadline = time.monotonic() + timeout_seconds
    last_active: int | None = None
    while time.monotonic() < deadline:
        try:
            await core.close_city_modal(page)
        except Exception:
            pass
        signature = await catalog_signature(page)
        last_active = await active_page_number(page)
        if signature and signature != previous:
            if expected_page is None or last_active is None or last_active == expected_page:
                await page.wait_for_timeout(400)
                return signature
        await page.wait_for_timeout(250)
    raise RuntimeError(
        "карточки каталога не обновились"
        + (f"; ожидалась страница {expected_page}, активная={last_active}" if expected_page else "")
    )


async def find_brand_block(page: Any) -> Any:
    blocks = page.locator(".filters__filter")
    for index in range(await blocks.count()):
        block = blocks.nth(index)
        title = block.locator(".filters__filter-title")
        if await title.count():
            try:
                if clean(await title.first.inner_text(timeout=1200)).casefold() == "бренд":
                    return block
            except Exception:
                pass
    heading = page.get_by_text("Бренд", exact=True)
    if await heading.count():
        candidate = heading.last.locator("xpath=ancestor::*[contains(@class,'filters__filter')][1]")
        if await candidate.count():
            return candidate.first
    raise RuntimeError("на странице не найден фильтр «Бренд»")


async def brand_row(block: Any, brand: str) -> Any | None:
    rows = block.locator(".filters__filter-row")
    for index in range(await rows.count()):
        row = rows.nth(index)
        label = row.locator(".filters__filter-row__description-label")
        try:
            text = clean(await (label.first if await label.count() else row).inner_text(timeout=1200))
        except Exception:
            continue
        if text.casefold() == brand.casefold():
            return row
    return None


async def expand_brand_list(block: Any) -> None:
    """Раскрывает полный список брендов.

    На реальной странице Kaspi кнопка называется «Показать еще». Именно этого варианта
    не было в версии 2.0.1, из-за чего BFGoodrich и другие скрытые бренды не находились.
    """
    spoiler = block.locator(".filters__spoiler")
    if await spoiler.count():
        try:
            await spoiler.first.scroll_into_view_if_needed(timeout=3000)
            await spoiler.first.click(force=True, timeout=5000)
            await asyncio.sleep(0.5)
            return
        except Exception:
            pass
    for text in ("Показать еще", "Показать ещё", "Показать все", "Ещё", "Еще"):
        node = block.get_by_text(text, exact=False)
        if await node.count():
            try:
                await node.first.click(force=True, timeout=5000)
                await asyncio.sleep(0.5)
                return
            except Exception:
                pass


async def response_payload(response: Any) -> dict[str, Any] | None:
    try:
        if response is None or int(response.status) != 200 or FILTER_PATH not in response.url:
            return None
        payload = await response.json()
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload
    except Exception:
        return None
    return None


async def click_brand_filter(
    page: Any,
    seller_url: str,
    brand: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    await page.goto(seller_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
    await core.close_city_modal(page)
    controller = core.BlockController()
    await controller.handle(page)
    await wait_catalog(page, timeout_seconds)
    previous = await catalog_signature(page)

    block = await find_brand_block(page)
    row = await brand_row(block, brand)
    if row is None:
        await expand_brand_list(block)
        row = await brand_row(block, brand)
    if row is None:
        raise RuntimeError(f"бренд {brand} отсутствует в интерфейсе после раскрытия списка")

    # Кликаем по label, а не делаем input.check(). Kaspi привязывает загрузку каталога
    # к обработчику клика на строке; прямое изменение checked не всегда вызывает запрос.
    target = row.locator(".filters__filter-row__description")
    if not await target.count():
        target = row.locator(".filters__filter-row__checkbox")
    if not await target.count():
        target = row

    captured = None
    clicked = False
    try:
        async with page.expect_response(
            lambda response: FILTER_PATH in response.url and int(response.status) == 200,
            timeout=timeout_seconds * 1000,
        ) as response_info:
            await target.first.scroll_into_view_if_needed(timeout=3000)
            await target.first.click(force=True, timeout=5000)
            clicked = True
        captured = await response_info.value
    except Exception:
        # При тайм-ауте expect_response клик обычно уже выполнен. Повторный клик
        # снял бы выбранный фильтр, поэтому кликаем только если действие не произошло.
        if not clicked:
            await target.first.click(force=True, timeout=5000)

    signature = await wait_signature_change(page, previous, timeout_seconds)
    return await response_payload(captured), signature


async def click_next_page(
    page: Any,
    previous: tuple[str, ...],
    expected_page: int,
    timeout_seconds: int,
) -> tuple[dict[str, Any] | None, tuple[str, ...], bool]:
    pagination = page.locator(".pagination")
    if not await pagination.count():
        return None, previous, True
    next_items = pagination.locator(".pagination__el").filter(has_text=re.compile("Следующая", re.I))
    if not await next_items.count():
        return None, previous, True
    next_item = next_items.last
    classes = clean(await next_item.get_attribute("class"))
    if "_disabled" in classes:
        return None, previous, True

    try:
        await next_item.scroll_into_view_if_needed(timeout=3000)
        await next_item.click(force=True, timeout=5000)
    except Exception:
        await next_item.dispatch_event("click")

    signature = await wait_signature_change(page, previous, timeout_seconds, expected_page)
    # Pagination does not consistently use /pl/filters. Waiting for that exact
    # endpoint made every successful page click consume the full request
    # timeout. The changed card signature and active page are the authoritative
    # completion checks; cards are then read from the updated DOM.
    return None, signature, False


async def dom_cards(page: Any, brand: str) -> list[dict[str, Any]]:
    raw = await page.locator(CARD_SELECTOR).evaluate_all(DOM_CARD_JS)
    result = []
    for item in raw:
        if isinstance(item, dict):
            item = dict(item)
            if brand.casefold() != "all":
                item.setdefault("brand", brand)
            result.append(item)
    return result


def upsert_cards(
    conn: sqlite3.Connection,
    cards: list[dict[str, Any]],
    source_segment: str,
    global_start: int,
    catalog_page_url: str,
    seen: set[str],
    collected_products: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, int]:
    stamp = now_iso()
    product_snapshot = collected_products if collected_products is not None else {}
    new_in_run = 0
    db_new = 0
    for offset, card in enumerate(cards):
        code = clean(card.get("id") or card.get("configSku"))
        title = clean(card.get("title"))
        if not code or not title:
            continue
        existed = conn.execute("SELECT 1 FROM catalog_products WHERE product_code=?", (code,)).fetchone() is not None
        price = core.parse_int(card.get("unitSalePrice") or card.get("unitPrice"))
        rating = core.parse_float(card.get("rating"))
        reviews = core.parse_int(card.get("reviewsQuantity"))
        shop_link = clean(card.get("shopLink"))
        if shop_link.startswith("http"):
            product_url = shop_link
        else:
            product_url = "https://kaspi.kz/shop" + (shop_link if shop_link.startswith("/") else "/" + shop_link)
        images = card.get("previewImages") or []
        image_url = ""
        if images and isinstance(images[0], dict):
            image_url = clean(images[0].get("large") or images[0].get("medium") or images[0].get("small"))
        position = global_start + offset
        page_number = position // 12 + 1
        position_on_page = position % 12 + 1
        card_brand = clean(
            card.get("brand")
            or (source_segment if source_segment.casefold() != "all" else "")
        )
        stock = core.parse_int(card.get("stock"))
        product_snapshot[code] = {
            "product_id": code,
            "title": title,
            "brand": card_brand,
            "url": product_url,
            "image_url": image_url,
            "category": clean(card.get("categoryId")),
            "price": price,
            "currency": "KZT",
            "availability": "in_stock" if (stock or 0) > 0 else "",
            "attributes": [],
            "updated_at": stamp,
            "metadata": {
                "rating": rating,
                "reviews": reviews,
                "stock": stock,
                "source_segment": source_segment,
            },
        }
        conn.execute(
            """
            INSERT INTO catalog_products(
                product_code,page_number,position_on_page,title_catalog,
                catalog_price_kzt,catalog_rating,catalog_reviews,product_url,
                image_url,catalog_page_url,collected_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(product_code) DO UPDATE SET
                page_number=excluded.page_number,
                position_on_page=excluded.position_on_page,
                title_catalog=excluded.title_catalog,
                catalog_price_kzt=COALESCE(excluded.catalog_price_kzt,catalog_products.catalog_price_kzt),
                catalog_rating=COALESCE(excluded.catalog_rating,catalog_products.catalog_rating),
                catalog_reviews=COALESCE(excluded.catalog_reviews,catalog_products.catalog_reviews),
                product_url=excluded.product_url,
                image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE catalog_products.image_url END,
                catalog_page_url=excluded.catalog_page_url,
                collected_at=excluded.collected_at
            """,
            (code, page_number, position_on_page, title, price, rating, reviews,
             product_url, image_url, catalog_page_url, stamp),
        )
        conn.execute(
            """
            INSERT INTO catalog_product_meta(
                product_code,brand,category_id,category_codes_json,
                base_product_codes_json,groups_json,has_variants,stock,
                delivery_duration,best_merchant,source_segment,active,
                first_seen_at,last_seen_at,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(product_code) DO UPDATE SET
                brand=CASE
                    WHEN excluded.brand<>'' THEN excluded.brand
                    WHEN lower(trim(COALESCE(catalog_product_meta.brand,'')))='all' THEN ''
                    ELSE catalog_product_meta.brand
                END,
                category_id=CASE WHEN excluded.category_id<>'' THEN excluded.category_id ELSE catalog_product_meta.category_id END,
                category_codes_json=CASE WHEN excluded.category_codes_json<>'[]' THEN excluded.category_codes_json ELSE catalog_product_meta.category_codes_json END,
                base_product_codes_json=CASE WHEN excluded.base_product_codes_json<>'[]' THEN excluded.base_product_codes_json ELSE catalog_product_meta.base_product_codes_json END,
                groups_json=CASE WHEN excluded.groups_json<>'[]' THEN excluded.groups_json ELSE catalog_product_meta.groups_json END,
                has_variants=excluded.has_variants,
                stock=COALESCE(excluded.stock,catalog_product_meta.stock),
                delivery_duration=CASE WHEN excluded.delivery_duration<>'' THEN excluded.delivery_duration ELSE catalog_product_meta.delivery_duration END,
                best_merchant=CASE WHEN excluded.best_merchant<>'' THEN excluded.best_merchant ELSE catalog_product_meta.best_merchant END,
                source_segment=excluded.source_segment,
                active=1,
                last_seen_at=excluded.last_seen_at,
                raw_json=excluded.raw_json
            """,
            (
                code,
                card_brand,
                clean(card.get("categoryId")),
                json_text(card.get("categoryCodes") or []),
                json_text(card.get("baseProductCodes") or []),
                json_text(card.get("groups") or []),
                1 if card.get("hasVariants") else 0,
                core.parse_int(card.get("stock")),
                clean(card.get("deliveryDuration")),
                clean(card.get("bestMerchant")),
                source_segment,
                1,
                stamp,
                stamp,
                json.dumps(card, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        if code not in seen:
            seen.add(code)
            new_in_run += 1
        if not existed:
            db_new += 1
    conn.commit()
    return new_in_run, db_new


def start_segment(conn: sqlite3.Connection, run_id: int, name: str, expected: int) -> int:
    segment_id = conn.execute(
        """
        INSERT INTO catalog_segment_runs(run_id,segment_name,expected_count,status,started_at)
        VALUES(?,?,?,'running',?)
        """,
        (run_id, name, expected, now_iso()),
    ).lastrowid
    conn.commit()
    return int(segment_id)


def finish_segment(
    conn: sqlite3.Connection,
    segment_id: int,
    status: str,
    reported: int,
    collected: int,
    strategy: str,
    request_url: str,
    error: str | None,
) -> None:
    conn.execute(
        """
        UPDATE catalog_segment_runs SET reported_count=?,collected_unique=?,strategy=?,
            request_url=?,status=?,error=?,finished_at=? WHERE id=?
        """,
        (reported, collected, strategy, request_url, status, error, now_iso(), segment_id),
    )
    conn.commit()


async def crawl_brand_segment(
    conn: sqlite3.Connection,
    page: Any,
    seller_url: str,
    brand: str,
    expected: int,
    timeout: int,
    min_delay: float,
    max_delay: float,
    global_index: int,
    seen: set[str],
    collected_products: dict[str, dict[str, Any]],
) -> tuple[int, int, int, str]:
    first_payload, signature = await click_brand_filter(page, seller_url, brand, timeout)
    page_no = 1
    segment_seen_before = len(seen)
    strategy_parts: list[str] = []

    while True:
        payload = first_payload if page_no == 1 else None
        if payload and payload.get("data", {}).get("cards"):
            cards = payload["data"].get("cards") or []
            strategy_parts.append("network")
        else:
            cards = await dom_cards(page, brand)
            strategy_parts.append("dom")
        if not cards:
            raise RuntimeError(f"страница {page_no}: карточки не найдены")

        new_count, db_new = upsert_cards(
            conn, cards, brand, global_index, page.url, seen, collected_products
        )
        global_index += len(cards)
        collected_segment = len(seen) - segment_seen_before
        print(
            f"[Каталог] {brand}: {page_no}/{max(1, math.ceil(expected / 12))}; "
            f"карточек={len(cards)}; новых в запуске={new_count}; новых в базе={db_new}; "
            f"сегмент={collected_segment}/{expected}; всего уникальных={len(seen)}"
        )

        # Достигли ожидаемого количества или последней реальной страницы.
        if expected > 0 and collected_segment >= expected:
            break
        next_payload, next_signature, finished = await click_next_page(
            page, signature, page_no + 1, timeout
        )
        if finished:
            break
        first_payload = next_payload
        signature = next_signature
        page_no += 1
        await asyncio.sleep(random.uniform(min_delay, max_delay))

    strategy = "ui-click+" + ("network" if "network" in strategy_parts else "dom")
    return len(seen) - segment_seen_before, global_index, page_no, strategy


async def crawl_root_fallback(
    conn: sqlite3.Connection,
    page: Any,
    seller_url: str,
    timeout: int,
    min_delay: float,
    max_delay: float,
    global_index: int,
    seen: set[str],
    available_brands: list[str] | None = None,
    collected_products: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, int]:
    """Проверенный V6 fallback: обычный каталог и реальная кнопка «Следующая».

    Он не гарантирует больше лимита витрины, но возвращает рабочий сбор вместо нулевого результата,
    если Kaspi временно изменил фильтр брендов.
    """
    print("[Каталог] Запускается стабильный резервный проход без фильтров (DOM-пагинация V6).")
    await page.goto(seller_url, wait_until="domcontentloaded", timeout=timeout * 1000)
    await core.close_city_modal(page)
    controller = core.BlockController()
    await controller.handle(page)
    await wait_catalog(page, timeout)
    signature = await catalog_signature(page)
    page_no = 1
    before = len(seen)
    no_progress = 0
    product_snapshot = collected_products if collected_products is not None else {}
    while True:
        cards = await dom_cards(page, "all")
        brand_names = available_brands or []
        for card in cards:
            inferred = infer_card_brand(card.get("title"), brand_names)
            if inferred:
                card["brand"] = inferred
        new_count, db_new = upsert_cards(
            conn, cards, "all", global_index, page.url, seen, product_snapshot
        )
        global_index += len(cards)
        no_progress = no_progress + 1 if new_count == 0 else 0
        print(
            f"[Каталог] Резервный проход: страница {page_no}; карточек={len(cards)}; "
            f"новых={new_count}; новых в базе={db_new}; уникальных={len(seen)}"
        )
        if no_progress >= 2:
            break
        _, next_signature, finished = await click_next_page(page, signature, page_no + 1, timeout)
        if finished:
            break
        signature = next_signature
        page_no += 1
        await asyncio.sleep(random.uniform(min_delay, max_delay))
    return len(seen) - before, global_index


async def run(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    ensure_database(db_path)
    conn = configure_connection(connect_database(db_path, timeout=60), journal_mode="WAL", busy_timeout=60000)
    run_id = conn.execute(
        "INSERT INTO catalog_sync_runs(status,started_at) VALUES('running',?)", (now_iso(),)
    ).lastrowid
    conn.commit()

    seen: set[str] = set()
    collected_products: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    segment_success = 0
    global_index = 0
    reported_total = 0

    await core.ensure_playwright()
    async with core.async_playwright() as playwright:
        context = await core.launch_context(playwright, Path(args.profile), args.headless)
        page = await context.new_page()
        try:
            seller_url = SELLER_URL.format(seller_id=args.seller_id, city_id=args.city_id)
            root_payload = await fetch_root_payload(
                page, seller_url, args.retries, args.timeout
            )
            await core.close_city_modal(page)
            controller = core.BlockController()
            await controller.handle(page)
            root_data = root_payload["data"]
            reported_total = int(root_data.get("total") or 0)
            limit = int(root_data.get("limit") or 12)
            available_brand_segments = brand_rows(root_payload)
            available_brand_names = [
                str(item["name"]) for item in available_brand_segments
            ]

            brand_reported_total = sum(
                int(item.get("expected") or 0)
                for item in available_brand_segments
            )

            if reported_total <= 0:
                # This API response is the seller-specific authority. Never
                # reinterpret generic page cards as the seller's catalogue:
                # recommendation and cross-sell widgets can otherwise poison
                # a tenant's active snapshot.
                warning = "seller verification failed: authoritative seller total is zero"
                conn.execute(
                    """UPDATE catalog_sync_runs
                       SET status='verification_failed',reported_total=0,
                           collected_unique=0,warning=?,finished_at=? WHERE id=?""",
                    (warning, now_iso(), run_id),
                )
                conn.commit()
                print("[Catalog] Seller verification failed: authoritative total is zero.")
                return 2

            # Корневая витрина Kaspi может обрывать DOM-пагинацию примерно
            # после первой тысячи позиций, хотя reported_total больше.
            # Поэтому при наличии брендовых фильтров собираем каталог
            # сегментами, а общий root-проход используем как fallback.
            if available_brand_segments:
                segments = available_brand_segments
                print(
                    f"[Каталог] Используем брендовый сбор: "
                    f"сегментов={len(segments)}; "
                    f"сумма по фильтрам={brand_reported_total}; "
                    f"заявлено Kaspi={reported_total}"
                )
            else:
                segments = [{"name": "all", "expected": reported_total}]
                print(
                    "[Каталог] Брендовые сегменты недоступны; "
                    "используется общий DOM-проход."
                )
            # Persist the reported total immediately. If the user safely stops the
            # long crawl, the dashboard still knows that the local catalogue is incomplete.
            conn.execute(
                "UPDATE catalog_sync_runs SET reported_total=?,segments_total=? WHERE id=?",
                (reported_total, len(segments), run_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('catalog_reported_total',?)",
                (json.dumps(reported_total),),
            )
            conn.commit()
            print(
                f"[Каталог] Kaspi сообщает {reported_total} товаров; "
                f"брендовых фильтров доступно: {len(available_brand_segments)}; "
                f"размер страницы: {limit}; режим=реальные клики DOM"
            )

            for index, segment in enumerate(segments, start=1):
                name = clean(segment.get("name")) or f"segment-{index}"
                expected = int(segment.get("expected") or 0)
                segment_id = start_segment(conn, int(run_id), name, expected)
                try:
                    if name == "all":
                        collected, global_index = await crawl_root_fallback(
                            conn, page, seller_url, args.timeout, args.min_delay, args.max_delay,
                            global_index, seen, available_brand_names,
                            collected_products,
                        )
                        pages = max(1, math.ceil(collected / 12))
                        strategy = "dom-pagination-v6"
                    else:
                        collected, global_index, pages, strategy = await crawl_brand_segment(
                            conn, page, seller_url, name, expected, args.timeout,
                            args.min_delay, args.max_delay, global_index, seen,
                            collected_products,
                        )
                    tolerance = max(1, int(expected * 0.04)) if expected else 0
                    if expected and collected + tolerance < expected:
                        raise RuntimeError(f"собрано {collected} из ожидаемых {expected}")
                    segment_success += 1
                    finish_segment(
                        conn, segment_id, "ok", expected, collected, strategy, page.url, None
                    )
                    print(
                        f"[Каталог] [{index}/{len(segments)}] {name}: готово; "
                        f"собрано={collected}; страниц={pages}; стратегия={strategy}"
                    )
                except Exception as exc:
                    message = clean(str(exc))[:700]
                    warnings.append(f"{name}: {message}")
                    finish_segment(
                        conn, segment_id, "error", expected, 0, "ui-click", page.url, message
                    )
                    print(f"[Каталог] ОШИБКА сегмента {name}: {message}")

            # Если брендовый сбор частично сломался, используем старый рабочий V6-проход.
            minimum_complete = reported_total

            if reported_total > 0 and len(seen) < minimum_complete:
                try:
                    added, global_index = await crawl_root_fallback(
                        conn,
                        page,
                        seller_url,
                        args.timeout,
                        args.min_delay,
                        args.max_delay,
                        global_index,
                        seen,
                        available_brand_names,
                        collected_products,
                    )
                    print(
                        f"[Каталог] Резервный общий проход завершён: "
                        f"добавлено={added}; всего уникальных={len(seen)}"
                    )
                except Exception as exc:
                    warnings.append(f"резервный проход: {clean(exc)}")
                    print(
                        f"[Каталог] ОШИБКА резервного прохода: {clean(exc)}"
                    )

            full_enough = complete_seller_snapshot(reported_total, len(seen))
            if full_enough:
                placeholders = ",".join("?" for _ in seen)
                # Shared legacy staging contains products collected for many
                # companies. A tenant sync must never deactivate another
                # seller's rows; its own active set lives in
                # tenant_catalog_products and is replaced below.
                if seen and int(args.tenant_id or 0) <= 0:
                    conn.execute(
                        f"UPDATE catalog_product_meta SET active=0 WHERE product_code NOT IN ({placeholders})",
                        tuple(seen),
                    )
                status = "ok"
                warning = " | ".join(warnings[:5]) if warnings else None
                if warning and len(warnings) > 5:
                    warning += f" | ещё предупреждений: {len(warnings) - 5}"
            else:
                status = "partial"
                warning = " | ".join(warnings[:5]) if warnings else (
                    f"собрано {len(seen)} из заявленных {reported_total}; существующие товары не удалялись"
                )
                if len(warnings) > 5:
                    warning += f" | ещё ошибок: {len(warnings) - 5}"

            # Release the staging connection's write lock before the tenant
            # materializer opens its own atomic transaction.
            conn.commit()
            if int(args.tenant_id or 0) > 0:
                saved = materialize_verified_tenant_snapshot(
                    db_path,
                    args,
                    collected_products,
                    seen,
                    is_complete=full_enough,
                )
                print(
                    f"[Каталог] Каталог компании обновлён: {saved} товаров; "
                    f"режим={'replace' if full_enough else 'active snapshot unchanged'}",
                    flush=True,
                )

            conn.execute(
                """
                UPDATE catalog_sync_runs SET status=?,reported_total=?,collected_unique=?,
                    segments_total=?,segments_success=?,warning=?,finished_at=? WHERE id=?
                """,
                (status, reported_total, len(seen), len(segments), segment_success,
                 warning, now_iso(), run_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('catalog_reported_total',?)",
                (json.dumps(reported_total),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('catalog_last_sync_at',?)",
                (json.dumps(now_iso()),),
            )
            conn.commit()
            print(
                f"[Каталог] Завершено: статус={status}; уникальных={len(seen)}; "
                f"сегментов={segment_success}/{len(segments)}"
            )
            return 0 if status == "ok" else 2
        finally:
            try:
                await page.close()
            finally:
                await context.close()
                conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Синхронизация каталога Unityre через реальные фильтры и DOM-пагинацию Kaspi"
    )
    parser.add_argument("--db", default="data/kaspi_market.db")
    parser.add_argument("--profile", default=".kaspi_profile")
    parser.add_argument("--seller-id", default="Unityre")
    parser.add_argument("--tenant-id", type=int, default=0)
    parser.add_argument("--tenant-seller-id", type=int, default=0)
    parser.add_argument("--city-id", default="750000000")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--min-delay", type=float, default=0.8)
    parser.add_argument("--max-delay", type=float, default=1.8)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--strategy", default="auto", help="сохранено для совместимости с интерфейсом")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(parse_args())))
    except KeyboardInterrupt:
        print("\n[Каталог] Остановлено. Уже полученные товары сохранены.")
        raise SystemExit(130)
