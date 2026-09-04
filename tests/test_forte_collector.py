from __future__ import annotations

import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.forte import forte_collector as forte
from data_service import DataService
from schema import ensure_database
from tests.subscription_fixtures import activate_legacy_subscription


def collector_args(db_path: Path, **changes: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "action": "full-sync",
        "db": str(db_path),
        "tenant_id": 0,
        "seller_name": "Unityre",
        "merchant_id": "unity-mid",
        "city_id": "KZ",
        "category_id": forte.DEFAULT_TIRE_CATEGORY_ID,
        "page_size": 20,
        "workers": 2,
        "max_products": 0,
        "timeout": 10,
        "sleep": 0.0,
        "product_ids": [],
        "source_url": "",
        "seed_product_id": "",
    }
    values.update(changes)
    return argparse.Namespace(**values)


CATALOG_PRODUCT = {
    "uid": "product-1",
    "short_id": "P1",
    "slug": "test-tire-product-1",
    "name": "TestTire Ice 205/55 R16",
    "product_price": 42_000,
    "old_product_price": 45_000,
    "img_url": "/forte/test-tire.jpg",
    "categories_array": ["tires"],
    "category_map": {"1": "Автотовары", "2": "Шины"},
    "aggs_rating": 4.8,
    "reviews_count": 12,
}

DETAIL = {
    "showcase": dict(CATALOG_PRODUCT),
    "characteristics": [
        {"GroupName": "Основное", "Title": "Бренд", "Values": ["TestTire"]},
        {"GroupName": "Размер", "Title": "Ширина", "Values": ["205"]},
    ],
    "nomenclatures_data": [
        {
            "merchant_name": "Unityre",
            "rating": 4.9,
            "reviews_amount": 24,
            "nomenclature": {
                "merchant_id": "unity-mid",
                "price": 42_000,
                "available": True,
                "sale_channels": ["DELIVERY"],
            },
        },
        {
            "merchant_name": "Конкурент",
            "rating": 4.6,
            "reviews_amount": 8,
            "nomenclature": {
                "merchant_id": "competitor-mid",
                "price": 40_500,
                "available": True,
                "sale_channels": ["DELIVERY", "PICKUP"],
            },
        },
        {
            "merchant_name": "Конкурент",
            "rating": 4.6,
            "reviews_amount": 8,
            "nomenclature": {
                "merchant_id": "competitor-mid",
                "price": 41_000,
                "available": True,
                "sale_channels": ["PICKUP"],
            },
        },
    ],
}


