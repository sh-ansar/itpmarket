from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from billing_service import (
    BillingService,
    PAYMENT_PROOF_MAX_BYTES,
)
from schema import ensure_database
from subscription_service import (
    SubscriptionError,
    SubscriptionService,
)


class BillingPaymentProofTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="billing_proof_"
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

        self.billing = BillingService(
            self.db_path,
            document_root=
                self.document_root,
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _create_invoice(
        self,
    ) -> dict:
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

        self.assertEqual(
            "awaiting_invoice",
            reviewed["status"],
        )

        invoice = (
            self.billing.create_invoice(
                int(reviewed["id"]),
                3,
                int(self.admin["id"]),
                seller_snapshot={
                    "name":
                        "Test Supplier",
                    "vat_rate":
                        0,
                },
            )
        )

        return invoice

    @staticmethod
    def _pdf() -> bytes:
        return (
            b"%PDF-1.4\n"
            b"billing payment proof\n"
            b"%%EOF\n"
        )

    def test_schema_contains_payment_proofs(
        self,
    ) -> None:
        conn = sqlite3.connect(
            self.db_path
        )

        try:
            row = conn.execute(
                """SELECT name
                   FROM sqlite_master
                   WHERE type='table'
                     AND name=
                       'subscription_payment_proofs'"""
            ).fetchone()

        finally:
            conn.close()

        self.assertIsNotNone(row)

    def test_upload_saves_metadata_and_moves_to_review(
        self,
    ) -> None:
        invoice = (
            self._create_invoice()
        )

        proof = (
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "payment.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(),
            )
        )

        self.assertEqual(
            "under_review",
            proof["status"],
        )

        self.assertEqual(
            "application/pdf",
            proof["mime_type"],
        )

        self.assertEqual(
            len(self._pdf()),
            int(proof["file_size"]),
        )

        self.assertTrue(
            str(
                proof["sha256"]
            )
        )

        stored_path = Path(
            str(
                proof[
                    "stored_path"
                ]
            )
        )

        self.assertFalse(
            stored_path.is_absolute()
        )

        document = (
            self.billing
            .payment_proof_file(
                int(proof["id"]),
                self.tenant_id,
            )
        )

        self.assertTrue(
            document["path"].is_file()
        )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            status = conn.execute(
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
            ).fetchone()[0]

        finally:
            conn.close()

        self.assertEqual(
            "payment_review",
            status,
        )

    def test_rejects_mime_or_signature_mismatch(
        self,
    ) -> None:
        invoice = (
            self._create_invoice()
        )

        with self.assertRaises(
            SubscriptionError
        ):
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "payment.pdf",
                mime_type=
                    "image/png",
                content=self._pdf(),
            )

        with self.assertRaises(
            SubscriptionError
        ):
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "payment.png",
                mime_type=
                    "image/png",
                content=b"not-a-png",
            )

    def test_rejects_oversized_file(
        self,
    ) -> None:
        invoice = (
            self._create_invoice()
        )

        content = (
            b"%PDF-"
            + (
                b"x"
                * PAYMENT_PROOF_MAX_BYTES
            )
        )

        with self.assertRaises(
            SubscriptionError
        ):
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "payment.pdf",
                mime_type=
                    "application/pdf",
                content=content,
            )

    def test_rejects_other_tenant(
        self,
    ) -> None:
        invoice = (
            self._create_invoice()
        )

        with self.assertRaises(
            SubscriptionError
        ):
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id + 999,
                int(self.admin["id"]),
                original_filename=
                    "payment.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(),
            )

    def test_tampered_file_is_detected(
        self,
    ) -> None:
        invoice = (
            self._create_invoice()
        )

        proof = (
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "payment.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(),
            )
        )

        document = (
            self.billing
            .payment_proof_file(
                int(proof["id"]),
                self.tenant_id,
            )
        )

        document["path"].write_bytes(
            b"%PDF-1.4\n"
            b"TAMPERED\n"
            b"%%EOF\n"
        )

        with self.assertRaises(
            SubscriptionError
        ):
            self.billing.payment_proof_file(
                int(proof["id"]),
                self.tenant_id,
            )

    def test_reupload_after_rejection_keeps_history(
        self,
    ) -> None:
        invoice = (
            self._create_invoice()
        )

        first = (
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "first.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(),
            )
        )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            conn.execute(
                """UPDATE
                   subscription_payment_proofs
                   SET status='rejected'
                   WHERE id=?""",
                (
                    int(first["id"]),
                ),
            )

            conn.execute(
                """UPDATE
                   tenant_subscriptions
                   SET status='payment_rejected'
                   WHERE id=?""",
                (
                    int(
                        invoice[
                            "subscription_id"
                        ]
                    ),
                ),
            )

            conn.commit()

        finally:
            conn.close()

        second = (
            self.billing.save_payment_proof(
                int(invoice["id"]),
                self.tenant_id,
                int(self.admin["id"]),
                original_filename=
                    "second.pdf",
                mime_type=
                    "application/pdf",
                content=(
                    self._pdf()
                    + b"second"
                ),
            )
        )

        self.assertNotEqual(
            int(first["id"]),
            int(second["id"]),
        )

        conn = sqlite3.connect(
            self.db_path
        )

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

        finally:
            conn.close()

        self.assertEqual(
            2,
            len(rows),
        )

        self.assertEqual(
            "rejected",
            rows[0][1],
        )

        self.assertEqual(
            "under_review",
            rows[1][1],
        )

    def test_runtime_schema_is_restored(
        self,
    ) -> None:
        conn = sqlite3.connect(
            self.db_path
        )

        try:
            conn.execute(
                """DROP TABLE
                   subscription_payment_proofs"""
            )
            conn.commit()

        finally:
            conn.close()

        BillingService(
            self.db_path,
            document_root=
                self.document_root,
        )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            row = conn.execute(
                """SELECT name
                   FROM sqlite_master
                   WHERE type='table'
                     AND name=
                       'subscription_payment_proofs'"""
            ).fetchone()

        finally:
            conn.close()

        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
