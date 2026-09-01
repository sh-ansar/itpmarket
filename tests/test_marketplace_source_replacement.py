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

    def test_replacement_is_immediate_and_reuses_pending_candidate(self) -> None:
        first = self.saas.connect_marketplace(
            self.tenant_id, "https://kaspi.kz/shop/m/first-seller/products", int(self.admin["id"]), "kaspi",
        )
        self.saas.review_marketplace_connection(
            self.tenant_id, "kaspi", "approved", int(self.admin["id"]), tenant_seller_id=int(first["tenant_seller_id"]),
        )
        staged = self.saas.connect_marketplace(
            self.tenant_id,
            "https://kaspi.kz/shop/m/second-seller/products", int(self.admin["id"]), "kaspi",
        )
        replacement = self.saas.replace_marketplace_source(
            self.tenant_id, "kaspi", int(first["tenant_seller_id"]),
            "https://kaspi.kz/shop/m/second-seller/products", int(self.admin["id"]),
        )
        sellers = {item["external_seller_id"]: item for item in self.saas.tenant_detail(self.tenant_id)["sellers"]}
        self.assertEqual("replaced", sellers["first-seller"]["status"])
        self.assertEqual("active", sellers["second-seller"]["status"])
        self.assertEqual("approved", sellers["second-seller"]["approval_status"])
        self.assertEqual(int(staged["tenant_seller_id"]), int(replacement["seller"]["id"]))

    def test_invalid_replacement_leaves_the_old_source_unchanged(self) -> None:
        first = self.saas.connect_marketplace(
            self.tenant_id, "https://kaspi.kz/shop/m/first-seller/products", int(self.admin["id"]), "kaspi",
        )
        seller_id = int(first["tenant_seller_id"])
        self.saas.review_marketplace_connection(
            self.tenant_id, "kaspi", "approved", int(self.admin["id"]), tenant_seller_id=seller_id,
        )
        with self.assertRaises(ValueError):
            self.saas.replace_marketplace_source(
                self.tenant_id, "kaspi", seller_id,
                "https://example.invalid/not-a-marketplace", int(self.admin["id"]),
            )
        seller = self.saas.seller(self.tenant_id, seller_id)
        self.assertEqual("active", seller["status"])
        self.assertEqual("approved", seller["approval_status"])

    def test_ozon_verification_failure_leaves_the_source_unchanged(self) -> None:
        stamp = now_iso()
        conn = self.saas._connect()
        try:
            seller_id = int(conn.execute(
                """INSERT INTO tenant_marketplace_sellers(
                       tenant_id,marketplace_code,external_seller_id,status,approval_status,created_at,updated_at
                   ) VALUES(?,?,?,'active','approved',?,?)""",
                (self.tenant_id, "ozon", "first-store", stamp, stamp),
            ).lastrowid)
            conn.commit()
        finally:
            conn.close()
        with patch("saas_service.verify_ozon_storefront", side_effect=OzonSourceVerificationError("blocked")):
            with self.assertRaisesRegex(ValueError, "not verified"):
                self.saas.replace_marketplace_source(
                    self.tenant_id, "ozon", seller_id,
                    "https://www.ozon.ru/продавец/second-store/", int(self.admin["id"]),
                )
        self.assertEqual(1, len(self.saas.sellers(self.tenant_id, "ozon")))
        self.assertEqual("active", self.saas.seller(self.tenant_id, seller_id)["status"])
