from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from billing_service import BillingService
from schema import ensure_database
from subscription_service import (
    SubscriptionError,
    SubscriptionService,
)


class FakeInvoicePDFService:
    def __init__(
        self,
        document_root: Path,
    ) -> None:
        self.document_root = Path(document_root)
        self.calls = 0

    def generate(
        self,
        payload: dict,
    ) -> dict:
        self.calls += 1

        invoice_number = str(
            payload["invoice_number"]
        )

        issued_at = str(
            payload.get("issued_at")
            or ""
        )

        year = (
            issued_at[:4]
            if len(issued_at) >= 4
            else "unknown"
        )

        file_path = (
            self.document_root
            / "output"
            / "invoices"
            / year
            / f"{invoice_number}.pdf"
        )

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        content = (
            b"%PDF-1.4\n"
            b"% billing integration test\n"
            + invoice_number.encode("utf-8")
            + b"\n%%EOF\n"
        )

        file_path.write_bytes(content)

        return {
            "path": str(file_path),
            "sha256": "",
            "size": len(content),
        }


class BillingPDFIntegrationTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="billing_pdf_"
            )
        )

        self.document_root = Path(
            self.folder.name
        )

        self.db_path = (
            self.document_root
            / "app.db"
        )

        ensure_database(
            self.db_path
        )

        self.auth = AuthService(
            self.db_path
        )

        self.admin, _ = (
            self.auth.create_initial_admin(
                "root@example.com",
                "Root",
                "StrongPassword123!",
            )
        )

        self.tenant_id = int(
            self.admin["tenant_id"]
        )

        self.subscriptions = (
            SubscriptionService(
                self.db_path
            )
        )

        self.fake_pdf = (
            FakeInvoicePDFService(
                self.document_root
            )
        )

        self.billing = BillingService(
            self.db_path,
            document_root=self.document_root,
            invoice_pdf_service=self.fake_pdf,
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _create_invoice(
        self,
        months: int = 3,
    ) -> dict:
        requested = (
            self.subscriptions.request_plan(
                self.tenant_id,
                "starter",
                int(self.admin["id"]),
            )
        )

        reviewed = (
            self.subscriptions.review_subscription(
                int(requested["id"]),
                "approved",
                int(self.admin["id"]),
            )
        )

        self.assertEqual(
            "awaiting_invoice",
            reviewed["status"],
        )

        return self.billing.create_invoice(
            int(reviewed["id"]),
            months,
            int(self.admin["id"]),
            seller_snapshot={
                "name": "Test Supplier",
                "vat_rate": 0,
            },
        )

    def test_generate_pdf_persists_metadata(
        self,
    ) -> None:
        invoice = self._create_invoice()

        generated = (
            self.billing.generate_invoice_pdf(
                int(invoice["id"])
            )
        )

        self.assertEqual(
            1,
            self.fake_pdf.calls,
        )

        self.assertTrue(
            generated["path"].is_file()
        )

        self.assertTrue(
            generated[
                "relative_path"
            ].startswith(
                "output/invoices/"
            )
        )

        self.assertFalse(
            Path(
                generated[
                    "relative_path"
                ]
            ).is_absolute()
        )

        stored = (
            self.billing.invoice_by_id(
                int(invoice["id"])
            )
        )

        self.assertIsNotNone(stored)

        self.assertEqual(
            generated["relative_path"],
            stored["pdf_path"],
        )

        self.assertEqual(
            generated["sha256"],
            stored["pdf_sha256"],
        )

        self.assertTrue(
            stored["pdf_sha256"]
        )

    def test_generate_pdf_is_idempotent(
        self,
    ) -> None:
        invoice = self._create_invoice()

        first = (
            self.billing.generate_invoice_pdf(
                int(invoice["id"])
            )
        )

        second = (
            self.billing.generate_invoice_pdf(
                int(invoice["id"])
            )
        )

        self.assertEqual(
            1,
            self.fake_pdf.calls,
        )

        self.assertEqual(
            first["sha256"],
            second["sha256"],
        )

        self.assertEqual(
            first["relative_path"],
            second["relative_path"],
        )

    def test_invoice_pdf_reads_saved_file(
        self,
    ) -> None:
        invoice = self._create_invoice()

        generated = (
            self.billing.generate_invoice_pdf(
                int(invoice["id"])
            )
        )

        loaded = self.billing.invoice_pdf(
            int(invoice["id"])
        )

        self.assertEqual(
            1,
            self.fake_pdf.calls,
        )

        self.assertEqual(
            generated["sha256"],
            loaded["sha256"],
        )

        self.assertEqual(
            generated["path"],
            loaded["path"],
        )

    def test_invoice_pdf_detects_tampering(
        self,
    ) -> None:
        invoice = self._create_invoice()

        generated = (
            self.billing.generate_invoice_pdf(
                int(invoice["id"])
            )
        )

        generated["path"].write_bytes(
            b"%PDF-1.4\n"
            b"TAMPERED\n"
            b"%%EOF\n"
        )

        with self.assertRaises(
            SubscriptionError
        ):
            self.billing.invoice_pdf(
                int(invoice["id"])
            )

        self.assertEqual(
            1,
            self.fake_pdf.calls,
        )

    def test_missing_pdf_is_not_regenerated(
        self,
    ) -> None:
        invoice = self._create_invoice()

        generated = (
            self.billing.generate_invoice_pdf(
                int(invoice["id"])
            )
        )

        generated["path"].unlink()

        with self.assertRaises(
            SubscriptionError
        ):
            self.billing.generate_invoice_pdf(
                int(invoice["id"])
            )

        self.assertEqual(
            1,
            self.fake_pdf.calls,
        )


if __name__ == "__main__":
    unittest.main()
