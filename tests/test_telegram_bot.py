from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from urllib.error import URLError
from pathlib import Path
from unittest.mock import patch

os.environ["ITP_DISABLE_SCHEDULER"] = "1"
os.environ.pop("ITP_TELEGRAM_BOT_ENABLED", None)
os.environ.pop("ITP_TELEGRAM_BOT_TOKEN", None)

import app as webapp
from auth_service import AuthService
from notification_service import NotificationService
from schema import ensure_database
from telegram_bot import (
    TelegramApiError,
    TelegramBotApi,
    TelegramBotWorker,
    TelegramLinkService,
)


FAKE_TOKEN = "100000:" + "A" * 40


class FakeTelegramApi:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.deleted: list[tuple[int, int]] = []

    def send_message(self, chat_id, text, *, parse_mode=None):
        item = {
            "chat_id": int(chat_id),
            "text": str(text),
            "parse_mode": parse_mode,
            "message_id": len(self.sent) + 1,
        }
        self.sent.append(item)
        return {"message_id": item["message_id"]}

    def delete_message(self, chat_id, message_id):
        self.deleted.append((int(chat_id), int(message_id)))
        return True

    def get_me(self):
        return {"id": 100000, "username": "spyon_test_bot"}

    def set_commands(self):
        return None

    def get_updates(self, offset, timeout=10):
        return []


def telegram_update(
    chat_id: int,
    message_id: int,
    text: str,
    *,
    chat_type: str = "private",
) -> dict[str, object]:
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "text": text,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {
                "id": chat_id,
                "username": f"user_{chat_id}",
                "first_name": "Test",
                "last_name": "User",
            },
        },
    }


class TelegramBotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="telegram_bot_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.user, _ = self.auth.create_initial_admin(
            "owner@example.com", "Owner", "StrongPassword123!"
        )
        self.api = FakeTelegramApi()
        self.worker = TelegramBotWorker(
            self.db_path,
            self.auth,
            FAKE_TOKEN,
            public_url="https://spyon.kz",
            api=self.api,
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_link_token_links_active_user_without_password(self) -> None:
        chat_id = 70001
        created = self.worker.links.create_link_token(self.user)
        self.worker.handle_update(telegram_update(chat_id, 1, f"/link {created['token']}"))

        link = self.worker.links.status_for_chat(chat_id)
        self.assertIsNotNone(link)
        self.assertEqual(int(self.user["id"]), int(link["user_id"]))
        sent_text = " ".join(str(item["text"]) for item in self.api.sent)
        self.assertNotIn(created["token"], sent_text)

        conn = sqlite3.connect(self.db_path)
        try:
            token = conn.execute(
                "SELECT token_hash,used_at FROM telegram_link_tokens"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(token)
        self.assertNotEqual(created["token"], token[0])
        self.assertIsNotNone(token[1])

    def test_invalid_or_used_link_token_never_creates_second_link(self) -> None:
        chat_id = 70002
        self.worker.handle_update(telegram_update(chat_id, 1, "/link invalid"))

        self.assertIsNone(self.worker.links.status_for_chat(chat_id))
        created = self.worker.links.create_link_token(self.user)
        self.worker.handle_update(telegram_update(chat_id, 2, f"/link {created['token']}"))
        self.worker.handle_update(telegram_update(70003, 3, f"/link {created['token']}"))
        self.assertIsNone(self.worker.links.status_for_chat(70003))

    def test_link_attempts_are_rate_limited(self) -> None:
        chat_id = 70007
        for _ in range(5):
            self.worker.handle_update(telegram_update(chat_id, _ + 1, "/link invalid"))
        created = self.worker.links.create_link_token(self.user)
        self.worker.handle_update(telegram_update(chat_id, 7, f"/link {created['token']}"))

        self.assertIsNone(self.worker.links.status_for_chat(chat_id))
        self.assertIn("Код", str(self.api.sent[-1]["text"]))

    def test_login_never_requests_password(self) -> None:
        self.worker.handle_update(telegram_update(70008, 1, "/login"))
        message = str(self.api.sent[-1]["text"])
        self.assertIn("Пароль", message)
        self.assertNotIn("email", message.casefold())

    def test_group_chat_cannot_start_authentication(self) -> None:
        self.worker.handle_update(
            telegram_update(-10070003, 1, "/login", chat_type="supergroup")
        )

        self.assertIsNone(self.worker.links.status_for_chat(-10070003))
        self.assertIn("личный чат", str(self.api.sent[-1]["text"]))

    def test_delivery_is_user_scoped_and_idempotent(self) -> None:
        service = TelegramLinkService(self.db_path)
        service.link_user(
            self.user,
            chat_id=70004,
            telegram_user_id=70004,
            username="owner",
        )
        second, _ = self.auth.create_user(
            "viewer@example.com",
            "Viewer",
            "AnotherStrongPassword123!",
            "viewer",
            int(self.user["id"]),
            tenant_id=int(self.user["tenant_id"]),
        )
        notifications = NotificationService(self.db_path)
        notifications.create(
            tenant_id=int(self.user["tenant_id"]),
            user_id=int(self.user["id"]),
            category="operations",
            event_type="task_completed",
            title="Синхронизация завершена",
            message="Kaspi",
            level="success",
            dedupe_key="telegram:test:owner",
        )
        notifications.create(
            tenant_id=int(self.user["tenant_id"]),
            user_id=int(second["id"]),
            category="operations",
            event_type="task_failed",
            title="Чужая ошибка",
            level="danger",
            dedupe_key="telegram:test:viewer",
        )

        self.assertEqual(1, self.worker.deliver_pending())
        self.assertEqual(0, self.worker.deliver_pending())
        delivered = [
            item for item in self.api.sent
            if item["parse_mode"] == "HTML"
        ]
        self.assertEqual(1, len(delivered))
        self.assertIn("Синхронизация завершена", str(delivered[0]["text"]))
        self.assertNotIn("Чужая ошибка", str(delivered[0]["text"]))

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT status,attempt_count FROM telegram_notification_deliveries"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([("sent", 1)], rows)

    def test_pause_skips_paused_period_and_resume_sends_only_new_events(self) -> None:
        self.worker.links.link_user(
            self.user,
            chat_id=70005,
            telegram_user_id=70005,
        )
        notifications = NotificationService(self.db_path)
        self.worker.handle_update(telegram_update(70005, 1, "/pause"))
        notifications.create(
            tenant_id=int(self.user["tenant_id"]),
            user_id=int(self.user["id"]),
            category="system",
            event_type="paused_event",
            title="Во время паузы",
            dedupe_key="telegram:test:paused",
        )
        self.worker.handle_update(telegram_update(70005, 2, "/resume"))
        notifications.create(
            tenant_id=int(self.user["tenant_id"]),
            user_id=int(self.user["id"]),
            category="system",
            event_type="resumed_event",
            title="После возобновления",
            dedupe_key="telegram:test:resumed",
        )

        self.assertEqual(1, self.worker.deliver_pending())
        delivered = [
            str(item["text"]) for item in self.api.sent
            if item["parse_mode"] == "HTML"
        ]
        self.assertEqual(1, len(delivered))
        self.assertIn("После возобновления", delivered[0])
        self.assertNotIn("Во время паузы", delivered[0])

    def test_inactive_user_cannot_receive_queued_notification(self) -> None:
        self.worker.links.link_user(
            self.user,
            chat_id=70006,
            telegram_user_id=70006,
        )
        NotificationService(self.db_path).create(
            tenant_id=int(self.user["tenant_id"]),
            user_id=int(self.user["id"]),
            category="system",
            event_type="security_test",
            title="Не должно уйти",
            dedupe_key="telegram:test:inactive",
        )
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE app_users SET is_active=0 WHERE id=?",
                (int(self.user["id"]),),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(0, self.worker.deliver_pending())
        self.assertFalse(any(
            item["parse_mode"] == "HTML" for item in self.api.sent
        ))

    def test_migration_is_additive_and_contains_delivery_constraints(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "20260818_telegram_notifications_v1.sql"
        ).read_text(encoding="utf-8")
        upper = migration.upper()
        self.assertIn("TELEGRAM_USER_LINKS", upper)
        self.assertIn("TELEGRAM_NOTIFICATION_DELIVERIES", upper)
        self.assertIn("UNIQUE(NOTIFICATION_ID,CHAT_ID)", upper)
        for destructive in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
            self.assertNotIn(destructive, upper)
        token_migration = (
            Path(__file__).resolve().parents[1] / "migrations" / "20260831_telegram_link_tokens_v1.sql"
        ).read_text(encoding="utf-8").upper()
        self.assertIn("TELEGRAM_LINK_TOKENS", token_migration)
        self.assertIn("TOKEN_HASH", token_migration)
        self.assertNotIn("DROP TABLE", token_migration)


class TelegramApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="telegram_api_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.user, _ = self.auth.create_initial_admin(
            "api-owner@example.com", "API Owner", "StrongPassword123!"
        )
        self.links = TelegramLinkService(self.db_path)
        self.patchers = [
            patch.object(webapp, "AUTH", self.auth),
            patch.object(webapp, "DB_PATH", self.db_path),
            patch.object(webapp, "TELEGRAM_LINKS", self.links),
            patch.object(webapp, "TELEGRAM_WORKER", None),
            patch.dict(
                os.environ,
                {
                    "ITP_TELEGRAM_BOT_ENABLED": "1",
                    "ITP_TELEGRAM_BOT_USERNAME": "spyon_test_bot",
                },
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = int(self.user["id"])
            session["csrf_token"] = "telegram-csrf"

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.folder.cleanup()

    def test_status_is_personal_and_disconnect_requires_session_csrf(self) -> None:
        self.links.link_user(
            self.user,
            chat_id=80001,
            telegram_user_id=80001,
            username="api_owner",
        )
        status = self.client.get("/api/telegram/status")
        self.assertEqual(200, status.status_code)
        payload = status.get_json()
        self.assertTrue(payload["available"])
        self.assertEqual("api_owner", payload["link"]["telegram_username"])
        self.assertNotIn("chat_id", payload["link"])

        denied = self.client.post("/api/telegram/disconnect", json={})
        self.assertEqual(419, denied.status_code)
        disconnected = self.client.post(
            "/api/telegram/disconnect",
            json={},
            headers={"X-CSRF-Token": "telegram-csrf"},
        )
        self.assertEqual(200, disconnected.status_code)
        self.assertTrue(disconnected.get_json()["disconnected"])
        self.assertIsNone(self.links.status_for_user(int(self.user["id"])))

    def test_account_link_token_is_returned_once_and_only_hash_is_stored(self) -> None:
        response = self.client.post(
            "/api/account/telegram/link-token", json={}, headers={"X-CSRF-Token": "telegram-csrf"}
        )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["command"].startswith("/link "))
        self.assertEqual("spyon_test_bot", payload["bot_username"])
        conn = sqlite3.connect(self.db_path)
        try:
            stored = conn.execute("SELECT token_hash,used_at,revoked_at FROM telegram_link_tokens").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(stored)
        self.assertNotEqual(payload["token"], stored[0])
        self.assertIsNone(stored[1])
        self.assertIsNone(stored[2])

    def test_network_error_never_contains_bot_token(self) -> None:
        client = TelegramBotApi(FAKE_TOKEN)
        with patch("telegram_bot.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(TelegramApiError) as raised:
                client.get_me()
        self.assertNotIn(FAKE_TOKEN, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
