from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

import app as webapp

from auth_service import AuthService
from email_service import (
    EmailService,
    EmailSettings,
)
from schema import ensure_database


class PasswordResetHttpTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(
            prefix="password_reset_http_"
        )

        self.db_path = (
            Path(self.folder.name)
            / "app.db"
        )

        ensure_database(self.db_path)

        self.auth = AuthService(
            self.db_path
        )

        self.user, _ = (
            self.auth.create_initial_admin(
                "owner@example.com",
                "Platform Owner",
                "SecureGate123!",
            )
        )

        self.email = EmailService(
            self.db_path,
            EmailSettings(
                enabled=True,
                host="smtp.example.test",
                port=587,
                username="",
                password="",
                security="starttls",
                require_tls=True,
                mail_from=(
                    "spyon@example.test"
                ),
                mail_from_name="Spyon",
                reply_to=(
                    "support@example.test"
                ),
                public_url=(
                    "https://spyon.example.test"
                ),
                timeout_seconds=5,
                max_attempts=3,
            ),
        )

        self.patchers = [
            patch.object(
                webapp,
                "AUTH",
                self.auth,
            ),
            patch.object(
                webapp,
                "EMAIL",
                self.email,
            ),
            patch.object(
                webapp,
                "DB_PATH",
                self.db_path,
            ),
        ]

        for item in self.patchers:
            item.start()

        webapp.FORM_ATTEMPTS.clear()

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

    @staticmethod
    def csrf(html: str) -> str:
        match = re.search(
            r'name="csrf_token" '
            r'value="([^"]+)"',
            html,
        )

        if not match:
            raise AssertionError(
                "CSRF token was not rendered"
            )

        return match.group(1)

    def connection(self):
        conn = sqlite3.connect(
            self.db_path
        )

        conn.row_factory = sqlite3.Row

        return conn

    def test_forgot_password_email_link_resets_password(
        self,
    ) -> None:
        page = self.client.get(
            "/forgot-password"
        )

        self.assertEqual(
            200,
            page.status_code,
        )

        csrf = self.csrf(
            page.get_data(
                as_text=True
            )
        )

        response = self.client.post(
            "/forgot-password",
            data={
                "csrf_token": csrf,
                "email": "owner@example.com",
            },
            follow_redirects=False,
        )

        self.assertEqual(
            302,
            response.status_code,
        )

        conn = self.connection()

        try:
            row = conn.execute(
                """
                SELECT payload_json,status
                FROM email_outbox
                WHERE template_key=
                    'password_reset'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)

        self.assertEqual(
            "pending",
            row["status"],
        )

        payload = (
            self.email._decrypt_payload(
                row["payload_json"]
            )
        )

        action_url = str(
            payload["action_url"]
        )

        path = urlparse(
            action_url
        ).path

        self.assertTrue(
            path.startswith(
                "/reset-password/"
            )
        )

        token = path.rsplit(
            "/",
            1,
        )[-1]

        reset_page = self.client.get(
            path
        )

        self.assertEqual(
            200,
            reset_page.status_code,
        )

        reset_csrf = self.csrf(
            reset_page.get_data(
                as_text=True
            )
        )

        mismatch = self.client.post(
            path,
            data={
                "csrf_token": reset_csrf,
                "password":
                    "ChangedGate456!",
                "password_confirm":
                    "DifferentGate456!",
            },
        )

        self.assertEqual(
            200,
            mismatch.status_code,
        )

        self.assertIsNotNone(
            self.auth.auth_token_status(
                token,
                "password_reset",
            )
        )

        changed = self.client.post(
            path,
            data={
                "csrf_token": reset_csrf,
                "password":
                    "ChangedGate456!",
                "password_confirm":
                    "ChangedGate456!",
            },
            follow_redirects=False,
        )

        self.assertEqual(
            302,
            changed.status_code,
        )

        self.assertIn(
            "/login",
            changed.headers["Location"],
        )

        self.assertIsNone(
            self.auth.authenticate(
                "owner@example.com",
                "SecureGate123!",
            )
        )

        self.assertIsNotNone(
            self.auth.authenticate(
                "owner@example.com",
                "ChangedGate456!",
            )
        )

        self.assertIsNone(
            self.auth.auth_token_status(
                token,
                "password_reset",
            )
        )

        conn = self.connection()

        try:
            changed_mail = conn.execute(
                """
                SELECT COUNT(*)
                FROM email_outbox
                WHERE template_key=
                    'password_changed'
                """
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(
            1,
            int(changed_mail),
        )


if __name__ == "__main__":
    unittest.main()
