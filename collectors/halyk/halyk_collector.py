#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import ensure_database

BASE_URL = "https://halykmarket.kz"
SHOP_ID = "693ff081028570920fd8a6b971eb5e"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def merchant_key(value: Any) -> str:
    text = clean_text(value).casefold()
    key = re.sub(r"[^a-zа-я0-9]+", "-", text).strip("-")
    return key or "merchant"


def same_seller(left: Any, right: Any) -> bool:
    return merchant_key(left) == merchant_key(right)


def as_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def first_scalar(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return ""
        return clean_text(value[0])
    return clean_text(value)


def normalize_param_name(value: Any) -> str:
    name = clean_text(value)
    if "_" in name:
        prefix, rest = name.split("_", 1)
        if prefix.isdigit():
            return clean_text(rest)
    return name


def normalize_specs(params: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    def add(name: Any, value: Any) -> None:
        spec_name = normalize_param_name(name)
        if not spec_name or spec_name.startswith("-1_") or spec_name.startswith("LOAN_"):
            return
        if spec_name in {"merchantName", "HAS_HIGH_RATING", "HIGH_RATING"}:
            return
        if isinstance(value, list):
            value = ", ".join(clean_text(item) for item in value if clean_text(item))
        text = clean_text(value)
        if text:
            rows.append({"section": "Halyk Market", "name": spec_name, "value": text})

    if isinstance(params, dict):
        for key, value in params.items():
            add(key, value)
    elif isinstance(params, list):
        for item in params:
            if not isinstance(item, dict):
                continue
            add(
                item.get("name") or item.get("key") or item.get("title"),
                item.get("value") or item.get("values") or item.get("text"),
            )
    return rows


def product_url(value: dict[str, Any]) -> str:
    raw = clean_text(value.get("url") or value.get("canonical_url") or "")
    if raw:
        if raw.startswith("/category/"):
            return urljoin(BASE_URL, raw)
        if raw.startswith("/"):
            return urljoin(BASE_URL, f"/category{raw}")
        return urljoin(BASE_URL, raw)
    product_id = clean_text(value.get("id") or value.get("_id"))
    return f"{BASE_URL}/search?{urlencode({'query': product_id})}" if product_id else ""


def image_url(value: dict[str, Any]) -> str:
    raw = clean_text(value.get("image_url") or value.get("picture") or "")
    return urljoin(BASE_URL, raw) if raw else ""


def halyk_get_json_once(path: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    url = f"{BASE_URL}{path}?{urlencode(params, doseq=True)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": BASE_URL,
    }
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except Exception as urllib_error:
        command = [
            "curl.exe",
            "-L",
            "-sS",
            "--retry",
            "2",
            "--retry-delay",
            "2",
            "--retry-connrefused",
            "--connect-timeout",
            "10",
            "--max-time",
            str(max(5, int(timeout))),
        ]
        for key, value in headers.items():
            command += ["-H", f"{key}: {value}"]
        command.append(url)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as curl_error:
            raise RuntimeError(f"Halyk Market недоступен: {urllib_error}") from curl_error
        if completed.returncode != 0:
            message = clean_text(completed.stderr or completed.stdout or urllib_error)
            raise RuntimeError(f"Halyk Market недоступен: {message}")
        raw = completed.stdout
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("Halyk Market вернул неожиданный формат ответа.")
    return value


def halyk_get_json(path: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return halyk_get_json_once(path, params, timeout)
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                print(f"Halyk сеть: попытка {attempt}/3 не прошла, повторяем.", flush=True)
                time.sleep(min(5, attempt * 2))
    raise RuntimeError(f"Halyk Market недоступен после 3 попыток: {last_error}")


def base_params(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "did": args.device_id,
        "shop_id": args.shop_id,
        "sid": "",
        "locations": args.location_id,
        "extended": "true",
        "filters_search_by": "popularity",
        "brand_limit": "5000",
        "category_limit": "500",
    }


def catalog_params(args: argparse.Namespace, page: int) -> dict[str, Any]:
    return {
        "type": "full_search",
        "search_query": args.catalog_query,
        "locations": args.location_id,
        "extended": "true",
        "merchants": args.seller_name,
        "limit": str(args.page_size),
        "page": str(page),
        "sort_by": "popular",
    }


def search_params(args: argparse.Namespace, product_id: str) -> dict[str, Any]:
    return {
        **base_params(args),
        "seance": "",
        "type": "full_search",
        "search_query": product_id,
        "page": "1",
        "limit": "20",
        "sort_by": "popular",
        "order": "desc",
        "price_max": "",
    }


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def mark_run(conn: sqlite3.Connection, run_id: str, action: str, args: argparse.Namespace, status: str) -> None:
    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO halyk_sync_runs(run_id,action,status,seller_name,location_id,started_at)
        VALUES(?,?,?,?,?,?)
        """,
        (run_id, action, status, args.seller_name, args.location_id, stamp),
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
        UPDATE halyk_sync_runs
        SET status=?,total_reported=?,products_seen=?,offers_seen=?,error=?,finished_at=?
        WHERE run_id=?
        """,
        (status, int(total_reported), int(products_seen), int(offers_seen), clean_text(error), now_iso(), run_id),
    )
    conn.commit()


def upsert_product(conn: sqlite3.Connection, product: dict[str, Any], seller_name: str, stamp: str) -> None:
    product_id = clean_text(product.get("id") or product.get("_id"))
    if not product_id:
        return
    specs = normalize_specs(product.get("params"))
    conn.execute(
        """
        INSERT INTO halyk_products(
            product_id,name,brand,product_url,image_url,price_kzt,price_full_kzt,currency,
            category_ids_json,categories_json,specs_json,params_json,raw_json,seller_name,
            active,first_seen_at,last_seen_at,last_catalog_at,last_error
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,'')
        ON CONFLICT(product_id) DO UPDATE SET
            name=excluded.name,
            brand=excluded.brand,
            product_url=excluded.product_url,
            image_url=excluded.image_url,
            price_kzt=excluded.price_kzt,
            price_full_kzt=excluded.price_full_kzt,
            currency=excluded.currency,
            category_ids_json=excluded.category_ids_json,
            categories_json=excluded.categories_json,
            specs_json=excluded.specs_json,
            params_json=excluded.params_json,
            raw_json=excluded.raw_json,
            seller_name=excluded.seller_name,
            active=1,
            last_seen_at=excluded.last_seen_at,
            last_catalog_at=excluded.last_catalog_at,
            last_error=''
        """,
        (
            product_id,
            clean_text(product.get("name")),
            clean_text(product.get("brand")),
            product_url(product),
            image_url(product),
            as_number(product.get("price")),
            as_number(product.get("price_full")),
            clean_text(product.get("currency") or "KZT"),
            json.dumps(product.get("category_ids") or [], ensure_ascii=False),
            json.dumps(product.get("categories") or [], ensure_ascii=False),
            json.dumps(specs, ensure_ascii=False),
            json.dumps(product.get("params") or {}, ensure_ascii=False),
            json.dumps(product, ensure_ascii=False),
            seller_name,
            stamp,
            stamp,
            stamp,
        ),
    )


def extract_offers(product: dict[str, Any], expected_seller: str) -> list[dict[str, Any]]:
    offers = ((product.get("city_offer") or {}).get("merchant_offers") or [])
    result: dict[str, dict[str, Any]] = {}
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        name = clean_text(offer.get("merchantName") or offer.get("merchant_name"))
        if not name:
            continue
        price = as_number(offer.get("price") or offer.get("amount"))
        key = merchant_key(name)
        current = result.get(key)
        if current is None or (price is not None and (current.get("price_kzt") is None or price < current["price_kzt"])):
            result[key] = {
                "merchant_key": key,
                "merchant_name": name,
                "price_kzt": price,
                "offer_type": clean_text(offer.get("type")),
                "is_own": 1 if same_seller(name, expected_seller) else 0,
                "raw_json": json.dumps(offer, ensure_ascii=False),
            }
    return sorted(result.values(), key=lambda row: (row.get("price_kzt") is None, row.get("price_kzt") or 0, row["merchant_name"]))


def save_offers(conn: sqlite3.Connection, run_id: str, product_id: str, offers: list[dict[str, Any]], stamp: str) -> int:
    seen = {row["merchant_key"] for row in offers}
    conn.execute(
        "UPDATE halyk_offers SET active=0 WHERE product_id=? AND merchant_key NOT IN (%s)" %
        ",".join("?" for _ in seen),
        [product_id, *seen],
    ) if seen else conn.execute("UPDATE halyk_offers SET active=0 WHERE product_id=?", (product_id,))
    for row in offers:
        conn.execute(
            """
            INSERT INTO halyk_offers(
                product_id,merchant_key,merchant_name,price_kzt,offer_type,is_own,active,
                first_seen_at,last_seen_at,last_checked_at,raw_json
            ) VALUES(?,?,?,?,?,?,1,?,?,?,?)
            ON CONFLICT(product_id,merchant_key) DO UPDATE SET
                merchant_name=excluded.merchant_name,
                price_kzt=excluded.price_kzt,
                offer_type=excluded.offer_type,
                is_own=excluded.is_own,
                active=1,
                last_seen_at=excluded.last_seen_at,
                last_checked_at=excluded.last_checked_at,
                raw_json=excluded.raw_json
            """,
            (
                product_id,
                row["merchant_key"],
                row["merchant_name"],
                row.get("price_kzt"),
                row.get("offer_type") or "",
                int(row.get("is_own") or 0),
                stamp,
                stamp,
                stamp,
                row.get("raw_json") or "{}",
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO halyk_price_history(
                run_id,product_id,merchant_key,merchant_name,price_kzt,is_own,captured_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                run_id,
                product_id,
                row["merchant_key"],
                row["merchant_name"],
                row.get("price_kzt"),
                int(row.get("is_own") or 0),
                stamp,
            ),
        )
    return len(offers)


def sync_catalog(conn: sqlite3.Connection, args: argparse.Namespace, run_id: str) -> tuple[int, int, int]:
    page = 0
    loaded = 0
    total_reported = 0
    offers_seen = 0
    stamp = now_iso()
    conn.execute("UPDATE halyk_products SET active=0")
    while True:
        data = halyk_get_json("/search-api/search", catalog_params(args, page), args.timeout)
        products = data.get("products") if isinstance(data.get("products"), list) else []
        total_reported = int(data.get("products_total") or total_reported or len(products))
        if not products:
            break
        with conn:
            for product in products:
                if not isinstance(product, dict):
                    continue
                product_id = clean_text(product.get("id") or product.get("_id"))
                if not product_id:
                    continue
                upsert_product(conn, product, args.seller_name, stamp)
                offers_seen += save_offers(conn, run_id, product_id, extract_offers(product, args.seller_name), stamp)
                loaded += 1
                if args.max_products and loaded >= args.max_products:
                    break
        print(f"Halyk каталог: {loaded}/{total_reported}", flush=True)
        if args.max_products and loaded >= args.max_products:
            break
        if loaded >= total_reported:
            break
        page += 1
        time.sleep(max(0.0, args.sleep))
    return total_reported, loaded, offers_seen


def product_ids_for_market(conn: sqlite3.Connection, args: argparse.Namespace) -> list[str]:
    if args.product_ids:
        values = [clean_text(item) for chunk in args.product_ids for item in chunk.split(",")]
        return [item.removeprefix("halyk:") for item in values if item]
    rows = conn.execute(
        "SELECT product_id FROM halyk_products WHERE active=1 ORDER BY last_seen_at DESC"
    ).fetchall()
    ids = [str(row["product_id"]) for row in rows]
    if args.max_products:
        ids = ids[: int(args.max_products)]
    return ids


def refresh_market_offers(conn: sqlite3.Connection, args: argparse.Namespace, run_id: str) -> tuple[int, int]:
    product_ids = product_ids_for_market(conn, args)
    offers_seen = 0
    stamp = now_iso()
    for index, product_id in enumerate(product_ids, 1):
        try:
            data = halyk_get_json("/search-api/search", search_params(args, product_id), args.timeout)
            products = data.get("products") if isinstance(data.get("products"), list) else []
            product = next((item for item in products if clean_text(item.get("id") or item.get("_id")) == product_id), None)
            if not product and len(products) == 1:
                product = products[0]
            if not isinstance(product, dict):
                conn.execute(
                    "UPDATE halyk_products SET last_market_at=?,last_error=? WHERE product_id=?",
                    (stamp, "Карточка не найдена в поиске Halyk", product_id),
                )
                conn.commit()
                print(f"Halyk предложения: {index}/{len(product_ids)}", flush=True)
                continue
            with conn:
                upsert_product(conn, product, args.seller_name, stamp)
                offers_seen += save_offers(conn, run_id, product_id, extract_offers(product, args.seller_name), stamp)
                conn.execute(
                    "UPDATE halyk_products SET last_market_at=?,last_error='' WHERE product_id=?",
                    (stamp, product_id),
                )
        except Exception as exc:
            conn.execute(
                "UPDATE halyk_products SET last_error=? WHERE product_id=?",
                (clean_text(exc), product_id),
            )
            conn.commit()
        print(f"Halyk предложения: {index}/{len(product_ids)}", flush=True)
        time.sleep(max(0.0, args.sleep))
    return len(product_ids), offers_seen


def run(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    ensure_database(db_path)
    conn = connect(db_path)
    run_id = f"halyk_{args.action}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    action = args.action
    mark_run(conn, run_id, action, args, "running")
    total = products = offers = 0
    try:
        print(
            f"Halyk старт: {action}; продавец={args.seller_name}; город={args.location_id}; запрос={args.catalog_query}",
            flush=True,
        )
        if action in {"sync-catalog", "full-sync"}:
            total, products, offers = sync_catalog(conn, args, run_id)
        if action in {"refresh-offers", "full-sync"}:
            refreshed, market_offers = refresh_market_offers(conn, args, run_id)
            products = max(products, refreshed)
            offers += market_offers
        finish_run(conn, run_id, "ok", total, products, offers)
        print(f"Halyk готово: товаров {products}, предложений {offers}", flush=True)
        return 0
    except Exception as exc:
        finish_run(conn, run_id, "error", total, products, offers, str(exc))
        print(f"Halyk ошибка: {exc}", flush=True)
        return 1
    finally:
        conn.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Halyk Market products and exact-card offers.")
    parser.add_argument("action", choices=["sync-catalog", "refresh-offers", "full-sync"])
    parser.add_argument("--db", default=str(ROOT / "data" / "unityre_kaspi.db"))
    parser.add_argument("--seller-name", default="Unityre")
    parser.add_argument("--location-id", default="-2")
    parser.add_argument("--catalog-query", default="shini-i-diski")
    parser.add_argument("--shop-id", default=SHOP_ID)
    parser.add_argument("--device-id", default="spyonCollector")
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--max-products", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--product-ids", action="append", default=[])
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
