#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OZON_RU_ROOT = PROJECT_ROOT / "collectors" / "ozon"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(OZON_RU_ROOT) not in sys.path:
    sys.path.insert(0, str(OZON_RU_ROOT))

from collector_config import Settings, load_settings  # noqa: E402
from ozon_collector import (  # noqa: E402
    Collector,
    materialize_tenant_catalog,
    result_exit_code,
    structured_result,
)
from registry import now_iso  # noqa: E402
from collectors.ozon_kz.storage import connect, ensure_schema  # noqa: E402

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ozon_kz_registry.db"


def seller_root_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    host = str(parsed.hostname or "").casefold()
    if host not in {"ozon.kz", "www.ozon.kz"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].casefold() not in {"seller", "продавец"}:
        return ""
    return urlunparse(("https", "ozon.kz", f"/seller/{parts[1]}/", "", "", ""))


def build_settings(args: argparse.Namespace) -> Settings:
    source_url = seller_root_url(args.source_url)
    if not source_url:
        raise ValueError("Укажите ссылку продавца вида https://ozon.kz/seller/name/.")
    base = load_settings()
    runtime_dir_value = getattr(args, "runtime_dir", "")
    profile_path_value = getattr(args, "profile_path", "")
    runtime_dir = Path(runtime_dir_value).resolve() if runtime_dir_value else ROOT
    profile_path = (
        Path(profile_path_value).resolve()
        if profile_path_value else ROOT / "chrome_kz_profile"
    )
    return replace(
        base,
        start_url=source_url,
        start_urls=(source_url,),
        expected_seller=str(args.expected_seller or "").strip(),
        debug_port=int(args.debug_port),
        browser_profile_path=profile_path,
        catalog_wait_seconds=min(30, base.catalog_wait_seconds),
        request_wait_seconds=min(30, base.request_wait_seconds),
        page_reloads=min(1, base.page_reloads),
        product_reloads=min(1, base.product_reloads),
        database_path=Path(args.db).resolve(),
        runs_dir=runtime_dir / "runs",
        reports_dir=runtime_dir / "reports",
        exports_dir=runtime_dir / "exports",
        raw_dir=runtime_dir / "raw",
    )


def _attributes(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"name": label, "value": value.get(key)}
        for key, label in (
            ("model", "Модель"),
            ("manufacturer_article", "Артикул производителя"),
            ("tire_size", "Размер"),
            ("season", "Сезон"),
        )
        if value.get(key) not in (None, "", "UNKNOWN")
    ]


def normalize_registry_currency(conn: Any) -> dict[str, int]:
    offers = conn.execute(
        "UPDATE offers SET currency='KZT' WHERE currency<>'KZT'"
    ).rowcount
    history = conn.execute(
        "UPDATE price_history SET currency='KZT' WHERE currency<>'KZT'"
    ).rowcount
    return {"offers": int(offers or 0), "price_history": int(history or 0)}


