from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from collectors.halyk import halyk_collector as halyk
from data_service import DataService
from schema import ensure_database


class HalykCollectorTests(unittest.TestCase):
    def test_exact_offers_storage_and_rbac_row_shape(self) -> None:
        product = {
            "id": "halyk-1",
            "name": "Test Tire 205/55 R16",
            "brand": "Test",
            "price": 42000,
            "currency": "KZT",
            "url": "/category/test-tire-halyk-1",
            "params": {"Размер": "205/55 R16"},
            "city_offer": {"merchant_offers": [
                {"merchantName": "Unityre", "price": 42000},
                {"merchantName": "Competitor", "price": 40500},
                {"merchantName": "Competitor", "price": 41000},
            ]},
        }
        offers = halyk.extract_offers(product, "Unityre")
        self.assertEqual(2, len(offers))
        self.assertEqual(40500, next(
            row["price_kzt"] for row in offers if row["merchant_name"] == "Competitor"
        ))

        with tempfile.TemporaryDirectory(prefix="halyk_test_") as folder:
            db_path = Path(folder) / "app.db"
            ensure_database(db_path)
            conn = halyk.connect(db_path)
            try:
                stamp = "2026-08-10T10:00:00+05:00"
                halyk.upsert_product(conn, product, "Unityre", stamp)
                halyk.save_offers(conn, "run-1", "halyk-1", offers, stamp)
                conn.execute(
                    "UPDATE halyk_products SET last_market_at=? WHERE product_id=?",
                    (stamp, "halyk-1"),
                )
                conn.commit()
                tenant_id = int(conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()[0])
                saved = halyk.materialize_tenant_catalog(
                    conn, db_path,
                    argparse.Namespace(tenant_id=tenant_id, seller_name="Unityre"),
                )
                snapshot = conn.execute(
                    """SELECT title,price_amount,attributes_json FROM tenant_catalog_products
                       WHERE tenant_id=? AND marketplace_code='halyk_market'
                         AND source_product_code='halyk-1'""",
                    (tenant_id,),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(1, saved)
            self.assertIsNotNone(snapshot)
            self.assertEqual(42000, snapshot["price_amount"])
            service = DataService(db_path, "Unityre", halyk_seller_name="Unityre")
            rows = [row for row in service.rows(0) if row.get("platform") == "halyk_market"]
            self.assertEqual(1, len(rows))
            self.assertEqual("halyk:halyk-1", rows[0]["product_code"])
            self.assertEqual("KZT", rows[0]["currency_original"])
            self.assertEqual(1, rows[0]["competitor_seller_count"])


if __name__ == "__main__":
    unittest.main()
