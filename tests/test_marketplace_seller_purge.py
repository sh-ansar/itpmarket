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
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_preview_and_purge_are_strictly_seller_scoped(self) -> None:
        preview = self.saas.marketplace_seller_purge_preview(self.tenant_id, "kaspi", self.seller_a)
        self.assertEqual(1, preview["counts"]["tenant_seller_catalog_products"])
        result = self.saas.purge_marketplace_seller_data(self.tenant_id, "kaspi", self.seller_a, int(self.admin["id"]))
        self.assertEqual(1, result["deleted"]["tenant_seller_catalog_products"])
        conn = self.saas._connect()
        try:
            remaining = conn.execute("SELECT tenant_seller_id FROM tenant_seller_catalog_products").fetchall()
            self.assertEqual([self.seller_b], [int(row["tenant_seller_id"]) for row in remaining])
            audit = conn.execute("SELECT action FROM platform_audit_log ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual("tenant_marketplace_seller_data_purged", audit["action"])
        finally:
            conn.close()

    def test_remove_disables_only_target_schedules_and_keeps_history(self) -> None:
        stamp = now_iso()
        conn = self.saas._connect()
        try:
            conn.execute(
                """UPDATE tenant_marketplace_sellers SET credential_ref=? WHERE id=?""",
                ("seller-a-token", self.seller_a),
            )
            conn.execute(
                """INSERT INTO seller_encrypted_credentials(credential_ref,tenant_id,tenant_seller_id,marketplace_code,credential_name,ciphertext,key_id,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("seller-a-token", self.tenant_id, self.seller_a, "kaspi", "token", "x", "k", int(self.admin["id"]), stamp, stamp),
            )
            conn.execute(
                """INSERT INTO encrypted_credentials(credential_ref,tenant_id,marketplace_code,credential_name,ciphertext,key_id,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                ("seller-a-token", self.tenant_id, "kaspi", "legacy-token", "x", "k", int(self.admin["id"]), stamp, stamp),
            )
            schedule_a = conn.execute(
                """INSERT INTO operation_schedules(tenant_id,name,action,platform,tenant_seller_id,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (self.tenant_id, "A", "kaspi_full_sync", "kaspi", self.seller_a, int(self.admin["id"]), stamp, stamp),
            ).lastrowid
            schedule_b = conn.execute(
                """INSERT INTO operation_schedules(tenant_id,name,action,platform,tenant_seller_id,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (self.tenant_id, "B", "kaspi_full_sync", "kaspi", self.seller_b, int(self.admin["id"]), stamp, stamp),
            ).lastrowid
            conn.execute(
                """INSERT INTO schedule_runs(schedule_id,tenant_id,tenant_seller_id,status,started_at)
                   VALUES(?,?,?,?,?)""",
                (schedule_a, self.tenant_id, self.seller_a, "success", stamp),
            )
            conn.execute(
                """INSERT INTO tenant_catalog_import_runs(tenant_id,marketplace_code,tenant_seller_id,status,started_at)
                   VALUES(?,?,?,?,?)""",
                (self.tenant_id, "kaspi", self.seller_a, "success", stamp),
            )
            conn.commit()
        finally:
            conn.close()

        result = self.saas.remove_marketplace_seller(
            self.tenant_id, "kaspi", self.seller_a, int(self.admin["id"]),
        )
        self.assertEqual(self.seller_b, result["fallback_seller_id"])
        conn = self.saas._connect()
        try:
            seller = conn.execute("SELECT status,approval_status,credential_ref FROM tenant_marketplace_sellers WHERE id=?", (self.seller_a,)).fetchone()
            self.assertEqual(("removed", "removed", None), (seller["status"], seller["approval_status"], seller["credential_ref"]))
            schedules = conn.execute("SELECT tenant_seller_id,is_enabled FROM operation_schedules ORDER BY id").fetchall()
            self.assertEqual([(self.seller_a, 0), (self.seller_b, 1)], [(row["tenant_seller_id"], row["is_enabled"]) for row in schedules])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM seller_encrypted_credentials WHERE tenant_seller_id=?", (self.seller_a,)).fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM encrypted_credentials WHERE credential_ref='seller-a-token'").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM schedule_runs WHERE tenant_seller_id=?", (self.seller_a,)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM tenant_catalog_import_runs WHERE tenant_seller_id=?", (self.seller_a,)).fetchone()[0])
            self.assertEqual("tenant_marketplace_seller_removed", conn.execute("SELECT action FROM platform_audit_log ORDER BY id DESC LIMIT 1").fetchone()["action"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
