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

    def test_preferences_are_rendered_in_system_settings(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "app.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            'notification-preferences-card" data-settings-section="system"',
            template,
        )
        self.assertNotIn(
            'class="notification-security"',
            template,
        )
        self.assertIn(
            "const assignedSection=String(",
            script,
        )

    def test_subscription_banner_and_theme_aware_expand_icon_are_global(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "app.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        styles = (root / "static" / "css" / "app.css").read_text(encoding="utf-8")

        self.assertIn('id="subscriptionStatusBanner"', template)
        self.assertIn("function renderSubscriptionStatusBanner(snapshot)", script)
        self.assertIn("html[data-theme=\"dark\"] .profile i img", styles)
        self.assertIn("filter: brightness(0) !important", styles)


if __name__ == "__main__":
    unittest.main()
