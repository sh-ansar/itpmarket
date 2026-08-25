from __future__ import annotations

import sqlite3
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


class BillingInvoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(
            prefix="billing_invoices_"
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

        self.service = SubscriptionService(
            self.db_path
        )

        self.billing = BillingService(
            self.db_path
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _approve_for_invoice(
        self,
        plan_code: str = "starter",
    ) -> dict:
        requested = self.service.request_plan(
            self.tenant_id,
            plan_code,
            int(self.admin["id"]),
        )

        reviewed = self.service.review_subscription(
            int(requested["id"]),
            "approved",
            int(self.admin["id"]),
        )

        self.assertEqual(
            "awaiting_invoice",
            reviewed["status"],
        )

        return reviewed

    def test_invoice_schema_is_in_base_database(self) -> None:
        schema_db = (
            Path(self.folder.name)
            / "schema_only.db"
        )

        ensure_database(
            schema_db
        )

        conn = sqlite3.connect(
            schema_db
        )

        try:
            tables = {
                str(row[0])
                for row in conn.execute(
                    """SELECT name
                       FROM sqlite_master
                       WHERE type='table'"""
                ).fetchall()
            }

            columns = {
                str(row[1])
                for row in conn.execute(
                    """PRAGMA table_info(
                           subscription_invoices
                       )"""
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn(
            "billing_sequences",
            tables,
        )

        self.assertIn(
            "subscription_invoices",
            tables,
        )

        self.assertTrue(
            {
                "invoice_number",
                "months_count",
                "seller_snapshot_json",
                "buyer_snapshot_json",
                "line_items_json",
                "pdf_path",
                "pdf_sha256",
            }.issubset(columns)
        )

    def test_invoice_creation_snapshots_period_and_amounts(self) -> None:
        conn = sqlite3.connect(
            self.db_path
        )

        try:
            conn.execute(
                """UPDATE tenants
                   SET name=?,
                       registration_number=?,
                       legal_address=?,
                       actual_address=?,
                       contact_email=?,
                       contact_phone=?
                   WHERE id=?""",
                (
                    "Buyer LLP",
                    "990840001823",
                    "Astana, Legal 1",
                    "Astana, Actual 2",
                    "billing@buyer.test",
                    "+77000000000",
                    int(self.tenant_id),
                ),
            )

            conn.commit()
        finally:
            conn.close()

        reviewed = self._approve_for_invoice(
            "starter"
        )

        seller = {
            "name":
                "ITP Mining",
            "registration_number":
                "161240002661",
            "iban":
                "KZ20722S000001855383",
            "bank_name":
                "KASPI BANK",
            "bic":
                "CASPKZKA",
            "kbe":
                "17",
            "payment_purpose_code":
                "851",
            "vat_rate":
                16,
        }

        invoice = self.billing.create_invoice(
            int(reviewed["id"]),
            3,
            int(self.admin["id"]),
            seller_snapshot=seller,
            due_days=7,
        )

        self.assertEqual(
            "issued",
            invoice["status"],
        )

        self.assertRegex(
            str(invoice["invoice_number"]),
            r"^SPY-\d{4}-000001$",
        )

        self.assertEqual(
            3,
            invoice["months_count"],
        )

        self.assertEqual(
            14900,
            invoice["unit_price"],
        )

        self.assertEqual(
            44700,
            invoice["total_amount"],
        )

        self.assertEqual(
            6165.52,
            invoice["vat_amount"],
        )

        self.assertEqual(
            38534.48,
            invoice["subtotal_amount"],
        )

        self.assertEqual(
            "ITP Mining",
            invoice["seller"]["name"],
        )

        self.assertEqual(
            16,
            invoice["seller"]["vat_rate"],
        )

        self.assertEqual(
            "Buyer LLP",
            invoice["buyer"]["name"],
        )

        self.assertEqual(
            "990840001823",
            invoice["buyer"][
                "registration_number"
            ],
        )

        self.assertEqual(
            3,
            invoice["line_items"][0][
                "quantity"
            ],
        )

        self.assertEqual(
            "starter",
            invoice["line_items"][0][
                "plan_code"
            ],
        )

        # Double-click / retry must be idempotent.
        same = self.billing.create_invoice(
            int(reviewed["id"]),
            3,
            int(self.admin["id"]),
            seller_snapshot=seller,
            due_days=7,
        )

        self.assertEqual(
            invoice["id"],
            same["id"],
        )

        self.assertEqual(
            invoice["invoice_number"],
            same["invoice_number"],
        )

        with self.assertRaisesRegex(
            SubscriptionError,
            "\u0434\u0435\u0439\u0441\u0442\u0432\u0443\u044e\u0449",
        ):
            self.billing.create_invoice(
                int(reviewed["id"]),
                6,
                int(self.admin["id"]),
                seller_snapshot=seller,
            )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            subscription_status = str(
                conn.execute(
                    """SELECT status
                       FROM tenant_subscriptions
                       WHERE id=?""",
                    (int(reviewed["id"]),),
                ).fetchone()[0]
            )

            # Change company data after issue.
            conn.execute(
                """UPDATE tenants
                   SET name='Changed Buyer'
                   WHERE id=?""",
                (int(self.tenant_id),),
            )

            conn.commit()
        finally:
            conn.close()

        self.assertEqual(
            "awaiting_payment",
            subscription_status,
        )

        stored = (
            self.billing
            .invoice_for_subscription(
                int(reviewed["id"])
            )
        )

        # Invoice is an immutable business snapshot.
        self.assertEqual(
            "Buyer LLP",
            stored["buyer"]["name"],
        )

    def test_invoice_rejects_unsupported_period(self) -> None:
        reviewed = self._approve_for_invoice(
            "starter"
        )

        with self.assertRaisesRegex(
            SubscriptionError,
            "1, 2, 3, 6",
        ):
            self.billing.create_invoice(
                int(reviewed["id"]),
                4,
                int(self.admin["id"]),
                seller_snapshot={
                    "name": "Seller",
                },
            )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            status = str(
                conn.execute(
                    """SELECT status
                       FROM tenant_subscriptions
                       WHERE id=?""",
                    (int(reviewed["id"]),),
                ).fetchone()[0]
            )
        finally:
            conn.close()

        self.assertEqual(
            "awaiting_invoice",
            status,
        )

    def test_invoice_can_be_cancelled_and_reissued(self) -> None:
        reviewed = self._approve_for_invoice(
            "starter"
        )

        seller = {
            "name": "Seller",
            "vat_rate": 0,
        }

        first = self.billing.create_invoice(
            int(reviewed["id"]),
            1,
            int(self.admin["id"]),
            seller_snapshot=seller,
        )

        cancelled = (
            self.billing.cancel_invoice(
                int(first["id"]),
                int(self.admin["id"]),
                "Change billing period",
            )
        )

        self.assertEqual(
            "cancelled",
            cancelled["status"],
        )

        self.assertEqual(
            "Change billing period",
            cancelled["cancel_reason"],
        )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            status = str(
                conn.execute(
                    """SELECT status
                       FROM tenant_subscriptions
                       WHERE id=?""",
                    (int(reviewed["id"]),),
                ).fetchone()[0]
            )
        finally:
            conn.close()

        self.assertEqual(
            "awaiting_invoice",
            status,
        )

        second = self.billing.create_invoice(
            int(reviewed["id"]),
            12,
            int(self.admin["id"]),
            seller_snapshot=seller,
        )

        self.assertNotEqual(
            first["id"],
            second["id"],
        )

        self.assertRegex(
            str(first["invoice_number"]),
            r"-000001$",
        )

        self.assertRegex(
            str(second["invoice_number"]),
            r"-000002$",
        )

        self.assertEqual(
            12,
            second["months_count"],
        )

        self.assertEqual(
            178800,
            second["total_amount"],
        )

    def test_billing_service_restores_runtime_schema(self) -> None:
        conn = sqlite3.connect(
            self.db_path
        )

        try:
            conn.execute(
                """DROP TABLE
                   subscription_invoices"""
            )
            conn.execute(
                """DROP TABLE
                   billing_sequences"""
            )
            conn.commit()
        finally:
            conn.close()

        BillingService(
            self.db_path
        )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            tables = {
                str(row[0])
                for row in conn.execute(
                    """SELECT name
                       FROM sqlite_master
                       WHERE type='table'"""
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn(
            "subscription_invoices",
            tables,
        )

        self.assertIn(
            "billing_sequences",
            tables,
        )


if __name__ == "__main__":
    unittest.main()
