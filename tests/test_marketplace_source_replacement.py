from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auth_service import AuthService
from ozon_source_verification import OzonSourceVerificationError
from saas_service import SaaSService, now_iso
from schema import ensure_database


class MarketplaceSourceReplacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="marketplace_source_replacement_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.admin, _ = self.auth.create_initial_admin("root@example.com", "Root", "StrongPassword123!")
        self.tenant_id = int(self.admin["tenant_id"])
        self.saas = SaaSService(self.db_path)
        self.saas.update_tenant_profile(self.tenant_id, {
            "name": "Replacement Test", "registration_number": "BIN-REPLACE-001",
            "contact_email": "replace@example.com", "contact_phone": "+7 700 123 45 67",
        }, int(self.admin["id"]))
        self.saas.set_marketplace_access(self.tenant_id, ["kaspi"], int(self.admin["id"]))

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_replace_is_immediate_scoped_and_reassigns_seller_schedule(self) -> None:
        first = self.saas.connect_marketplace(
            self.tenant_id, "https://kaspi.kz/shop/m/first-seller/products", int(self.admin["id"]), "kaspi",
        )
        self.saas.review_marketplace_connection(
            self.tenant_id, "kaspi", "approved", int(self.admin["id"]), tenant_seller_id=int(first["tenant_seller_id"]),
        )
        old_id = int(first["tenant_seller_id"])
        stamp = now_iso()
        conn = self.saas._connect()
        try:
            conn.execute(
                """INSERT INTO tenant_seller_catalog_products(
                       tenant_id,marketplace_code,tenant_seller_id,source_product_code,first_seen_at,last_seen_at
                   ) VALUES(?,?,?,?,?,?)""",
                (self.tenant_id, "kaspi", old_id, "old-product", stamp, stamp),
            )
            schedule_id = conn.execute(
                """INSERT INTO operation_schedules(
                       tenant_id,name,action,platform,tenant_seller_id,is_enabled,created_at,updated_at
                   ) VALUES(?,?,?,?,?,1,?,?)""",
                (self.tenant_id, "Old seller schedule", "catalog_sync", "kaspi", old_id, stamp, stamp),
            ).lastrowid
            run_id = conn.execute(
                """INSERT INTO schedule_runs(schedule_id,tenant_id,tenant_seller_id,status,message,started_at)
                   VALUES(?,?,?,'completed','historical run',?)""",
                (schedule_id, self.tenant_id, old_id, stamp),
            ).lastrowid
            import_run_id = conn.execute(
                """INSERT INTO tenant_catalog_import_runs(
                       tenant_id,marketplace_code,tenant_seller_id,status,started_at
                   ) VALUES(?,?,?,?,?)""",
                (self.tenant_id, "kaspi", old_id, "completed", stamp),
            ).lastrowid
            conn.execute(
                """INSERT INTO seller_encrypted_credentials(
                       credential_ref,tenant_id,tenant_seller_id,marketplace_code,credential_name,
                       ciphertext,key_id,created_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("cred-old-seller", self.tenant_id, old_id, "kaspi", "session",
                 "ciphertext", "test-key", int(self.admin["id"]), stamp, stamp),
            )
            conn.execute(
                "UPDATE tenant_marketplace_sellers SET credential_ref=? WHERE id=?",
                ("cred-old-seller", old_id),
            )
            conn.commit()
        finally:
            conn.close()

        result = self.saas.replace_marketplace_source(
            self.tenant_id, "kaspi", int(first["tenant_seller_id"]),
            "https://kaspi.kz/shop/m/second-seller/products", int(self.admin["id"]),
        )
        sellers = {item["external_seller_id"]: item for item in self.saas.tenant_detail(self.tenant_id)["sellers"]}
        self.assertEqual(old_id, result["replaced_tenant_seller_id"])
        self.assertEqual("replaced", sellers["first-seller"]["status"])
        self.assertEqual("active", sellers["second-seller"]["status"])
        self.assertEqual("approved", sellers["second-seller"]["approval_status"])
        self.assertNotEqual("pending", sellers["second-seller"]["approval_status"])
        new_id = int(sellers["second-seller"]["id"])
        conn = self.saas._connect()
        try:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM tenant_seller_catalog_products WHERE tenant_seller_id=?", (old_id,)).fetchone()[0])
            self.assertEqual(new_id, conn.execute("SELECT tenant_seller_id FROM operation_schedules WHERE id=?", (schedule_id,)).fetchone()[0])
            self.assertEqual(old_id, conn.execute("SELECT tenant_seller_id FROM schedule_runs WHERE id=?", (run_id,)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM tenant_catalog_import_runs WHERE id=?", (import_run_id,)).fetchone()[0])
            self.assertEqual(1, conn.execute(
                "SELECT COUNT(*) FROM seller_encrypted_credentials WHERE tenant_seller_id=?", (old_id,),
            ).fetchone()[0])
            self.assertEqual(0, conn.execute(
                "SELECT COUNT(*) FROM seller_encrypted_credentials WHERE tenant_seller_id=?", (new_id,),
            ).fetchone()[0])
            integration = conn.execute("SELECT seller_identifier,status,approval_status FROM tenant_integrations WHERE tenant_id=? AND integration_code='kaspi'", (self.tenant_id,)).fetchone()
            self.assertEqual("second-seller", integration["seller_identifier"])
            self.assertEqual("active", integration["status"])
            self.assertEqual("approved", integration["approval_status"])
            audit = conn.execute(
                "SELECT action FROM platform_audit_log ORDER BY id DESC LIMIT 1",
            ).fetchone()
            self.assertEqual("tenant_marketplace_source_replaced", audit["action"])
        finally:
            conn.close()
        resolved = self.saas.resolve_seller(self.tenant_id, "kaspi", require_active=True)
        self.assertEqual(new_id, int(resolved["id"]))

    def test_invalid_replacement_leaves_active_source_and_catalog_untouched(self) -> None:
        first = self.saas.connect_marketplace(
            self.tenant_id, "https://kaspi.kz/shop/m/first-seller/products", int(self.admin["id"]), "kaspi",
        )
        old_id = int(first["tenant_seller_id"])
        self.saas.review_marketplace_connection(self.tenant_id, "kaspi", "approved", int(self.admin["id"]), tenant_seller_id=old_id)
        pending = self.saas.connect_marketplace(
            self.tenant_id, "https://kaspi.kz/shop/m/pending-seller/products", int(self.admin["id"]), "kaspi",
        )
        pending_id = int(pending["tenant_seller_id"])
        stamp = now_iso()
        conn = self.saas._connect()
        try:
            conn.execute("INSERT INTO tenant_seller_catalog_products(tenant_id,marketplace_code,tenant_seller_id,source_product_code,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?)", (self.tenant_id, "kaspi", old_id, "old-product", stamp, stamp))
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(ValueError):
            self.saas.replace_marketplace_source(self.tenant_id, "kaspi", old_id, "https://example.invalid/not-a-marketplace", int(self.admin["id"]))
        conn = self.saas._connect()
        try:
            seller = conn.execute("SELECT status,approval_status FROM tenant_marketplace_sellers WHERE id=?", (old_id,)).fetchone()
            self.assertEqual("active", seller["status"])
            self.assertEqual("approved", seller["approval_status"])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM tenant_seller_catalog_products WHERE tenant_seller_id=?", (old_id,)).fetchone()[0])
            self.assertEqual(("pending", "pending"), tuple(conn.execute(
                "SELECT status,approval_status FROM tenant_marketplace_sellers WHERE id=?", (pending_id,),
            ).fetchone()))
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM tenant_marketplace_sellers WHERE tenant_id=? AND marketplace_code='kaspi'", (self.tenant_id,)).fetchone()[0])
        finally:
            conn.close()

    def test_replace_reuses_existing_pending_candidate(self) -> None:
        first = self.saas.connect_marketplace(
            self.tenant_id, "https://kaspi.kz/shop/m/first-seller/products", int(self.admin["id"]), "kaspi",
        )
        old_id = int(first["tenant_seller_id"])
        self.saas.review_marketplace_connection(
            self.tenant_id, "kaspi", "approved", int(self.admin["id"]), tenant_seller_id=old_id,
        )
        pending = self.saas.connect_marketplace(
            self.tenant_id, "https://kaspi.kz/shop/m/second-seller/products", int(self.admin["id"]), "kaspi",
        )
        new_id = int(pending["tenant_seller_id"])
        stamp = now_iso()
        conn = self.saas._connect()
        try:
            conn.execute(
                """INSERT INTO tenant_seller_catalog_products(
                       tenant_id,marketplace_code,tenant_seller_id,source_product_code,first_seen_at,last_seen_at
                   ) VALUES(?,?,?,?,?,?)""",
                (self.tenant_id, "kaspi", old_id, "old-product", stamp, stamp),
            )
            conn.commit()
        finally:
            conn.close()

        result = self.saas.replace_marketplace_source(
            self.tenant_id, "kaspi", old_id,
            "https://kaspi.kz/shop/m/second-seller/products", int(self.admin["id"]),
        )
        self.assertEqual(new_id, int(result["seller"]["id"]))
        conn = self.saas._connect()
        try:
            self.assertEqual(2, conn.execute(
                "SELECT COUNT(*) FROM tenant_marketplace_sellers WHERE tenant_id=? AND marketplace_code='kaspi'",
                (self.tenant_id,),
            ).fetchone()[0])
            self.assertEqual(("replaced", "replaced"), tuple(conn.execute(
                "SELECT status,approval_status FROM tenant_marketplace_sellers WHERE id=?", (old_id,),
            ).fetchone()))
            self.assertEqual(("active", "approved"), tuple(conn.execute(
                "SELECT status,approval_status FROM tenant_marketplace_sellers WHERE id=?", (new_id,),
            ).fetchone()))
            self.assertEqual(0, conn.execute(
                "SELECT COUNT(*) FROM tenant_marketplace_sellers WHERE tenant_id=? AND marketplace_code='kaspi' AND approval_status='pending'",
                (self.tenant_id,),
            ).fetchone()[0])
            self.assertEqual(0, conn.execute(
                "SELECT COUNT(*) FROM tenant_seller_catalog_products WHERE tenant_seller_id=?", (old_id,),
            ).fetchone()[0])
            integration = conn.execute(
                "SELECT seller_identifier FROM tenant_integrations WHERE tenant_id=? AND integration_code='kaspi'",
                (self.tenant_id,),
            ).fetchone()
            self.assertEqual("second-seller", integration["seller_identifier"])
        finally:
            conn.close()

    def test_ozon_replace_requires_live_verification_before_any_mutation(self) -> None:
        self.saas.set_marketplace_access(self.tenant_id, ["ozon"], int(self.admin["id"]))
        first = self.saas.connect_marketplace(
            self.tenant_id, "https://www.ozon.ru/seller/old-store-100/", int(self.admin["id"]), "ozon",
        )
        old_id = int(first["tenant_seller_id"])
        old_evidence = {
            "canonical_seller_id": "old-store-100", "canonical_seller_url": "https://www.ozon.ru/seller/old-store-100/",
            "seller_name": "Old Store", "catalogue_empty": "false",
        }
        with patch("saas_service.verify_ozon_storefront", return_value=old_evidence):
            self.saas.review_marketplace_connection(self.tenant_id, "ozon", "approved", int(self.admin["id"]), tenant_seller_id=old_id)
        with patch("saas_service.verify_ozon_storefront", side_effect=OzonSourceVerificationError("verification failed")):
            with self.assertRaises(ValueError):
                self.saas.replace_marketplace_source(
                    self.tenant_id, "ozon", old_id, "https://www.ozon.ru/seller/new-store-200/", int(self.admin["id"]),
                )
        conn = self.saas._connect()
        try:
            self.assertEqual("active", conn.execute("SELECT status FROM tenant_marketplace_sellers WHERE id=?", (old_id,)).fetchone()["status"])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM tenant_marketplace_sellers WHERE tenant_id=? AND marketplace_code='ozon'", (self.tenant_id,)).fetchone()[0])
        finally:
            conn.close()
        new_evidence = {
            "canonical_seller_id": "new-store-200", "canonical_seller_url": "https://www.ozon.ru/seller/new-store-200/",
            "seller_name": "New Store", "catalogue_empty": "false",
        }
        with patch("saas_service.verify_ozon_storefront", return_value=new_evidence) as verify:
            result = self.saas.replace_marketplace_source(
                self.tenant_id, "ozon", old_id, "https://www.ozon.ru/seller/new-store-200/", int(self.admin["id"]),
            )
        self.assertEqual("new-store-200", result["seller"]["external_seller_id"])
        self.assertEqual(1, verify.call_count)
