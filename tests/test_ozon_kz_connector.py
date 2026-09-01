from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from collectors.ozon_kz.ozon_kz_collector import (
    build_settings,
    normalize_registry_currency,
    require_success,
)
from collectors.ozon_kz.ozon_kz_connector import validate_source_url
from collectors.ozon_kz.storage import connect, ensure_schema, status
from collectors.ozon.ozon_probe_core import _extract_tile_price
from collectors.ozon.ozon_collector import (
    materialize_tenant_catalog,
    normalize_marketplace_item,
    portable_storage_path,
)
from collectors.ozon.registry import Registry
from data_service import DataService
from schema import ensure_database


class OzonKzConnectorTests(unittest.TestCase):
    def test_kz_normalization_and_legacy_registry_currency_are_kzt(self) -> None:
        normalized = normalize_marketplace_item(
            {"article": "kz-1", "card_price": 42000},
            "2026-08-19T14:00:00+05:00",
            "kz-run",
            "https://ozon.kz/seller/example/",
        )
        self.assertEqual("ozon_kz", normalized["source"])
        self.assertEqual("KZT", normalized["currency"])

        with tempfile.TemporaryDirectory(prefix="ozon_kz_currency_") as folder:
            registry = Registry(Path(folder) / "ozon_kz_registry.db")
            try:
                stamp = "2026-08-19T14:00:00+05:00"
                source_url = "https://ozon.kz/продавец/alfa-tires-3381444/"
                registry.upsert_catalog_product(
                    {
                        "article": "kz-1",
                        "name": "Test Tire 205/55 R16",
                        "catalog_card_price": 42000,
                    },
                    source_url,
                    "kz-run",
                    1,
                    stamp,
                )
                registry.conn.execute(
                    """INSERT INTO offers(
                           article,seller_key,seller_id,seller_name,seller_url,
                           card_price,currency,availability_status,
                           first_seen_at,last_seen_at,last_checked_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "kz-1", "own", "3381444", "Alfa Tires", source_url,
                        42000, "RUB", "OUT_OF_STOCK", stamp, stamp, stamp,
                    ),
                )
                registry.conn.execute(
                    """INSERT INTO price_history(
                           run_id,article,seller_key,currency,collected_at
                       ) VALUES(?,?,?,?,?)""",
                    ("kz-run", "kz-1", "own", "RUB", stamp),
                )
                changed = normalize_registry_currency(registry.conn)
                self.assertEqual({"offers": 1, "price_history": 1}, changed)
                self.assertEqual(
                    "KZT",
                    registry.conn.execute(
                        "SELECT currency FROM offers WHERE article='kz-1'"
                    ).fetchone()[0],
                )
                self.assertEqual(
                    "KZT",
                    registry.conn.execute(
                        "SELECT currency FROM price_history WHERE article='kz-1'"
                    ).fetchone()[0],
                )

                registry.conn.execute(
                    "UPDATE offers SET currency='RUB' WHERE article='kz-1'"
                )
                registry.conn.commit()
                settings = build_settings(Namespace(
                    source_url=source_url,
                    expected_seller="alfa tires 3381444",
                    debug_port=9333,
                    db=str(registry.path),
                ))
                with patch(
                    "collectors.ozon.ozon_collector.CatalogConfigurationService"
                ) as service:
                    service.return_value.replace_catalog_products.return_value = 1
                    self.assertEqual(
                        1,
                        materialize_tenant_catalog(
                            settings, 1, str(Path(folder) / "app.db"), "ozon_kz"
                        ),
                    )
                    products = service.return_value.replace_catalog_products.call_args.args[2]
                    self.assertEqual("KZT", products[0]["currency"])
                    self.assertEqual(42000, products[0]["price"])
                    self.assertEqual("OUT_OF_STOCK", products[0]["availability"])
            finally:
                registry.close()

    def test_partial_collector_result_fails_the_kz_cli(self) -> None:
        self.assertEqual(
            "PASSED",
            require_success(
                {"status": "PASSED"},
                "refresh",
            )["status"],
        )

        with self.assertRaisesRegex(RuntimeError, "PARTIAL"):
            require_success({"status": "PARTIAL"}, "refresh")

        with self.assertRaisesRegex(
            RuntimeError,
            "BLOCKED",
        ):
            require_success(
                {"status": "BLOCKED"},
                "refresh",
            )

    def test_raw_artifact_path_supports_kz_collector_directory(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        raw_path = project_root / "collectors" / "ozon_kz" / "raw" / "42" / "run.json"
        self.assertEqual(
            str(Path("collectors") / "ozon_kz" / "raw" / "42" / "run.json"),
            portable_storage_path(raw_path),
        )

    def test_installment_is_not_mistaken_for_full_kzt_price(self) -> None:
        item = {"mainState": [
            {"type": "priceV2", "priceV2": {
                "price": [{"text": "263 ₸ × 12 мес", "textStyle": "PRICE"}],
                "priceStyle": {"styleType": "ACTUAL_PRICE"},
            }},
            {"type": "priceV2", "priceV2": {
                "price": [
                    {"text": "3 147 ₸", "textStyle": "PRICE"},
                    {"text": "8 266 ₸", "textStyle": "ORIGINAL_PRICE"},
                ],
                "priceStyle": {"styleType": "SALE_PRICE"},
            }},
        ]}
        current, prices, style = _extract_tile_price(item)
        self.assertEqual(3147, current)
        self.assertEqual([3147, 8266], prices)
        self.assertEqual("SALE_PRICE", style)

    def test_direct_status_cli_and_kz_browser_limits(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_kz_cli_") as folder:
            db_path = Path(folder) / "status.db"
            script = Path(__file__).resolve().parents[1] / "collectors" / "ozon_kz" / "ozon_kz_connector.py"
            result = subprocess.run(
                [sys.executable, str(script), "status", "--db", str(db_path)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('"ok": true', result.stdout)

        settings = build_settings(Namespace(
            source_url="https://ozon.kz/seller/ridial/",
            expected_seller="ridial",
            debug_port=9333,
            db="collectors/ozon_kz/data/test.db",
        ))
        self.assertLessEqual(settings.catalog_wait_seconds, 30)
        self.assertLessEqual(settings.page_reloads, 1)
        self.assertEqual("https://ozon.kz/продавец/ridial/", settings.start_url)

    def test_source_boundary_accepts_kz_and_rejects_ru(self) -> None:
        self.assertEqual("https://ozon.kz/shop/", validate_source_url("https://ozon.kz/shop/"))
        with self.assertRaises(ValueError):
            validate_source_url("https://www.ozon.ru/seller/example/")
        with self.assertRaises(ValueError):
            validate_source_url("http://ozon.kz/")

    def test_separate_kzt_registry_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_kz_") as folder:
            root = Path(folder)
            main_db = root / "main.db"
            kz_db = root / "ozon_kz.db"
            ensure_database(main_db)
            ensure_schema(kz_db)
            conn = connect(kz_db)
            try:
                conn.execute(
                    """INSERT INTO ozon_kz_products(
                           product_id,seller_sku,title,brand,model,specifications_json,
                           canonical_url,currency,own_price_kzt,availability_status,
                           first_seen_at,last_seen_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "kz-1", "SKU-KZ", "Test Tire 205/55 R16", "Test", "Ice",
                        '[{"name":"Размер","value":"205/55 R16"}]',
                        "https://ozon.kz/product/kz-1/", "KZT", 42000, "AVAILABLE",
                        "2026-08-10T10:00:00+05:00", "2026-08-10T10:00:00+05:00",
                    ),
                )
                conn.executemany(
                    """INSERT INTO ozon_kz_offers(
                           product_id,seller_id,seller_name,price_kzt,
                           availability_status,is_own,captured_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    [
                        ("kz-1", "own", "Unityre", 42000, "AVAILABLE", 1, "2026-08-10T10:00:00+05:00"),
                        ("kz-1", "other", "Competitor", 40000, "AVAILABLE", 0, "2026-08-10T10:00:00+05:00"),
                    ],
                )
                conn.execute(
                    """INSERT INTO ozon_kz_price_history(
                           product_id,seller_id,price_kzt,availability_status,captured_at
                       ) VALUES(?,?,?,?,?)""",
                    ("kz-1", "own", 42000, "AVAILABLE", "2026-08-10T10:00:00+05:00"),
                )
                conn.commit()
            finally:
                conn.close()

            service = DataService(main_db, "Unityre", ozon_kz_db_path=kz_db)
            rows = [row for row in service.rows(0) if row.get("platform") == "ozon_kz"]
            self.assertEqual(1, len(rows))
            self.assertEqual("ozon_kz:kz-1", rows[0]["product_code"])
            self.assertEqual("KZT", rows[0]["currency_original"])
            self.assertEqual("Ozon.kz", rows[0]["platform_label"])
            self.assertEqual(1, rows[0]["competitor_seller_count"])
            self.assertEqual(1, len(service.price_history("ozon_kz:kz-1")))
            overview = service.overview(0, allowed_platforms={"ozon_kz"})
            self.assertEqual(1, overview["ozon_kz_count"])
            self.assertEqual(1, overview["ozon_kz_data_ready_count"])
            self.assertEqual("source_required", status(kz_db)["status"])


if __name__ == "__main__":
    unittest.main()
