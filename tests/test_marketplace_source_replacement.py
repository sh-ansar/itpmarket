from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from saas_service import SaaSService
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

    def test_replacement_keeps_old_seller_active_until_candidate_is_approved(self) -> None:
        first = self.saas.connect_marketplace(
            self.tenant_id, "https://kaspi.kz/shop/m/first-seller/products", int(self.admin["id"]), "kaspi",
        )
        self.saas.review_marketplace_connection(
            self.tenant_id, "kaspi", "approved", int(self.admin["id"]), tenant_seller_id=int(first["tenant_seller_id"]),
        )
        staged = self.saas.stage_marketplace_source_replacement(
            self.tenant_id, "kaspi", int(first["tenant_seller_id"]),
            "https://kaspi.kz/shop/m/second-seller/products", int(self.admin["id"]),
        )
        sellers = {item["external_seller_id"]: item for item in self.saas.tenant_detail(self.tenant_id)["sellers"]}
        self.assertEqual("active", sellers["first-seller"]["status"])
        self.assertEqual("pending", sellers["second-seller"]["status"])

        self.saas.review_marketplace_connection(
            self.tenant_id, "kaspi", "approved", int(self.admin["id"]), tenant_seller_id=int(staged["tenant_seller_id"]),
        )
        sellers = {item["external_seller_id"]: item for item in self.saas.tenant_detail(self.tenant_id)["sellers"]}
        self.assertEqual("replaced", sellers["first-seller"]["status"])
        self.assertEqual("active", sellers["second-seller"]["status"])
