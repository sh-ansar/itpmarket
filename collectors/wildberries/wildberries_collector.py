from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog_configuration_service import CatalogConfigurationService
from schema import ensure_database
from storage.postgres_compat import connect_database


CATALOG_ENDPOINT = "https://catalog.wb.ru/sellers/v4/catalog"
MARKETPLACE_CODE = "wildberries"
PAGE_PRODUCTS = 100
SORT_ORDERS = ("popular", "newly", "priceup", "pricedown", "rate")

# Wildberries keeps public product images on numbered basket hosts. The host
# changes by article volume, while the rest of the path is stable. Boundaries
# are kept in one place so a future CDN change does not affect catalog logic.
WB_BASKET_BOUNDARIES = (
    (143, 1), (287, 2), (431, 3), (719, 4), (1007, 5),
    (1061, 6), (1115, 7), (1169, 8), (1313, 9), (1601, 10),
    (1655, 11), (1919, 12), (2045, 13), (2189, 14), (2405, 15),
    (2621, 16), (2837, 17), (3053, 18), (3269, 19), (3485, 20),
    (3701, 21), (3917, 22), (4133, 23), (4349, 24), (4565, 25),
    (4877, 26), (5189, 27), (5501, 28), (5813, 29), (6125, 30),
    (6437, 31), (6749, 32), (7061, 33), (7373, 34), (7685, 35),
    (7997, 36), (8309, 37), (8621, 38), (8933, 39), (9245, 40),
    (10109, 41), (10639, 42), (11169, 43), (11699, 44),
)
_VERIFIED_IMAGE_BASKETS: dict[int, int] = {}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def request_json(url: str, timeout: int, retries: int) -> dict[str, Any]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/139.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://global.wildberries.ru",
        "Referer": "https://global.wildberries.ru/",
    }
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            with urlopen(Request(url, headers=headers), timeout=max(5, timeout)) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise RuntimeError("Wildberries вернул неожиданный формат ответа.")
                return payload
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 498, 500, 502, 503, 504}:
                raise RuntimeError(f"Wildberries API: HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt + 1 < max(1, retries):
            time.sleep(min(8.0, 0.8 * (2**attempt)))
    raise RuntimeError(f"Wildberries API недоступен после {max(1, retries)} попыток: {last_error}")


def catalog_page(args: argparse.Namespace, page: int, sort_order: str) -> dict[str, Any]:
    query = urlencode({
        "ab_testing": "false",
        "appType": "1",
        "curr": str(args.currency).casefold(),
        "dest": str(args.destination),
        "hide_dtype": "13",
        "lang": "ru",
        "page": max(1, int(page)),
        "sort": sort_order,
        "spp": "30",
        "supplier": str(args.seller_id),
    })
    return request_json(
        f"{CATALOG_ENDPOINT}?{query}", int(args.timeout), int(args.retries)
    )


def _price(product: dict[str, Any], key: str) -> float | None:
    values: list[float] = []
    for size in product.get("sizes") or []:
        if not isinstance(size, dict) or not isinstance(size.get("price"), dict):
            continue
        raw = size["price"].get(key)
        try:
            amount = float(raw) / 100.0
        except (TypeError, ValueError):
            continue
        if amount > 0:
            values.append(amount)
    return min(values) if values else None


def _basket_number(product_id: int) -> int:
    volume = product_id // 100_000
    for upper_volume, basket in WB_BASKET_BOUNDARIES:
        if volume <= upper_volume:
            return basket
    # New baskets are appended monotonically. This is only a first guess; the
    # live collector verifies it and searches neighbouring/current hosts.
    return 45


def _image_url(product_id: int, basket: int, size: str = "c246x328") -> str:
    volume = product_id // 100_000
    part = product_id // 1_000
    return (
        f"https://basket-{int(basket):02d}.wbbasket.ru/vol{volume}/part{part}/"
        f"{product_id}/images/{size}/1.webp"
    )


def image_url_for_article(product_id: Any, size: str = "c246x328") -> str:
    """Build the current deterministic WB CDN URL without a network request."""
    try:
        article = int(str(product_id).strip())
    except (TypeError, ValueError):
        return ""
    if article <= 0:
        return ""
    volume = article // 100_000
    basket = _VERIFIED_IMAGE_BASKETS.get(volume, _basket_number(article))
    return _image_url(article, basket, size)


def resolve_image_url(product_id: Any, timeout: int = 5) -> str:
    """Return a usable public WB image URL and cache its basket per volume."""
    try:
        article = int(str(product_id).strip())
    except (TypeError, ValueError):
        return ""
    if article <= 0:
        return ""
    volume = article // 100_000
    guess = _VERIFIED_IMAGE_BASKETS.get(volume, _basket_number(article))
    candidates = [guess]
    candidates.extend(
        value for distance in range(1, 7)
        for value in (guess - distance, guess + distance)
        if 1 <= value <= 64
    )
    candidates.extend(value for value in range(1, 65) if value not in candidates)
    for basket in candidates:
        url = _image_url(article, basket)
        try:
            request = Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=max(1, min(int(timeout), 8))) as response:
                if int(getattr(response, "status", 0) or 0) == 200 and str(
                    response.headers.get("Content-Type") or ""
                ).casefold().startswith("image/"):
                    _VERIFIED_IMAGE_BASKETS[volume] = basket
                    return url
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
    # A temporary CDN/network failure must not erase a previously displayable
    # URL from the catalog. The deterministic candidate remains useful for the
    # browser and will be revalidated on the next collection.
    return image_url_for_article(article)


