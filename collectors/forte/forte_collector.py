#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import ensure_database
from catalog_configuration_service import CatalogConfigurationService
from storage.postgres_compat import connect_database

MARKET_BASE_URL = "https://market.forte.kz"
API_BASE_URL = "https://apigw.forte.kz/fm"
IMAGE_BASE_URL = "https://object.pscloud.io"
SELLER_CATALOG_PATH = "/api/v4/products/showcase/merchant-filter/{merchant_id}"
SELLER_CATEGORIES_PATH = "/api/v4/catalogs/merchant-catalog/"
MARKET_CATALOG_PATH = "/api/v4/products/showcase/filter-lite"
DETAIL_PATH = "/api/v4/products/showcase/fulldata/{product_id}"
DETAIL_SLUG_PATH = "/api/v4/products/showcase/fulldata/slug/{slug}"
DEFAULT_TIRE_CATEGORY_ID = "171884c9-4db0-11e7-abc4-708bcda3b266"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def merchant_key(value: Any) -> str:
    text = clean_text(value).casefold()
    key = re.sub(r"[^a-zа-яё0-9]+", "-", text).strip("-")
    return key or "merchant"


def same_seller(left: Any, right: Any) -> bool:
    return bool(clean_text(left) and clean_text(right) and merchant_key(left) == merchant_key(right))


def as_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def image_url(value: Any) -> str:
    raw = clean_text(value)
    return urljoin(IMAGE_BASE_URL, raw) if raw else ""


def product_url(product: dict[str, Any]) -> str:
    raw = clean_text(product.get("product_url") or product.get("url"))
    if raw:
        return urljoin(MARKET_BASE_URL, raw)
    slug = clean_text(product.get("slug"))
    product_id = clean_text(product.get("uid") or product.get("product_id"))
    if slug:
        return f"{MARKET_BASE_URL}/items/{quote(slug, safe='-')}"
    return f"{MARKET_BASE_URL}/items/{quote(product_id)}" if product_id else ""


