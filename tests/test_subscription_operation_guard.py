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
from saas_service import SaaSService
from schema import ensure_database
from subscription_service import (
    NO_ACTIVE_SUBSCRIPTION_MESSAGE,
    SubscriptionService,
)


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
        self.saas.set_marketplace_access(
            self.tenant_id, ["halyk_market"], int(self.superadmin["id"])
        )
        self.tasks = RecordingTasks()
        self.patchers = [
            patch.object(webapp, "AUTH", self.auth),
            patch.object(webapp, "DB_PATH", self.db_path),
            patch.object(webapp, "SAAS", self.saas),
            patch.object(webapp, "SUBSCRIPTIONS", self.subscriptions),
            patch.object(webapp, "TASKS", self.tasks),
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

    def test_service_reinitialization_auto_creates_legacy_and_allows_operation(self) -> None:
        self.assertFalse(self.subscriptions.entitlement(self.tenant_id)["active"])

        legacy_service = SubscriptionService(self.db_path)
        legacy = legacy_service.entitlement(self.tenant_id)
        self.assertTrue(legacy["active"])
        self.assertEqual("legacy", legacy["subscription"]["plan_code"])
        self.assertIsNone(legacy_service.operation_error(self.tenant_id, "halyk_market"))

        with patch.object(webapp, "SUBSCRIPTIONS", legacy_service):
            response = self._start_operation()

        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        self.assertEqual(1, len(self.tasks.start_calls))
        self.assertEqual(1, self._usage_count())


if __name__ == "__main__":
    unittest.main()
