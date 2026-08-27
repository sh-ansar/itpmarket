from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as webapp

from auth_service import AuthService
from billing_service import BillingService
from notification_service import NotificationService
from schema import ensure_database
from subscription_service import (
    SubscriptionService,
)


class BillingPlatformHttpTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="billing_platform_http_"
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

        self.superadmin, _ = (
            self.auth
            .create_initial_admin(
                "root@example.com",
                "Root",
                "StrongPassword123!",
            )
        )

        self.tenant_id = int(
            self.superadmin[
                "tenant_id"
            ]
        )

        self.accountant, _ = (
            self.auth.create_user(
                "accountant@example.com",
                "Accountant",
                "StrongPassword456!",
                "viewer",
                int(
                    self.superadmin[
                        "id"
                    ]
                ),
                tenant_id=
                    self.tenant_id,
                platform_role=
                    "accountant",
            )
        )

        self.tenant_admin, _ = (
            self.auth.create_user(
                "tenant-admin@example.com",
                "Tenant Admin",
                "StrongPassword789!",
                "admin",
                int(
                    self.superadmin[
                        "id"
                    ]
                ),
                tenant_id=
                    self.tenant_id,
                platform_role="",
            )
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
            "billing-platform-http-csrf"
        )

    def tearDown(self) -> None:
        for item in reversed(
            self.patchers
        ):
            item.stop()

        self.folder.cleanup()

    def _login(
        self,
        user: dict,
    ) -> None:
        with (
            self.client
            .session_transaction()
        ) as session:
            session["user_id"] = int(
                user["id"]
            )

            session[
                "session_version"
            ] = int(
                user.get(
                    "session_version"
                )
                or 0
            )

            session[
                "csrf_token"
            ] = self.csrf

    def _headers(self) -> dict:
        return {
            "X-CSRF-Token":
                self.csrf,
        }

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
                    self.superadmin[
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
                    self.superadmin[
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
                    self.superadmin[
                        "id"
                    ]
                ),
                seller_snapshot={
                    "name":
                        "HTTP Billing Test",
                    "vat_rate":
                        0,
                },
            )
        )

    @staticmethod
    def _pdf(
        suffix: bytes = b"",
    ) -> bytes:
        return (
            b"%PDF-1.4\n"
            b"billing-http\n"
            + suffix
            + b"\n%%EOF\n"
        )

    def _proof(
        self,
        invoice: dict,
    ) -> dict:
        return (
            self.billing
            .save_payment_proof(
                int(
                    invoice["id"]
                ),
                self.tenant_id,
                int(
                    self.superadmin[
                        "id"
                    ]
                ),
                original_filename=
                    "payment.pdf",
                mime_type=
                    "application/pdf",
                content=self._pdf(
                    b"proof"
                ),
            )
        )

    def _attach_invoice_pdf(
        self,
        invoice: dict,
    ) -> bytes:
        content = self._pdf(
            b"invoice"
        )

        relative = (
            "output/invoices/http-tests/"
            "invoice-"
            + str(
                int(invoice["id"])
            )
            + ".pdf"
        )

        target = (
            self.billing
            ._normalize_invoice_file(
                relative
            )
        )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        target.write_bytes(
            content
        )

        digest = hashlib.sha256(
            content
        ).hexdigest()

        conn = self.billing._connect()

        try:
            conn.execute(
                """UPDATE subscription_invoices
                   SET
                       pdf_path=?,
                       pdf_sha256=?
                   WHERE id=?""",
                (
                    relative,
                    digest,
                    int(
                        invoice["id"]
                    ),
                ),
            )

            conn.commit()

        finally:
            conn.close()

        return content

    def test_accountant_can_list_payment_queue(
        self,
    ) -> None:
        invoice = self._invoice()

        self._login(
            self.accountant
        )

        response = self.client.get(
            "/api/platform/billing/payments"
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        payload = response.get_json()

        item = next(
            item
            for item
            in payload["items"]
            if int(
                item["invoice_id"]
            )
            == int(
                invoice["id"]
            )
        )

        self.assertEqual(
            "awaiting_payment",
            item[
                "subscription_status"
            ],
        )

        self.assertEqual(
            int(invoice["id"]),
            int(
                item["invoice_id"]
            ),
        )

    def test_tenant_admin_cannot_access_platform_billing(
        self,
    ) -> None:
        invoice = self._invoice()
        self._proof(
            invoice
        )

        self._login(
            self.tenant_admin
        )

        get_paths = (
            "/api/platform/billing/payments",
            (
                "/api/platform/billing/"
                f"invoices/{invoice['id']}/pdf"
            ),
            (
                "/api/platform/billing/"
                f"invoices/{invoice['id']}/"
                "payment-proof"
            ),
        )

        for url in get_paths:
            response = self.client.get(
                url
            )

            self.assertEqual(
                403,
                response.status_code,
                url,
            )

        for action in (
            "confirm",
            "reject",
        ):
            response = self.client.post(
                (
                    "/api/platform/billing/"
                    f"invoices/{invoice['id']}/"
                    f"{action}"
                ),
                json={
                    "review_note":
                        "no access",
                    "note":
                        "no access",
                },
                headers=self._headers(),
            )

            self.assertEqual(
                403,
                response.status_code,
                action,
            )

    def test_accountant_can_download_payment_proof(
        self,
    ) -> None:
        invoice = self._invoice()
        self._proof(
            invoice
        )

        self._login(
            self.accountant
        )

        response = self.client.get(
            (
                "/api/platform/billing/"
                f"invoices/{invoice['id']}/"
                "payment-proof"
            )
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        self.assertTrue(
            response.data.startswith(
                b"%PDF-"
            )
        )

        disposition = str(
            response.headers.get(
                "Content-Disposition"
            )
            or ""
        )

        self.assertIn(
            "payment-proof-",
            disposition,
        )

    def test_accountant_can_download_invoice_pdf(
        self,
    ) -> None:
        invoice = self._invoice()

        expected = (
            self
            ._attach_invoice_pdf(
                invoice
            )
        )

        self._login(
            self.accountant
        )

        response = self.client.get(
            (
                "/api/platform/billing/"
                f"invoices/{invoice['id']}/pdf"
            )
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        self.assertEqual(
            expected,
            response.data,
        )

        self.assertEqual(
            "application/pdf",
            response.mimetype,
        )

    def test_accountant_can_confirm_payment(
        self,
    ) -> None:
        invoice = self._invoice(
            2
        )

        self._login(
            self.accountant
        )

        response = self.client.post(
            (
                "/api/platform/billing/"
                f"invoices/{invoice['id']}/"
                "confirm"
            ),
            json={
                "note":
                    "Bank payment verified",
            },
            headers=self._headers(),
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        payload = response.get_json()

        self.assertFalse(
            payload[
                "result"
            ][
                "already_confirmed"
            ]
        )

        self.assertEqual(
            "active",
            payload[
                "result"
            ][
                "subscription"
            ][
                "status"
            ],
        )

        self.assertFalse(
            any(
                int(
                    item[
                        "invoice_id"
                    ]
                )
                == int(
                    invoice["id"]
                )
                for item
                in payload[
                    "items"
                ]
            )
        )

        inbox = NotificationService(self.db_path).list_for_user(
            int(self.tenant_admin["id"])
        )
        event = next(
            item for item in inbox["items"]
            if item["event_type"] == "payment_confirmed"
        )
        self.assertEqual("billing", event["category"])
        self.assertIn("Тариф активирован до", event["message"])

    def test_reject_requires_reason_and_updates_queue(
        self,
    ) -> None:
        invoice = self._invoice()
        self._proof(
            invoice
        )

        self._login(
            self.accountant
        )

        url = (
            "/api/platform/billing/"
            f"invoices/{invoice['id']}/"
            "reject"
        )

        response = self.client.post(
            url,
            json={},
            headers=self._headers(),
        )

        self.assertEqual(
            409,
            response.status_code,
        )

        response = self.client.post(
            url,
            json={
                "review_note":
                    "Payment not found",
            },
            headers=self._headers(),
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        payload = response.get_json()

        self.assertEqual(
            "payment_rejected",
            payload[
                "result"
            ][
                "subscription"
            ][
                "status"
            ],
        )

        item = next(
            item
            for item
            in payload[
                "items"
            ]
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
            "payment_rejected",
            item[
                "subscription_status"
            ],
        )

        self.assertEqual(
            "rejected",
            item[
                "proof"
            ][
                "status"
            ],
        )


if __name__ == "__main__":
    unittest.main()
