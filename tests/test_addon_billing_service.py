from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from addon_billing_service import AddonBillingService
from auth_service import AuthService
from billing_service import BillingService
from schema import ensure_database
from subscription_service import SubscriptionService


class FakeInvoicePDFService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0
        self.payloads: list[dict] = []

    def generate(self, payload: dict) -> dict:
        self.calls += 1
        self.payloads.append(payload)
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
            "invoice_due_days": 5,
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

    def _order(self, quantity: int = 2, *, marketplace_code: str | None = None) -> dict:
        return self.service.create_order(
            self.tenant_id, "positions_100", quantity, self.actor_id,
            marketplace_code=marketplace_code,
        )

    def test_create_order_snapshots_catalog_and_creates_pdf_invoice(self) -> None:
        order = self._order()
        self.assertEqual("awaiting_payment", order["status"])
        self.assertEqual("", order["marketplace_code"])
        self.assertEqual(100, order["positions"])
        self.assertEqual(2, order["quantity"])
        self.assertEqual(5000.0, order["unit_price"])
        self.assertEqual(10000.0, order["total_price"])
        self.assertEqual("issued", order["invoice"]["status"])
        line = order["invoice"]["line_items"][0]
        self.assertEqual("пак.", line["unit_label"])
        self.assertEqual(10000.0, line["amount"])
        self.assertEqual('Пакет "+100 позиций"', line["name"])
        self.assertEqual('Пакет "+100 позиций"', line["description"])
        self.assertEqual(2, line["quantity"])
        self.assertNotIn("marketplace", line)
        self.assertNotIn("Spyon", line["description"])
        self.assertTrue(order["invoice"]["pdf_path"])
        self.assertEqual(1, self.pdf.calls)
        self.assertEqual([order["id"]], [item["id"] for item in self.service.list_orders(self.tenant_id)])

    def test_replace_proof_and_approve_is_idempotent_and_changes_exact_limit(self) -> None:
        order = self._order()
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
        for marketplace in ("kaspi", "ozon", "ozon_kz", "halyk_market", "forte_market", "wildberries"):
            with self.subTest(marketplace=marketplace):
                self.assertEqual(100, entitlement["marketplaces"][marketplace]["base_position_limit"])
                self.assertEqual(200, entitlement["marketplaces"][marketplace]["extra_positions"])
                self.assertEqual(300, entitlement["marketplaces"][marketplace]["position_limit"])

    def test_legacy_marketplace_argument_is_ignored_and_global_addon_applies_once(self) -> None:
        order = self._order(1, marketplace_code="kaspi")
        self.assertEqual("", order["marketplace_code"])
        proof = self.service.upload_payment_proof(
            order["id"], self.tenant_id, self.actor_id,
            original_filename="global.pdf", mime_type="application/pdf",
            content=b"%PDF-1.4\nglobal",
        )
        self.service.approve_payment(proof["id"], self.actor_id)
        entitlement = self.subscriptions.entitlement(self.tenant_id)
        for marketplace in ("kaspi", "ozon", "ozon_kz"):
            with self.subTest(marketplace=marketplace):
                self.assertEqual(100, entitlement["marketplaces"][marketplace]["extra_positions"])
                self.assertEqual(200, entitlement["marketplaces"][marketplace]["position_limit"])

    def test_historical_marketplace_scoped_paid_order_keeps_original_semantics(self) -> None:
        order = self._order(1)
        proof = self.service.upload_payment_proof(
            order["id"], self.tenant_id, self.actor_id,
            original_filename="historical.pdf", mime_type="application/pdf",
            content=b"%PDF-1.4\nhistorical",
        )
        self.service.approve_payment(proof["id"], self.actor_id)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE tenant_addon_orders SET marketplace_code='kaspi' WHERE id=?",
                (order["id"],),
            )
            conn.commit()
        finally:
            conn.close()
        entitlement = self.subscriptions.entitlement(self.tenant_id)
        self.assertEqual(100, entitlement["marketplaces"]["kaspi"]["extra_positions"])
        self.assertEqual(0, entitlement["marketplaces"]["ozon"]["extra_positions"])
        self.assertEqual(0, entitlement["marketplaces"]["ozon_kz"]["extra_positions"])

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

        visible = self.service.list_orders(self.tenant_id)
        self.assertEqual(
            [replacement["id"]],
            [item["id"] for item in visible],
        )

        audit_history = self.service.list_orders(
            self.tenant_id,
            include_superseded=True,
        )
        self.assertEqual(
            [replacement["id"], original["id"]],
            [item["id"] for item in audit_history],
        )

    def test_addon_uses_subscription_supplier_snapshot_and_inclusive_vat(self) -> None:
        supplier = self.billing.update_supplier_settings({
            "name": "Unified supplier", "registration_number": "BIN-1",
            "legal_address": "Astana", "bank_name": "Bank", "iban": "KZ1",
            "bic": "BIC", "kbe": "17", "payment_purpose_code": "851",
            "vat_enabled": True, "vat_rate": 16,
        }, self.actor_id)
        request = self.subscriptions.request_plan(self.tenant_id, "starter", self.actor_id)
        reviewed = self.subscriptions.review_subscription(int(request["id"]), "approved", self.actor_id)
        subscription_invoice = self.billing.create_invoice(
            int(reviewed["id"]), 1, self.actor_id,
            seller_snapshot={key: value for key, value in supplier.items() if key not in {"is_complete", "missing_fields"}},
            due_days=int(supplier["invoice_due_days"]),
        )
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE tenant_subscriptions SET status='active' WHERE id=?", (int(reviewed["id"]),))
            conn.commit()
        finally:
            conn.close()
        addon = self.service.create_order(self.tenant_id, "positions_100", 1, self.actor_id)
        addon_invoice = addon["invoice"]
        self.assertEqual(subscription_invoice["seller"], addon_invoice["seller"])
        self.assertEqual(16.0, addon_invoice["vat_rate"])
        self.assertEqual(689.66, addon_invoice["vat_amount"])
        self.assertEqual(4310.34, addon_invoice["subtotal_amount"])
        self.assertEqual(5000.0, addon_invoice["total_amount"])
        self.assertEqual(addon_invoice["seller"], self.pdf.payloads[-1]["seller_snapshot"])
        proof = self.service.upload_payment_proof(
            addon["id"], self.tenant_id, self.actor_id,
            original_filename="payment.pdf", mime_type="application/pdf", content=b"%PDF-1.4\nproof",
        )
        self.service.reject_payment(proof["id"], self.actor_id, "retry")
        reissued = self.service.reissue(addon["id"], self.actor_id, tenant_id=self.tenant_id)
        self.assertEqual(addon_invoice["seller"], reissued["invoice"]["seller"])
        self.assertEqual(689.66, reissued["invoice"]["vat_amount"])

    def test_expired_global_order_stops_increasing_every_marketplace(self) -> None:
        order = self._order()
        proof = self.service.upload_payment_proof(
            order["id"], self.tenant_id, self.actor_id,
            original_filename="payment.pdf", mime_type="application/pdf",
            content=b"%PDF-1.4\nproof",
        )
        self.service.approve_payment(proof["id"], self.actor_id)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE tenant_addon_orders SET valid_until=? WHERE id=?",
                ((datetime.now().astimezone() - timedelta(days=1)).isoformat(timespec="seconds"), order["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        entitlement = self.subscriptions.entitlement(self.tenant_id)
        for marketplace in ("kaspi", "ozon", "ozon_kz"):
            self.assertEqual(0, entitlement["marketplaces"][marketplace]["extra_positions"])
            self.assertEqual(100, entitlement["marketplaces"][marketplace]["position_limit"])

    def test_global_order_uses_postgres_returning_identity_path(self) -> None:
        # SQLite supports INSERT ... RETURNING and can therefore exercise the
        # service's PostgreSQL identity branch without a production database.
        with patch("addon_billing_service.PostgresConnection", sqlite3.Connection):
            order = self.service.create_order(
                self.tenant_id, "positions_100", 1, self.actor_id
            )
        self.assertGreater(order["id"], 0)
        self.assertEqual("", order["marketplace_code"])
        self.assertEqual('Пакет "+100 позиций"', order["invoice"]["line_items"][0]["name"])


if __name__ == "__main__":
    unittest.main()
