from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as webapp
from auth_service import AuthService
from billing_service import BillingService
from schema import ensure_database
from subscription_service import (
    SubscriptionService,
)


class BillingPaymentProofHttpTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="billing_proof_http_"
            )
        )

        self.root = Path(
            self.folder.name
        )

        self.db_path = (
            self.root
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

        self.billing = BillingService(
            self.db_path,
            document_root=self.root,
        )

        self.patchers = [
            patch.object(
                webapp,
                "AUTH",
                self.auth,
            ),
            patch.object(
                webapp,
                "DB_PATH",
                self.db_path,
            ),
            patch.object(
                webapp,
                "SUBSCRIPTIONS",
                self.subscriptions,
            ),
            patch.object(
                webapp,
                "BILLING",
                self.billing,
            ),
        ]

        for item in self.patchers:
            item.start()

        webapp.app.config.update(
            TESTING=True
        )

        self.client = (
            webapp.app.test_client()
        )

        self.csrf = (
            "billing-proof-http-csrf"
        )

        with self.client.session_transaction() as session:
            session["user_id"] = int(
                self.admin["id"]
            )

            session["session_version"] = int(
                self.admin.get(
                    "session_version"
                )
                or 0
            )

            session[
                "csrf_token"
            ] = self.csrf

    def tearDown(self) -> None:
        for item in reversed(
            self.patchers
        ):
            item.stop()

        self.folder.cleanup()

    @staticmethod
    def _pdf() -> bytes:
        return (
            b"%PDF-1.4\n"
            b"HTTP payment proof\n"
            b"%%EOF\n"
        )

    def _invoice(self) -> dict:
        requested = (
            self.subscriptions
            .request_plan(
                self.tenant_id,
                "starter",
                int(self.admin["id"]),
            )
        )

        reviewed = (
            self.subscriptions
            .review_subscription(
                int(requested["id"]),
                "approved",
                int(self.admin["id"]),
            )
        )

        return (
            self.billing
            .create_invoice(
                int(reviewed["id"]),
                3,
                int(self.admin["id"]),
                seller_snapshot={
                    "name":
                        "HTTP Test Supplier",
                    "vat_rate":
                        0,
                },
            )
        )

    def _upload(
        self,
        invoice_id: int,
        *,
        filename: str = "payment.pdf",
        mime_type: str = "application/pdf",
        content: bytes | None = None,
    ):
        return self.client.post(
            (
                "/api/subscription/invoice/"
                f"{int(invoice_id)}"
                "/payment-proof"
            ),
            data={
                "file": (
                    io.BytesIO(
                        content
                        if content is not None
                        else self._pdf()
                    ),
                    filename,
                    mime_type,
                )
            },
            headers={
                "X-CSRF-Token":
                    self.csrf,
            },
        )

    def test_upload_returns_public_proof_snapshot(
        self,
    ) -> None:
        invoice = self._invoice()

        response = self._upload(
            int(invoice["id"])
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        payload = response.get_json()

        self.assertTrue(
            payload["ok"]
        )

        billing = payload[
            "billing"
        ]

        self.assertEqual(
            "payment_review",
            billing[
                "subscription"
            ][
                "status"
            ],
        )

        proof = billing[
            "payment_proof"
        ]

        self.assertEqual(
            "under_review",
            proof["status"],
        )

        self.assertEqual(
            "payment.pdf",
            proof[
                "original_filename"
            ],
        )

        self.assertTrue(
            proof[
                "download_ready"
            ]
        )

        self.assertNotIn(
            "stored_path",
            proof,
        )

        self.assertNotIn(
            "sha256",
            proof,
        )

    def test_upload_requires_file(
        self,
    ) -> None:
        invoice = self._invoice()

        response = self.client.post(
            (
                "/api/subscription/invoice/"
                f"{int(invoice['id'])}"
                "/payment-proof"
            ),
            data={},
            headers={
                "X-CSRF-Token":
                    self.csrf,
            },
        )

        self.assertEqual(
            400,
            response.status_code,
        )

        self.assertFalse(
            response.get_json()["ok"]
        )

    def test_upload_rejects_invalid_file(
        self,
    ) -> None:
        invoice = self._invoice()

        response = self._upload(
            int(invoice["id"]),
            filename="payment.png",
            mime_type="image/png",
            content=b"not-a-real-png",
        )

        self.assertEqual(
            409,
            response.status_code,
        )

        snapshot = (
            self.billing
            .tenant_billing_snapshot(
                self.tenant_id
            )
        )

        self.assertEqual(
            "awaiting_payment",
            snapshot[
                "subscription"
            ][
                "status"
            ],
        )

        self.assertIsNone(
            snapshot[
                "payment_proof"
            ]
        )

    def test_payment_proof_can_be_downloaded(
        self,
    ) -> None:
        invoice = self._invoice()

        upload = self._upload(
            int(invoice["id"])
        )

        self.assertEqual(
            200,
            upload.status_code,
        )

        response = self.client.get(
            (
                "/api/subscription/invoice/"
                f"{int(invoice['id'])}"
                "/payment-proof"
            )
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        self.assertEqual(
            self._pdf(),
            response.data,
        )

        disposition = str(
            response.headers.get(
                "Content-Disposition"
            )
            or ""
        )

        self.assertIn(
            str(
                invoice[
                    "invoice_number"
                ]
            ),
            disposition,
        )

        self.assertIn(
            ".pdf",
            disposition,
        )

    def test_second_upload_is_rejected_while_under_review(
        self,
    ) -> None:
        invoice = self._invoice()

        first = self._upload(
            int(invoice["id"])
        )

        self.assertEqual(
            200,
            first.status_code,
        )

        second = self._upload(
            int(invoice["id"]),
            content=(
                self._pdf()
                + b"second"
            ),
        )

        self.assertEqual(
            409,
            second.status_code,
        )

        proof = (
            self.billing
            .payment_proof_for_invoice(
                int(invoice["id"]),
                tenant_id=self.tenant_id,
            )
        )

        self.assertIsNotNone(
            proof
        )

        document = (
            self.billing
            .payment_proof_file(
                int(proof["id"]),
                self.tenant_id,
            )
        )

        self.assertEqual(
            self._pdf(),
            document[
                "path"
            ].read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