def mirror_public_registry(
    settings: Settings, *, catalog_run_id: str = ""
) -> dict[str, int]:
    ensure_schema(settings.database_path)
    conn = connect(settings.database_path)
    stamp = now_iso()
    try:
        normalize_registry_currency(conn)
        run_clause = " AND ps.last_run_id=?" if str(catalog_run_id).strip() else ""
        product_params: list[Any] = [settings.start_url]
        if run_clause:
            product_params.append(str(catalog_run_id).strip())
        products = conn.execute(
            """SELECT p.* FROM products p
               WHERE p.active=1 AND EXISTS(
                   SELECT 1 FROM product_sources ps
                   WHERE ps.article=p.article AND ps.source_url=?"""
            + run_clause
            + """
               ) ORDER BY p.article""",
            product_params,
        ).fetchall()
        offers = conn.execute(
            "SELECT * FROM offers WHERE active=1 ORDER BY article,last_checked_at DESC"
        ).fetchall()
        offers_by_article: dict[str, list[dict[str, Any]]] = {}
        for raw in offers:
            item = dict(raw)
            offers_by_article.setdefault(str(item["article"]), []).append(item)
        conn.execute("UPDATE ozon_kz_products SET active=0")
        conn.execute("UPDATE ozon_kz_offers SET active=0")
        offer_count = 0
        for raw in products:
            value = dict(raw)
            article = str(value.get("article") or "")
            article_offers = offers_by_article.get(article, [])
            expected = settings.expected_seller.casefold()
            own = next((item for item in article_offers if expected and expected in {
                str(item.get("seller_id") or "").casefold(),
                str(item.get("seller_name") or "").casefold(),
            }), article_offers[0] if article_offers else {})
            conn.execute(
                """INSERT INTO ozon_kz_products(
                       product_id,seller_sku,title,brand,model,specifications_json,
                       canonical_url,image_url,currency,own_price_kzt,availability_status,
                       active,source_payload_json,first_seen_at,last_seen_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(product_id) DO UPDATE SET
                       title=excluded.title,brand=excluded.brand,model=excluded.model,
                       specifications_json=excluded.specifications_json,
                       canonical_url=excluded.canonical_url,image_url=excluded.image_url,
                       own_price_kzt=excluded.own_price_kzt,
                       availability_status=excluded.availability_status,active=1,
                       source_payload_json=excluded.source_payload_json,
                       last_seen_at=excluded.last_seen_at""",
                (
                    article, value.get("manufacturer_article") or "",
                    value.get("title") or article, value.get("brand") or "",
                    value.get("model") or "", json.dumps(_attributes(value), ensure_ascii=False),
                    value.get("canonical_url") or "", value.get("image_url") or "", "KZT",
                    own.get("card_price") or value.get("catalog_price") or None,
                    own.get("availability_status") or "UNKNOWN", 1,
                    json.dumps({"source_url": settings.start_url}, ensure_ascii=False),
                    value.get("first_seen_at") or stamp, value.get("last_seen_at") or stamp,
                ),
            )
            for item in article_offers:
                seller_id = str(item.get("seller_id") or item.get("seller_key") or "unknown")
                is_own = int(item is own)
                conn.execute(
                    """INSERT INTO ozon_kz_offers(
                           product_id,seller_id,seller_name,seller_url,price_kzt,
                           regular_price_kzt,availability_status,is_own,active,captured_at
                       ) VALUES(?,?,?,?,?,?,?,?,1,?)
                       ON CONFLICT(product_id,seller_id) DO UPDATE SET
                           seller_name=excluded.seller_name,seller_url=excluded.seller_url,
                           price_kzt=excluded.price_kzt,regular_price_kzt=excluded.regular_price_kzt,
                           availability_status=excluded.availability_status,is_own=excluded.is_own,
                           active=1,captured_at=excluded.captured_at""",
                    (
                        article, seller_id, item.get("seller_name") or "",
                        item.get("seller_url") or "", item.get("card_price") or None,
                        item.get("regular_price") or None,
                        item.get("availability_status") or "UNKNOWN", is_own,
                        item.get("last_checked_at") or stamp,
                    ),
                )
                conn.execute(
                    """INSERT INTO ozon_kz_price_history(
                           product_id,seller_id,price_kzt,availability_status,captured_at
                       ) VALUES(?,?,?,?,?)""",
                    (
                        article, seller_id, item.get("card_price") or None,
                        item.get("availability_status") or "UNKNOWN",
                        item.get("last_checked_at") or stamp,
                    ),
                )
                offer_count += 1
        conn.execute(
            """UPDATE ozon_kz_connector_metadata
               SET status='connected',source_url=?,source_type='public_storefront',
                   auth_mode='separate_browser_profile',last_success_at=?,last_error='',updated_at=?
               WHERE id=1""",
            (settings.start_url, stamp, stamp),
        )
        conn.commit()
        return {"products": len(products), "offers": offer_count}
    finally:
        conn.close()


def parse_articles(value: str) -> set[str] | None:
    result = {
        item.strip().removeprefix("ozon_kz:")
        for item in str(value or "").split(",") if item.strip()
    }
    return result or None


def require_success(result: dict[str, Any], operation: str) -> dict[str, Any]:
    """Legacy stage gate: partial data is inspectable but never successful."""
    if str(result.get("status") or "").upper() in {"BLOCKED", "FAILED", "INTERRUPTED"}:
        raise RuntimeError(
            f"Ozon.kz {operation} завершён со статусом "
            f"{str(result.get('status') or 'FAILED')}."
        )
    return result


