from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from schema import ensure_database
from subscription_service import (
    SubscriptionError,
    SubscriptionService,
)


class SubscriptionPlanReplacementTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(
            prefix="subscription_replace_"
        )
        self.db_path = (
            Path(self.folder.name)
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
        self.user_id = int(
            self.admin["id"]
        )

        self.service = SubscriptionService(
            self.db_path
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _plan(
        self,
        code: str,
        price: float,
    ) -> dict:
        return self.service.save_plan(
            {
                "code": code,
                "name": (
                    "Plan "
                    + code
                ),
                "description": "Test",
                "price_amount": price,
                "currency": "KZT",
                "term_days": 30,
                "position_limit": 1000,
                "daily_operation_limit": 100,
                "is_public": True,
                "is_active": True,
                "features": {},
                "marketplaces": {},
            },
            self.user_id,
        )

    def _awaiting_invoice(
        self,
        code: str,
    ) -> dict:
        request = (
            self.service.request_plan(
                self.tenant_id,
                code,
                self.user_id,
            )
        )

        return (
            self.service.review_subscription(
                int(
                    request["id"]
                ),
                "approved",
                self.user_id,
            )
        )

    def _fake_issued_invoice(
        self,
        subscription_id: int,
    ) -> int:
        conn = sqlite3.connect(
            self.db_path
        )

        try:
            stamp = (
                "2026-08-27T00:00:00+00:00"
            )

            conn.execute(
                """UPDATE tenant_subscriptions
                   SET status='awaiting_payment',
                       updated_at=?
                   WHERE id=?""",
                (
                    stamp,
                    int(
                        subscription_id
                    ),
                ),
            )

            cursor = conn.execute(
                """INSERT INTO subscription_invoices(
                       tenant_id,
                       subscription_id,
                       invoice_number,
                       status,
                       months_count,
                       unit_price,
                       subtotal_amount,
                       vat_rate,
                       vat_amount,
                       total_amount,
                       currency,
                       seller_snapshot_json,
                       buyer_snapshot_json,
                       line_items_json,
                       issued_at,
                       created_by,
                       created_at,
                       updated_at
                   )
                   VALUES(
                       ?,?,
                       ?,
                       'issued',
                       1,
                       1000,
                       1000,
                       0,
                       0,
                       1000,
                       'KZT',
                       '{}',
                       '{}',
                       '[]',
                       ?,?,
                       ?,?
                   )""",
                (
                    self.tenant_id,
                    int(
                        subscription_id
                    ),
                    (
                        "TEST-"
                        + str(
                            subscription_id
                        )
                    ),
                    stamp,
                    self.user_id,
                    stamp,
                    stamp,
                ),
            )

            conn.commit()

            return int(
                cursor.lastrowid
            )

        finally:
            conn.close()

    def test_different_unpaid_plan_replaces_invoice(
        self,
    ) -> None:
        self._plan(
            "plan_a",
            1000,
        )
        self._plan(
            "plan_b",
            2000,
        )

        old = self._awaiting_invoice(
            "plan_a"
        )

        invoice_id = (
            self._fake_issued_invoice(
                int(
                    old["id"]
                )
            )
        )

        new = self.service.request_plan(
            self.tenant_id,
            "plan_b",
            self.user_id,
            replace_unpaid=True,
        )

        self.assertEqual(
            "pending",
            new["status"],
        )

        conn = sqlite3.connect(
            self.db_path
        )
        conn.row_factory = sqlite3.Row

        try:
            old_row = conn.execute(
                """SELECT status
                   FROM tenant_subscriptions
                   WHERE id=?""",
                (
                    int(
                        old["id"]
                    ),
                ),
            ).fetchone()

            invoice = conn.execute(
                """SELECT
                       status,
                       cancel_reason
                   FROM subscription_invoices
                   WHERE id=?""",
                (
                    invoice_id,
                ),
            ).fetchone()

            self.assertEqual(
                "cancelled",
                old_row["status"],
            )

            self.assertEqual(
                "cancelled",
                invoice["status"],
            )

            self.assertTrue(
                invoice[
                    "cancel_reason"
                ]
            )

        finally:
            conn.close()

    def test_same_unpaid_plan_is_idempotent(
        self,
    ) -> None:
        self._plan(
            "plan_same",
            1000,
        )

        old = self._awaiting_invoice(
            "plan_same"
        )

        invoice_id = (
            self._fake_issued_invoice(
                int(
                    old["id"]
                )
            )
        )

        current = self.service.request_plan(
            self.tenant_id,
            "plan_same",
            self.user_id,
            replace_unpaid=True,
        )

        self.assertEqual(
            int(
                old["id"]
            ),
            int(
                current["id"]
            ),
        )

        conn = sqlite3.connect(
            self.db_path
        )
        conn.row_factory = sqlite3.Row

        try:
            invoice = conn.execute(
                """SELECT status
                   FROM subscription_invoices
                   WHERE id=?""",
                (
                    invoice_id,
                ),
            ).fetchone()

            self.assertEqual(
                "issued",
                invoice["status"],
            )

        finally:
            conn.close()

    def test_payment_review_replacement_cancels_invoice_and_supersedes_proof(
        self,
    ) -> None:
        self._plan(
            "review_a",
            1000,
        )
        self._plan(
            "review_b",
            2000,
        )

        old = self._awaiting_invoice(
            "review_a"
        )

        invoice_id = self._fake_issued_invoice(
            int(old["id"])
        )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            conn.execute(
                """UPDATE tenant_subscriptions
                   SET status='payment_review'
                   WHERE id=?""",
                (
                    int(
                        old["id"]
                    ),
                ),
            )
            stamp = "2026-08-27T00:00:00+00:00"
            conn.execute(
                """INSERT INTO subscription_payment_proofs(
                       tenant_id,subscription_id,invoice_id,status,
                       original_filename,stored_path,mime_type,file_size,
                       sha256,uploaded_by,uploaded_at,created_at,updated_at
                   ) VALUES(?,?,?,'under_review',? ,?,'application/pdf',1,?,?,?, ?,?)""",
                (
                    self.tenant_id,
                    int(old["id"]),
                    invoice_id,
                    "payment.pdf",
                    "data/billing/payment-proofs/test-review.pdf",
                    "a" * 64,
                    self.user_id,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            conn.commit()

        finally:
            conn.close()

        new = self.service.request_plan(
            self.tenant_id,
            "review_b",
            self.user_id,
            replace_unpaid=True,
        )

        self.assertEqual("pending", new["status"])

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            old_row = conn.execute(
                "SELECT status FROM tenant_subscriptions WHERE id=?",
                (int(old["id"]),),
            ).fetchone()
            invoice = conn.execute(
                "SELECT status FROM subscription_invoices WHERE id=?",
                (invoice_id,),
            ).fetchone()
            proof = conn.execute(
                "SELECT status FROM subscription_payment_proofs WHERE invoice_id=?",
                (invoice_id,),
            ).fetchone()
            self.assertEqual("cancelled", old_row["status"])
            self.assertEqual("cancelled", invoice["status"])
            self.assertEqual("superseded", proof["status"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
