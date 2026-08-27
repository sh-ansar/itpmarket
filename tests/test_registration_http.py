from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as webapp
from auth_service import AuthService
from data_service import DataService
from public_product_service import PublicProductService
from saas_service import SaaSService
from schema import ensure_database


class RegistrationHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="registration_http_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.auth.create_initial_admin(
            "platform-owner@example.com", "Platform Owner", "StrongPassword123!"
        )
        self.saas = SaaSService(self.db_path)
        self.data = DataService(self.db_path, "Test Seller")
        self.public = PublicProductService(self.db_path)
        self.patchers = [
            patch.object(webapp, "AUTH", self.auth),
            patch.object(webapp, "SAAS", self.saas),
            patch.object(webapp, "DATA", self.data),
            patch.object(webapp, "PUBLIC", self.public),
            patch.object(webapp, "DB_PATH", self.db_path),
        ]
        for item in self.patchers:
            item.start()
        webapp.FORM_ATTEMPTS.clear()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()

    def tearDown(self) -> None:
        for item in reversed(self.patchers):
            item.stop()
        self.folder.cleanup()

    def test_registration_creates_approved_company_owner_without_marketplace_grants(self) -> None:
        page = self.client.get("/register")
        self.assertEqual(200, page.status_code)
        html = page.get_data(as_text=True)
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        self.assertIsNotNone(token)
        self.assertIn('name="registration_number"', html)
        self.assertNotIn('name="launch_mode"', html)

        response = self.client.post(
            "/register",
            data={
                "csrf_token": token.group(1),
                "company_name": "HTTP Company",
                "registration_number": "BIN-HTTP-001",
                "contact_name": "HTTP Owner",
                "email": "http-owner@example.com",
                "phone_country_code": "+7",
                "phone": "700 123 45 67",
                "legal_address": "Astana, Legal Street 10",
                "actual_address": "Astana, Office Street 20",
                "estimated_products": "250",
                "plan_code": "growth",
                "marketplaces": ["kaspi", "ozon_kz"],
                "password": "SafeVaultNumber927!",
                "password_confirm": "SafeVaultNumber927!",
                "privacy_consent": "1",
                "terms_consent": "1",
                "locale": "ru",
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("http-owner@example.com", response.get_data(as_text=True))

        owner = self.auth.get_user_by_email("http-owner@example.com")
        self.assertIsNotNone(owner)
        tenant_id = int(owner["tenant_id"])
        conn = sqlite3.connect(self.db_path)
        try:
            tenant = conn.execute(
                "SELECT status,registration_number FROM tenants WHERE id=?", (tenant_id,)
            ).fetchone()
            request_row = conn.execute(
                "SELECT status,tenant_id FROM registration_requests WHERE email=?",
                ("http-owner@example.com",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(("approved", "BIN-HTTP-001"), tenant)
        self.assertEqual(("approved", tenant_id), request_row)
        self.assertFalse(any(item["is_allowed"] for item in self.saas.marketplace_access(tenant_id)))

    def test_public_registration_runtime_has_no_manual_approval_copy(self) -> None:
        runtime = (
            Path(__file__).resolve().parents[1]
            / "static" / "js" / "public_i18n_runtime.js"
        ).read_text(encoding="utf-8-sig").casefold()
        self.assertNotIn("после подтверждения супер-администратором", runtime)
        self.assertNotIn("super-administrator approval", runtime)
        self.assertIn("доступ откроется после подтверждения оплаты", runtime)

    def test_registration_rejects_missing_required_commercial_fields(self) -> None:
        def csrf_token() -> str:
            page = self.client.get("/register")
            self.assertEqual(200, page.status_code)
            match = re.search(
                r'name="csrf_token"[^>]*value="([^"]+)"',
                page.get_data(as_text=True),
            )
            self.assertIsNotNone(match)
            return match.group(1)

        def valid_form(email: str, registration_number: str) -> dict:
            return {
                "csrf_token": csrf_token(),
                "company_name": "Validation Company",
                "registration_number": registration_number,
                "contact_name": "Validation Owner",
                "email": email,
                "phone_country_code": "+7",
                "phone": "700 123 45 67",
                "legal_address": "Astana, Legal Street 10",
                "actual_address": "Astana, Office Street 20",
                "estimated_products": "250",
                "plan_code": "growth",
                "marketplaces": ["kaspi"],
                "password": "SafeVaultNumber927!",
                "password_confirm": "SafeVaultNumber927!",
                "privacy_consent": "1",
                "terms_consent": "1",
                "locale": "ru",
            }

        missing_plan = valid_form(
            "missing-plan@example.com",
            "BIN-MISSING-PLAN",
        )
        missing_plan.pop("plan_code")

        response = self.client.post(
            "/register",
            data=missing_plan,
        )

        self.assertEqual(400, response.status_code)
        self.assertIn(
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u0430\u043a\u0435\u0442",
            response.get_data(as_text=True),
        )
        self.assertIsNone(
            self.auth.get_user_by_email(
                "missing-plan@example.com"
            )
        )

        invalid_plan = valid_form(
            "invalid-plan@example.com",
            "BIN-INVALID-PLAN",
        )
        invalid_plan["plan_code"] = "does-not-exist"

        response = self.client.post(
            "/register",
            data=invalid_plan,
        )

        self.assertEqual(400, response.status_code)
        self.assertIn(
            "\u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d",
            response.get_data(as_text=True),
        )
        self.assertIsNone(
            self.auth.get_user_by_email(
                "invalid-plan@example.com"
            )
        )

        missing_country = valid_form(
            "missing-country@example.com",
            "BIN-MISSING-COUNTRY",
        )
        missing_country.pop("phone_country_code")

        response = self.client.post(
            "/register",
            data=missing_country,
        )

        self.assertEqual(400, response.status_code)
        self.assertIn(
            "\u043a\u043e\u0434 \u0441\u0442\u0440\u0430\u043d\u044b",
            response.get_data(as_text=True),
        )
        self.assertIsNone(
            self.auth.get_user_by_email(
                "missing-country@example.com"
            )
        )

        zero_products = valid_form(
            "zero-products@example.com",
            "BIN-ZERO-PRODUCTS",
        )
        zero_products["estimated_products"] = "0"

        response = self.client.post(
            "/register",
            data=zero_products,
        )

        self.assertEqual(200, response.status_code)
        self.assertIn(
            "\u043d\u0435 \u043c\u0435\u043d\u044c\u0448\u0435 1",
            response.get_data(as_text=True),
        )
        self.assertIsNone(
            self.auth.get_user_by_email(
                "zero-products@example.com"
            )
        )

    def test_registration_public_bundle_has_complete_utf8_translations(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "public_i18n.js"
        ).read_text(encoding="utf-8")
        prefix = "window.ITP_PUBLIC_LOCALES="
        self.assertTrue(source.startswith(prefix))
        payload = source[len(prefix):]
        translations, parsed_end = json.JSONDecoder().raw_decode(payload)
        self.assertTrue(
            payload[parsed_end:].lstrip().startswith(";")
        )
        keys = (
            "register_title", "register_intro", "register_submit",
            "company_registration_number", "company_contact_name",
            "phone_country_code", "phone_number",
            "company_legal_address", "company_actual_address",
        )
        for language in ("ru", "kk", "en"):
            for key in keys:
                value = translations[language].get(key, "")
                self.assertTrue(value, f"{language}: missing {key}")
                self.assertNotIn("?", value, f"{language}: broken encoding in {key}")


if __name__ == "__main__":
    unittest.main()
