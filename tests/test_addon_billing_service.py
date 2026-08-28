from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from addon_billing_service import AddonBillingService
from auth_service import AuthService
from billing_service import BillingService
from schema import ensure_database
from subscription_service import SubscriptionService


class FakeInvoicePDFService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0

    def generate(self, payload: dict) -> dict:
        self.calls += 1
        folder = self.root / "output" / "invoices" / str(payload["issued_at"][:4])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{payload['invoice_number']}.pdf"
        content = b"%PDF-1.4\naddon invoice\n%%EOF\n"
        path.write_bytes(content)
        return {"path": str(path), "sha256": "", "size": len(content)}


class AddonBillingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="addon_billing_")
        self.root = Path(self.temp.name)
        self.db_path = self.root / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.admin, _ = self.auth.create_initial_admin(
            "admin@example.test", "Admin", "StrongPassword123!"
        )
        self.tenant_id = int(self.admin["tenant_id"])
        self.actor_id = int(self.admin["id"])
        self.subscriptions = SubscriptionService(self.db_path)
        self.pdf = FakeInvoicePDFService(self.root)
        self.billing = BillingService(
            self.db_path, document_root=self.root, invoice_pdf_service=self.pdf
        )
        self.billing.update_supplier_settings({
            "name": "Supplier", "registration_number": "123456789012",
            "legal_address": "Astana", "iban": "KZ00TEST",
            "bank_name": "Bank", "bic": "TESTKZKX", "invoice_due_days": 5,
        }, self.actor_id)
        self.service = AddonBillingService(
            self.db_path, document_root=self.root, billing_service=self.billing
        )
        self._activate_subscription()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _activate_subscription(self) -> None:
        request = self.subscriptions.request_plan(self.tenant_id, "starter", self.actor_id)
        start = datetime.now().astimezone()
        end = start + timedelta(days=30)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """UPDATE tenant_subscriptions SET status='active',starts_at=?,ends_at=?
                   WHERE id=?""",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), int(request["id"])),
            )
            conn.commit()
        finally:
            conn.close()

    def _order(self, marketplace: str = "kaspi") -> dict:
        return self.service.create_order(
            self.tenant_id, "positions_100", marketplace, 2, self.actor_id
        )

    def test_create_order_snapshots_catalog_and_creates_pdf_invoice(self) -> None:
        order = self._order()
        self.assertEqual("awaiting_payment", order["status"])
        self.assertEqual("kaspi", order["marketplace_code"])
        self.assertEqual(100, order["positions"])
        self.assertEqual(2, order["quantity"])
        self.assertEqual(5000.0, order["unit_price"])
        self.assertEqual(10000.0, order["total_price"])
        self.assertEqual("issued", order["invoice"]["status"])
        line = order["invoice"]["line_items"][0]
        self.assertEqual("пак.", line["unit_label"])
        self.assertEqual(10000.0, line["amount"])
        self.assertIn("Kaspi", line["description"])
        self.assertIn("всего +200 позиций", line["description"])
        self.assertTrue(order["invoice"]["pdf_path"])
        self.assertEqual(1, self.pdf.calls)
        self.assertEqual([order["id"]], [item["id"] for item in self.service.list_orders(self.tenant_id)])

    def test_replace_proof_and_approve_is_idempotent_and_changes_exact_limit(self) -> None:
        order = self._order("kaspi")
        first = self.service.upload_payment_proof(
            order["id"], self.tenant_id, self.actor_id,
            original_filename="first.pdf", mime_type="application/pdf", content=b"%PDF-1.4\nfirst",
        )
        replacement = self.service.upload_payment_proof(
            order["id"], self.tenant_id, self.actor_id,
            original_filename="second.png", mime_type="image/png", content=b"\x89PNG\r\n\x1a\nsecond",
        )
        self.assertNotEqual(first["id"], replacement["id"])
        self.assertEqual("second.png", replacement["original_filename"])
        queue = self.service.list_payments_for_accountant()
        self.assertEqual([replacement["id"]], [item["id"] for item in queue])
        approved = self.service.approve_payment(replacement["id"], self.actor_id)
        again = self.service.approve_payment(replacement["id"], self.actor_id)
        self.assertEqual("approved", approved["status"])
        self.assertEqual(approved["id"], again["id"])
        active = self.service.get_order(order["id"], tenant_id=self.tenant_id)
        self.assertEqual("active", active["status"])
        self.assertEqual("paid", active["invoice"]["status"])
        entitlement = self.subscriptions.entitlement(self.tenant_id)
        self.assertEqual(300, entitlement["marketplaces"]["kaspi"]["position_limit"])
        self.assertEqual(100, entitlement["marketplaces"]["wildberries"]["position_limit"])

    def test_reject_then_reissue_supersedes_old_order_and_invoice(self) -> None:
        original = self._order()
        proof = self.service.upload_payment_proof(
            original["id"], self.tenant_id, self.actor_id,
            original_filename="payment.jpg", mime_type="image/jpeg", content=b"\xff\xd8\xffpayment",
        )
        rejected = self.service.reject_payment(proof["id"], self.actor_id, "Amount mismatch")
        self.assertEqual("rejected", rejected["status"])
        replacement = self.service.reissue(original["id"], self.actor_id, tenant_id=self.tenant_id)
        old = self.service.get_order(original["id"], tenant_id=self.tenant_id)
        self.assertEqual("superseded", old["status"])
        self.assertEqual("superseded", old["invoice"]["status"])
        self.assertEqual(replacement["id"], old["superseded_by"])
        self.assertEqual("awaiting_payment", replacement["status"])


if __name__ == "__main__":
    unittest.main()