def product_row(
    product: dict[str, Any], seller_id: str, currency: str = "KZT",
    image_url: str | None = None,
) -> dict[str, Any]:
    product_id = str(product.get("id") or "").strip()
    brand = str(product.get("brand") or "").strip()
    name = str(product.get("name") or product_id).strip()
    supplier = str(product.get("supplier") or seller_id).strip()
    attributes = [
        {"name": "Бренд", "value": brand},
        {"name": "Продавец", "value": supplier},
        {"name": "Рейтинг товара", "value": str(product.get("reviewRating") or product.get("rating") or "")},
        {"name": "Категория Wildberries", "value": str(product.get("subjectId") or "")},
    ]
    attributes = [item for item in attributes if item["value"]]
    return {
        "product_id": product_id,
        "seller_sku": str(product.get("root") or product_id),
        "title": f"{brand} / {name}" if brand and not name.casefold().startswith(brand.casefold()) else name,
        "brand": brand,
        "model": name,
        "url": f"https://global.wildberries.ru/catalog/{product_id}/detail.aspx",
        "image_url": (
            image_url if image_url is not None
            else ("" if product.get("pics") == 0 else _image_url(int(product_id), _basket_number(int(product_id))))
        ),
        "category": str(product.get("subjectId") or ""),
        "price": _price(product, "product"),
        "currency": str(currency or "KZT").upper(),
        "availability": "in_stock" if int(product.get("totalQuantity") or 0) > 0 else "out_of_stock",
        "attributes": attributes,
        "updated_at": now_iso(),
        "metadata": {
            "seller_id": seller_id,
            "seller_name": supplier,
            "full_price": _price(product, "basic"),
            "quantity": int(product.get("totalQuantity") or 0),
            "rating": product.get("reviewRating") or product.get("rating"),
            "feedbacks": int(product.get("feedbacks") or 0),
            "image_count": int(product.get("pics") or 0),
            "supplier_rating": product.get("supplierRating"),
            "raw": product,
        },
    }


