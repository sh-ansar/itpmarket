from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


os.environ["ITP_DISABLE_SCHEDULER"] = "1"

import app as webapp
from auth_service import AuthService
from catalog_configuration_service import CatalogConfigurationService
from saas_service import SaaSService
from schema import ensure_database
from subscription_service import (
    NO_ACTIVE_SUBSCRIPTION_MESSAGE,
    SubscriptionService,
)
from tests.subscription_fixtures import activate_legacy_subscription


class RecordingTasks:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []

    def start(
        self,
        action: str,
        label: str,
        command: list[str],
        resources: list[str],
        **kwargs: object,
    ) -> dict[str, object]:
        self.start_calls.append(
            {
                "action": action,
                "label": label,
                "command": command,
                "resources": resources,
                **kwargs,
            }
        )
        return {
            "id": "subscription-guard-task",
            "action": action,
            "name": label,
            "status": "queued",
            "running": False,
            "metadata": kwargs.get("metadata") or {},
        }


class SubscriptionOperationGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="subscription_operation_guard_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.superadmin, _ = self.auth.create_initial_admin(
            "root@example.test", "Root", "StrongPassword123!"
        )
        # Seed plans before the test company exists. This produces a genuine
        # approved tenant without an active subscription until a later service
        # initialization runs the documented legacy compatibility lifecycle.
        self.subscriptions = SubscriptionService(self.db_path)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        conn = sqlite3.connect(self.db_path)
        try:
            tenant_id = int(
                conn.execute(
                    """INSERT INTO tenants(
                           name,slug,status,plan_code,registration_number,
                           contact_email,contact_phone,created_at,updated_at,approved_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "No Subscription Company",
                        "no-subscription-company",
                        "approved",
                        "",
                        "BIN-NO-SUB-1",
                        "company@example.test",
                        "+77000000001",
                        stamp,
                        stamp,
                        stamp,
                    ),
                ).lastrowid
            )
            conn.commit()
        finally:
            conn.close()
        self.tenant_id = tenant_id
        # Materialize the standard tenant roles/permissions for the company
        # inserted after the initial schema pass, without creating a plan.
        ensure_database(self.db_path)
        self.user, _ = self.auth.create_user(
            "admin@example.test",
            "Company Admin",
            "StrongPassword456!",
            "admin",
            int(self.superadmin["id"]),
            tenant_id=self.tenant_id,
            email_verified=True,
        )
        self.saas = SaaSService(self.db_path)
        self.catalog = CatalogConfigurationService(self.db_path)
        self.saas.set_marketplace_access(
            self.tenant_id, ["halyk_market"], int(self.superadmin["id"])
        )
        self.tasks = RecordingTasks()
        self.patchers = [
            patch.object(webapp, "AUTH", self.auth),
            patch.object(webapp, "DB_PATH", self.db_path),
            patch.object(webapp, "SAAS", self.saas),
            patch.object(webapp, "CATALOG", self.catalog),
            patch.object(webapp, "SUBSCRIPTIONS", self.subscriptions),
            patch.object(webapp, "TASKS", self.tasks),
            patch.object(
                webapp,
                "allowed_marketplaces",
                return_value={
                    "halyk_market"
                },
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()
        self.csrf = "subscription-guard-csrf"
        with self.client.session_transaction() as session:
            session["user_id"] = int(self.user["id"])
            session["session_version"] = int(self.user.get("session_version") or 0)
            session["csrf_token"] = self.csrf

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.folder.cleanup()

    def _usage_count(self) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            return int(
                conn.execute(
                    "SELECT COALESCE(SUM(used_count),0) FROM tenant_daily_usage WHERE tenant_id=?",
                    (self.tenant_id,),
                ).fetchone()[0]
            )
        finally:
            conn.close()

    def _start_operation(self):
        return self.client.post(
            "/api/tasks/start",
            json={"action": "halyk_full_sync", "scope": "all"},
            headers={"X-CSRF-Token": self.csrf},
        )

    def _activate_legacy(self) -> None:
        activate_legacy_subscription(
            self.db_path,
            self.tenant_id,
            actor_user_id=int(self.superadmin["id"]),
        )

    def _post_action(self, action: str, tenant_seller_id: int | None = None):
        payload: dict[str, object] = {"action": action, "scope": "all"}
        if tenant_seller_id is not None:
            payload["tenant_seller_id"] = tenant_seller_id
        return self.client.post(
            "/api/tasks/start",
            json=payload,
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_price_actualization_requires_real_catalog_before_side_effects(self) -> None:
        self._activate_legacy()
        with (
            patch.object(webapp, "build_action_command") as build,
            patch.object(self.subscriptions, "consume_operation") as consume,
        ):
            response = self._post_action("halyk_price_actualize")
        self.assertEqual(409, response.status_code)
        self.assertEqual(
            "Сначала выполните сбор каталога.", response.get_json()["error"]
        )
        build.assert_not_called()
        consume.assert_not_called()
        self.assertEqual([], self.tasks.start_calls)
        self.assertEqual(0, self._usage_count())

    def test_price_actualization_passes_after_real_catalog_exists(self) -> None:
        self._activate_legacy()
        self.catalog.upsert_catalog_product(
            self.tenant_id,
            "halyk_market",
            {"product_id": "READY-1", "title": "Ready product"},
        )
        with patch.object(
            webapp, "build_action_command", return_value=["collector", "prices"]
        ) as build:
            response = self._post_action("halyk_price_actualize")
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        build.assert_called_once()
        self.assertEqual(1, len(self.tasks.start_calls))
        self.assertEqual(1, self._usage_count())

    def test_price_actualization_readiness_is_seller_scoped(self) -> None:
        self._activate_legacy()
        conn = sqlite3.connect(self.db_path)
        try:
            seller_ids = []
            for external_id in ("seller-a", "seller-b"):
                seller_ids.append(int(conn.execute(
                    """INSERT INTO tenant_marketplace_sellers(
                           tenant_id,marketplace_code,external_seller_id,display_name,
                           source_url,status,approval_status,created_at,updated_at
                       ) VALUES(?,'halyk_market',?,?,?,'active','approved',datetime('now'),datetime('now'))""",
                    (
                        self.tenant_id,
                        external_id,
                        external_id,
                        f"https://halykmarket.kz/merchant/{external_id}",
                    ),
                ).lastrowid))
            conn.commit()
        finally:
            conn.close()
        seller_a, seller_b = seller_ids
        self.catalog.replace_catalog_products(
            self.tenant_id,
            "halyk_market",
            [{"product_id": "SELLER-A-1", "title": "Seller A product"}],
            tenant_seller_id=seller_a,
        )
        with patch.object(
            webapp, "build_action_command", return_value=["collector", "prices"]
        ):
            allowed = self._post_action("halyk_price_actualize", seller_a)
            blocked = self._post_action("halyk_price_actualize", seller_b)
        self.assertEqual(200, allowed.status_code, allowed.get_data(as_text=True))
        self.assertEqual(409, blocked.status_code)
        self.assertEqual(
            "Сначала выполните сбор каталога.", blocked.get_json()["error"]
        )
        self.assertEqual(1, len(self.tasks.start_calls))
        self.assertEqual(1, self._usage_count())

        onboarding = webapp.tenant_onboarding_state(
            self.auth.get_user(int(self.user["id"])) or self.user
        )
        seller_readiness = {
            int(item["id"]): item["catalog_ready"]
            for item in onboarding["marketplace_sellers"]["halyk_market"]
        }
        self.assertTrue(onboarding["catalog_ready"]["halyk_market"])
        self.assertEqual({seller_a: True, seller_b: False}, seller_readiness)

    def test_catalog_collection_and_platform_full_sync_do_not_need_catalog(self) -> None:
        self._activate_legacy()
        with patch.object(
            webapp, "build_action_command", return_value=["collector", "catalog"]
        ):
            catalog_response = self._post_action("halyk_catalog_collect")
            full_sync_response = self._post_action("halyk_full_sync")
        self.assertEqual(200, catalog_response.status_code)
        self.assertEqual(200, full_sync_response.status_code)
        self.assertEqual(2, len(self.tasks.start_calls))
        self.assertEqual(2, self._usage_count())

    def test_true_no_active_subscription_cannot_create_task_or_consume_usage(self) -> None:
        entitlement = self.subscriptions.entitlement(self.tenant_id)
        self.assertFalse(entitlement["active"])
        self.assertEqual(0, self._usage_count())

        response = self._start_operation()

        self.assertEqual(409, response.status_code)
        self.assertEqual(
            {"ok": False, "error": NO_ACTIVE_SUBSCRIPTION_MESSAGE},
            response.get_json(),
        )
        self.assertEqual([], self.tasks.start_calls)
        self.assertEqual(0, self._usage_count())

    def test_service_reinitialization_does_not_grant_legacy_to_unsubscribed_tenant(self) -> None:
        self.assertFalse(
            self.subscriptions.entitlement(
                self.tenant_id
            )["active"]
        )

        reinitialized = SubscriptionService(
            self.db_path
        )

        entitlement = reinitialized.entitlement(
            self.tenant_id
        )

        self.assertFalse(
            entitlement["active"]
        )
        self.assertIsNone(
            entitlement["subscription"]
        )

        with patch.object(
            webapp,
            "SUBSCRIPTIONS",
            reinitialized,
        ):
            response = self._start_operation()

        self.assertEqual(
            409,
            response.status_code,
        )
        self.assertEqual(
            {
                "ok": False,
                "error": NO_ACTIVE_SUBSCRIPTION_MESSAGE,
            },
            response.get_json(),
        )
        self.assertEqual(
            [],
            self.tasks.start_calls,
        )
        self.assertEqual(
            0,
            self._usage_count(),
        )

    def test_existing_legacy_subscription_remains_operational(self) -> None:
        self._activate_legacy()

        service = SubscriptionService(
            self.db_path
        )

        entitlement = service.entitlement(
            self.tenant_id
        )

        self.assertTrue(
            entitlement["active"]
        )
        self.assertEqual(
            "legacy",
            entitlement[
                "subscription"
            ]["plan_code"],
        )

        self.assertIsNone(
            service.operation_error(
                self.tenant_id,
                "halyk_market",
            )
        )

        with patch.object(
            webapp,
            "SUBSCRIPTIONS",
            service,
        ):
            response = self._start_operation()

        self.assertEqual(
            200,
            response.status_code,
            response.get_data(
                as_text=True
            ),
        )
        self.assertEqual(
            1,
            len(
                self.tasks.start_calls
            ),
        )
        self.assertEqual(
            1,
            self._usage_count(),
        )


if __name__ == "__main__":
    unittest.main()
