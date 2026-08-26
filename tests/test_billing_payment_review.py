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


class BillingPaymentReviewTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="billing_review_"
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
            self.auth
            .create_initial_admin(
                "root@example.com",
                "Root",
                "StrongPassword123!",
            )
        )

        self.tenant_id = int(
            self.admin[
                "tenant_id"
            ]
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
    def _pdf() -> bytes:
        return (
            b"%PDF-1.4\n"
            b"Payment confirmation\n"
            b"%%EOF\n"
        )

    def _invoice(
        self,
        months: int = 3,
    ) -> dict:
        requested = (
            self.subscriptions
            .request_plan(
                self.tenant_id,
                "starter",
                int(
                    self.admin[
                        "id"
                    ]
                ),
            )
        )

        reviewed = (
            self.subscriptions
            .review_subscription(
                int(
                    requested[
                        "id"
                    ]
                ),
                "approved",
                int(
                    self.admin[
                        "id"
                    ]
                ),
            )
        )

        return (
            self.billing
            .create_invoice(
                int(
                    reviewed[
                        "id"
                    ]
                ),
                months,
                int(
                    self.admin[
                        "id"
                    ]
                ),
                seller_snapshot={
                    "name":
                        "Billing Test",
                    "vat_rate":
                        0,
                },
            )
        )

    def _proof(
        self,
        invoice: dict,
    ) -> dict:
        return (
            self.billing
            .save_payment_proof(
                int(
                    invoice[
                        "id"
                    ]
                ),
                self.tenant_id,
                int(
                    self.admin[
                        "id"
                    ]
                ),
                original_filename=
                    "payment.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(),
            )
        )

    def _connect(self):
        return (
            self.billing
            ._connect()
        )

    def test_platform_queue_contains_invoice_and_proof(
        self,
    ) -> None:
        invoice = self._invoice(
            3
        )

        items = (
            self.billing
            .platform_payment_items()
        )

        current = next(
            item
            for item in items
            if int(
                item[
                    "invoice_id"
                ]
            )
            == int(
                invoice["id"]
            )
        )

        self.assertEqual(
            "awaiting_payment",
            current[
                "subscription_status"
            ],
        )

        self.assertIsNone(
            current["proof"]
        )

        self._proof(
            invoice
        )

        items = (
            self.billing
            .platform_payment_items()
        )

        current = next(
            item
            for item in items
            if int(
                item[
                    "invoice_id"
                ]
            )
            == int(
                invoice["id"]
            )
        )

        self.assertEqual(
            "payment_review",
            current[
                "subscription_status"
            ],
        )

        self.assertEqual(
            "under_review",
            current[
                "proof"
            ][
                "status"
            ],
        )

        self.assertNotIn(
            "stored_path",
            current[
                "proof"
            ],
        )

        self.assertNotIn(
            "sha256",
            current[
                "proof"
            ],
        )

    def test_reject_marks_proof_and_subscription(
        self,
    ) -> None:
        invoice = self._invoice()
        proof = self._proof(
            invoice
        )

        result = (
            self.billing
            .reject_invoice_payment(
                int(
                    invoice["id"]
                ),
                int(
                    self.admin[
                        "id"
                    ]
                ),
                review_note=
                    "Payment not found",
            )
        )

        self.assertEqual(
            "payment_rejected",
            result[
                "subscription"
            ][
                "status"
            ],
        )

        self.assertEqual(
            "rejected",
            result[
                "proof"
            ][
                "status"
            ],
        )

        self.assertEqual(
            "Payment not found",
            result[
                "proof"
            ][
                "review_note"
            ],
        )

        self.assertEqual(
            int(proof["id"]),
            int(
                result[
                    "proof"
                ][
                    "id"
                ]
            ),
        )

        conn = self._connect()

        try:
            audit = conn.execute(
                """SELECT action
                   FROM platform_audit_log
                   WHERE entity_type='subscription_invoice'
                     AND entity_id=?
                   ORDER BY id DESC
                   LIMIT 1""",
                (
                    str(
                        int(
                            invoice[
                                "id"
                            ]
                        )
                    ),
                ),
            ).fetchone()

        finally:
            conn.close()

        self.assertEqual(
            "billing_payment_rejected",
            audit["action"],
        )

    def test_confirm_with_proof_uses_invoice_amount_and_months(
        self,
    ) -> None:
        invoice = self._invoice(
            3
        )

        proof = self._proof(
            invoice
        )

        result = (
            self.billing
            .confirm_invoice_payment(
                int(
                    invoice[
                        "id"
                    ]
                ),
                int(
                    self.admin[
                        "id"
                    ]
                ),
            )
        )

        self.assertFalse(
            result[
                "already_confirmed"
            ]
        )

        payment = result[
            "payment"
        ]

        self.assertEqual(
            float(
                invoice[
                    "total_amount"
                ]
            ),
            float(
                payment[
                    "amount"
                ]
            ),
        )

        self.assertEqual(
            3,
            int(
                payment[
                    "months_count"
                ]
            ),
        )

        self.assertEqual(
            "active",
            result[
                "subscription"
            ][
                "status"
            ],
        )

        conn = self._connect()

        try:
            stored_proof = conn.execute(
                """SELECT status
                   FROM subscription_payment_proofs
                   WHERE id=?""",
                (
                    int(
                        proof[
                            "id"
                        ]
                    ),
                ),
            ).fetchone()

            stored_invoice = conn.execute(
                """SELECT status
                   FROM subscription_invoices
                   WHERE id=?""",
                (
                    int(
                        invoice[
                            "id"
                        ]
                    ),
                ),
            ).fetchone()

            tenant = conn.execute(
                """SELECT plan_code
                   FROM tenants
                   WHERE id=?""",
                (
                    self.tenant_id,
                ),
            ).fetchone()

        finally:
            conn.close()

        self.assertEqual(
            "confirmed",
            stored_proof[
                "status"
            ],
        )

        self.assertEqual(
            "paid",
            stored_invoice[
                "status"
            ],
        )

        self.assertEqual(
            "starter",
            tenant[
                "plan_code"
            ],
        )

    def test_confirm_without_proof_is_allowed_and_idempotent(
        self,
    ) -> None:
        invoice = self._invoice(
            2
        )

        first = (
            self.billing
            .confirm_invoice_payment(
                int(
                    invoice[
                        "id"
                    ]
                ),
                int(
                    self.admin[
                        "id"
                    ]
                ),
                note=
                    "Bank payment verified",
            )
        )

        second = (
            self.billing
            .confirm_invoice_payment(
                int(
                    invoice[
                        "id"
                    ]
                ),
                int(
                    self.admin[
                        "id"
                    ]
                ),
            )
        )

        self.assertFalse(
            first[
                "already_confirmed"
            ]
        )

        self.assertTrue(
            second[
                "already_confirmed"
            ]
        )

        self.assertEqual(
            int(
                first[
                    "payment"
                ][
                    "id"
                ]
            ),
            int(
                second[
                    "payment"
                ][
                    "id"
                ]
            ),
        )

        conn = self._connect()

        try:
            count = int(
                conn.execute(
                    """SELECT COUNT(*)
                       FROM subscription_payments
                       WHERE subscription_id=?""",
                    (
                        int(
                            invoice[
                                "subscription_id"
                            ]
                        ),
                    ),
                ).fetchone()[0]
            )

        finally:
            conn.close()

        self.assertEqual(
            1,
            count,
        )

    def test_calendar_month_period_is_used(
        self,
    ) -> None:
        invoice = self._invoice(
            1
        )

        conn = self._connect()

        try:
            conn.execute(
                """UPDATE tenant_subscriptions
                   SET starts_at=?
                   WHERE id=?""",
                (
                    "2027-01-31T12:00:00+05:00",
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

        result = (
            self.billing
            .confirm_invoice_payment(
                int(
                    invoice[
                        "id"
                    ]
                ),
                int(
                    self.admin[
                        "id"
                    ]
                ),
            )
        )

        subscription = (
            result[
                "subscription"
            ]
        )

        self.assertEqual(
            "scheduled",
            subscription[
                "status"
            ],
        )

        self.assertTrue(
            str(
                subscription[
                    "starts_at"
                ]
            ).startswith(
                "2027-01-31T12:00:00"
            )
        )

        self.assertTrue(
            str(
                subscription[
                    "ends_at"
                ]
            ).startswith(
                "2027-02-28T12:00:00"
            )
        )


if __name__ == "__main__":
    unittest.main()
