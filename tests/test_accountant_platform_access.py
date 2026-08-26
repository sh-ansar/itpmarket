from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as webapp

from auth_service import AuthService
from schema import ensure_database
from subscription_service import (
    SubscriptionService,
)
from tenant_security import (
    has_platform_permission,
)


class AccountantPlatformAccessTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="accountant_rbac_"
            )
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
            self.auth
            .create_initial_admin(
                "root@example.com",
                "Root",
                "StrongPassword123!",
            )
        )

        self.accountant, _ = (
            self.auth.create_user(
                "accountant@example.com",
                "Accountant",
                "StrongPassword456!",
                "viewer",
                int(self.admin["id"]),
                tenant_id=int(
                    self.admin[
                        "tenant_id"
                    ]
                ),
                platform_role=
                    "accountant",
            )
        )

        self.subscriptions = (
            SubscriptionService(
                self.db_path
            )
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
        ]

        for item in self.patchers:
            item.start()

        webapp.app.config.update(
            TESTING=True
        )

        self.client = (
            webapp.app.test_client()
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
            ] = "accountant-rbac-csrf"

    def test_accountant_has_billing_permissions(
        self,
    ) -> None:
        self.assertTrue(
            has_platform_permission(
                self.accountant,
                "billing.view",
            )
        )

        self.assertTrue(
            has_platform_permission(
                self.accountant,
                "billing.payment.confirm",
            )
        )

        self.assertFalse(
            has_platform_permission(
                self.accountant,
                "unknown.permission",
            )
        )

    def test_accountant_can_open_payments_page(
        self,
    ) -> None:
        self._login(
            self.accountant
        )

        response = self.client.get(
            "/platform/payments"
        )

        self.assertEqual(
            200,
            response.status_code,
        )

    def test_accountant_cannot_open_other_platform_sections(
        self,
    ) -> None:
        self._login(
            self.accountant
        )

        for path in (
            "/platform/companies",
            "/platform/packages",
            "/platform/link-rules",
        ):
            response = self.client.get(
                path
            )

            self.assertEqual(
                403,
                response.status_code,
                path,
            )

    def test_accountant_root_redirects_to_payments(
        self,
    ) -> None:
        self._login(
            self.accountant
        )

        response = self.client.get(
            "/platform",
            follow_redirects=False,
        )

        self.assertEqual(
            302,
            response.status_code,
        )

        self.assertTrue(
            response.headers[
                "Location"
            ].endswith(
                "/platform/payments"
            )
        )

    def test_accountant_api_is_payments_only(
        self,
    ) -> None:
        self._login(
            self.accountant
        )

        response = self.client.get(
            "/api/platform/overview"
            "?section=payments"
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        payload = response.get_json()

        subscriptions = payload[
            "subscriptions"
        ]

        self.assertEqual(
            {
                "payments",
                "active_subscriptions",
            },
            set(
                subscriptions.keys()
            ),
        )

        response = self.client.get(
            "/api/platform/overview"
            "?section=packages"
        )

        self.assertEqual(
            403,
            response.status_code,
        )

    def test_superadmin_keeps_platform_access(
        self,
    ) -> None:
        self._login(
            self.admin
        )

        for path in (
            "/platform/companies",
            "/platform/packages",
            "/platform/payments",
        ):
            response = self.client.get(
                path
            )

            self.assertEqual(
                200,
                response.status_code,
                path,
            )


if __name__ == "__main__":
    unittest.main()
