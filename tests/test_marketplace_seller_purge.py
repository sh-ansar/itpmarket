from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from saas_service import SaaSService, now_iso
from schema import ensure_database


class MarketplaceSellerPurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="seller_purge_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.admin, _ = self.auth.create_initial_admin("root@example.com", "Root", "StrongPassword123!")
        self.tenant_id = int(self.admin["tenant_id"])
        self.saas = SaaSService(self.db_path)
        stamp = now_iso()
        conn = self.saas._connect()
        try:
            first = conn.execute(
                """INSERT INTO tenant_marketplace_sellers(tenant_id,marketplace_code,external_seller_id,status,approval_status,created_at,updated_at)
                   VALUES(?,?,?,'active','approved',?,?)""",
                (self.tenant_id, "kaspi", "seller-a", stamp, stamp),
            ).lastrowid
            second = conn.execute(
                """INSERT INTO tenant_marketplace_sellers(tenant_id,marketplace_code,external_seller_id,status,approval_status,created_at,updated_at)
                   VALUES(?,?,?,'active','approved',?,?)""",
                (self.tenant_id, "kaspi", "seller-b", stamp, stamp),
            ).lastrowid
            self.seller_a, self.seller_b = int(first), int(second)
            for seller_id, code in ((self.seller_a, "a-product"), (self.seller_b, "b-product")):
                conn.execute(
                    """INSERT INTO tenant_seller_catalog_products(tenant_id,marketplace_code,tenant_seller_id,source_product_code,first_seen_at,last_seen_at)
                       VALUES(?,?,?,?,?,?)""",
                    (self.tenant_id, "kaspi", seller_id, code, stamp, stamp),
                )
            self.schedule_a = int(conn.execute(
                """INSERT INTO operation_schedules(
                       tenant_id,name,action,platform,tenant_seller_id,is_enabled,created_at,updated_at
                   ) VALUES(?,?,?,?,?,1,?,?)""",
                (self.tenant_id, "Seller A sync", "catalog_sync", "kaspi", self.seller_a, stamp, stamp),
            ).lastrowid)
            self.run_a = int(conn.execute(
                """INSERT INTO schedule_runs(schedule_id,tenant_id,tenant_seller_id,status,message,started_at)
                   VALUES(?,?,?,'completed','historical run',?)""",
                (self.schedule_a, self.tenant_id, self.seller_a, stamp),
            ).lastrowid)
            self.import_run_a = int(conn.execute(
                """INSERT INTO tenant_catalog_import_runs(
                       tenant_id,marketplace_code,tenant_seller_id,status,started_at
                   ) VALUES(?,?,?,?,?)""",
                (self.tenant_id, "kaspi", self.seller_a, "completed", stamp),
            ).lastrowid)
            conn.execute(
                """INSERT INTO seller_encrypted_credentials(
                       credential_ref,tenant_id,tenant_seller_id,marketplace_code,credential_name,
                       ciphertext,key_id,created_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("cred-seller-a", self.tenant_id, self.seller_a, "kaspi", "session",
                 "ciphertext", "test-key", int(self.admin["id"]), stamp, stamp),
            )
            conn.execute(
                "UPDATE tenant_marketplace_sellers SET credential_ref=? WHERE id=?",
                ("cred-seller-a", self.seller_a),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_remove_is_strictly_seller_scoped_and_marks_seller_removed(self) -> None:
        preview = self.saas.marketplace_seller_purge_preview(self.tenant_id, "kaspi", self.seller_a)
        self.assertEqual(1, preview["counts"]["tenant_seller_catalog_products"])
        result = self.saas.remove_marketplace_seller(self.tenant_id, "kaspi", self.seller_a, int(self.admin["id"]))
        self.assertEqual(1, result["deleted"]["tenant_seller_catalog_products"])
        conn = self.saas._connect()
        try:
            remaining = conn.execute("SELECT tenant_seller_id FROM tenant_seller_catalog_products").fetchall()
            self.assertEqual([self.seller_b], [int(row["tenant_seller_id"]) for row in remaining])
            seller_a = conn.execute("SELECT status,approval_status FROM tenant_marketplace_sellers WHERE id=?", (self.seller_a,)).fetchone()
            seller_b = conn.execute("SELECT status,approval_status FROM tenant_marketplace_sellers WHERE id=?", (self.seller_b,)).fetchone()
            self.assertEqual(("removed", "removed"), (seller_a["status"], seller_a["approval_status"]))
            self.assertEqual(("active", "approved"), (seller_b["status"], seller_b["approval_status"]))
            schedule = conn.execute(
                "SELECT is_enabled,last_status FROM operation_schedules WHERE id=?", (self.schedule_a,),
            ).fetchone()
            self.assertFalse(bool(schedule["is_enabled"]))
            self.assertEqual("disabled_source_removed", schedule["last_status"])
            self.assertEqual(self.seller_a, conn.execute(
                "SELECT tenant_seller_id FROM schedule_runs WHERE id=?", (self.run_a,),
            ).fetchone()[0])
            self.assertEqual(1, conn.execute(
                "SELECT COUNT(*) FROM tenant_catalog_import_runs WHERE id=?", (self.import_run_a,),
            ).fetchone()[0])
            self.assertEqual(0, conn.execute(
                "SELECT COUNT(*) FROM seller_encrypted_credentials WHERE tenant_seller_id=?", (self.seller_a,),
            ).fetchone()[0])
            integration = conn.execute(
                "SELECT seller_identifier,status,approval_status FROM tenant_integrations WHERE tenant_id=? AND integration_code='kaspi'",
                (self.tenant_id,),
            ).fetchone()
            self.assertEqual("seller-b", integration["seller_identifier"])
            self.assertEqual(("active", "approved"), (integration["status"], integration["approval_status"]))
            audit = conn.execute("SELECT action FROM platform_audit_log ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual("tenant_marketplace_seller_removed", audit["action"])
        finally:
            conn.close()

    def test_remove_last_active_seller_disables_integration_but_keeps_grant(self) -> None:
        self.saas.remove_marketplace_seller(self.tenant_id, "kaspi", self.seller_a, int(self.admin["id"]))
        self.saas.remove_marketplace_seller(self.tenant_id, "kaspi", self.seller_b, int(self.admin["id"]))
        conn = self.saas._connect()
        try:
            integration = conn.execute(
                """SELECT status,seller_identifier,seller_url,product_count,last_sync_at
                   FROM tenant_integrations WHERE tenant_id=? AND integration_code='kaspi'""",
                (self.tenant_id,),
            ).fetchone()
            self.assertEqual("disabled", integration["status"])
            self.assertEqual("", integration["seller_identifier"])
            self.assertEqual("", integration["seller_url"])
            self.assertEqual(0, integration["product_count"])
            self.assertIsNone(integration["last_sync_at"])
            grant = conn.execute(
                "SELECT is_allowed FROM tenant_marketplace_access WHERE tenant_id=? AND marketplace_code='kaspi'",
                (self.tenant_id,),
            ).fetchone()
            self.assertTrue(bool(grant["is_allowed"]))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
