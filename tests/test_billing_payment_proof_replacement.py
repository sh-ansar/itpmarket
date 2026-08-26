from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from billing_service import BillingService
from schema import ensure_database
from subscription_service import (
    SubscriptionService,
    SubscriptionError,
)


class BillingPaymentProofReplacementTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="billing_proof_replace_"
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

    def tearDown(self) -> None:
        self.folder.cleanup()

    @staticmethod
    def _pdf(
        suffix: bytes = b"",
    ) -> bytes:
        return (
            b"%PDF-1.4\n"
            b"payment-proof\n"
            + suffix
            + b"\n%%EOF\n"
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
                        "Replacement Test",
                    "vat_rate":
                        0,
                },
            )
        )

    def test_proof_can_be_replaced_during_payment_review(
        self,
    ) -> None:
        invoice = self._invoice()

        first = (
            self.billing
            .save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "first.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(
                    b"first"
                ),
            )
        )

        second = (
            self.billing
            .save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "second.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(
                    b"second"
                ),
            )
        )

        conn = self.billing._connect()

        try:
            rows = conn.execute(
                """SELECT id,status
                   FROM subscription_payment_proofs
                   WHERE invoice_id=?
                   ORDER BY id""",
                (
                    int(invoice["id"]),
                ),
            ).fetchall()

            subscription = conn.execute(
                """SELECT status
                   FROM tenant_subscriptions
                   WHERE id=?""",
                (
                    int(
                        invoice[
                            "subscription_id"
                        ]
                    ),
                ),
            ).fetchone()

        finally:
            conn.close()

        self.assertEqual(
            2,
            len(rows),
        )

        self.assertEqual(
            "superseded",
            rows[0]["status"],
        )

        self.assertEqual(
            "under_review",
            rows[1]["status"],
        )

        self.assertEqual(
            int(first["id"]),
            int(rows[0]["id"]),
        )

        self.assertEqual(
            int(second["id"]),
            int(rows[1]["id"]),
        )

        self.assertEqual(
            "payment_review",
            subscription["status"],
        )

        current = (
            self.billing
            .payment_proof_for_invoice(
                int(invoice["id"]),
                tenant_id=
                    self.tenant_id,
            )
        )

        self.assertEqual(
            int(second["id"]),
            int(current["id"]),
        )

    def test_original_filename_is_sanitized(
        self,
    ) -> None:
        invoice = self._invoice()

        proof = (
            self.billing
            .save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    r"..\..\secret\payment.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(),
            )
        )

        self.assertEqual(
            "payment.pdf",
            proof[
                "original_filename"
            ],
        )

        self.assertNotIn(
            "secret",
            str(
                proof[
                    "stored_path"
                ]
            ),
        )

        self.assertNotIn(
            "..",
            str(
                proof[
                    "stored_path"
                ]
            ),
        )

    def test_overlong_filename_is_rejected(
        self,
    ) -> None:
        invoice = self._invoice()

        filename = (
            "a" * 252
            + ".pdf"
        )

        with self.assertRaises(
            SubscriptionError
        ):
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    filename,
                mime_type=
                    "application/pdf",
                content=self._pdf(),
            )

        conn = self.billing._connect()

        try:
            count = int(
                conn.execute(
                    """SELECT COUNT(*)
                       FROM subscription_payment_proofs
                       WHERE invoice_id=?""",
                    (
                        int(invoice["id"]),
                    ),
                ).fetchone()[0]
            )

        finally:
            conn.close()

        self.assertEqual(
            0,
            count,
        )


if __name__ == "__main__":
    unittest.main()