def normalize_specs(characteristics: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(characteristics, list):
        return result
    for item in characteristics:
        if not isinstance(item, dict):
            continue
        name = clean_text(item.get("Title") or item.get("title") or item.get("name"))
        raw_value = item.get("Values") or item.get("Value") or item.get("value")
        if isinstance(raw_value, list):
            value = ", ".join(clean_text(part) for part in raw_value if clean_text(part))
        else:
            value = clean_text(raw_value)
        if name and value:
            result.append({
                "section": clean_text(item.get("GroupName") or "Forte Market"),
                "name": name,
                "value": value,
            })
    return result


def specs_brand(specs: list[dict[str, str]]) -> str:
    for row in specs:
        if clean_text(row.get("name")).casefold() in {"бренд", "brand", "производитель"}:
            return clean_text(row.get("value"))
    return ""


def catalog_categories(product: dict[str, Any]) -> list[str]:
    raw = product.get("category_map") or product.get("CategoryMap") or product.get("categories")
    if isinstance(raw, dict):
        return [clean_text(value) for _, value in sorted(raw.items()) if clean_text(value)]
    if isinstance(raw, list):
        return [clean_text(value) for value in raw if clean_text(value)]
    return []


def request_json_once(
    path: str,
    timeout: int,
    *,
    method: str = "GET",
    payload: dict[str, Any] | list[Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    url = f"{API_BASE_URL}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json",
        "Origin": MARKET_BASE_URL,
        "Referer": f"{MARKET_BASE_URL}/",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=max(5, int(timeout))) as response:
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Forte Market HTTP {exc.code}: {clean_text(detail)}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Forte Market недоступен: {clean_text(exc)}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Forte Market вернул ответ не в формате JSON.") from exc


def request_json(
    path: str,
    timeout: int,
    *,
    method: str = "GET",
    payload: dict[str, Any] | list[Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return request_json_once(path, timeout, method=method, payload=payload, params=params)
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                print(f"Forte Market сеть: попытка {attempt}/3 не прошла, повторяем.", flush=True)
                time.sleep(attempt * 2)
    raise RuntimeError(f"Forte Market недоступен после 3 попыток: {last_error}")


def catalog_payload(
    args: argparse.Namespace,
    offset: int,
    *,
    probe: bool = False,
    include_category: bool = True,
    category_id: str = "",
    sort_order: str = "rating",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "size": max(1, min(int(args.page_size), 100)),
        "from": max(0, int(offset)),
        "is_visible": True,
        "sku_is_visible": True,
        "city": args.city_id,
        "delivery_city": args.city_id,
        "sort": clean_text(sort_order) or "rating",
        "only_in_stock": bool(probe),
    }
    selected_category = clean_text(category_id) or (
        clean_text(args.category_id) if include_category else ""
    )
    if selected_category:
        payload["category"] = selected_category
    return payload


def get_catalog_page(
    args: argparse.Namespace,
    offset: int,
    *,
    probe: bool = False,
    category_id: str = "",
    sort_order: str = "rating",
) -> dict[str, Any]:
    if args.merchant_id:
        path = SELLER_CATALOG_PATH.format(merchant_id=quote(args.merchant_id, safe=""))
    else:
        if not probe:
            raise ValueError(
                "Для синхронизации каталога Forte Market укажите merchant_id продавца в настройках. "
                "Его можно взять из ссылки /merchant-products/<merchant_id> или кабинета продавца."
            )
        path = MARKET_CATALOG_PATH
    # The merchant storefront already defines the catalogue boundary. Applying
    # the global/default category here can legitimately turn a seller with
    # products into an empty response (for example, an electronics seller with
    # the legacy tyre category configured globally).
    value = request_json(
        path,
        args.timeout,
        method="POST",
        payload=catalog_payload(
            args,
            offset,
            probe=probe,
            include_category=not bool(args.merchant_id),
            category_id=category_id,
            sort_order=sort_order,
        ),
    )
    if not isinstance(value, dict):
        raise RuntimeError("Forte Market вернул неожиданный формат каталога.")
    return value


def merchant_leaf_categories(args: argparse.Namespace) -> list[dict[str, Any]]:
    if not clean_text(args.merchant_id):
        return []
    value = request_json(
        SELLER_CATEGORIES_PATH,
        args.timeout,
        params={
            "city": args.city_id,
            "merchant_id": args.merchant_id,
            "nocache": str(int(time.time() * 1000)),
        },
    )
    root = value.get("cached_category") if isinstance(value, dict) else None
    if not isinstance(root, dict):
        return []
    leaves: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        children = [item for item in node.get("ch") or [] if isinstance(item, dict)]
        if children:
            for child in children:
                walk(child)
            return
        category_id = clean_text(node.get("id"))
        if category_id:
            leaves.append({
                "id": category_id,
                "name": clean_text(node.get("nm") or node.get("slug") or category_id),
                "total_products": int(node.get("total_products") or 0),
            })

    walk(root)
    return leaves


def catalog_scope_pages(
    args: argparse.Namespace,
    *,
    category_id: str = "",
    sort_order: str = "rating",
    concurrent_fetch: bool = True,
) -> tuple[int, list[dict[str, Any]]]:
    first = get_catalog_page(
        args, 0, category_id=category_id, sort_order=sort_order
    )
    total = int(first.get("total_hits") or len(first.get("products") or []))
    page_size = max(1, min(int(args.page_size), 100))
    limit = min(total, int(args.max_products)) if args.max_products else total
    offsets = list(range(page_size, max(0, limit), page_size))
    pages = [first]
    if not offsets:
        return total, pages
    if not concurrent_fetch:
        pages.extend(
            get_catalog_page(
                args, offset, category_id=category_id, sort_order=sort_order
            )
            for offset in offsets
        )
        return total, pages
    workers = max(1, min(int(getattr(args, "workers", 4) or 4), 8))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                get_catalog_page,
                args,
                offset,
                category_id=category_id,
                sort_order=sort_order,
            ): offset
            for offset in offsets
        }
        ordered: dict[int, dict[str, Any]] = {}
        for future in as_completed(futures):
            ordered[futures[future]] = future.result()
    pages.extend(ordered[offset] for offset in sorted(ordered))
    return total, pages


def get_product_detail(args: argparse.Namespace, product_id: str) -> dict[str, Any]:
    value = request_json(
        DETAIL_PATH.format(product_id=quote(product_id, safe="")),
        args.timeout,
        params={"cityid": args.city_id, "lite": "true", "ts": str(int(time.time() * 1000))},
    )
    if not isinstance(value, dict) or not isinstance(value.get("showcase"), dict):
        raise RuntimeError(f"Forte Market не вернул карточку товара {product_id}.")
    return value


def source_product_slug(args: argparse.Namespace) -> str:
    parsed = urlparse(str(getattr(args, "source_url", "") or "").strip())
    if str(parsed.hostname or "").casefold() != "market.forte.kz":
        return ""
    match = re.search(r"/items/([^/]+)", parsed.path, re.IGNORECASE)
    return clean_text(match.group(1)) if match else ""


def get_source_product_detail(args: argparse.Namespace) -> dict[str, Any]:
    slug = source_product_slug(args)
    if not slug:
        raise ValueError("Ссылка Forte Market не содержит карточку /items/<товар>.")
    value = request_json(
        DETAIL_SLUG_PATH.format(slug=quote(slug, safe="-")),
        args.timeout,
        params={"cityid": args.city_id, "lite": "true", "ts": str(int(time.time() * 1000))},
    )
    if not isinstance(value, dict) or not isinstance(value.get("showcase"), dict):
        raise RuntimeError("Forte Market не вернул карточку товара по ссылке.")
    return value


def source_product_id(args: argparse.Namespace) -> str:
    explicit = clean_text(getattr(args, "seed_product_id", ""))
    if explicit:
        return explicit
    parsed = urlparse(str(getattr(args, "source_url", "") or "").strip())
    query = parse_qs(parsed.query)
    for key in ("productId", "product_id"):
        values = query.get(key) or []
        if values and clean_text(values[0]):
            return clean_text(values[0])
    return ""


def save_seed_product(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    run_id: str,
) -> tuple[int, int, int] | None:
    product_id = source_product_id(args)
    if not product_id:
        return None
    detail = get_product_detail(args, product_id)
    offers = extract_offers(detail, args)
    if args.merchant_id and not any(
        clean_text(row.get("merchant_id")) == clean_text(args.merchant_id)
        for row in offers
    ):
        raise RuntimeError(
            "Forte Market вернул карточку productId, но в её предложениях нет "
            f"продавца {args.merchant_id}. Проверьте ссылку магазина."
        )
    stamp = now_iso()
    product = dict(detail.get("showcase") or {})
    saved_id = upsert_product(conn, product, args, stamp, detail=detail)
    offer_count = save_offers(conn, run_id, saved_id, offers, stamp) if saved_id else 0
    conn.commit()
    return (1, 1 if saved_id else 0, offer_count)


def resolve_source_merchant(args: argparse.Namespace, detail: dict[str, Any]) -> dict[str, Any] | None:
    raw_offers = detail.get("nomenclatures_data") or []
    candidates: list[dict[str, Any]] = []
    for item in raw_offers if isinstance(raw_offers, list) else []:
        if not isinstance(item, dict):
            continue
        nomenclature = item.get("nomenclature") if isinstance(item.get("nomenclature"), dict) else {}
        merchant_id = clean_text(nomenclature.get("merchant_id"))
        merchant_name = clean_text(item.get("merchant_name"))
        if merchant_id:
            candidates.append({
                "merchant_id": merchant_id,
                "merchant_name": merchant_name or merchant_id,
                "available": bool(nomenclature.get("available")),
            })
    if not candidates:
        return None
    expected = clean_text(args.seller_name)
    selected = next((item for item in candidates if same_seller(item["merchant_name"], expected)), None)
    return selected or next((item for item in candidates if item["available"]), candidates[0])


def connect(db_path: Path) -> sqlite3.Connection:
    conn = connect_database(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def mark_run(conn: sqlite3.Connection, run_id: str, action: str, args: argparse.Namespace) -> None:
    conn.execute(
        """
        INSERT INTO forte_sync_runs(run_id,action,status,seller_name,merchant_id,city_id,started_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (run_id, action, "running", args.seller_name, args.merchant_id, args.city_id, now_iso()),
    )
    conn.commit()


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    total_reported: int = 0,
    products_seen: int = 0,
    offers_seen: int = 0,
    error: str = "",
) -> None:
    conn.execute(
        """
        UPDATE forte_sync_runs
        SET status=?,total_reported=?,products_seen=?,offers_seen=?,error=?,finished_at=?
        WHERE run_id=?
        """,
        (status, int(total_reported), int(products_seen), int(offers_seen), clean_text(error), now_iso(), run_id),
    )
    conn.commit()


def upsert_product(
    conn: sqlite3.Connection,
    product: dict[str, Any],
    args: argparse.Namespace,
    stamp: str,
    *,
    detail: dict[str, Any] | None = None,
) -> str:
    showcase = detail.get("showcase") if isinstance(detail, dict) else None
    source = showcase if isinstance(showcase, dict) else product
    product_id = clean_text(source.get("uid") or product.get("uid") or product.get("product_id"))
    if not product_id:
        return ""
    characteristics = detail.get("characteristics") if isinstance(detail, dict) else None
    specs = normalize_specs(characteristics)
    existing_specs: list[dict[str, str]] = []
    if not specs:
        row = conn.execute("SELECT specs_json FROM forte_products WHERE product_id=?", (product_id,)).fetchone()
        if row:
            try:
                existing_specs = json.loads(row["specs_json"] or "[]")
            except json.JSONDecodeError:
                existing_specs = []
    specs = specs or existing_specs
    raw_categories = source.get("categories_array") or product.get("categories_array") or []
    categories = catalog_categories(product)
    media = source.get("media") or product.get("media") or []
    first_media = next((item.get("media_url") for item in media if isinstance(item, dict) and item.get("media_url")), "")
    price = as_number(product.get("product_price"))
    old_price = as_number(product.get("old_product_price") or source.get("product_old_price"))
    if detail:
        own = next((row for row in extract_offers(detail, args) if row.get("is_own")), None)
        if own and own.get("price_kzt") is not None:
            price = own["price_kzt"]
    raw_value = {"catalog": product}
    if detail:
        raw_value["showcase"] = showcase
    conn.execute(
        """
        INSERT INTO forte_products(
            product_id,short_id,slug,name,brand,product_url,image_url,price_kzt,price_full_kzt,currency,
            category_ids_json,categories_json,specs_json,raw_json,seller_name,merchant_id,
            catalog_rating,catalog_reviews,active,first_seen_at,last_seen_at,last_catalog_at,last_market_at,last_error
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,'')
        ON CONFLICT(product_id) DO UPDATE SET
            short_id=excluded.short_id,slug=excluded.slug,name=excluded.name,brand=excluded.brand,
            product_url=excluded.product_url,image_url=excluded.image_url,
            price_kzt=COALESCE(excluded.price_kzt,forte_products.price_kzt),
            price_full_kzt=COALESCE(excluded.price_full_kzt,forte_products.price_full_kzt),
            currency=excluded.currency,category_ids_json=excluded.category_ids_json,
            categories_json=CASE WHEN excluded.categories_json='[]' THEN forte_products.categories_json ELSE excluded.categories_json END,
            specs_json=CASE WHEN excluded.specs_json='[]' THEN forte_products.specs_json ELSE excluded.specs_json END,
            raw_json=excluded.raw_json,seller_name=excluded.seller_name,merchant_id=excluded.merchant_id,
            catalog_rating=excluded.catalog_rating,catalog_reviews=excluded.catalog_reviews,active=1,
            last_seen_at=excluded.last_seen_at,last_catalog_at=COALESCE(excluded.last_catalog_at,forte_products.last_catalog_at),
            last_market_at=COALESCE(excluded.last_market_at,forte_products.last_market_at),last_error=''
        """,
        (
            product_id,
            clean_text(source.get("short_id") or product.get("short_id")),
            clean_text(source.get("slug") or product.get("slug")),
            clean_text(source.get("name") or product.get("name")),
            specs_brand(specs),
            product_url(source),
            image_url(product.get("img_url") or first_media),
            price,
            old_price,
            "KZT",
            json.dumps(raw_categories, ensure_ascii=False),
            json.dumps(categories, ensure_ascii=False),
            json.dumps(specs, ensure_ascii=False),
            json.dumps(raw_value, ensure_ascii=False),
            args.seller_name,
            args.merchant_id,
            as_number(product.get("aggs_rating") or source.get("aggs_rating")),
            int(product.get("reviews_count") or source.get("reviews_count") or 0),
            stamp,
            stamp,
            stamp,
            stamp if detail else None,
        ),
    )
    return product_id


def extract_offers(detail: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    raw_offers = detail.get("nomenclatures_data") or []
    if not isinstance(raw_offers, list):
        return []
    result: dict[str, dict[str, Any]] = {}
    for item in raw_offers:
        if not isinstance(item, dict):
            continue
        nomenclature = item.get("nomenclature") if isinstance(item.get("nomenclature"), dict) else {}
        merchant_id = clean_text(nomenclature.get("merchant_id"))
        name = clean_text(item.get("merchant_name") or merchant_id)
        if not name and not merchant_id:
            continue
        key = merchant_id or merchant_key(name)
        price = as_number(nomenclature.get("price"))
        is_own = bool(
            (args.merchant_id and merchant_id == args.merchant_id)
            or same_seller(name, args.seller_name)
        )
        available = bool(nomenclature.get("available"))
        availability = "AVAILABLE" if available else "OUT_OF_STOCK"
        row = {
            "merchant_key": key,
            "merchant_id": merchant_id,
            "merchant_name": name,
            "price_kzt": price,
            "merchant_rating": as_number(item.get("rating")),
            "merchant_reviews": int(item.get("reviews_amount") or 0),
            "offer_type": ",".join(clean_text(value) for value in nomenclature.get("sale_channels") or [] if clean_text(value)),
            "availability_status": availability,
            "is_own": 1 if is_own else 0,
            "raw_json": json.dumps(item, ensure_ascii=False),
        }
        current = result.get(key)
        if current is None or (price is not None and (current.get("price_kzt") is None or price < current["price_kzt"])):
            result[key] = row
    return sorted(
        result.values(),
        key=lambda row: (0 if row.get("is_own") else 1, row.get("price_kzt") is None, row.get("price_kzt") or 0),
    )


def save_offers(
    conn: sqlite3.Connection,
    run_id: str,
    product_id: str,
    offers: list[dict[str, Any]],
    stamp: str,
) -> int:
    keys = [str(row["merchant_key"]) for row in offers]
    if keys:
        conn.execute(
            "UPDATE forte_offers SET active=0 WHERE product_id=? AND merchant_key NOT IN (%s)" % ",".join("?" for _ in keys),
            [product_id, *keys],
        )
    else:
        conn.execute("UPDATE forte_offers SET active=0 WHERE product_id=?", (product_id,))
    for row in offers:
        conn.execute(
            """
            INSERT INTO forte_offers(
                product_id,merchant_key,merchant_id,merchant_name,price_kzt,merchant_rating,merchant_reviews,
                offer_type,availability_status,is_own,active,first_seen_at,last_seen_at,last_checked_at,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)
            ON CONFLICT(product_id,merchant_key) DO UPDATE SET
                merchant_id=excluded.merchant_id,merchant_name=excluded.merchant_name,price_kzt=excluded.price_kzt,
                merchant_rating=excluded.merchant_rating,merchant_reviews=excluded.merchant_reviews,
                offer_type=excluded.offer_type,availability_status=excluded.availability_status,is_own=excluded.is_own,
                active=1,last_seen_at=excluded.last_seen_at,last_checked_at=excluded.last_checked_at,raw_json=excluded.raw_json
            """,
            (
                product_id, row["merchant_key"], row.get("merchant_id") or "", row["merchant_name"], row.get("price_kzt"),
                row.get("merchant_rating"), int(row.get("merchant_reviews") or 0), row.get("offer_type") or "",
                row.get("availability_status") or "", int(row.get("is_own") or 0), stamp, stamp, stamp,
                row.get("raw_json") or "{}",
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO forte_price_history(
                run_id,product_id,merchant_key,merchant_name,price_kzt,is_own,captured_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (run_id, product_id, row["merchant_key"], row["merchant_name"], row.get("price_kzt"), int(row.get("is_own") or 0), stamp),
        )
    return len(offers)


def sync_catalog(
    conn: sqlite3.Connection, args: argparse.Namespace, run_id: str
) -> tuple[int, int, int]:
    seed_detail: dict[str, Any] | None = None
    if not args.merchant_id and source_product_slug(args):
        seed_detail = get_source_product_detail(args)
        merchant = resolve_source_merchant(args, seed_detail)
        if merchant:
            args.merchant_id = merchant["merchant_id"]
            args.seller_name = merchant["merchant_name"]
    if not args.merchant_id and seed_detail is None:
        raise ValueError("Не удалось определить merchant_id продавца Forte Market.")
    if not args.merchant_id and seed_detail is not None:
        stamp = now_iso()
        product = dict(seed_detail.get("showcase") or {})
        product_id = upsert_product(conn, product, args, stamp, detail=seed_detail)
        offers = extract_offers(seed_detail, args)
        offer_count = save_offers(conn, run_id, product_id, offers, stamp) if product_id else 0
        conn.commit()
        return (1, 1 if product_id else 0, offer_count)
    loaded = 0
    total_reported = 0
    offers_seen = 0
    seen_product_ids: set[str] = set()
    stamp = now_iso()
    conn.execute("UPDATE forte_products SET active=0 WHERE merchant_id=?", (args.merchant_id,))
    conn.commit()

    def ingest(pages: list[dict[str, Any]], label: str, *, announce: bool = True) -> None:
        nonlocal loaded
        for data in pages:
            products = data.get("products") if isinstance(data.get("products"), list) else []
            with conn:
                for product in products:
                    if not isinstance(product, dict):
                        continue
                    product_id = clean_text(product.get("uid") or product.get("product_id"))
                    if not product_id or product_id in seen_product_ids:
                        continue
                    if upsert_product(conn, product, args, stamp):
                        seen_product_ids.add(product_id)
                        loaded += 1
                    if args.max_products and loaded >= args.max_products:
                        break
            if args.max_products and loaded >= args.max_products:
                break
        if announce:
            print(f"Forte Market каталог ({label}): {loaded}/{total_reported}", flush=True)

    total_reported, pages = catalog_scope_pages(args, sort_order="rating")
    if not any(page.get("products") for page in pages):
        seeded = save_seed_product(conn, args, run_id)
        if seeded:
            print(
                "Forte Market: каталог продавца пуст, сохранена и проверена "
                "карточка productId из исходной ссылки.",
                flush=True,
            )
            return seeded
        raise RuntimeError(
            "Forte Market не вернул товары продавца. Проверьте merchant_id "
            "или передайте ссылку с productId для диагностической проверки."
        )
    ingest(pages, "общий проход")

    target = min(total_reported, int(args.max_products)) if args.max_products else total_reported
    if loaded < target and not args.max_products:
        categories = merchant_leaf_categories(args)
        print(
            f"Forte Market: восстанавливаем пропуски по {len(categories)} категориям продавца.",
            flush=True,
        )

        def fetch_category(category: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            _, category_pages = catalog_scope_pages(
                args,
                category_id=str(category["id"]),
                sort_order="rating",
                concurrent_fetch=False,
            )
            return category, category_pages

        workers = max(1, min(int(getattr(args, "workers", 4) or 4), 8))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_category, category): category for category in categories}
            completed = 0
            for future in as_completed(futures):
                category, category_pages = future.result()
                ingest(
                    category_pages,
                    str(category.get("name") or category.get("id")),
                    announce=False,
                )
                completed += 1
                if completed % 25 == 0 or loaded >= target:
                    print(
                        f"Forte Market категории: {completed}/{len(categories)}; "
                        f"уникальных товаров {loaded}/{total_reported}",
                        flush=True,
                    )
                if loaded >= target:
                    for pending in futures:
                        pending.cancel()
                    break

    if loaded < target and not args.max_products:
        # Independent orders fill rare uncategorized/tie-sorted gaps. Forte's
        # rating order is not stable when many products share the same score.
        for fallback_sort in ("new", "price_desc", "popularity", "price_asc"):
            _, fallback_pages = catalog_scope_pages(args, sort_order=fallback_sort)
            ingest(fallback_pages, f"резервная сортировка {fallback_sort}")
            if loaded >= target:
                break

    # A few products may appear/disappear while a long collection is running,
    # but a larger discrepancy must never be reported as a successful sync.
    minimum_complete = (
        total_reported if total_reported <= 1_000 else max(1, total_reported - 5)
    )
    if not args.max_products and loaded < minimum_complete:
        raise RuntimeError(
            f"Forte Market сообщил {total_reported} товаров, но удалось получить только "
            f"{loaded} уникальных карточек. Сбор остановлен как неполный."
        )
    return total_reported, loaded, offers_seen


def product_ids_for_market(conn: sqlite3.Connection, args: argparse.Namespace) -> list[str]:
    if args.product_ids:
        values = [clean_text(item) for chunk in args.product_ids for item in chunk.split(",")]
        return [item.removeprefix("forte:") for item in values if item]
    rows = conn.execute(
        "SELECT product_id FROM forte_products WHERE active=1 AND (?='' OR merchant_id=?) ORDER BY last_seen_at DESC",
        (args.merchant_id, args.merchant_id),
    ).fetchall()
    result = [str(row["product_id"]) for row in rows]
    return result[: int(args.max_products)] if args.max_products else result


def refresh_market_offers(
    conn: sqlite3.Connection, args: argparse.Namespace, run_id: str
) -> tuple[int, int, int]:
    product_ids = product_ids_for_market(conn, args)
    offers_seen = 0
    errors = 0
    for index, product_id in enumerate(product_ids, 1):
        stamp = now_iso()
        try:
            detail = get_product_detail(args, product_id)
            showcase = detail.get("showcase") or {}
            offers = extract_offers(detail, args)
            with conn:
                upsert_product(conn, showcase, args, stamp, detail=detail)
                offers_seen += save_offers(conn, run_id, product_id, offers, stamp)
                conn.execute(
                    "UPDATE forte_products SET last_market_at=?,last_error='' WHERE product_id=?",
                    (stamp, product_id),
                )
        except Exception as exc:
            errors += 1
            conn.execute("UPDATE forte_products SET last_error=? WHERE product_id=?", (clean_text(exc), product_id))
            conn.commit()
        print(f"Forte Market предложения: {index}/{len(product_ids)}", flush=True)
        time.sleep(max(0.0, args.sleep))
    return len(product_ids), offers_seen, errors


def probe(args: argparse.Namespace) -> dict[str, Any]:
    if source_product_slug(args):
        detail = get_source_product_detail(args)
        product = dict(detail.get("showcase") or {})
        merchant = resolve_source_merchant(args, detail)
        offers = extract_offers(detail, args)
        return {
            "ok": True,
            "api": API_BASE_URL,
            "merchant_id": merchant.get("merchant_id") if merchant else "",
            "merchant_name": merchant.get("merchant_name") if merchant else "",
            "source_scope": "seller" if merchant else "product",
            "sample_product_id": clean_text(product.get("uid")),
            "sample_title": clean_text(product.get("name")),
            "sample_offers": len(offers),
            "sample_url": str(args.source_url),
        }
    data = get_catalog_page(args, 0, probe=True)
    products = data.get("products") if isinstance(data.get("products"), list) else []
    if not products:
        raise RuntimeError("Forte Market доступен, но тестовый запрос не вернул товары.")
    product = products[0]
    product_id = clean_text(product.get("uid"))
    offers: list[dict[str, Any]] = []
    checked = 0
    for candidate in products[:5]:
        candidate_id = clean_text(candidate.get("uid"))
        if not candidate_id:
            continue
        checked += 1
        candidate_offers = extract_offers(get_product_detail(args, candidate_id), args)
        if candidate_offers:
            product = candidate
            product_id = candidate_id
            offers = candidate_offers
            break
    return {
        "ok": True,
        "api": API_BASE_URL,
        "merchant_id": args.merchant_id,
        "city_id": args.city_id,
        "category_id": args.category_id,
        "total_reported": int(data.get("total_hits") or len(products)),
        "sample_product_id": product_id,
        "sample_title": clean_text(product.get("name")),
        "sample_price_kzt": as_number(product.get("product_price")),
        "sample_offers": len(offers),
        "sample_products_checked": checked,
        "sample_url": product_url(product),
    }


def materialize_tenant_catalog(
    conn: sqlite3.Connection, db_path: Path, args: argparse.Namespace
) -> int:
    if int(args.tenant_id or 0) <= 0:
        return 0
    rows = conn.execute(
        """SELECT product_id,name,brand,product_url,image_url,price_kzt,currency,
                  categories_json,specs_json,last_seen_at
           FROM forte_products WHERE active=1 AND merchant_id=? ORDER BY product_id""",
        (str(args.merchant_id),),
    ).fetchall()
    products: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        try:
            attributes = json.loads(str(value.get("specs_json") or "[]"))
        except json.JSONDecodeError:
            attributes = []
        try:
            categories = json.loads(str(value.get("categories_json") or "[]"))
        except json.JSONDecodeError:
            categories = []
        products.append({
            "product_id": value["product_id"], "title": value.get("name") or "",
            "brand": value.get("brand") or "", "url": value.get("product_url") or "",
            "image_url": value.get("image_url") or "", "price": value.get("price_kzt"),
            "currency": value.get("currency") or "KZT",
            "category": categories[-1] if isinstance(categories, list) and categories else "",
            "attributes": attributes, "updated_at": value.get("last_seen_at"),
        })
    return CatalogConfigurationService(db_path).replace_catalog_products(
        int(args.tenant_id), "forte_market", products,
        tenant_seller_id=int(getattr(args, "tenant_seller_id", 0) or 0) or None,
    )


def run(args: argparse.Namespace) -> int:
    if args.action == "probe":
        try:
            print(json.dumps(probe(args), ensure_ascii=False, indent=2), flush=True)
            return 0
        except Exception as exc:
            print(json.dumps({"ok": False, "error": clean_text(exc)}, ensure_ascii=False), flush=True)
            return 1

    db_path = Path(args.db)
    app_db_path = Path(getattr(args, "app_db", "") or db_path)
    ensure_database(db_path)
    conn = connect(db_path)
    run_id = f"forte_{args.action}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    mark_run(conn, run_id, args.action, args)
    total = products = offers = 0
    errors = 0
    try:
        print(
            f"Forte Market старт: {args.action}; продавец={args.seller_name}; merchant_id={args.merchant_id or 'не задан'}; город={args.city_id}",
            flush=True,
        )
        if args.action in {"sync-catalog", "full-sync"}:
            total, products, offers = sync_catalog(conn, args, run_id)
        if args.action in {"refresh-offers", "full-sync"}:
            refreshed, offers, errors = refresh_market_offers(conn, args, run_id)
            products = max(products, refreshed)
        materialize_tenant_catalog(conn, app_db_path, args)
        status = "partial" if errors else "ok"
        error_text = f"Ошибок обновления карточек: {errors}" if errors else ""
        finish_run(conn, run_id, status, total, products, offers, error_text)
        print(
            f"Forte Market готово: товаров {products}, предложений {offers}, ошибок {errors}",
            flush=True,
        )
        return 2 if errors else 0
    except Exception as exc:
        finish_run(conn, run_id, "error", total, products, offers, str(exc))
        print(f"Forte Market ошибка: {exc}", flush=True)
        return 1
    finally:
        conn.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Forte Market seller products and exact-card offers.")
    parser.add_argument("action", choices=["probe", "sync-catalog", "refresh-offers", "full-sync"])
    parser.add_argument("--db", default=str(ROOT / "data" / "unityre_kaspi.db"))
    parser.add_argument("--app-db", default="")
    parser.add_argument("--tenant-id", type=int, default=0)
    parser.add_argument("--tenant-seller-id", type=int, default=0)
    parser.add_argument("--seller-name", default="Unityre")
    parser.add_argument("--merchant-id", default="")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--seed-product-id", default="")
    parser.add_argument("--city-id", default="KZ")
    parser.add_argument("--category-id", default=DEFAULT_TIRE_CATEGORY_ID)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-products", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--product-ids", action="append", default=[])
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
