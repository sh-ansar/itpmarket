from __future__ import annotations

import sqlite3
import tempfile
import unittest
import argparse
from pathlib import Path
from unittest.mock import patch

from auth_service import AuthService
from catalog_configuration_service import CatalogConfigurationService
from data_service import DataService
from schema import ensure_database
from saas_service import SaaSService
from tenant_security import company_is_approved, has_permission
from collectors.forte import forte_collector as forte
from tests.subscription_fixtures import activate_legacy_subscription


class CompanyMultitenancyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="company_tenant_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.admin_a, _ = self.auth.create_initial_admin(
            "admin-a@example.com", "Admin A", "StrongPassword123!"
        )
        self.tenant_a = int(self.admin_a["tenant_id"])
        conn = sqlite3.connect(self.db_path)
        stamp = conn.execute("SELECT datetime('now')").fetchone()[0]
        self.tenant_b = int(conn.execute(
            """INSERT INTO tenants(name,slug,registration_number,status,plan_code,contact_email,contact_phone,created_at,updated_at)
               VALUES('Company B','company-b','BIN-B','approved','demo','b@example.com','+7 700 000 00 02',?,?)""",
            (stamp, stamp),
        ).lastrowid)
        conn.commit()
        conn.close()
        ensure_database(self.db_path)
        activate_legacy_subscription(
            self.db_path, self.tenant_a, actor_user_id=int(self.admin_a["id"])
        )
        activate_legacy_subscription(
            self.db_path, self.tenant_b, actor_user_id=int(self.admin_a["id"])
        )
        self.user_b, _ = self.auth.create_user(
            "operator-b@example.com", "Operator B", "StrongPassword456!",
            "operator", int(self.admin_a["id"]), tenant_id=self.tenant_b,
        )
        saas = SaaSService(self.db_path)
        saas.update_tenant_profile(
            self.tenant_a,
            {"name":"Company A","registration_number":"BIN-A","contact_email":"a@example.com","contact_phone":"+7 700 000 00 01"},
            int(self.admin_a["id"]),
        )
        saas.set_marketplace_access(self.tenant_a, ["ozon", "halyk_market"], int(self.admin_a["id"]))
        saas.set_marketplace_access(self.tenant_b, ["ozon"], int(self.admin_a["id"]))
        self.admin_a = self.auth.get_user(int(self.admin_a["id"])) or self.admin_a
        self.user_b = self.auth.get_user(int(self.user_b["id"])) or self.user_b
        self.catalog = CatalogConfigurationService(self.db_path)
        self.data = DataService(self.db_path, "Tenant seller")

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_two_company_catalogs_and_marketplaces_are_isolated(self) -> None:
        self.catalog.upsert_catalog_product(
            self.tenant_a, "ozon",
            {"product_id": "A-OZ", "title": "A phone", "attributes": {"Color": "Black"}},
        )
        self.catalog.upsert_catalog_product(
            self.tenant_a, "halyk_market",
            {"product_id": "A-HL", "title": "A appliance", "attributes": {"Power": "900 W"}},
        )
        self.catalog.upsert_catalog_product(
            self.tenant_b, "ozon",
            {"product_id": "B-OZ", "title": "B phone", "attributes": {"Color": "Blue"}},
        )

        codes_a = {row["product_code"] for row in self.data.rows_for_user(int(self.admin_a["id"]))}
        codes_b = {row["product_code"] for row in self.data.rows_for_user(int(self.user_b["id"]))}
        self.assertEqual({"ozon:A-OZ", "halyk:A-HL"}, codes_a)
        self.assertEqual({"ozon:B-OZ"}, codes_b)
        self.assertNotIn("ozon:B-OZ", codes_a)
        self.assertNotIn("halyk:A-HL", codes_b)
        with self.assertRaises(PermissionError):
            self.data.set_product_state(
                ["ozon:B-OZ"], True, "high", "cross tenant", int(self.admin_a["id"])
            )

    def test_empty_non_owner_catalog_skips_legacy_materialization(self) -> None:
        with patch.object(
            self.data, "rows", side_effect=AssertionError("legacy rows must not load")
        ):
            self.assertEqual([], self.data.rows_for_user(int(self.user_b["id"])))

    def test_unfiltered_wildberries_only_catalog_skips_legacy_materialization(self) -> None:
        self.catalog.upsert_catalog_product(
            self.tenant_b, "wildberries",
            {"product_id": "WB-B-1", "title": "Company B WB product", "price": 1234},
        )
        with patch.object(
            self.data, "rows", side_effect=AssertionError("legacy rows must not load")
        ):
            rows = self.data.rows_for_user(int(self.user_b["id"]))
        self.assertEqual(["wb:WB-B-1"], [row["product_code"] for row in rows])

    def test_kaspi_uses_company_seller_and_does_not_count_own_offer_as_competitor(self) -> None:
        product_code = "149994952"
        stamp = "2026-08-11T16:41:46+05:00"
        self.catalog.upsert_catalog_product(
            self.tenant_b, "kaspi",
            {
                "product_id": product_code,
                "title": "Company B product",
                "price": 1299,
                "currency": "KZT",
            },
        )
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """UPDATE tenant_integrations
                   SET seller_identifier='30387605',seller_name='30387605',
                       seller_url='https://kaspi.kz/shop/m/30387605/products',
                       status='active',approval_status='approved'
                   WHERE tenant_id=? AND integration_code='kaspi'""",
                (self.tenant_b,),
            )
            conn.execute(
                """INSERT INTO catalog_products(
                       product_code,page_number,position_on_page,title_catalog,
                       catalog_price_kzt,product_url,collected_at
                   ) VALUES(?,1,1,?,1299,?,?)""",
                (
                    product_code, "Company B product",
                    f"https://kaspi.kz/shop/p/{product_code}", stamp,
                ),
            )
            conn.execute(
                """INSERT INTO market_seller_offers(
                       source_product_code,candidate_product_code,merchant_id,
                       merchant_name,merchant_sku,price_kzt,captured_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (product_code, product_code, "30387605", "LICK", "sku", 1299, stamp),
            )
            conn.execute(
                """INSERT INTO exact_offer_scans(
                       product_code,status,offers_count,competitor_count,checked_at
                   ) VALUES(?,'no_competitors',1,0,?)""",
                (product_code, stamp),
            )
            conn.commit()
        finally:
            conn.close()

        row = self.data.rows_for_user(
            int(self.user_b["id"]), {"kaspi"}
        )[0]
        self.assertEqual("LICK", row["seller_name"])
        self.assertEqual("NO_OTHER_SELLERS", row["price_status"])
        self.assertEqual(0, row["reference_count"])
        self.assertIsNone(row["price_rank"])
        self.assertIsNone(row["price_rank_total"])

    def test_tenant_catalog_membership_enriches_forte_price_analysis(self) -> None:
        args = argparse.Namespace(
            merchant_id="tenant-b-forte", seller_name="Company B Forte"
        )
        product = {
            "uid": "forte-shared-card", "slug": "forte-shared-card",
            "name": "Company B phone", "product_price": 120_000,
        }
        detail = {
            "showcase": dict(product), "characteristics": [],
            "nomenclatures_data": [
                {
                    "merchant_name": "Company B Forte",
                    "nomenclature": {
                        "merchant_id": "tenant-b-forte", "price": 120_000,
                        "available": True, "sale_channels": ["DELIVERY"],
                    },
                },
                {
                    "merchant_name": "Market competitor",
                    "nomenclature": {
                        "merchant_id": "competitor", "price": 110_000,
                        "available": True, "sale_channels": ["DELIVERY"],
                    },
                },
            ],
        }
        conn = forte.connect(self.db_path)
        try:
            product_id = forte.upsert_product(
                conn, product, args, "2026-08-11T12:00:00+05:00", detail=detail
            )
            forte.save_offers(
                conn, "tenant-forte-run", product_id,
                forte.extract_offers(detail, args), "2026-08-11T12:00:00+05:00",
            )
            conn.execute(
                "UPDATE forte_products SET last_market_at=? WHERE product_id=?",
                ("2026-08-11T12:00:00+05:00", product_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.catalog.upsert_catalog_product(
            self.tenant_b, "forte_market",
            {"product_id": product_id, "title": "Company B phone", "price": 120_000},
        )

        row = next(
            item for item in self.data.rows_for_user(int(self.user_b["id"]))
            if item["product_code"] == "forte:forte-shared-card"
        )
        detail_row = self.data.product(row["product_code"], user_id=int(self.user_b["id"]))

        self.assertEqual(120_000, row["own_price_kzt"])
        self.assertEqual(110_000, row["market_min_price_kzt"])
        self.assertEqual(1, row["reference_count"])
        self.assertEqual("EXACT_HIGHEST", row["price_status"])
        self.assertIsNotNone(detail_row)
        self.assertEqual(2, len(detail_row["offers"]))

    def test_attribute_aliases_enabled_filters_and_platform_scope(self) -> None:
        self.catalog.upsert_catalog_product(
            self.tenant_a, "ozon",
            {"product_id": "A1", "title": "Ozon item", "attributes": {"Сезон": "Зима"}},
        )
        self.catalog.upsert_catalog_product(
            self.tenant_a, "halyk_market",
            {"product_id": "A2", "title": "Halyk item", "attributes": {"Сезонность": "Зима"}},
        )
        self.catalog.upsert_catalog_product(
            self.tenant_b, "ozon",
            {"product_id": "B2", "title": "B item", "attributes": {"company_b_secret": "Только B"}},
        )
        config = self.catalog.filter_configuration(
            self.tenant_a, {"ozon", "halyk_market"}
        )
        season = [item for item in config["attributes"] if item["attribute_key"] == "season"]
        self.assertEqual(1, len(season))
        self.assertEqual({"ozon", "halyk_market"}, {
            source["marketplace_code"] for source in season[0]["sources"]
        })
        self.assertNotIn(
            "company_b_secret", {item["attribute_key"] for item in config["attributes"]}
        )
        config_b = self.catalog.filter_configuration(self.tenant_b, {"ozon"})
        self.assertIn(
            "company_b_secret", {item["attribute_key"] for item in config_b["attributes"]}
        )
        self.assertEqual(
            {"filters": [], "attributes": []},
            self.catalog.filter_configuration(self.tenant_a, set()),
        )

        self.catalog.update_filters(
            self.tenant_a,
            [{"attribute_key": "season", "is_enabled": True}],
            int(self.admin_a["id"]),
        )
        self.assertEqual(
            {"ozon:A1"},
            self.catalog.matching_product_codes(
                self.tenant_a, {"ozon"}, {"season": ["Зима"]}
            ),
        )
        self.assertEqual(
            {"ozon:A1", "halyk:A2"},
            self.catalog.matching_product_codes(
                self.tenant_a, {"ozon", "halyk_market"}, {"season": ["Зима"]}
            ),
        )

    def test_permissions_and_pending_status_are_independent_guards(self) -> None:
        self.user_b = self.auth.update_user(
            int(self.user_b["id"]),
            {"permissions": {"view_products": True, "run_operations": False}},
            int(self.admin_a["id"]),
        )
        self.assertTrue(has_permission(self.user_b, "view_products"))
        self.assertFalse(has_permission(self.user_b, "run_operations"))
        preserved = self.auth.update_user(
            int(self.user_b["id"]),
            {"role": "operator"},
            int(self.admin_a["id"]),
        )
        self.assertFalse(has_permission(preserved, "run_operations"))
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE tenants SET status='pending' WHERE id=?", (self.tenant_b,))
        conn.commit()
        conn.close()
        pending = self.auth.get_user(int(self.user_b["id"]))
        assert pending
        self.assertFalse(company_is_approved(pending["tenant_status"]))
        self.assertFalse(any(pending["marketplaces"].values()))
        self.assertTrue(has_permission(pending, "view_products"))

    def test_platform_review_approves_existing_pending_company(self) -> None:
        conn = sqlite3.connect(self.db_path)
        stamp = conn.execute("SELECT datetime('now')").fetchone()[0]
        conn.execute("UPDATE tenants SET status='pending' WHERE id=?", (self.tenant_b,))
        request_id = int(conn.execute(
            """INSERT INTO registration_requests(
                   company_name,contact_name,email,integrations_json,workspace_profile_json,
                   estimated_products,status,tenant_id,created_at,updated_at
               ) VALUES('Company B','Owner','owner-b@example.com','[]','{}',0,'new',?,?,?)""",
            (self.tenant_b, stamp, stamp),
        ).lastrowid)
        conn.commit()
        conn.close()
        result = SaaSService(self.db_path).review_registration_v2(
            request_id, "approved", int(self.admin_a["id"])
        )
        self.assertEqual("approved", result["status"])
        access = SaaSService(self.db_path).marketplace_access(
            self.tenant_b, include_unavailable=True
        )
        self.assertEqual(6, len(access))
        self.assertTrue(all(item["is_allowed"] for item in access))
        conn = sqlite3.connect(self.db_path)
        status = conn.execute("SELECT status FROM tenants WHERE id=?", (self.tenant_b,)).fetchone()[0]
        conn.close()
        self.assertEqual("approved", status)

    def test_legacy_kaspi_stage_is_materialized_only_for_target_company(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO catalog_products(
                       product_code,title_catalog,catalog_price_kzt,product_url,collected_at
                   ) VALUES('KASPI-B-1','Company B tire',55500,'https://kaspi.kz/p/1','2026-08-10')"""
            )
            conn.execute(
                """INSERT INTO catalog_product_meta(
                       product_code,brand,category_id,active,stock,last_seen_at
                   ) VALUES('KASPI-B-1','Brand B','tires',1,4,'2026-08-10')"""
            )
            conn.execute(
                """INSERT INTO product_details(
                       product_code,title_detail,price_kzt,specifications_json,
                       detail_status,detail_collected_at
                   ) VALUES('KASPI-B-1','Company B tire',55500,?,'ok','2026-08-10')""",
                ('[{"name":"Ширина","value":"205"}]',),
            )
            conn.commit()
        finally:
            conn.close()

        saved = self.catalog.materialize_legacy_kaspi_catalog(
            self.tenant_b, ["KASPI-B-1"], replace=True
        )
        self.assertEqual(1, saved)
        conn = sqlite3.connect(self.db_path)
        try:
            in_b = conn.execute(
                """SELECT COUNT(*) FROM tenant_catalog_products
                   WHERE tenant_id=? AND marketplace_code='kaspi'
                     AND source_product_code='KASPI-B-1' AND active=1""",
                (self.tenant_b,),
            ).fetchone()[0]
            in_a = conn.execute(
                """SELECT COUNT(*) FROM tenant_catalog_products
                   WHERE tenant_id=? AND marketplace_code='kaspi'
                     AND source_product_code='KASPI-B-1' AND active=1""",
                (self.tenant_a,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(1, in_b)
        self.assertEqual(0, in_a)
        keys = {
            item["attribute_key"]
            for item in self.catalog.filter_configuration(self.tenant_b, {"kaspi"})["attributes"]
        }
        self.assertIn("width", keys)

    def test_explicit_empty_kaspi_collection_never_imports_shared_legacy_rows(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO catalog_products(
                       product_code,title_catalog,catalog_price_kzt,product_url,collected_at
                   ) VALUES('SHARED-LEGACY-1','Other company product',1000,
                            'https://kaspi.kz/p/shared','2026-08-11')"""
            )
            conn.commit()
        finally:
            conn.close()

        saved = self.catalog.materialize_legacy_kaspi_catalog(
            self.tenant_b, [], replace=True
        )
        self.assertEqual(0, saved)
        conn = sqlite3.connect(self.db_path)
        try:
            active = conn.execute(
                """SELECT COUNT(*) FROM tenant_catalog_products
                   WHERE tenant_id=? AND marketplace_code='kaspi' AND active=1""",
                (self.tenant_b,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(0, active)

    def test_kaspi_brand_is_recovered_from_specs_not_all_segment_marker(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO catalog_products(
                       product_code,title_catalog,catalog_price_kzt,product_url,collected_at
                   ) VALUES('KASPI-BRAND-1','Cosmex product',1000,
                            'https://kaspi.kz/p/brand','2026-08-11')"""
            )
            conn.execute(
                """INSERT INTO catalog_product_meta(
                       product_code,brand,active,last_seen_at
                   ) VALUES('KASPI-BRAND-1','all',1,'2026-08-11')"""
            )
            conn.execute(
                """INSERT INTO product_details(
                       product_code,title_detail,specifications_json,detail_status,detail_collected_at
                   ) VALUES('KASPI-BRAND-1','Cosmex product',?,'ok','2026-08-11')""",
                ('[{"name":"Бренд","value":"Cosmex"}]',),
            )
            conn.commit()
        finally:
            conn.close()
        self.catalog.materialize_legacy_kaspi_catalog(
            self.tenant_b, ["KASPI-BRAND-1"], replace=True
        )
        conn = sqlite3.connect(self.db_path)
        try:
            brand = conn.execute(
                """SELECT brand FROM tenant_catalog_products
                   WHERE tenant_id=? AND marketplace_code='kaspi'
                     AND source_product_code='KASPI-BRAND-1'""",
                (self.tenant_b,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual("Cosmex", brand)

    def test_kaspi_root_pass_infers_only_a_brand_advertised_by_the_store(self) -> None:
        from engine.catalog_sync import infer_card_brand

        brands = ["Cosmex", "Kapous Professional", "TEFIA"]
        self.assertEqual(
            "Kapous Professional",
            infer_card_brand("Краска Kapous Professional Hyaluronic 100 мл", brands),
        )
        self.assertEqual("Cosmex", infer_card_brand("Набор для волос Cosmex", brands))
        self.assertEqual("", infer_card_brand("Неизвестный шампунь", brands))

    def test_kaspi_root_pass_clears_stale_all_brand_marker(self) -> None:
        from engine.catalog_sync import upsert_cards

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO catalog_products(
                       product_code,title_catalog,catalog_price_kzt,product_url,collected_at
                   ) VALUES('KASPI-NO-BRAND','Unbranded product',1000,
                            'https://kaspi.kz/p/no-brand','2026-08-11')"""
            )
            conn.execute(
                """INSERT INTO catalog_product_meta(
                       product_code,brand,active,last_seen_at
                   ) VALUES('KASPI-NO-BRAND','all',1,'2026-08-11')"""
            )
            conn.commit()
            upsert_cards(
                conn,
                [{"id": "KASPI-NO-BRAND", "title": "Unbranded product"}],
                "all",
                0,
                "https://kaspi.kz/shop/m/test/products",
                set(),
            )
            brand = conn.execute(
                "SELECT brand FROM catalog_product_meta WHERE product_code='KASPI-NO-BRAND'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual("", brand)

    def test_kaspi_followup_operations_select_only_the_company_snapshot(self) -> None:
        from engine.exact_offer_refresh import get_jobs
        from engine.own_price_refresh import tenant_catalog_rows

        conn = sqlite3.connect(self.db_path)
        try:
            for code, title in (("TENANT-A-SKU", "A product"), ("TENANT-B-SKU", "B product")):
                conn.execute(
                    """INSERT INTO catalog_products(
                           product_code,title_catalog,catalog_price_kzt,product_url,collected_at
                       ) VALUES(?,?,?,?,?)""",
                    (code, title, 1000, f"https://kaspi.kz/p/{code}", "2026-08-11"),
                )
            conn.commit()
        finally:
            conn.close()
        self.catalog.materialize_legacy_kaspi_catalog(
            self.tenant_a, ["TENANT-A-SKU"], replace=True
        )
        self.catalog.materialize_legacy_kaspi_catalog(
            self.tenant_b, ["TENANT-B-SKU"], replace=True
        )

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            own_price_codes = {
                row["product_code"] for row in tenant_catalog_rows(conn, self.tenant_b)
            }
        finally:
            conn.close()
        exact_codes = {
            row["product_code"]
            for row in get_jobs(
                self.db_path, tenant_id=self.tenant_b, codes=[], limit=0,
                refresh=True, only_errors=False, stale_hours=0,
            )
        }
        self.assertEqual({"TENANT-B-SKU"}, own_price_codes)
        self.assertEqual({"TENANT-B-SKU"}, exact_codes)


if __name__ == "__main__":
    unittest.main()
