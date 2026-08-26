from __future__ import annotations

from PIL import Image
import hashlib
import tempfile
import unittest
from pathlib import Path

from invoice_pdf_service import (
    InvoicePDFError,
    InvoicePDFService,
    amount_in_words,
    format_money,
)


class InvoicePDFServiceTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="invoice_pdf_"
            )
        )

        self.output_dir = Path(
            self.folder.name
        )

        # Tests must not depend on the real company stamp/logo.
        # A clean CI clone does not contain data/billing-assets.
        self.logo_path = (
            self.output_dir
            / "test-logo.png"
        )

        self.stamp_path = (
            self.output_dir
            / "test-stamp.png"
        )

        logo = Image.new(
            "RGB",
            (120, 40),
            (255, 255, 255),
        )
        logo.save(
            self.logo_path,
            format="PNG",
        )

        stamp = Image.new(
            "RGBA",
            (120, 120),
            (255, 255, 255, 0),
        )
        stamp.save(
            self.stamp_path,
            format="PNG",
        )

        self.service = (
            InvoicePDFService(
                self.output_dir,
                logo_path=self.logo_path,
                stamp_path=self.stamp_path,
            )
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    @staticmethod
    def invoice() -> dict:
        return {
            "id": 62,
            "invoice_number":
                "SPY-2026-000062",
            "status": "issued",
            "months_count": 3,
            "unit_price": 14900,
            "subtotal_amount":
                38534.48,
            "vat_rate": 16,
            "vat_amount": 6165.52,
            "total_amount": 44700,
            "currency": "KZT",
            "issued_at":
                "2026-08-25T12:00:00+05:00",
            "due_at":
                "2026-09-01T12:00:00+05:00",
            "seller_snapshot": {
                "name":
                    "\u0422\u041e\u041e ITP Mining",
                "registration_number":
                    "161240002661",
                "legal_address":
                    "\u0433. \u0410\u0441\u0442\u0430\u043d\u0430",
                "iban":
                    "KZ20722S000001855383",
                "bank_name":
                    "AO Kaspi Bank",
                "bic":
                    "CASPKZKA",
                "kbe":
                    "17",
                "payment_purpose_code":
                    "851",
                "service_name":
                    (
                        "\u0410\u0431\u043e\u043d\u0435\u043d\u0442\u0441\u043a\u0430\u044f "
                        "\u043f\u043b\u0430\u0442\u0430 "
                        "\u043f\u043e "
                        "\u0441\u043e\u043f\u0440\u043e\u0432\u043e\u0436\u0434\u0435\u043d\u0438\u044e "
                        "\u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u043d\u043e\u0433\u043e "
                        "\u043f\u0440\u043e\u0434\u0443\u043a\u0442\u0430"
                    ),
                "agreement_basis":
                    "\u0414\u043e\u0433\u043e\u0432\u043e\u0440",
                "executor_name":
                    "\u0418\u0432\u0430\u043d\u043e\u0432 \u0418.\u0418.",
            },
            "buyer_snapshot": {
                "name":
                    "\u0422\u041e\u041e Test Company",
                "registration_number":
                    "123456789012",
                "legal_address":
                    "\u0433. \u0410\u043b\u043c\u0430\u0442\u044b",
                "contact_email":
                    "finance@example.com",
                "contact_phone":
                    "+77000000000",
            },
            "line_items": [
                {
                    "service_code":
                        "subscription",
                    "plan_code":
                        "starter",
                    "plan_name":
                        "Starter",
                    "quantity": 3,
                    "unit_code":
                        "month",
                    "unit_label":
                        "\u043c\u0435\u0441.",
                    "unit_price":
                        14900,
                    "amount":
                        44700,
                }
            ],
        }

    def test_money_format_and_words(self) -> None:
        self.assertEqual(
            "44 700,00",
            format_money(
                44700
            ),
        )

        self.assertEqual(
            (
                "\u0421\u043e\u0440\u043e\u043a "
                "\u0447\u0435\u0442\u044b\u0440\u0435 "
                "\u0442\u044b\u0441\u044f\u0447\u0438 "
                "\u0441\u0435\u043c\u044c\u0441\u043e\u0442 "
                "\u0442\u0435\u043d\u0433\u0435 "
                "00 "
                "\u0442\u0438\u044b\u043d"
            ),
            amount_in_words(
                44700
            ),
        )

        self.assertEqual(
            (
                "\u041e\u0434\u043d\u0430 "
                "\u0442\u044b\u0441\u044f\u0447\u0430 "
                "\u0434\u0432\u0430 "
                "\u0442\u0435\u043d\u0433\u0435 "
                "05 "
                "\u0442\u0438\u044b\u043d"
            ),
            amount_in_words(
                "1002.05"
            ),
        )

    def test_generate_pdf_and_sha256(self) -> None:
        invoice = self.invoice()

        result = self.service.generate(
            invoice
        )

        path = Path(
            result["path"]
        )

        self.assertTrue(
            path.is_file()
        )

        content = path.read_bytes()

        self.assertTrue(
            content.startswith(
                b"%PDF-"
            )
        )

        self.assertIn(
            b"%%EOF",
            content[-64:],
        )

        self.assertGreater(
            len(content),
            3000,
        )

        self.assertEqual(
            hashlib.sha256(
                content
            ).hexdigest(),
            result["sha256"],
        )

        self.assertEqual(
            "SPY-2026-000062.pdf",
            path.name,
        )

    def test_same_snapshot_is_deterministic(self) -> None:
        invoice = self.invoice()

        first = self.service.generate(
            invoice
        )

        first_content = Path(
            first["path"]
        ).read_bytes()

        second = self.service.generate(
            invoice
        )

        second_content = Path(
            second["path"]
        ).read_bytes()

        self.assertEqual(
            first["sha256"],
            second["sha256"],
        )

        self.assertEqual(
            first_content,
            second_content,
        )

    def test_invalid_invoice_is_rejected(self) -> None:
        invoice = self.invoice()

        invoice[
            "seller_snapshot"
        ] = {}

        with self.assertRaises(
            InvoicePDFError
        ):
            self.service.generate(
                invoice
            )

        invoice = self.invoice()

        invoice[
            "buyer_snapshot"
        ]["name"] = ""

        with self.assertRaises(
            InvoicePDFError
        ):
            self.service.generate(
                invoice
            )

    def test_invoice_number_cannot_escape_output_dir(self) -> None:
        invoice = self.invoice()

        invoice[
            "invoice_number"
        ] = "../SPY/2026:62"

        result = self.service.generate(
            invoice
        )

        path = Path(
            result["path"]
        )

        self.assertEqual(
            self.output_dir.resolve(),
            path.parent.resolve(),
        )

        self.assertNotIn(
            "/",
            path.name,
        )

        self.assertNotIn(
            "\\",
            path.name,
        )


if __name__ == "__main__":
    unittest.main()
