from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auth_service import AuthService
from data_service import DataService
from schema import ensure_database


class ProductSqlPaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="spyon_product_page_")
        self.db_path = Path(self.tmp.name) / "app.db"
        ensure_database(self.db_path)
        admin, _ = AuthService(self.db_path).create_initial_admin(
            "paging@example.test", "Paging", "StrongPassword123!"
        )
        self.user_id = int(admin["id"])
        self.tenant_id = int(admin["tenant_id"])
        stamp = "2026-09-01T12:00:00+00:00"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executemany(
                """INSERT INTO tenant_catalog_products(
                       tenant_id,marketplace_code,source_product_code,title,brand,
                       price_amount,currency,active,first_seen_at,last_seen_at,source_updated_at
                   ) VALUES(?,?,?,?,'Brand',?,'KZT',1,?,?,?)""",
                [
                    (self.tenant_id, "kaspi", f"SKU-{number:04d}", f"Product {number:04d}", number, stamp, stamp, stamp)
                    for number in range(3005)
                ],
            )
            conn.commit()
        finally:
            conn.close()
        self.data = DataService(self.db_path, "Paging")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_page_is_sql_limited_without_materializing_3000_rows(self) -> None:
        with patch.object(
            self.data, "rows_for_user", side_effect=AssertionError("full catalogue must not be enriched")
        ):
            result = self.data.products(2, 30, {"sort": "title", "direction": "asc"}, self.user_id)
        self.assertEqual("sql_projection", result["lookup_strategy"])
        self.assertEqual(3005, result["total"])
        self.assertEqual(30, len(result["items"]))
        self.assertEqual("Product 0030", result["items"][0]["title"])

    def test_targeted_drawer_does_not_use_legacy_catalog_for_projected_marketplace(self) -> None:
        with patch.object(
            self.data, "product", wraps=self.data.product
        ) as product:
            item = self.data.targeted_product("SKU-0001", self.user_id)
        self.assertEqual("targeted", item["lookup_strategy"])
        self.assertEqual(1, product.call_count)
        self.assertEqual([{"product_code": "SKU-0001"}], [
            {"product_code": call.kwargs["rows"][0]["product_code"]}
            for call in product.call_args_list
        ])

    def test_kaspi_targeted_detail_matches_list_analytics_and_isolates_seller(self) -> None:
        stamp = "2026-09-03T12:00:00+00:00"
        conn = sqlite3.connect(self.db_path)
        try:
            seller_a = int(conn.execute(
                """INSERT INTO tenant_marketplace_sellers(
                       tenant_id,marketplace_code,external_seller_id,display_name,
                       source_url,status,approval_status,created_at,updated_at
                   ) VALUES(?,'kaspi','seller-a','Seller A','https://kaspi.kz/a',
                            'active','approved',?,?)""",
                (self.tenant_id, stamp, stamp),
            ).lastrowid)
            seller_b = int(conn.execute(
                """INSERT INTO tenant_marketplace_sellers(
                       tenant_id,marketplace_code,external_seller_id,display_name,
                       source_url,status,approval_status,created_at,updated_at
                   ) VALUES(?,'kaspi','seller-b','Seller B','https://kaspi.kz/b',
                            'active','approved',?,?)""",
                (self.tenant_id, stamp, stamp),
            ).lastrowid)
            conn.executemany(
                """INSERT INTO tenant_seller_catalog_products(
                       tenant_id,marketplace_code,tenant_seller_id,
                       source_product_code,title,brand,source_url,price_amount,
                       currency,active,first_seen_at,last_seen_at,source_updated_at
                   ) VALUES(?,'kaspi',?,'120426914','Kaspi product','Brand',
                            'https://kaspi.kz/shop/p/120426914',?,'KZT',1,?,?,?)""",
                [
                    (self.tenant_id, seller_a, 32_000, stamp, stamp, stamp),
                    (self.tenant_id, seller_b, 999_000, stamp, stamp, stamp),
                ],
            )
            conn.executemany(
                """INSERT INTO tenant_seller_offer_scans(
                       tenant_id,marketplace_code,tenant_seller_id,
                       source_product_code,status,offers_count,competitor_count,
                       min_price,max_price,duration_seconds,error,checked_at
                   ) VALUES(?,'kaspi',?,'120426914','ok',?,?,?,?,0.1,'',?)""",
                [
                    (self.tenant_id, seller_a, 5, 4, 29_600, 32_000, stamp),
                    (self.tenant_id, seller_b, 3, 2, 1, 999_000, stamp),
                ],
            )
            offers = [
                ("own-a", "Seller A", 32_000, 1),
                ("competitor-a", "Competitor A", 29_600, 0),
                ("competitor-b", "Competitor B", 29_600, 0),
                ("competitor-c", "Competitor C", 29_900, 0),
                ("competitor-d", "Competitor D", 32_000, 0),
            ]
            other_seller_offers = [
                ("own-b", "Seller B", 999_000, 1),
                ("other-a", "Other A", 1, 0),
                ("other-b", "Other B", 2, 0),
            ]
            conn.executemany(
                """INSERT INTO tenant_seller_offer_snapshots(
                       run_id,tenant_id,marketplace_code,tenant_seller_id,
                       source_product_code,merchant_id,merchant_name,
                       price_amount,currency,is_own,captured_at
                   ) VALUES('run-a',?,'kaspi',?,'120426914',?,?,?,'KZT',?,?)""",
                [
                    (self.tenant_id, seller_a, merchant_id, merchant_name, price, is_own, stamp)
                    for merchant_id, merchant_name, price, is_own in offers
                ],
            )
            conn.executemany(
                """INSERT INTO tenant_seller_offer_snapshots(
                       run_id,tenant_id,marketplace_code,tenant_seller_id,
                       source_product_code,merchant_id,merchant_name,
                       price_amount,currency,is_own,captured_at
                   ) VALUES('run-b',?,'kaspi',?,'120426914',?,?,?,'KZT',?,?)""",
                [
                    (self.tenant_id, seller_b, merchant_id, merchant_name, price, is_own, stamp)
                    for merchant_id, merchant_name, price, is_own in other_seller_offers
                ],
            )
            conn.commit()
        finally:
            conn.close()

        item = self.data.targeted_product(
            f"kaspi:s{seller_a}:120426914",
            self.user_id,
        )

        list_analytics = {
            "reference_count": 4,
            "market_min_price_kzt": 29_600,
            "market_median_price_kzt": 29_750,
            "market_max_price_kzt": 32_000,
            "price_status": "EXACT_TIED_HIGHEST",
        }
        self.assertIsNotNone(item)
        for key, value in list_analytics.items():
            self.assertEqual(value, item[key])
        self.assertEqual(5, len(item["offers"]))
        self.assertEqual(4, len(item["candidates"]))
        self.assertEqual(
            {seller_a},
            {int(offer["tenant_seller_id"]) for offer in item["offers"]},
        )

    def test_targeted_drawer_reports_targeted_for_every_marketplace_family(self) -> None:
        stamp = "2026-09-01T12:00:00+00:00"
        families = {
            "kaspi": "target-kaspi", "ozon": "target-ozon", "ozon_kz": "target-ozon-kz",
            "halyk_market": "target-halyk", "forte_market": "target-forte", "wildberries": "target-wb",
        }
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executemany(
                """INSERT INTO tenant_catalog_products(
                       tenant_id,marketplace_code,source_product_code,title,active,first_seen_at,last_seen_at
                   ) VALUES(?,?,?,?,1,?,?)""",
                [(self.tenant_id, family, source, family, stamp, stamp) for family, source in families.items()],
            )
            conn.commit()
        finally:
            conn.close()
        prefixes = {"kaspi": "", "ozon": "ozon:", "ozon_kz": "ozon_kz:", "halyk_market": "halyk:", "forte_market": "forte:", "wildberries": "wb:"}
        with patch.object(self.data, "rows_for_user", side_effect=AssertionError("legacy catalogue must not load")):
            for family, source in families.items():
                item = self.data.targeted_product(prefixes[family] + source, self.user_id)
                self.assertEqual("targeted", item["lookup_strategy"], family)

    def test_targeted_ozon_drawers_overlay_market_snapshot_but_keep_own_price(self) -> None:
        stamp = "2026-09-04T12:00:00+00:00"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executemany(
                """INSERT INTO tenant_catalog_products(
                       tenant_id,marketplace_code,source_product_code,title,
                       price_amount,currency,active,first_seen_at,last_seen_at
                   ) VALUES(?,?,?,?,?,?,1,?,?)""",
                [
                    (self.tenant_id, "ozon", "ru-overlay", "RU", 100, "RUB", stamp, stamp),
                    (self.tenant_id, "ozon_kz", "kz-overlay", "KZ", 42000, "KZT", stamp, stamp),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        ru_analytics = {
            "source_product_code": "ru-overlay",
            "market_median_price_original": 200,
            "market_min_price_original": 180,
            "market_max_price_original": 220,
            "price_status": "EXACT_BELOW",
            "exact_candidates": [
                {"merchant_name": "RU competitor", "price_rub": 200}
            ],
            "comparable_candidates": [],
            "candidate_count": 1,
            "exact_candidate_count": 1,
            "comparable_candidate_count": 0,
            "market_run_id": "ru-market",
        }
        kz_analytics = {
            "source_product_code": "kz-overlay",
            "market_price_kzt": 40000,
            "market_median_price_kzt": 40000,
            "market_min_price_kzt": 39000,
            "market_max_price_kzt": 41000,
            "price_status": "EXACT_ABOVE",
            "exact_candidates": [
                {"merchant_name": "KZ competitor", "price_kzt": 40000}
            ],
            "comparable_candidates": [],
            "candidate_count": 1,
            "exact_candidate_count": 1,
            "comparable_candidate_count": 0,
            "market_run_id": "kz-market",
        }
        with patch.object(
            self.data, "_ozon_rows", return_value=[ru_analytics]
        ), patch.object(
            self.data, "_ozon_kz_rows", return_value=[kz_analytics]
        ), patch.object(self.data, "price_history", return_value=[]):
            ru = self.data.targeted_product("ozon:ru-overlay", self.user_id)
            kz = self.data.targeted_product("ozon_kz:kz-overlay", self.user_id)

        self.assertEqual(100, ru["price_original"])
        self.assertEqual(550, ru["price_kzt"])
        self.assertEqual(1100, ru["market_median_price_kzt"])
        self.assertEqual(1100, ru["offers"][0]["price_kzt"])
        self.assertEqual("ru-market", ru["market_run_id"])
        self.assertEqual(42000, kz["price_kzt"])
        self.assertEqual(40000, kz["market_median_price_kzt"])
        self.assertEqual("KZ competitor", kz["offers"][0]["merchant_name"])
        self.assertEqual("kz-market", kz["market_run_id"])


if __name__ == "__main__":
    unittest.main()
