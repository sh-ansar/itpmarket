from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import app as webapp
from auth_service import AuthService
from data_service import DataService
from legal_documents import LEGAL_DOCUMENTS, OFFER_ACCEPTANCE_TEXT, PRIVACY_ACCEPTANCE_TEXT
from public_product_service import PublicProductService
from saas_service import SaaSService
from schema import ensure_database


class LegalDocumentsTests(unittest.TestCase):
    CURRENT_DOCUMENTS = (
        ("offer", "offer", "Публичная оферта SPYON"),
        ("tariff-policy", "tariff_policy", "Тарифная политика и условия обслуживания SPYON"),
        ("acceptable-use", "acceptable_use", "Правила допустимого использования SPYON"),
        ("personal-data-consent", "personal_data_consent", "Согласие на сбор и обработку персональных данных"),
        ("privacy", "privacy", "Политика конфиденциальности SPYON"),
    )

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="legal_documents_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.platform_admin, _ = self.auth.create_initial_admin(
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
        for slug, document_type, title in self.CURRENT_DOCUMENTS:
            definition = LEGAL_DOCUMENTS.get(document_type, "04.09.2026")
            self.assertIsNotNone(definition)
            self.assertTrue(definition.source_path.is_file())
            self.assertTrue(definition.pdf_path.is_file())

            page = self.client.get(f"/legal/{slug}/04.09.2026")
            self.assertEqual(200, page.status_code)
            self.assertEqual("text/html", page.mimetype)
            html = page.get_data(as_text=True)
            self.assertIn("Редакция 04.09.2026", html)
            self.assertIn(title, html)

            current_page = self.client.get(f"/legal/{slug}")
            self.assertEqual(200, current_page.status_code)
            self.assertEqual("text/html", current_page.mimetype)

            pdf = self.client.get(f"/legal/{slug}/04.09.2026.pdf")
            self.assertEqual(200, pdf.status_code)
            self.assertEqual("application/pdf", pdf.mimetype)
            self.assertNotIn("attachment", pdf.headers.get("Content-Disposition", ""))
            self.assertTrue(pdf.data.startswith(b"%PDF"))
            pdf.close()
            stable_pdf = self.client.get(f"/legal/{slug}/pdf")
            self.assertEqual(200, stable_pdf.status_code)
            self.assertEqual("application/pdf", stable_pdf.mimetype)
            self.assertTrue(stable_pdf.data.startswith(b"%PDF"))

            self.assertEqual(
                "SAMEORIGIN",
                stable_pdf.headers.get(
                    "X-Frame-Options"
                ),
            )

            self.assertIn(
                "frame-ancestors 'self'",
                stable_pdf.headers.get(
                    "Content-Security-Policy",
                    "",
                ),
            )

            stable_pdf.close()
            download = self.client.get(f"/legal/{slug}/pdf?download=1")
            self.assertTrue(download.headers["Content-Disposition"].startswith("attachment;"))
            download.close()
        self.assertEqual(404, self.client.get("/legal/offer/../../app.py").status_code)

    def test_historical_unpublished_lifecycle_types_have_no_public_page(self) -> None:
        for document_type in ("terms", "cookies"):
            self.assertEqual(404, self.client.get(f"/legal/{document_type}").status_code)
            self.assertEqual(404, self.client.get(f"/legal/{document_type}/pdf").status_code)

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
        self.assertEqual(5, len(rows))
        expected = {
            "offer": ("SPYON-OF-001", OFFER_ACCEPTANCE_TEXT),
            "tariff_policy": ("SPYON-TP-001", OFFER_ACCEPTANCE_TEXT),
            "acceptable_use": ("SPYON-AU-001", OFFER_ACCEPTANCE_TEXT),
            "personal_data_consent": ("SPYON-PC-001", PRIVACY_ACCEPTANCE_TEXT),
            "privacy": ("SPYON-PR-001", PRIVACY_ACCEPTANCE_TEXT),
        }
        for row in rows:
            definition = LEGAL_DOCUMENTS.get(row[0], "04.09.2026")
            self.assertEqual(expected[row[0]][0], row[1])
            self.assertEqual("04.09.2026", row[2])
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
        self.assertEqual(
            {item[1] for item in self.CURRENT_DOCUMENTS},
            {item["type"] for item in documents},
        )
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

    def test_registration_has_two_required_checkbox_groups_linked_to_pdfs(self) -> None:
        html = self.client.get("/register").get_data(as_text=True)
        required_legal = re.findall(
            r'<input\s+[^>]*name="(offer_acceptance|privacy_consent)"[^>]*required',
            html,
            flags=re.DOTALL,
        )
        self.assertCountEqual(["offer_acceptance", "privacy_consent"], required_legal)
        for slug, _document_type, _title in self.CURRENT_DOCUMENTS:
            self.assertIn(f'href="/legal/{slug}/pdf"', html)
        self.assertEqual(5, html.count('class="legal-inline-link"'))

    def test_landing_and_platform_use_html_and_pdf_routes_respectively(self) -> None:
        landing = self.client.get("/").get_data(as_text=True)
        for slug, _document_type, _title in self.CURRENT_DOCUMENTS:
            self.assertIn(f'href="/legal/{slug}"', landing)
        self.assertNotIn('data-legal-link href="/legal/offer/pdf"', landing)

        with self.client.session_transaction() as session:
            session["user_id"] = int(self.platform_admin["id"])
        platform = self.client.get("/platform/legal-documents")
        self.assertEqual(200, platform.status_code)
        platform_html = platform.get_data(as_text=True)
        for _slug, _document_type, title in self.CURRENT_DOCUMENTS:
            self.assertIn(title, platform_html)
        payload = self.client.get("/api/platform/legal-documents").get_json()
        current = [
            item for item in payload["items"]
            if item["version"] == "04.09.2026"
        ]
        self.assertEqual(
            {item[1] for item in self.CURRENT_DOCUMENTS},
            {item["type"] for item in current},
        )
        self.assertTrue(all(item["status"] == "published" for item in current))
        self.assertTrue(all(item["pdf_available"] for item in current))
        platform_script = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "platform.js"
        ).read_text(encoding="utf-8")
        self.assertIn("item.slug||item.type", platform_script)
        self.assertIn("${encodeURIComponent(item.version)}.pdf", platform_script)

    @staticmethod
    def _document_text(path: Path) -> str:
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        with ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find("w:body", namespace)
        assert body is not None
        parts = []
        for node in body:
            tag = node.tag.rsplit("}", 1)[-1]
            if tag not in {"p", "tbl"}:
                continue
            text = "".join(value.text or "" for value in node.findall(".//w:t", namespace)).strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    def test_current_docx_content_is_split_from_approved_sources_without_loss(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offer_source = self._document_text(
            root / "docs" / "legal" / "incoming" / "SPYON_Public_Offer_KZ_2026.docx"
        )
        tariff_source = self._document_text(
            root / "docs" / "legal" / "incoming" / "Tariff_Policy_SPYON.docx"
        )
        current = root / "docs" / "legal" / "current"

        public_offer = self._document_text(current / "public-offer.docx")
        for section in range(1, 27):
            self.assertRegex(public_offer, rf"(?m)^{section}\. ")
        for appendix in range(1, 5):
            self.assertNotIn(f"ПРИЛОЖЕНИЕ {appendix}", public_offer.upper())

        source_acceptable = offer_source.split("ПРАВИЛА ДОПУСТИМОГО ИСПОЛЬЗОВАНИЯ", 1)[1]
        source_acceptable = source_acceptable.split("ПРИЛОЖЕНИЕ 3.", 1)[0]
        final_acceptable = self._document_text(current / "acceptable-use.docx")
        self.assertEqual(
            ("ПРАВИЛА ДОПУСТИМОГО ИСПОЛЬЗОВАНИЯ" + source_acceptable).strip(),
            final_acceptable.strip(),
        )

        source_consent = offer_source.split("СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ", 1)[1]
        source_consent = source_consent.split("ПРИЛОЖЕНИЕ 4.", 1)[0]
        final_consent = self._document_text(current / "personal-data-consent.docx")
        self.assertEqual(
            ("СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ" + source_consent).strip(),
            final_consent.strip(),
        )

        source_privacy = offer_source.split(
            "ПОЛИТИКА КОНФИДЕНЦИАЛЬНОСТИ ИНТЕРНЕТ-СЕРВИСА SPYON", 1
        )[1]
        final_privacy = self._document_text(current / "privacy-policy.docx")
        self.assertEqual(
            ("ПОЛИТИКА КОНФИДЕНЦИАЛЬНОСТИ ИНТЕРНЕТ-СЕРВИСА SPYON" + source_privacy).strip(),
            final_privacy.strip(),
        )

        expected_tariff = tariff_source.replace(
            "Правил допустимого использования (Приложение №2 к Оферте)",
            "Правил допустимого использования SPYON",
        )
        self.assertEqual(
            expected_tariff.strip(),
            self._document_text(current / "tariff-policy.docx").strip(),
        )

        for path in (*current.glob("*.docx"), *(root / "static" / "legal" / "current").glob("*.pdf")):
            content = path.read_bytes()
            self.assertNotIn(b"??????", content)
            self.assertNotIn("�", content.decode("utf-8", errors="ignore"))


if __name__ == "__main__":
    unittest.main()
