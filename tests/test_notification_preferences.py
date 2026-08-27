from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auth_service import AuthService
from notification_service import NotificationService
from schema import ensure_database


class _EmailRecorder:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def queue_notification(self, **payload: object) -> None:
        self.items.append(dict(payload))


class NotificationPreferencesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="notification_preferences_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        user, _ = AuthService(self.db_path).create_initial_admin(
            "owner@example.com", "Owner", "StrongPassword123!"
        )
        self.user_id = int(user["id"])
        self.tenant_id = int(user["tenant_id"])
        self.email = _EmailRecorder()
        self.service = NotificationService(self.db_path, self.email)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_channel_preferences_filter_in_app_and_email_delivery(self) -> None:
        self.service.save_preferences(
            self.user_id,
            {
                "operations": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "telegram_enabled": True,
                }
            },
        )

        self.service.create(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            category="operations",
            event_type="task_completed",
            title="Completed",
            dedupe_key="operations-hidden",
        )

        self.assertEqual([], self.service.list_for_user(self.user_id)["items"])
        self.assertEqual([], self.email.items)

    def test_security_delivery_cannot_be_disabled(self) -> None:
        self.service.save_preferences(
            self.user_id,
            {
                "security": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "telegram_enabled": False,
                }
            },
        )

        self.service.create(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            category="security",
            event_type="password_changed",
            title="Password changed",
            dedupe_key="security-required",
        )

        self.assertEqual(1, len(self.service.list_for_user(self.user_id)["items"]))
        self.assertEqual(1, len(self.email.items))


if __name__ == "__main__":
    unittest.main()