class ForteCollectorTests(unittest.TestCase):
    @patch.object(forte, "request_json", return_value={"total_hits": 0, "products": []})
    def test_seller_catalog_does_not_inherit_global_category(self, request: object) -> None:
        args = collector_args(Path("unused.db"), merchant_id="electronics-seller")
        forte.get_catalog_page(args, 0)
        payload = request.call_args.kwargs["payload"]
        self.assertNotIn("category", payload)
        self.assertEqual("KZ", payload["city"])

    def test_extract_offers_marks_own_and_keeps_lowest_duplicate(self) -> None:
        offers = forte.extract_offers(DETAIL, collector_args(Path("unused.db")))

        self.assertEqual(2, len(offers))
        self.assertEqual("Unityre", offers[0]["merchant_name"])
        self.assertEqual(1, offers[0]["is_own"])
        competitor = next(item for item in offers if item["merchant_id"] == "competitor-mid")
        self.assertEqual(40_500, competitor["price_kzt"])
        self.assertEqual("AVAILABLE", competitor["availability_status"])

    def test_storage_history_and_data_service_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forte_unit_") as folder:
            db_path = Path(folder) / "market.db"
            ensure_database(db_path)
            args = collector_args(db_path)
            stamp = "2026-08-07T10:00:00+05:00"
            conn = forte.connect(db_path)
            try:
                product_id = forte.upsert_product(conn, CATALOG_PRODUCT, args, stamp, detail=DETAIL)
                offers = forte.extract_offers(DETAIL, args)
                forte.save_offers(conn, "test-run", product_id, offers, stamp)
                conn.commit()
                product = conn.execute(
                    "SELECT product_id,brand,price_kzt,merchant_id FROM forte_products WHERE product_id=?",
                    (product_id,),
                ).fetchone()
                offer_count = conn.execute(
                    "SELECT COUNT(*) FROM forte_offers WHERE product_id=? AND active=1", (product_id,)
                ).fetchone()[0]
                history_count = conn.execute(
                    "SELECT COUNT(*) FROM forte_price_history WHERE product_id=?", (product_id,)
                ).fetchone()[0]
                tenant_id = int(conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()[0])
                activate_legacy_subscription(db_path, tenant_id)
                args.tenant_id = tenant_id
                saved = forte.materialize_tenant_catalog(conn, db_path, args)
                snapshot = conn.execute(
                    """SELECT title,brand,price_amount,attributes_json
                       FROM tenant_catalog_products
                       WHERE tenant_id=? AND marketplace_code='forte_market'
                         AND source_product_code=?""",
                    (tenant_id, product_id),
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual("TestTire", product["brand"])
            self.assertEqual(42_000, product["price_kzt"])
            self.assertEqual("unity-mid", product["merchant_id"])
            self.assertEqual(2, offer_count)
            self.assertEqual(2, history_count)
            self.assertEqual(1, saved)
            self.assertEqual("TestTire", snapshot["brand"])
            self.assertEqual(42_000, snapshot["price_amount"])

            service = DataService(
                db_path,
                "Unityre",
                Path(folder) / "missing-ozon.db",
                forte_seller_name="Unityre",
            )
            row = next(item for item in service.rows(ttl_seconds=0) if item["product_code"] == "forte:product-1")
            self.assertEqual("forte_market", row["platform"])
            self.assertEqual(42_000, row["own_price_kzt"])
            self.assertEqual(1, row["competitor_seller_count"])
            detail = service.product("forte:product-1")
            self.assertIsNotNone(detail)
            self.assertEqual(2, len(detail["offers"]))
            self.assertEqual(2, len(detail["history"]))

    @patch.object(forte, "get_product_detail", return_value=DETAIL)
    @patch.object(
        forte,
        "get_catalog_page",
        return_value={"total_hits": 0, "products": []},
    )
    def test_empty_seller_catalog_uses_seed_product_diagnostic(
        self, _catalog: object, _detail: object
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="forte_seed_") as folder:
            db_path = Path(folder) / "market.db"
            ensure_database(db_path)
            args = collector_args(db_path, seed_product_id="product-1")
            conn = forte.connect(db_path)
            try:
                result = forte.sync_catalog(conn, args, "seed-run")
                saved = conn.execute(
                    "SELECT COUNT(*) FROM forte_products WHERE merchant_id='unity-mid'"
                ).fetchone()[0]
            finally:
                conn.close()
        self.assertEqual((1, 1, 2), result)
        self.assertEqual(1, saved)

    @patch.object(forte, "get_catalog_page")
    def test_catalog_pagination_counts_unique_products(self, catalog: object) -> None:
        product_two = {**CATALOG_PRODUCT, "uid": "product-2", "slug": "product-2"}
        product_three = {**CATALOG_PRODUCT, "uid": "product-3", "slug": "product-3"}
        catalog.side_effect = [
            {"total_hits": 3, "products": [CATALOG_PRODUCT, product_two]},
            {"total_hits": 3, "products": [CATALOG_PRODUCT, product_three]},
        ]
        with tempfile.TemporaryDirectory(prefix="forte_pages_") as folder:
            db_path = Path(folder) / "market.db"
            ensure_database(db_path)
            args = collector_args(db_path, page_size=2)
            conn = forte.connect(db_path)
            try:
                result = forte.sync_catalog(conn, args, "page-run")
                saved = conn.execute(
                    "SELECT COUNT(*) FROM forte_products WHERE active=1 AND merchant_id='unity-mid'"
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual((3, 3, 0), result)
        self.assertEqual(3, saved)
        self.assertEqual([0, 2], [call.args[1] for call in catalog.call_args_list])

    @patch.object(forte, "merchant_leaf_categories", return_value=[])
    @patch.object(forte, "catalog_scope_pages")
    def test_unstable_catalog_recovers_missing_products_with_price_order(
        self, scopes: object, _categories: object
    ) -> None:
        product_two = {**CATALOG_PRODUCT, "uid": "product-2", "slug": "product-2"}
        product_three = {**CATALOG_PRODUCT, "uid": "product-3", "slug": "product-3"}

        def pages(_args: object, **kwargs: object) -> tuple[int, list[dict[str, object]]]:
            products = {
                "rating": [CATALOG_PRODUCT],
                "new": [CATALOG_PRODUCT],
                "price_desc": [CATALOG_PRODUCT],
                "popularity": [CATALOG_PRODUCT],
                "price_asc": [product_two, product_three],
            }[str(kwargs.get("sort_order"))]
            return 3, [{"total_hits": 3, "products": products}]

        scopes.side_effect = pages
        with tempfile.TemporaryDirectory(prefix="forte_recovery_") as folder:
            db_path = Path(folder) / "market.db"
            ensure_database(db_path)
            args = collector_args(db_path)
            conn = forte.connect(db_path)
            try:
                result = forte.sync_catalog(conn, args, "recovery-run")
            finally:
                conn.close()

        self.assertEqual((3, 3, 0), result)
        self.assertEqual(
            ["rating", "new", "price_desc", "popularity", "price_asc"],
            [call.kwargs.get("sort_order") for call in scopes.call_args_list],
        )

    @patch.object(forte, "get_product_detail", return_value=DETAIL)
    @patch.object(
        forte,
        "get_catalog_page",
        return_value={"total_hits": 1, "products": [CATALOG_PRODUCT]},
    )
    def test_probe_checks_catalog_and_exact_offers(self, _catalog: object, _detail: object) -> None:
        result = forte.probe(collector_args(Path("unused.db"), merchant_id=""))

        self.assertTrue(result["ok"])
        self.assertEqual("product-1", result["sample_product_id"])
        self.assertEqual(2, result["sample_offers"])
        self.assertEqual(1, result["sample_products_checked"])


if __name__ == "__main__":
    unittest.main()
