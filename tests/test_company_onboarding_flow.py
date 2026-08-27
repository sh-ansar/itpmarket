from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from saas_service import SaaSService
from schema import ensure_database


class CompanyOnboardingFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="company_onboarding_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.root, _ = self.auth.create_initial_admin(
            "platform-owner@example.com", "Platform Owner", "StrongPassword123!"
        )
        self.saas = SaaSService(self.db_path)

    def tearDown(self) -> None:
        self.folder.cleanup()

    @staticmethod
    def registration_payload() -> dict:
        return {
            "company_name": "Pending Company",
            "registration_number": "BIN-PENDING-001",
            "contact_name": "Company Owner",
            "email": "pending-owner@example.com",
            "phone": "+7 700 100 20 30",
            "privacy_consent": True,
            "terms_consent": True,
            "launch_mode": "self_service",
            "template_code": "general",
            "marketplaces": ["kaspi", "ozon_kz"],
        }

    def test_registration_is_pending_has_no_grants_and_rejects_duplicate(self) -> None:
        submission = self.saas.submit_registration_request(self.registration_payload())
        provision = self.saas.provision_tenant_from_request(
            int(submission["request_id"]), None, "pending"
        )
        self.assertEqual("pending", provision["tenant"]["status"])
        access = self.saas.marketplace_access(int(provision["tenant_id"]))
        self.assertEqual(6, len(access))
        self.assertFalse(any(item["is_allowed"] for item in access))
        with self.assertRaisesRegex(ValueError, "уже зарегистрирована"):
            self.saas.submit_registration_request(self.registration_payload())

    def test_company_bin_and_email_are_unique_for_new_database_writes(self) -> None:
        first = self.registration_payload()
        self.saas.submit_registration_request(first)
        same_bin = dict(first, email="another-owner@example.com")
        same_email = dict(first, registration_number="BIN-ANOTHER-002")
        with self.assertRaisesRegex(ValueError, "уже (зарегистрирована|ожидает)"):
            self.saas.submit_registration_request(same_bin)
        with self.assertRaisesRegex(ValueError, "уже (зарегистрирована|ожидает)"):
            self.saas.submit_registration_request(same_email)

        conn = sqlite3.connect(self.db_path)
        stamp = "2026-08-12T12:00:00+05:00"
        try:
            conn.execute(
                """INSERT INTO tenants(name,slug,registration_number,status,contact_email,
                                         created_at,updated_at)
                   VALUES('Identity A','identity-a','UNIQUE-BIN-A','pending',
                          'identity-a@example.com',?,?)""",
                (stamp, stamp),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO tenants(name,slug,registration_number,status,contact_email,
                                             created_at,updated_at)
                       VALUES('Identity B','identity-b','unique-bin-a','pending',
                              'identity-b@example.com',?,?)""",
                    (stamp, stamp),
                )
        finally:
            conn.rollback()
            conn.close()

    def test_pending_company_cannot_receive_or_connect_marketplace(self) -> None:
        submission = self.saas.submit_registration_request(self.registration_payload())
        tenant_id = int(self.saas.provision_tenant_from_request(
            int(submission["request_id"]), None, "pending"
        )["tenant_id"])
        with self.assertRaisesRegex(ValueError, "Сначала подтвердите"):
            self.saas.set_marketplace_access(tenant_id, ["ozon"], int(self.root["id"]))
        with self.assertRaisesRegex(ValueError, "не подтверждена"):
            self.saas.detect_marketplace_url(
                tenant_id, "https://www.ozon.ru/seller/store-100/", "ozon"
            )

    def test_selected_marketplaces_precede_available_marketplaces(self) -> None:
        tenant_id = int(self.root["tenant_id"])
        self.saas.set_marketplace_access(
            tenant_id, ["wildberries"], int(self.root["id"])
        )

        access = self.saas.marketplace_access(tenant_id)

        self.assertEqual("wildberries", access[0]["code"])
        self.assertTrue(access[0]["is_allowed"])
        self.assertTrue(all(not item["is_allowed"] for item in access[1:]))

    def test_all_six_marketplace_urls_are_detected_and_connected(self) -> None:
        tenant_id = int(self.root["tenant_id"])
        self.saas.update_tenant_profile(
            tenant_id,
            {
                "name": "Six Stores", "registration_number": "BIN-SIX-001",
                "contact_email": "six@example.com", "contact_phone": "+7 700 111 22 33",
            },
            int(self.root["id"]),
        )
        urls = {
            "kaspi": "https://kaspi.kz/shop/m/12917020/products?productCode=123271857&masterSku=123271857",
            "ozon": "https://www.ozon.ru/seller/ridial/",
            "ozon_kz": "https://ozon.kz/seller/ridial/",
            "halyk_market": "https://halykmarket.kz/merchant/24955?f=merchantName%3AMechta.kz",
            "forte_market": "https://market.forte.kz/merchant/B8pXMdkk110XZRswXw?productId=c681a9d9-6ef7-11ed-9013-92962dec7f6b&type=all",
            "wildberries": "https://global.wildberries.ru/seller/250000260",
        }
        self.saas.set_marketplace_access(tenant_id, list(urls), int(self.root["id"]))
        for code, url in urls.items():
            checked = self.saas.detect_marketplace_url(tenant_id, url, code)
            self.assertEqual(code, checked["marketplace_code"])
            self.assertTrue(checked["verified"])
            submitted = self.saas.connect_marketplace(
                tenant_id, url, int(self.root["id"]), code
            )
            self.assertFalse(submitted["is_connected"])
            self.assertEqual("pending", submitted["approval_status"])
            reviewed = self.saas.review_marketplace_connection(
                tenant_id, code, "approved", int(self.root["id"])
            )
            self.assertEqual("approved", reviewed["approval_status"])
        halyk = self.saas.detect_marketplace_url(tenant_id, urls["halyk_market"], "halyk_market")
        self.assertEqual("24955", halyk["seller_identifier"])
        self.assertEqual("Mechta.kz", halyk["seller_name"])
        kaspi = self.saas.detect_marketplace_url(tenant_id, urls["kaspi"], "kaspi")
        self.assertEqual("12917020", kaspi["seller_identifier"])
        self.assertEqual("123271857", kaspi["product_id"])
        forte = self.saas.detect_marketplace_url(tenant_id, urls["forte_market"], "forte_market")
        self.assertEqual("seller", forte["source_scope"])
        self.assertEqual("B8pXMdkk110XZRswXw", forte["seller_identifier"])
        self.assertEqual("c681a9d9-6ef7-11ed-9013-92962dec7f6b", forte["product_id"])
        wildberries = self.saas.detect_marketplace_url(
            tenant_id, urls["wildberries"], "wildberries"
        )
        self.assertEqual("250000260", wildberries["seller_identifier"])
        self.assertEqual("https://global.wildberries.ru/seller/250000260", wildberries["seller_url"])
        integrations = {item["integration_code"]: item for item in self.saas.integrations(tenant_id)}
        self.assertEqual("123271857", integrations["kaspi"]["discovery"]["product_id"])
        self.assertEqual(
            "c681a9d9-6ef7-11ed-9013-92962dec7f6b",
            integrations["forte_market"]["discovery"]["product_id"],
        )
        self.assertTrue(all(item["is_connected"] for item in self.saas.marketplace_access(tenant_id)))
        with self.assertRaisesRegex(ValueError, "поддерживаемой площадке"):
            self.saas.detect_marketplace_url(tenant_id, "https://example.com/store/1")
        with self.assertRaises(ValueError):
            self.saas.detect_marketplace_url(
                tenant_id, "http://www.ozon.ru/seller/insecure-1/", "ozon"
            )

    def test_removed_admin_modules_and_company_table_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        platform = (root / "templates" / "platform.html").read_text(encoding="utf-8")
        app_template = (root / "templates" / "app.html").read_text(encoding="utf-8")
        company_admin = (root / "static" / "js" / "platform_company_admin.js").read_text(encoding="utf-8")
        self.assertIn("company-table", platform)
        self.assertIn("tenantTableBody", platform)
        self.assertNotIn("public-product", platform)
        self.assertNotIn("tenantGrid", platform)
        self.assertNotIn("marketplace-access-field", app_template)
        self.assertNotIn("data-user-marketplace", company_admin)
        combined = platform + app_template + company_admin
        self.assertNotIn("Доступ внутри сети", combined)
        self.assertNotIn("Правила сопоставления", combined)
        self.assertNotIn("Merchant", combined)

    def test_new_admin_and_marketplace_ui_keys_exist_in_every_locale(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sources = "\n".join(
            (root / "static" / "js" / name).read_text(encoding="utf-8")
            for name in ("platform.js", "platform_company_admin.js", "marketplace_settings.js")
        )
        used_keys = set(re.findall(r"\bt\('([^']+)'", sources))
        for language in ("ru", "kk", "en"):
            values = json.loads(
                (root / "static" / "locales" / f"{language}.json").read_text(encoding="utf-8")
            )["strings"]
            missing = sorted(key for key in used_keys if key not in values)
            self.assertFalse(missing, f"{language}: missing UI keys: {missing}")
            broken = sorted(key for key, value in values.items() if "??" in str(value))
            self.assertFalse(broken, f"{language}: broken encoding: {broken}")


if __name__ == "__main__":
    unittest.main()
