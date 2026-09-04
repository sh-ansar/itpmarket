from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import app as webapp
from addon_billing_service import AddonBillingService
from auth_service import AuthService
from billing_service import BillingService
from schema import ensure_database
from subscription_service import SubscriptionService


class FakeInvoicePDFService:
    def __init__(self, root: Path) -> None:
        self.root = root

    def generate(self, payload: dict) -> dict:
        path = self.root / "output" / "invoices" / payload["issued_at"][:4] / f"{payload['invoice_number']}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\naddon http\n%%EOF\n")
        return {"path": str(path), "sha256": ""}


class AddonBillingHttpUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="addon_billing_http_")
        self.root = Path(self.folder.name)
        self.db_path = self.root / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.superadmin, _ = self.auth.create_initial_admin(
            "root@example.test", "Root", "StrongPassword123!"
        )
        self.tenant_id = int(self.superadmin["tenant_id"])
        self.subscriptions = SubscriptionService(self.db_path)
        self.billing = BillingService(
            self.db_path, document_root=self.root,
            invoice_pdf_service=FakeInvoicePDFService(self.root),
        )
        self.billing.update_supplier_settings({
            "invoice_due_days": 5,
        }, int(self.superadmin["id"]))
        self.addons = AddonBillingService(
            self.db_path, document_root=self.root, billing_service=self.billing,
        )
        self._activate(self.tenant_id)
        self.accountant, _ = self.auth.create_user(
            "accountant@example.test", "Accountant", "StrongPassword456!", "viewer",
            int(self.superadmin["id"]), tenant_id=self.tenant_id, platform_role="accountant",
        )
        self.other_tenant, self.other_user = self._other_tenant()
        self.patchers = [
            patch.object(webapp, "AUTH", self.auth),
            patch.object(webapp, "DB_PATH", self.db_path),
            patch.object(webapp, "SUBSCRIPTIONS", self.subscriptions),
            patch.object(webapp, "BILLING", self.billing),
            patch.object(webapp, "ADDON_BILLING", self.addons),
        ]
        for patcher in self.patchers:
            patcher.start()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()
        self.csrf = "addon-billing-http-csrf"

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.folder.cleanup()

    def _activate(self, tenant_id: int) -> None:
        request = self.subscriptions.request_plan(tenant_id, "starter", int(self.superadmin["id"]))
        now = datetime.now().astimezone()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE tenant_subscriptions SET status='active',starts_at=?,ends_at=? WHERE id=?",
                (now.isoformat(timespec="seconds"), (now + timedelta(days=30)).isoformat(timespec="seconds"), int(request["id"])),
            )
            conn.commit()
        finally:
            conn.close()

    def _other_tenant(self) -> tuple[int, dict]:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """INSERT INTO tenants(name,slug,status,plan_code,contact_email,created_at,updated_at)
                   VALUES('Other tenant','other-tenant','approved','starter','other@example.test',?,?)""",
                (stamp, stamp),
            )
            tenant_id = int(cursor.lastrowid)
            conn.commit()
        finally:
            conn.close()
        user, _ = self.auth.create_user(
            "other@example.test", "Other", "StrongPassword789!", "admin",
            int(self.superadmin["id"]), tenant_id=tenant_id,
        )
        return tenant_id, user

    def _login(self, user: dict) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = int(user["id"])
            session["session_version"] = int(user.get("session_version") or 0)
            session["csrf_token"] = self.csrf

    def _headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    def _create(self) -> dict:
        response = self.client.post(
            "/api/addon-billing/orders", json={
                "addon_code": "positions_100", "quantity": 2,
                "unit_price": 1, "positions": 1,
            }, headers=self._headers(),
        )
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        return response.get_json()["order"]

    def test_tenant_and_accountant_payment_flow_with_isolation(self) -> None:
        self._login(self.superadmin)
        catalog = self.client.get("/api/addon-billing/catalog")
        self.assertEqual(200, catalog.status_code)
        self.assertEqual({"positions_100", "positions_500", "positions_1000"}, {item["code"] for item in catalog.get_json()["addons"]})
        order = self._create()
        self.assertEqual("", order["marketplace"])
        self.assertEqual(200, self.client.get(f"/api/addon-billing/orders/{order['id']}").status_code)
        invoice_response = self.client.get(order["invoice"]["download_url"])
        self.assertEqual(200, invoice_response.status_code)
        self.assertEqual("application/pdf", invoice_response.mimetype)
        invoice_response.close()

        reissued = self.client.post(
            f"/api/addon-billing/orders/{order['id']}/reissue",
            json={"marketplace": "wildberries", "addon_code": "positions_500", "quantity": 1}, headers=self._headers(),
        )
        self.assertEqual(200, reissued.status_code, reissued.get_data(as_text=True))
        order = reissued.get_json()["order"]
        self.assertEqual("", order["marketplace"])

        visible_orders_response = self.client.get(
            "/api/addon-billing/orders"
        )
        self.assertEqual(200, visible_orders_response.status_code)
        visible_orders = visible_orders_response.get_json()["orders"]
        self.assertEqual(
            [order["id"]],
            [item["id"] for item in visible_orders],
        )

        self._login(self.other_user)
        self.assertIn(self.client.get(f"/api/addon-billing/orders/{order['id']}").status_code, {403, 404})
        self.assertIn(self.client.get(order["invoice"]["download_url"]).status_code, {403, 404})
        self.assertIn(self.client.post(
            f"/api/addon-billing/orders/{order['id']}/payment-proof",
            data={"file": (io.BytesIO(b"%PDF-1.4\nforeign"), "foreign.pdf")}, headers=self._headers(),
        ).status_code, {403, 404})

        self._login(self.superadmin)
        invalid = self.client.post(
            f"/api/addon-billing/orders/{order['id']}/payment-proof",
            data={"file": (io.BytesIO(b"bad"), "bad.txt")}, headers=self._headers(),
        )
        self.assertEqual(409, invalid.status_code)
        proof_upload = self.client.post(
            f"/api/addon-billing/orders/{order['id']}/payment-proof",
            data={"file": (io.BytesIO(b"%PDF-1.4\nproof"), "proof.pdf")}, headers=self._headers(),
        )
        self.assertEqual(200, proof_upload.status_code, proof_upload.get_data(as_text=True))
        proof = proof_upload.get_json()["proof"]
        self.assertEqual("under_review", proof_upload.get_json()["order"]["status"])
        self.assertEqual(409, self.client.post(
            f"/api/addon-billing/orders/{order['id']}/reissue", json={}, headers=self._headers(),
        ).status_code)

        self._login(self.other_user)
        self.assertEqual(403, self.client.post(
            f"/api/platform/billing/addon-payments/{proof['id']}/approve", json={}, headers=self._headers(),
        ).status_code)
        self._login(self.accountant)
        queue = self.client.get("/api/platform/billing/addon-payments")
        self.assertEqual([proof["id"]], [item["id"] for item in queue.get_json()["items"]])
        rejected = self.client.post(
            f"/api/platform/billing/addon-payments/{proof['id']}/reject",
            json={"review_note": "Amount mismatch"}, headers=self._headers(),
        )
        self.assertEqual(200, rejected.status_code)
        self._login(self.superadmin)
        rejected_order = self.client.get(
            f"/api/addon-billing/orders/{order['id']}"
        ).get_json()["order"]
        self.assertEqual("payment_rejected", rejected_order["status"])
        self.assertEqual("Amount mismatch", rejected_order["payment_proof"]["review_note"])

        replacement = self.client.post(
            f"/api/addon-billing/orders/{order['id']}/payment-proof",
            data={"file": (io.BytesIO(b"\x89PNG\r\n\x1a\nreplacement"), "replacement.png")}, headers=self._headers(),
        )
        self.assertEqual(200, replacement.status_code)
        new_proof = replacement.get_json()["proof"]

        self._login(self.accountant)
        approved = self.client.post(
            f"/api/platform/billing/addon-payments/{new_proof['id']}/approve", json={}, headers=self._headers(),
        )
        self.assertEqual(200, approved.status_code)
        self.assertEqual(200, self.client.post(
            f"/api/platform/billing/addon-payments/{new_proof['id']}/approve", json={}, headers=self._headers(),
        ).status_code)
        self._login(self.superadmin)
        self.assertEqual("active", self.client.get(f"/api/addon-billing/orders/{order['id']}").get_json()["order"]["status"])

    def test_create_ignores_cached_frontend_marketplace_field(self) -> None:
        self._login(self.superadmin)
        response = self.client.post(
            "/api/addon-billing/orders",
            json={
                "marketplace": "ozon_kz",
                "marketplace_code": "kaspi",
                "addon_code": "positions_100",
                "quantity": 1,
            },
            headers=self._headers(),
        )
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        self.assertEqual("", response.get_json()["order"]["marketplace"])

    def test_ui_uses_invoice_backed_addon_flow(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text(encoding="utf-8")
        platform = (Path(__file__).resolve().parents[1] / "static" / "js" / "platform.js").read_text(encoding="utf-8")
        self.assertIn("Купить дополнительные позиции", source)
        self.assertIn("/api/addon-billing/orders", source)
        self.assertNotIn("/api/subscription/addons/request", source)
        self.assertNotIn("addonBillingMarketplace", source)
        self.assertNotIn("order.marketplace", source)
        self.assertIn("Дополнительные позиции применяются ко всем подключенным площадкам.", source)
        self.assertIn("дополнительных позиций", source)
        self.assertIn("позиций на каждую площадку", source)
        self.assertIn("Скачать PDF", source)
        self.assertIn("PAYMENT CONFIRMATION", source)
        self.assertIn("subscription-proof-file", source)
        self.assertIn("Сформировать счёт", source)
        self.assertIn("Отправить на проверку", source)
        self.assertIn("/api/platform/billing/addon-payments", platform)
        self.assertIn("Подтвердить оплату", platform)


if __name__ == "__main__":
    unittest.main()
