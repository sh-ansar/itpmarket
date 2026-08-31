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


if __name__ == "__main__":
    unittest.main()