def require_complete(result: dict[str, Any], operation: str) -> dict[str, Any]:
    """A partial operation is diagnostic data, never publication authority."""
    require_success(result, operation)
    if str(result.get("status") or "").upper() not in {"PASSED", "OK", "READY"}:
        raise RuntimeError(
            f"Ozon.kz {operation} завершён со статусом "
            f"{str(result.get('status') or 'PARTIAL')}."
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ozon.kz public storefront collector")
    parser.add_argument("action", choices=("sync-catalog", "refresh-prices", "full-sync"))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--app-db", required=True)
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--tenant-seller-id", type=int, default=0)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--expected-seller", default="")
    parser.add_argument("--debug-port", type=int, default=9333)
    parser.add_argument("--runtime-dir", default="")
    parser.add_argument("--profile-path", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pages", type=int, default=100)
    parser.add_argument("--articles", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = build_settings(args)
    ensure_schema(settings.database_path)
    collector = Collector(settings)
    limit = int(args.limit) or None
    articles = parse_articles(args.articles)
    outcome: dict[str, Any] | None = None
    failure: Exception | None = None
    stages: list[dict[str, Any]] = []
    try:
        catalog: dict[str, Any] | None = None
        if args.action == "full-sync":
            full_sync = collector.full_sync(limit)
            outcome = full_sync
            catalog = full_sync.get("catalog") if isinstance(full_sync, dict) else None
            if isinstance(catalog, dict):
                stages.append(catalog)
            market = full_sync.get("market") if isinstance(full_sync, dict) else None
            if isinstance(market, dict):
                stages.append(market)
        elif args.action == "sync-catalog":
            catalog = collector.sync_catalog(limit)
            outcome = catalog
            stages.append(catalog)
        else:
            refresh = collector.process("refresh-prices", limit, articles)
            outcome = refresh
            stages.append(refresh)
            require_complete(refresh, "refresh-prices")

        mirrored: dict[str, int] = {}
        tenant_count = 0
        if catalog is not None:
            require_complete(catalog, "sync-catalog")
            discovery = catalog.get("discovery") if isinstance(catalog, dict) else {}
            if int((discovery or {}).get("items_total") or 0) <= 0:
                raise RuntimeError(
                    "Ozon.kz не отдал карточки продавца. Проверьте открытую вкладку "
                    "Ozon.kz; если показана проверка доступа, пройдите её и повторите запуск."
                )
            # A completed discovery is the only publication input. Market
            # analysis cannot replace or block that authoritative catalogue.
            catalog_run_id = str((discovery or {}).get("run_id") or "")
            mirrored = mirror_public_registry(settings, catalog_run_id=catalog_run_id)
            tenant_count = materialize_tenant_catalog(
                settings, int(args.tenant_id), str(getattr(args, "app_db", "") or args.db), "ozon_kz",
                tenant_seller_id=int(getattr(args, "tenant_seller_id", 0) or 0) or None,
                catalog_run_id=catalog_run_id,
            )
            collector.registry.mark_catalog_published(catalog_run_id)
        final_status = str((outcome or {}).get("status") or "PASSED").upper()
        outcome = {"status": final_status, "stages": stages}
        print(json.dumps({"ok": final_status == "PASSED", **mirrored, "tenant_products": tenant_count, "status": final_status}, ensure_ascii=False))
        return result_exit_code(outcome)
    except Exception as exc:
        failure = exc
        outcome = outcome or {"status": "FAILED"}
        conn = connect(settings.database_path)
        try:
            conn.execute(
                """UPDATE ozon_kz_connector_metadata
                   SET status='error',source_url=?,last_error=?,updated_at=? WHERE id=1""",
                (settings.start_url, str(exc)[:1000], now_iso()),
            )
            conn.commit()
        finally:
            conn.close()
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        if outcome is not None:
            print("SPYON_RESULT " + json.dumps(structured_result(outcome, failure), ensure_ascii=False))
        collector.close()


if __name__ == "__main__":
    raise SystemExit(main())
