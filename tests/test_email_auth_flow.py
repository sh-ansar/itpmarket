from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from auth_service import AuthService
from email_service import (
    EmailService,
    EmailSettings,
    SmtpTlsRequiredError,
    TEMPLATE_SUBJECTS,
    safe_error,
)
from schema import ensure_database


class EmailAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(
            prefix="email_auth_flow_"
        )
        self.db_path = Path(self.folder.name) / "app.db"

        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)

        self.root, _ = self.auth.create_initial_admin(
            "root@example.com",
            "Platform Owner",
            "SecureGate123!",
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    def create_member(
        self,
        *,
        email: str = "member@example.com",
        verified: bool = False,
    ):
        return self.auth.create_user(
            email,
            "Member User",
            "SecureGate234!",
            "operator",
            int(self.root["id"]),
            tenant_id=int(self.root["tenant_id"]),
            email_verified=verified,
        )[0]

    def connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_new_user_is_unverified_and_login_is_not_recorded(self):
        user = self.create_member()

        self.assertFalse(user["email_verified"])
        self.assertIsNone(user["email_verified_at"])

        authenticated = self.auth.authenticate(
            "member@example.com",
            "SecureGate234!",
        )

        self.assertIsNotNone(authenticated)
        self.assertFalse(authenticated["email_verified"])

        stored = self.auth.get_user(int(user["id"]))
        self.assertIsNone(stored["last_login_at"])

    def test_verify_token_is_hashed_and_single_use(self):
        user = self.create_member()

        token = self.auth.issue_auth_token(
            int(user["id"]),
            "verify_email",
            expires_minutes=60,
        )

        conn = self.connection()
        try:
            row = conn.execute(
                """
                SELECT token_hash,consumed_at
                FROM auth_tokens
                WHERE user_id=? AND purpose='verify_email'
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(user["id"]),),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertNotEqual(token, row["token_hash"])
        self.assertNotIn(token, row["token_hash"])
        self.assertIsNone(row["consumed_at"])

        verified = self.auth.verify_email(token)

        self.assertIsNotNone(verified)
        self.assertTrue(verified["email_verified"])
        self.assertIsNotNone(verified["email_verified_at"])

        self.assertIsNone(
            self.auth.verify_email(token)
        )

    def test_expired_verification_token_is_rejected(self):
        user = self.create_member()

        token = self.auth.issue_auth_token(
            int(user["id"]),
            "verify_email",
            expires_minutes=60,
        )

        conn = self.connection()
        try:
            conn.execute(
                """
                UPDATE auth_tokens
                SET expires_at='2000-01-01T00:00:00+00:00'
                WHERE user_id=? AND purpose='verify_email'
                """,
                (int(user["id"]),),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertIsNone(
            self.auth.auth_token_status(
                token,
                "verify_email",
            )
        )
        self.assertIsNone(
            self.auth.verify_email(token)
        )

        user_after = self.auth.get_user(int(user["id"]))
        self.assertFalse(user_after["email_verified"])

    def test_password_reset_changes_password_and_session_version(self):
        user = self.create_member(verified=True)

        before_version = int(
            user.get("session_version") or 0
        )

        token = self.auth.request_password_reset(
            "member@example.com"
        )

        self.assertIsNotNone(token)

        changed = self.auth.reset_password_from_token(
            token,
            "ChangedGate456!",
        )

        self.assertIsNotNone(changed)
        self.assertEqual(
            before_version + 1,
            int(changed["session_version"]),
        )

        self.assertIsNone(
            self.auth.authenticate(
                "member@example.com",
                "SecureGate234!",
            )
        )

        authenticated = self.auth.authenticate(
            "member@example.com",
            "ChangedGate456!",
        )

        self.assertIsNotNone(authenticated)
        self.assertTrue(authenticated["email_verified"])

        self.assertIsNone(
            self.auth.reset_password_from_token(
                token,
                "AnotherPassword789!",
            )
        )

    def test_invitation_is_single_use_and_verifies_user(self):
        user, token = self.auth.create_invitation(
            email="invited@example.com",
            display_name="Invited User",
            role="viewer",
            tenant_id=int(self.root["tenant_id"]),
            actor_user_id=int(self.root["id"]),
        )

        self.assertFalse(user["email_verified"])

        accepted = self.auth.accept_invitation(
            token,
            "SecureJoin789!",
        )

        self.assertIsNotNone(accepted)
        self.assertTrue(accepted["email_verified"])
        self.assertEqual(
            int(self.root["tenant_id"]),
            int(accepted["tenant_id"]),
        )
        self.assertEqual(
            1,
            int(accepted["session_version"]),
        )

        self.assertIsNone(
            self.auth.accept_invitation(
                token,
                "AnotherPassword456!",
            )
        )

    def test_verify_email_rolls_back_if_flow_fails(self):
        user = self.create_member()

        token = self.auth.issue_auth_token(
            int(user["id"]),
            "verify_email",
            expires_minutes=60,
        )

        with patch.object(
            self.auth,
            "_event",
            side_effect=RuntimeError("forced rollback"),
        ):
            with self.assertRaises(RuntimeError):
                self.auth.verify_email(token)

        self.assertIsNotNone(
            self.auth.auth_token_status(
                token,
                "verify_email",
            )
        )

        stored = self.auth.get_user(
            int(user["id"])
        )

        self.assertFalse(
            stored["email_verified"]
        )

    def test_password_reset_rolls_back_if_flow_fails(self):
        user = self.create_member(
            verified=True
        )

        before_version = int(
            user["session_version"]
        )

        token = self.auth.request_password_reset(
            "member@example.com"
        )

        self.assertIsNotNone(token)

        with patch.object(
            self.auth,
            "_event",
            side_effect=RuntimeError("forced rollback"),
        ):
            with self.assertRaises(RuntimeError):
                self.auth.reset_password_from_token(
                    token,
                    "ChangedGate456!",
                )

        self.assertIsNotNone(
            self.auth.auth_token_status(
                token,
                "password_reset",
            )
        )

        stored = self.auth.get_user(
            int(user["id"])
        )

        self.assertEqual(
            before_version,
            int(stored["session_version"]),
        )

        self.assertIsNotNone(
            self.auth.authenticate(
                "member@example.com",
                "SecureGate234!",
            )
        )

        self.assertIsNone(
            self.auth.authenticate(
                "member@example.com",
                "ChangedGate456!",
            )
        )

    def test_invitation_rolls_back_if_flow_fails(self):
        user, token = self.auth.create_invitation(
            email="rollback-invite@example.com",
            display_name="Rollback Invite",
            role="viewer",
            tenant_id=int(
                self.root["tenant_id"]
            ),
            actor_user_id=int(
                self.root["id"]
            ),
        )

        with patch.object(
            self.auth,
            "_event",
            side_effect=RuntimeError("forced rollback"),
        ):
            with self.assertRaises(RuntimeError):
                self.auth.accept_invitation(
                    token,
                    "SecureJoin789!",
                )

        self.assertIsNotNone(
            self.auth.auth_token_status(
                token,
                "user_invitation",
            )
        )

        stored = self.auth.get_user(
            int(user["id"])
        )

        self.assertFalse(
            stored["email_verified"]
        )

        self.assertEqual(
            0,
            int(stored["session_version"]),
        )

    def test_email_outbox_payload_is_encrypted_and_deduplicated(self):
        settings = EmailSettings(
            enabled=False,
            host="smtp.example.com",
            port=587,
            username="spyon@example.com",
            password="test-only-password",
            security="starttls",
            require_tls=True,
            mail_from="spyon@example.com",
            mail_from_name="Spyon",
            reply_to="support@example.com",
            public_url="https://spyon.example.com",
            timeout_seconds=5,
            max_attempts=3,
        )

        service = EmailService(
            self.db_path,
            settings=settings,
        )

        secret_url = (
            "https://spyon.example.com/"
            "verify-email/raw-secret-token"
        )

        first_id = service.queue(
            recipient="member@example.com",
            template_key="verify_email",
            payload={
                "recipient_name": "Member",
                "action_url": secret_url,
                "action_label": "Verify email",
            },
            dedupe_key="test-email-dedupe-1",
            security=True,
        )

        second_id = service.queue(
            recipient="member@example.com",
            template_key="verify_email",
            payload={
                "recipient_name": "Member",
                "action_url": secret_url,
                "action_label": "Verify email",
            },
            dedupe_key="test-email-dedupe-1",
            security=True,
        )

        self.assertEqual(first_id, second_id)

        conn = self.connection()
        try:
            row = conn.execute(
                """
                SELECT payload_json
                FROM email_outbox
                WHERE id=?
                """,
                (int(first_id),),
            ).fetchone()
        finally:
            conn.close()

        encrypted = str(row["payload_json"])

        self.assertNotIn(
            "raw-secret-token",
            encrypted,
        )
        self.assertNotIn(
            secret_url,
            encrypted,
        )

        decrypted = service._decrypt_payload(
            encrypted
        )

        self.assertEqual(
            secret_url,
            decrypted["action_url"],
        )

    def test_safe_error_redacts_email_addresses(self):
        result = safe_error(
            "550 recipient customer@example.com rejected"
        )

        self.assertNotIn(
            "customer@example.com",
            result,
        )

        self.assertIn(
            "<redacted-email>",
            result,
        )

        multiple = safe_error(
            "from sender@example.com to receiver@example.org failed"
        )

        self.assertNotIn(
            "sender@example.com",
            multiple,
        )

        self.assertNotIn(
            "receiver@example.org",
            multiple,
        )

        self.assertEqual(
            "sensitive_smtp_error",
            safe_error(
                "authentication password leaked"
            ),
        )

    def test_outbox_postgres_claim_uses_skip_locked(self):
        settings = EmailSettings(
            enabled=True,
            host="smtp.example.com",
            port=587,
            username="",
            password="",
            security="starttls",
            require_tls=True,
            mail_from="spyon@example.com",
            mail_from_name="Spyon",
            reply_to="support@example.com",
            public_url="https://spyon.example.com",
            timeout_seconds=5,
            max_attempts=3,
        )

        service = EmailService(
            self.db_path,
            settings=settings,
        )

        class FakeCursor:
            def __init__(
                self,
                rows=None,
                rowcount=0,
            ):
                self._rows = rows or []
                self.rowcount = rowcount

            def fetchall(self):
                return list(self._rows)

        class FakePostgresConnection:
            def __init__(self):
                self.raw = object()
                self.queries = []
                self.committed = False
                self.rolled_back = False
                self.closed = False

            def execute(
                self,
                query,
                parameters=(),
            ):
                normalized = " ".join(
                    str(query).split()
                )

                self.queries.append(
                    normalized
                )

                if (
                    "SELECT id,updated_at "
                    "FROM email_outbox"
                    in normalized
                ):
                    return FakeCursor([])

                if (
                    "SELECT * FROM email_outbox"
                    in normalized
                ):
                    return FakeCursor([
                        {
                            "id": 77,
                            "status": "pending",
                            "attempt_count": 0,
                        }
                    ])

                if (
                    "UPDATE email_outbox "
                    "SET status='sending'"
                    in normalized
                ):
                    return FakeCursor(
                        rowcount=1
                    )

                raise AssertionError(
                    "Unexpected SQL: "
                    + normalized
                )

            def commit(self):
                self.committed = True

            def rollback(self):
                self.rolled_back = True

            def close(self):
                self.closed = True

        fake = FakePostgresConnection()

        with patch.object(
            service,
            "_connect",
            return_value=fake,
        ):
            claimed = service._claim_due(1)

        self.assertEqual(
            [77],
            [
                int(row["id"])
                for row in claimed
            ],
        )

        due_queries = [
            query
            for query in fake.queries
            if "SELECT * FROM email_outbox"
            in query
        ]

        self.assertEqual(
            1,
            len(due_queries),
        )

        self.assertIn(
            "FOR UPDATE SKIP LOCKED",
            due_queries[0],
        )

        stale_queries = [
            query
            for query in fake.queries
            if (
                "SELECT id,updated_at "
                "FROM email_outbox"
                in query
            )
        ]

        self.assertEqual(
            1,
            len(stale_queries),
        )

        self.assertIn(
            "FOR UPDATE SKIP LOCKED",
            stale_queries[0],
        )

        self.assertTrue(
            fake.committed
        )

        self.assertFalse(
            fake.rolled_back
        )

        self.assertTrue(
            fake.closed
        )

    def test_all_email_templates_use_spyon_branding(self):
        settings = EmailSettings(
            enabled=False,
            host="smtp.example.com",
            port=587,
            username="",
            password="",
            security="starttls",
            require_tls=True,
            mail_from="spyon@example.com",
            mail_from_name="Spyon",
            reply_to="support@example.com",
            public_url="https://spyon.example.com",
            timeout_seconds=5,
            max_attempts=3,
        )

        service = EmailService(
            self.db_path,
            settings=settings,
        )

        payload = {
            "recipient_name": "Preview User",
            "company_name": "ITP Mining",
            "marketplace": "Kaspi",
            "message": "Preview message",
            "action_url": "https://spyon.example.com/action",
            "action_label": 'Открыть Spyon',
        }

        for template_key in TEMPLATE_SUBJECTS:
            subject, plain, html = service.render(
                template_key,
                dict(payload),
            )

            combined = (
                subject
                + "\n"
                + plain
                + "\n"
                + html
            )

            self.assertNotIn(
                "MARKETPLACE INTELLIGENCE",
                combined.upper(),
            )

            self.assertIn(
                "spyon-logo.svg",
                html,
            )

            self.assertIn(
                "linear-gradient(",
                html,
            )

            self.assertIn(
                "#06a9e7",
                html,
            )

            self.assertIn(
                "#087eb4",
                html,
            )

    def test_smtp_credentials_require_tls(self):
        settings = EmailSettings(
            enabled=True,
            host="smtp.example.com",
            port=25,
            username="spyon@example.com",
            password="test-only-password",
            security="none",
            require_tls=True,
            mail_from="spyon@example.com",
            mail_from_name="Spyon",
            reply_to="support@example.com",
            public_url="https://spyon.example.com",
            timeout_seconds=5,
            max_attempts=3,
        )

        service = EmailService(
            self.db_path,
            settings=settings,
        )

        with self.assertRaises(
            SmtpTlsRequiredError
        ):
            service._validate_configuration()

    def test_user_facing_sources_do_not_contain_encoding_damage(self):
        root = Path(__file__).resolve().parents[1]

        paths = [
            root / "app.py",
            root / "telegram_bot.py",
            root / "templates" / "auth_flow_base.html",
            root / "templates" / "forgot_password.html",
            root / "templates" / "verification_sent.html",
            root / "templates" / "verify_email.html",
            root / "templates" / "email_verified.html",
            root / "templates" / "auth_token_error.html",
            root / "templates" / "reset_password.html",
            root / "templates" / "accept_invitation.html",
        ]

        email_template_dir = (
            root / "templates" / "email"
        )

        paths.extend(
            sorted(
                path
                for path in email_template_dir.iterdir()
                if path.is_file()
                and path.suffix.lower()
                in {".html", ".txt"}
            )
        )

        damaged = []

        for path in paths:
            text = path.read_text(encoding="utf-8-sig")
            if "???" in text:
                damaged.append(str(path.relative_to(root)))

        self.assertEqual(
            [],
            damaged,
            "Encoding damage found: " + ", ".join(damaged),
        )


    @unittest.skipUnless(
        os.environ.get("ITP_EMAIL_INTEGRATION_TEST") == "1",
        "real SMTP preview is disabled",
    )
    def test_send_all_email_templates_to_preview_recipient(self):
        recipient = str(
            os.environ.get(
                "ITP_EMAIL_TEST_RECIPIENT"
            )
            or ""
        ).strip()

        self.assertTrue(
            recipient
            and "@" in recipient,
            "Set ITP_EMAIL_TEST_RECIPIENT",
        )

        settings = EmailSettings.from_environment()

        self.assertTrue(
            settings.configured,
            "SMTP is not configured",
        )

        service = EmailService(
            self.db_path,
            settings=settings,
        )

        service._validate_configuration()

        # For the current mail.itpmining.kz:25 configuration,
        # credentials must never be sent before STARTTLS.
        if settings.security == "starttls":
            probe = service.smtp_probe()

            self.assertEqual(
                250,
                int(probe["ehlo_code"]),
            )

            self.assertTrue(
                probe["starttls"],
                "SMTP server does not advertise STARTTLS",
            )

        total = len(TEMPLATE_SUBJECTS)

        payload = {
            "recipient_name": "Test User",
            "company_name": "ITP Mining",
            "marketplace": "Kaspi",
            "title": 'Тестовое уведомление Spyon',
            "message": 'Тестовое уведомление Spyon для проверки внешнего вида и доставки письма.',
            "action_url": settings.public_url,
            "action_label": 'Открыть Spyon',
        }

        sent = []

        for index, (
            template_key,
            expected_subject,
        ) in enumerate(
            TEMPLATE_SUBJECTS.items(),
            start=1,
        ):
            subject, plain, html = service.render(
                template_key,
                dict(payload),
            )

            self.assertEqual(
                expected_subject,
                subject,
            )

            preview_subject = (
                f"[PREVIEW {index:02d}/{total:02d}] "
                f"{subject}"
            )

            message_id = service._smtp_send(
                recipient,
                preview_subject,
                plain,
                html,
            )

            self.assertTrue(message_id)

            sent.append(template_key)

            print(
                f"SMTP PREVIEW SENT "
                f"{index}/{total}: "
                f"{template_key}"
            )

        self.assertEqual(
            set(TEMPLATE_SUBJECTS),
            set(sent),
        )

if __name__ == "__main__":
    unittest.main()
