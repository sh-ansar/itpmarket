from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as webapp
from auth_service import AuthService
from data_service import DataService
from legal_documents import LEGAL_DOCUMENTS, OFFER_ACCEPTANCE_TEXT, PRIVACY_ACCEPTANCE_TEXT
from public_product_service import PublicProductService
from saas_service import SaaSService
from schema import ensure_database


class LegalDocumentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="legal_documents_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.auth.create_initial_admin("platform-owner@example.com", "Platform Owner", "StrongPassword123!")
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
        for patcher in self.patchers:
            patcher.start()
        webapp.FORM_ATTEMPTS.clear()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.folder.cleanup()

    def form(self, email: str = "legal-owner@example.com", bin_value: str = "BIN-LEGAL-001") -> dict[str, str | list[str]]:
        page = self.client.get("/register")
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True))
        self.assertIsNotNone(token)
        return {
            "csrf_token": token.group(1),
            "company_name": "Legal Company",
            "registration_number": bin_value,
            "contact_name": "Legal Owner",
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
            "offer_acceptance": "1",
            "locale": "ru",
        }

    def test_versioned_public_pages_and_pdfs_are_available(self) -> None:
        for document_type in ("offer", "privacy"):
            page = self.client.get(f"/legal/{document_type}/1.0")
            self.assertEqual(200, page.status_code)
            self.assertIn("Версия 1.0", page.get_data(as_text=True))
            pdf = self.client.get(f"/legal/{document_type}/1.0.pdf")
            self.assertEqual(200, pdf.status_code)
            self.assertEqual("application/pdf", pdf.mimetype)
            self.assertNotIn("attachment", pdf.headers.get("Content-Disposition", ""))
            self.assertTrue(pdf.data.startswith(b"%PDF"))
            pdf.close()
            stable_pdf = self.client.get(f"/legal/{document_type}/pdf")
            self.assertEqual(200, stable_pdf.status_code)
            self.assertEqual("application/pdf", stable_pdf.mimetype)
            self.assertTrue(stable_pdf.data.startswith(b"%PDF"))
            stable_pdf.close()
            download = self.client.get(f"/legal/{document_type}/pdf?download=1")
            self.assertTrue(download.headers["Content-Disposition"].startswith("attachment;"))
            download.close()
        self.assertEqual(404, self.client.get("/legal/offer/../../app.py").status_code)

    def test_registration_requires_each_document_and_preserves_final_step(self) -> None:
        without_offer = self.form("without-offer@example.com", "BIN-NO-OFFER")
        without_offer.pop("offer_acceptance")
        response = self.client.post("/register", data=without_offer)
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("offer_acceptance", html)
        self.assertIn('data-step-code="account"', html)
        self.assertIsNone(self.auth.get_user_by_email("without-offer@example.com"))

        without_privacy = self.form("without-privacy@example.com", "BIN-NO-PRIVACY")
        without_privacy.pop("privacy_consent")
        response = self.client.post("/register", data=without_privacy)
        self.assertEqual(200, response.status_code)
        self.assertIsNone(self.auth.get_user_by_email("without-privacy@example.com"))

    def test_registration_persists_exact_server_side_acceptances(self) -> None:
        response = self.client.post(
            "/register", data=self.form(), headers={"User-Agent": "Spyon Legal Test Agent"},
            environ_base={"REMOTE_ADDR": "198.51.100.42"},
        )
        self.assertEqual(200, response.status_code)
        user = self.auth.get_user_by_email("legal-owner@example.com")
        self.assertIsNotNone(user)
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT document_type,document_number,document_version,document_sha256,
                          ip_address,user_agent,locale,acceptance_text,source,user_id,tenant_id
                   FROM legal_acceptances ORDER BY document_type"""
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(2, len(rows))
        expected = {
            "offer": ("SPYON-OF-001", OFFER_ACCEPTANCE_TEXT),
            "privacy": ("SPYON-PD-001", PRIVACY_ACCEPTANCE_TEXT),
        }
        for row in rows:
            definition = LEGAL_DOCUMENTS.get(row[0], "1.0")
            self.assertEqual(expected[row[0]][0], row[1])
            self.assertEqual("1.0", row[2])
            self.assertEqual(LEGAL_DOCUMENTS.sha256(definition), row[3])
            self.assertEqual("198.51.100.42", row[4])
            self.assertEqual("Spyon Legal Test Agent", row[5])
            self.assertEqual("ru", row[6])
            self.assertEqual(expected[row[0]][1], row[7])
            self.assertEqual("registration", row[8])
            self.assertEqual(int(user["id"]), row[9])
            self.assertEqual(int(user["tenant_id"]), row[10])
        settings = self.client.get("/api/settings")
        self.assertEqual(200, settings.status_code)
        documents = settings.get_json()["legal_documents"]
        self.assertEqual({"offer", "privacy"}, {item["type"] for item in documents})
        self.assertTrue(all(item["accepted_at"] for item in documents))

    def test_user_creation_failure_rolls_back_legal_acceptances(self) -> None:
        with patch.object(self.auth, "create_user", side_effect=ValueError("forced failure")):
            response = self.client.post("/register", data=self.form("rollback@example.com", "BIN-ROLLBACK"))
        self.assertEqual(200, response.status_code)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM legal_acceptances").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM tenants WHERE registration_number='BIN-ROLLBACK'").fetchone()[0])
        finally:
            conn.close()

    def test_registration_markup_has_modal_and_download_links(self) -> None:
        html = self.client.get("/register").get_data(as_text=True)
        self.assertIn('data-legal-modal="offer"', html)
        self.assertIn('data-legal-modal="privacy"', html)
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "registration_wizard.js").read_text(encoding="utf-8")
        self.assertIn("legalModalPdf", script)
        self.assertIn("legal-modal-viewer", script)
        self.assertNotIn("fetch(\"/legal/", script)
        self.assertIn("event.stopPropagation()", script)
        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "registration_guide.css").read_text(encoding="utf-8")
        self.assertIn(".legal-modal[hidden]", css)
        self.assertIn("display: none !important", css)


if __name__ == "__main__":
    unittest.main()
