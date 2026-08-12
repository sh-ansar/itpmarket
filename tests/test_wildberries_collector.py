from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from auth_service import AuthService
from catalog_configuration_service import CatalogConfigurationService
from collectors.wildberries import wildberries_collector as collector
from schema import ensure_database


def wb_product(product_id: int, *, price: int = 12_345_600) -> dict:
    return {
        "id": product_id,
        "root": product_id - 1,
        "brand": "Test Brand",
        "name": "Test Product",
        "supplier": "Test Seller",
        "supplierId": 250000260,
        "subjectId": 515,
        "totalQuantity": 7,
        "reviewRating": 4.8,
        "feedbacks": 21,
        "sizes": [{"price": {"product": price, "basic": price + 100_000}}],
    }


def args(**overrides):
    values = {
        "action": "sync-catalog",
        "db": "",
        "tenant_id": 1,
        "seller_id": "250000260",
        "source_url": "https://global.wildberries.ru/seller/250000260",
        "currency": "kzt",
        "destination": "123585596",
        "timeout": 10,
        "retries": 2,
        "sleep": 0,
        "max_products": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class WildberriesCollectorTests(unittest.TestCase):
    def test_product_mapping_uses_requested_currency_and_hundredths(self) -> None:
        row = collector.product_row(wb_product(544795769), "250000260", "kzt")
        self.assertEqual("544795769", row["product_id"])
        self.assertEqual(123_456, row["price"])
        self.assertEqual("KZT", row["currency"])
        self.assertEqual("in_stock", row["availability"])
        self.assertIn("/catalog/544795769/detail.aspx", row["url"])
        self.assertEqual(
            "https://basket-28.wbbasket.ru/vol5447/part544795/544795769/images/c246x328/1.webp",
            row["image_url"],
        )
        self.assertEqual("Test Seller", row["metadata"]["seller_name"])

    def test_current_wb_basket_paths_are_generated_for_real_article_ranges(self) -> None:
        self.assertEqual(29, collector._basket_number(568_549_112))
        self.assertEqual(32, collector._basket_number(647_921_893))
        self.assertEqual(41, collector._basket_number(993_570_413))
        self.assertEqual(45, collector._basket_number(1_304_118_091))

    @patch.object(collector, "catalog_page")
    def test_collect_reads_every_reported_product_without_duplicates(self, page) -> None:
        page.return_value = {
            "total": 2,
            "products": [wb_product(1001), wb_product(1002)],
        }
        total, products, seller_name = collector.collect(args())
        self.assertEqual(2, total)
        self.assertEqual({"1001", "1002"}, {item["product_id"] for item in products})
        self.assertEqual("Test Seller", seller_name)
        page.assert_called_once()

    @patch.object(collector, "catalog_page")
    def test_probe_is_fast_and_fetches_only_first_page(self, page) -> None:
        page.return_value = {"total": 4124, "products": [wb_product(544795769)]}
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = collector.run(args(action="probe"))
        self.assertEqual(0, exit_code)
        payload = json.loads(output.getvalue())
        self.assertEqual(4124, payload["total"])
        self.assertEqual("544795769", payload["sample_product_id"])
        page.assert_called_once()

    def test_persist_replaces_only_requested_company_catalog(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wildberries_catalog_") as folder:
            db_path = Path(folder) / "app.db"
            ensure_database(db_path)
            auth = AuthService(db_path)
            admin, _ = auth.create_initial_admin(
                "root@example.com", "Root Admin", "StrongPassword123!"
            )
            tenant_a = int(admin["tenant_id"])
            conn = sqlite3.connect(db_path)
            stamp = "2026-08-11T12:00:00+05:00"
            tenant_b = int(conn.execute(
                """INSERT INTO tenants(
                       name,slug,registration_number,status,plan_code,
                       contact_email,contact_phone,created_at,updated_at,approved_at
                   ) VALUES('Company B','company-b','BIN-B','approved','demo',
                            'b@example.com','+7 700 000 00 02',?,?,?)""",
                (stamp, stamp, stamp),
            ).lastrowid)
            conn.commit()
            conn.close()
            ensure_database(db_path)
            service = CatalogConfigurationService(db_path)
            service.replace_catalog_products(
                tenant_b, "wildberries",
                [collector.product_row(wb_product(9001), "900", "kzt")],
            )

            saved = collector.persist(
                args(db=str(db_path), tenant_id=tenant_a),
                [collector.product_row(wb_product(1001), "250000260", "kzt")],
                "Test Seller",
            )
            self.assertEqual(1, saved)
            self.assertEqual(
                {("wildberries", "1001")},
                service.catalog_memberships(tenant_a, ["wildberries"]),
            )
            self.assertEqual(
                {("wildberries", "9001")},
                service.catalog_memberships(tenant_b, ["wildberries"]),
            )


if __name__ == "__main__":
    unittest.main()
