from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from auth_service import AuthService
from catalog_configuration_service import CatalogConfigurationService
from notification_service import NotificationService
from schema import ensure_database
from subscription_service import (
    SubscriptionLimitError,
    SubscriptionService,
)


class SubscriptionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="subscriptions_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.admin, _ = self.auth.create_initial_admin(
            "root@example.com", "Root", "StrongPassword123!"
        )
        self.tenant_id = int(self.admin["tenant_id"])
        self.service = SubscriptionService(self.db_path)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _create_limited_plan(self, code: str = "test_limited") -> dict:
        return self.service.save_plan({
            "code": code,
            "name": "Тестовый лимит",
            "description": "Проверка серверных ограничений",
            "price_amount": 12345,
            "currency": "KZT",
            "term_days": 30,
            "position_limit": 2,
            "daily_operation_limit": 2,
            "is_public": True,
            "is_active": True,
            "features": {
                "products": True,
                "operations": True,
                "reports": False,
                "schedules": False,
                "dynamic_filters": True,
                "team_management": True,
                "marketplace.kaspi": True,
                "marketplace.wildberries": False,
            },
            "marketplaces": {
                "kaspi": {
                    "is_enabled": True,
                    "position_limit": 2,
                    "daily_operation_limit": 2,
                },
                "wildberries": {
                    "is_enabled": False,
                    "position_limit": 2,
                    "daily_operation_limit": 2,
                },
            },
        }, int(self.admin["id"]))

    def _activate(self, plan_code: str) -> dict:
        request = self.service.request_plan(
            self.tenant_id, plan_code, int(self.admin["id"])
        )
        return self.service.review_subscription(
            int(request["id"]), "approved", int(self.admin["id"]),
            term_days=31, price_amount=12000,
        )

    def test_plan_approval_snapshots_price_term_features_and_marketplaces(self) -> None:
        self._create_limited_plan()
        active = self._activate("test_limited")
        entitlement = self.service.entitlement(self.tenant_id)

        self.assertEqual("active", active["status"])
        self.assertEqual(31, active["term_days"])
        self.assertEqual(12000, active["price_amount"])
        self.assertTrue(entitlement["active"])
        self.assertFalse(entitlement["features"]["reports"])
        self.assertTrue(entitlement["marketplaces"]["kaspi"]["enabled"])
        self.assertFalse(entitlement["marketplaces"]["wildberries"]["enabled"])
        self.assertEqual(2, entitlement["marketplaces"]["kaspi"]["position_limit"])

    def test_plan_change_waits_for_current_period_end(self) -> None:
        self._create_limited_plan()

        current = self._activate(
            "test_limited"
        )

        request = self.service.request_plan(
            self.tenant_id,
            "starter",
            int(self.admin["id"]),
        )

        scheduled = (
            self.service.review_subscription(
                int(request["id"]),
                "approved",
                int(self.admin["id"]),
            )
        )

        self.assertEqual(
            "scheduled",
            scheduled["status"],
        )

        self.assertEqual(
            current["ends_at"],
            scheduled["starts_at"],
        )

        entitlement = (
            self.service.entitlement(
                self.tenant_id
            )
        )

        self.assertEqual(
            "test_limited",
            entitlement["subscription"][
                "plan_code"
            ],
        )

    def test_admin_can_assign_future_package_dates(self) -> None:
        assigned = self.service.assign_plan(
            self.tenant_id,
            "starter",
            int(self.admin["id"]),
            starts_at=(
                "2030-01-01T10:00:00+05:00"
            ),
            ends_at=(
                "2030-02-01T10:00:00+05:00"
            ),
            review_note="Admin schedule",
        )

        self.assertEqual(
            "scheduled",
            assigned["status"],
        )

        self.assertEqual(
            "2030-01-01T10:00:00+05:00",
            assigned["starts_at"],
        )

        self.assertEqual(
            "2030-02-01T10:00:00+05:00",
            assigned["ends_at"],
        )

    def test_daily_operation_limit_is_enforced_and_release_is_recoverable(self) -> None:
        self._create_limited_plan()
        self._activate("test_limited")

        self.service.consume_operation(self.tenant_id, "kaspi")
        self.service.consume_operation(self.tenant_id, "kaspi")
        with self.assertRaisesRegex(SubscriptionLimitError, "лимит"):
            self.service.consume_operation(self.tenant_id, "kaspi")
        self.service.release_operation(self.tenant_id, "kaspi")
        self.service.consume_operation(self.tenant_id, "kaspi")

    def test_concurrent_operation_attempts_cannot_overshoot_daily_limit(self) -> None:
        self._create_limited_plan()
        self._activate("test_limited")

        def attempt(_: int) -> bool:
            try:
                self.service.consume_operation(self.tenant_id, "kaspi")
                return True
            except SubscriptionLimitError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = list(pool.map(attempt, range(8)))

        self.assertEqual(2, sum(accepted))
        entitlement = self.service.entitlement(self.tenant_id)
        self.assertEqual(
            2, entitlement["marketplaces"]["kaspi"]["daily_operations_used"]
        )

    def test_catalog_limit_fails_before_existing_snapshot_is_changed(self) -> None:
        self._create_limited_plan()
        self._activate("test_limited")
        catalog = CatalogConfigurationService(self.db_path)
        products = [
            {"product_id": "P1", "title": "One"},
            {"product_id": "P2", "title": "Two"},
        ]
        self.assertEqual(2, catalog.replace_catalog_products(
            self.tenant_id, "kaspi", products
        ))
        with self.assertRaisesRegex(SubscriptionLimitError, "Недостаточно позиций"):
            catalog.replace_catalog_products(
                self.tenant_id, "kaspi",
                products + [{"product_id": "P3", "title": "Three"}],
            )
        self.assertEqual(
            {("kaspi", "P1"), ("kaspi", "P2")},
            catalog.catalog_memberships(self.tenant_id, ["kaspi"]),
        )

    def test_approved_addon_increases_only_requested_marketplace_capacity(self) -> None:
        self._create_limited_plan()
        self._activate("test_limited")
        addon_request = self.service.request_addon(
            self.tenant_id, "positions_100", "kaspi", 1, int(self.admin["id"])
        )
        self.service.review_addon(
            int(addon_request["id"]), "approved", int(self.admin["id"])
        )
        entitlement = self.service.entitlement(self.tenant_id)
        self.assertEqual(102, entitlement["marketplaces"]["kaspi"]["position_limit"])
        self.assertEqual(100, entitlement["marketplaces"]["kaspi"]["extra_positions"])
        self.assertEqual(2, entitlement["marketplaces"]["wildberries"]["position_limit"])

    def test_new_approved_company_gets_legacy_compatibility_but_pending_does_not(self) -> None:
        conn = sqlite3.connect(self.db_path)
        stamp = "2026-08-11T12:00:00+05:00"
        approved = int(conn.execute(
            """INSERT INTO tenants(name,slug,status,created_at,updated_at)
               VALUES('Approved','approved','approved',?,?)""",
            (stamp, stamp),
        ).lastrowid)
        pending = int(conn.execute(
            """INSERT INTO tenants(name,slug,status,created_at,updated_at)
               VALUES('Pending','pending','pending',?,?)""",
            (stamp, stamp),
        ).lastrowid)
        conn.commit()
        conn.close()
        service = SubscriptionService(self.db_path)
        self.assertTrue(service.entitlement(approved)["active"])
        self.assertFalse(service.entitlement(pending)["active"])

    def test_trial_is_one_time_records_payment_and_creates_expiry_reminder(self) -> None:
        requested = self.service.request_plan(
            self.tenant_id, "trial", int(self.admin["id"])
        )
        active = self.service.review_subscription(
            int(requested["id"]), "approved", int(self.admin["id"])
        )
        self.assertEqual(3, active["term_days"])
        self.assertEqual(0, active["price_amount"])

        snapshot = self.service.admin_snapshot()
        payment = next(
            item for item in snapshot["payments"]
            if int(item["subscription_id"]) == int(active["id"])
        )
        self.assertEqual("trial", payment["plan_code"])
        self.assertEqual(0, payment["amount"])

        with self.assertRaisesRegex(Exception, "пробный"):
            self.service.request_plan(
                self.tenant_id, "trial", int(self.admin["id"])
            )

        notifications = NotificationService(self.db_path)
        notifications.ensure_expiry_reminders(self.tenant_id)
        inbox = notifications.list_for_user(int(self.admin["id"]))
        self.assertEqual(1, inbox["unread"])
        self.assertEqual("subscription_expiry", inbox["items"][0]["event_type"])
        # The daily reminder is idempotent even when the UI polls repeatedly.
        notifications.ensure_expiry_reminders(self.tenant_id)
        self.assertEqual(1, notifications.list_for_user(int(self.admin["id"]))["unread"])


if __name__ == "__main__":
    unittest.main()
