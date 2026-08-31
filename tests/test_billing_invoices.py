from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from billing_service import (
    BillingService,
    OPERATOR_LEGAL_PROFILE,
)
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

    def test_unpaid_invoice_can_be_revised_with_a_new_period(self) -> None:
        reviewed = self._approve_for_invoice("starter")
        seller = {
            "name": "ITP Mining",
            "registration_number": "161240002661",
            "iban": "KZ20722S000001855383",
            "bank_name": "KASPI BANK",
            "bic": "CASPKZKA",
            "kbe": "17",
            "payment_purpose_code": "851",
            "vat_rate": 0,
        }
        original = self.billing.create_invoice(
            int(reviewed["id"]),
            1,
            int(self.admin["id"]),
            seller_snapshot=seller,
        )

        revised = self.billing.revise_invoice(
            int(original["id"]),
            self.tenant_id,
            int(self.admin["id"]),
            6,
            seller_snapshot=seller,
        )

        self.assertNotEqual(original["id"], revised["id"])
        self.assertEqual(6, revised["months_count"])
        self.assertEqual("issued", revised["status"])

        conn = sqlite3.connect(self.db_path)
        try:
            previous_status = conn.execute(
                "SELECT status FROM subscription_invoices WHERE id=?",
                (int(original["id"]),),
            ).fetchone()[0]
            subscription_status = conn.execute(
                "SELECT status FROM tenant_subscriptions WHERE id=?",
                (int(reviewed["id"]),),
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual("cancelled", previous_status)
        self.assertEqual("awaiting_payment", subscription_status)

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

    def test_supplier_settings_have_safe_defaults(self) -> None:
        settings = (
            self.billing
            .supplier_settings()
        )

        self.assertTrue(
            settings["is_complete"]
        )

        self.assertEqual(
            "SPY",
            settings[
                "invoice_prefix"
            ],
        )

        self.assertEqual(
            5,
            settings[
                "invoice_due_days"
            ],
        )

        self.assertEqual(
            0.0,
            settings["vat_rate"],
        )

        self.assertEqual(
            [],
            settings["missing_fields"],
        )

        self.assertEqual(
            OPERATOR_LEGAL_PROFILE["name"],
            settings["name"],
        )

    def test_supplier_settings_are_persisted_and_audited(self) -> None:
        updated = (
            self.billing
            .update_supplier_settings(
                {
                    "payment_purpose_code":
                        "851",
                    "vat_enabled":
                        True,
                    "vat_rate":
                        16,
                    "invoice_due_days":
                        7,
                    "invoice_prefix":
                        "spy",
                    "agreement_basis":
                        "Contract",
                    "executor_name":
                        "Executor",
                },
                int(self.admin["id"]),
            )
        )

        self.assertTrue(
            updated["is_complete"]
        )

        self.assertEqual(
            "SPY",
            updated[
                "invoice_prefix"
            ],
        )

        self.assertEqual(
            16.0,
            updated[
                "vat_rate"
            ],
        )

        self.assertEqual(
            7,
            updated[
                "invoice_due_days"
            ],
        )

        reloaded = BillingService(
            self.db_path
        ).supplier_settings()

        self.assertEqual(
            OPERATOR_LEGAL_PROFILE["name"],
            reloaded["name"],
        )

        self.assertEqual(
            OPERATOR_LEGAL_PROFILE["iban"],
            reloaded["iban"],
        )

        self.assertTrue(
            reloaded["is_complete"]
        )

        conn = sqlite3.connect(
            self.db_path
        )

        try:
            stored = conn.execute(
                """SELECT
                       value_json,
                       updated_by
                   FROM platform_settings
                   WHERE setting_key=
                       'billing_supplier'"""
            ).fetchone()

            audit = conn.execute(
                """SELECT
                       action,
                       entity_type,
                       entity_id,
                       details_json
                   FROM platform_audit_log
                   WHERE action=
                       'billing_supplier_settings_updated'
                   ORDER BY id DESC
                   LIMIT 1"""
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(
            stored
        )

        self.assertEqual(
            int(self.admin["id"]),
            int(stored[1]),
        )

        self.assertNotIn(
            "iban",
            str(stored[0]),
        )

        self.assertIsNotNone(
            audit
        )

        self.assertEqual(
            "platform_settings",
            audit[1],
        )

        self.assertEqual(
            "billing_supplier",
            audit[2],
        )

        # Audit tracks changed field names,
        # not full banking values.
        self.assertNotIn(
            "KZ20722S000001855383",
            str(audit[3]),
        )

    def test_operator_legal_profile_cannot_be_mutated(self) -> None:
        with self.assertRaisesRegex(
            SubscriptionError,
            "Юридические реквизиты оператора",
        ):
            self.billing.update_supplier_settings(
                {
                    "iban": "KZ000000000000000000",
                },
                int(self.admin["id"]),
            )

        self.assertEqual(
            OPERATOR_LEGAL_PROFILE["iban"],
            self.billing.supplier_settings()["iban"],
        )

    def test_issued_invoice_keeps_its_seller_snapshot(self) -> None:
        reviewed = self._approve_for_invoice("starter")
        supplier = self.billing.supplier_settings()
        seller_snapshot = {
            key: value
            for key, value in supplier.items()
            if key not in {"is_complete", "missing_fields"}
        }

        invoice = self.billing.create_invoice(
            int(reviewed["id"]),
            1,
            int(self.admin["id"]),
            seller_snapshot=seller_snapshot,
        )

        self.billing.update_supplier_settings(
            {"payment_purpose_code": "851"},
            int(self.admin["id"]),
        )

        persisted = self.billing.invoice_by_id(int(invoice["id"]))
        self.assertIsNotNone(persisted)
        self.assertEqual(
            "",
            persisted["seller"]["payment_purpose_code"],
        )
        self.assertEqual(
            OPERATOR_LEGAL_PROFILE["iban"],
            persisted["seller"]["iban"],
        )

    def test_supplier_settings_validation(self) -> None:
        with self.assertRaisesRegex(
            SubscriptionError,
            "\u041d\u0414\u0421",
        ):
            self.billing.update_supplier_settings(
                {
                    "vat_enabled": True,
                    "vat_rate": 101,
                },
                int(self.admin["id"]),
            )

        with self.assertRaisesRegex(
            SubscriptionError,
            "\u043e\u0442 1 "
            "\u0434\u043e 90",
        ):
            self.billing.update_supplier_settings(
                {
                    "invoice_due_days": 0,
                },
                int(self.admin["id"]),
            )

        with self.assertRaisesRegex(
            SubscriptionError,
            "\u041f\u0440\u0435\u0444\u0438\u043a\u0441",
        ):
            self.billing.update_supplier_settings(
                {
                    "invoice_prefix": "!",
                },
                int(self.admin["id"]),
            )

    def test_supplier_boolean_values_are_normalized(self) -> None:
        disabled = (
            self.billing
            .update_supplier_settings(
                {
                    "vat_enabled": "false",
                    "vat_rate": 16,
                },
                int(self.admin["id"]),
            )
        )

        self.assertFalse(
            disabled["vat_enabled"]
        )

        self.assertEqual(
            0.0,
            disabled["vat_rate"],
        )

        enabled = (
            self.billing
            .update_supplier_settings(
                {
                    "vat_enabled": "true",
                    "vat_rate": 16,
                },
                int(self.admin["id"]),
            )
        )

        self.assertTrue(
            enabled["vat_enabled"]
        )

        self.assertEqual(
            16.0,
            enabled["vat_rate"],
        )

        with self.assertRaises(
            SubscriptionError,
        ):
            self.billing.update_supplier_settings(
                {
                    "vat_enabled":
                        "definitely",
                },
                int(self.admin["id"]),
            )

    def test_disabled_vat_is_stored_as_zero(self) -> None:
        first = (
            self.billing
            .update_supplier_settings(
                {
                    "vat_enabled": True,
                    "vat_rate": 16,
                },
                int(self.admin["id"]),
            )
        )

        self.assertEqual(
            16.0,
            first["vat_rate"],
        )

        second = (
            self.billing
            .update_supplier_settings(
                {
                    "vat_enabled": False,
                },
                int(self.admin["id"]),
            )
        )

        self.assertFalse(
            second["vat_enabled"]
        )

        self.assertEqual(
            0.0,
            second["vat_rate"],
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
