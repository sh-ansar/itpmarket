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


if __name__ == "__main__":
    unittest.main()