def collect(args: argparse.Namespace) -> tuple[int, list[dict[str, Any]], str]:
    seen: dict[str, dict[str, Any]] = {}
    reported = 0
    seller_name = str(args.seller_id)
    limit = int(args.max_products or 0)
    for sort_order in SORT_ORDERS:
        first = catalog_page(args, 1, sort_order)
        reported = max(reported, int(first.get("total") or 0))
        pages = max(1, (reported + PAGE_PRODUCTS - 1) // PAGE_PRODUCTS)
        for page in range(1, pages + 1):
            data = first if page == 1 else catalog_page(args, page, sort_order)
            products = data.get("products") if isinstance(data.get("products"), list) else []
            if not products:
                break
            for product in products:
                if not isinstance(product, dict):
                    continue
                product_id = str(product.get("id") or "").strip()
                if not product_id:
                    continue
                if not seller_name or seller_name == str(args.seller_id):
                    seller_name = str(product.get("supplier") or seller_name)
                verified_image = None
                if int(product.get("pics") or 0) > 0:
                    verified_image = resolve_image_url(product_id, int(args.timeout))
                seen[product_id] = product_row(
                    product, str(args.seller_id), str(args.currency), verified_image
                )
                if limit and len(seen) >= limit:
                    break
            print(
                f"Wildberries каталог: {len(seen)}/{min(reported, limit) if limit else reported}; "
                f"страница {page}/{pages}; сортировка {sort_order}",
                flush=True,
            )
            if (limit and len(seen) >= limit) or (reported and len(seen) >= reported):
                break
            if args.sleep:
                time.sleep(max(0.0, float(args.sleep)))
        if (limit and len(seen) >= limit) or (reported and len(seen) >= reported):
            break
    target = min(reported, limit) if limit else reported
    if target and len(seen) < target:
        raise RuntimeError(
            f"Wildberries сообщил {reported} товаров, но получено только {len(seen)} уникальных позиций."
        )
    return reported, list(seen.values()), seller_name


def persist(args: argparse.Namespace, products: list[dict[str, Any]], seller_name: str) -> int:
    db_path = Path(args.db)
    ensure_database(db_path)
    saved = CatalogConfigurationService(db_path).replace_catalog_products(
        int(args.tenant_id), MARKETPLACE_CODE, products
    )
    import sqlite3

    conn = connect_database(db_path, timeout=30)
    try:
        stamp = now_iso()
        conn.execute(
            """UPDATE tenant_integrations
               SET seller_name=?,seller_identifier=?,product_count=?,last_sync_at=?,
                   last_status='completed',last_error='',updated_at=?
               WHERE tenant_id=? AND integration_code=?""",
            (
                seller_name, str(args.seller_id), saved, stamp, stamp,
                int(args.tenant_id), MARKETPLACE_CODE,
            ),
        )
        conn.execute(
            """UPDATE tenant_marketplace_sellers SET display_name=?,updated_at=?
               WHERE tenant_id=? AND marketplace_code=? AND external_seller_id=?""",
            (seller_name, stamp, int(args.tenant_id), MARKETPLACE_CODE, str(args.seller_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return saved


def run(args: argparse.Namespace) -> int:
    if not str(args.seller_id).isdigit():
        print("Wildberries ошибка: seller_id должен состоять из цифр.", flush=True)
        return 1
    try:
        if args.action == "probe":
            payload = catalog_page(args, 1, "popular")
            products = payload.get("products") if isinstance(payload.get("products"), list) else []
            sample = products[0] if products and isinstance(products[0], dict) else {}
            print(json.dumps({
                "ok": True,
                "seller_id": str(args.seller_id),
                "seller_name": str(sample.get("supplier") or args.seller_id),
                "total": int(payload.get("total") or 0),
                "sample_product_id": str(sample.get("id") or ""),
            }, ensure_ascii=False), flush=True)
            return 0
        total, products, seller_name = collect(args)
        saved = persist(args, products, seller_name)
        print(
            f"Wildberries готово: продавец={seller_name}; товаров {saved}/{total}",
            flush=True,
        )
        return 0
    except Exception as exc:
        if args.action != "probe" and int(args.tenant_id or 0) > 0:
            import sqlite3
            conn = connect_database(args.db, timeout=30)
            try:
                stamp = now_iso()
                conn.execute(
                    """UPDATE tenant_integrations SET last_status='failed',last_error=?,updated_at=?
                       WHERE tenant_id=? AND integration_code=?""",
                    (str(exc)[:2000], stamp, int(args.tenant_id), MARKETPLACE_CODE),
                )
                conn.commit()
            finally:
                conn.close()
        print(f"Wildberries ошибка: {exc}", flush=True)
        return 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect a public Wildberries seller catalog.")
    parser.add_argument("action", choices=("probe", "sync-catalog", "refresh-prices", "full-sync"))
    parser.add_argument("--db", default=str(ROOT / "data" / "unityre_kaspi.db"))
    parser.add_argument("--tenant-id", type=int, default=0)
    parser.add_argument("--seller-id", required=True)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--currency", default="kzt")
    parser.add_argument("--destination", default="123585596")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--max-products", type=int, default=0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
