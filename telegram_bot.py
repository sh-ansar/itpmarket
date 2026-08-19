from __future__ import annotations

import hashlib
import html
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from auth_service import AuthService
from notification_service import now_iso
from storage.postgres_compat import PostgresConnection, connect_database


LOGGER = logging.getLogger("spyon.telegram")
AUTH_MAX_ATTEMPTS = 5
AUTH_WINDOW_SECONDS = 15 * 60
SESSION_TTL_SECONDS = 5 * 60
DELIVERY_BATCH_SIZE = 30


class TelegramApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: int = 0,
        retry_after: int = 0,
    ) -> None:
        super().__init__(message)
        self.error_code = int(error_code or 0)
        self.retry_after = max(0, int(retry_after or 0))


class TelegramBotApi:
    """Small Bot API client that never logs or returns the bot token."""

    def __init__(self, token: str) -> None:
        value = str(token or "").strip()
        if not value or ":" not in value:
            raise ValueError("Telegram bot token is missing or invalid.")
        self._base_url = f"https://api.telegram.org/bot{value}/"

    def request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 20.0,
    ) -> Any:
        values: dict[str, str] = {}
        for key, value in (payload or {}).items():
            if value is None:
                continue
            if isinstance(value, bool):
                values[key] = "true" if value else "false"
            elif isinstance(value, (list, dict)):
                values[key] = json.dumps(value, ensure_ascii=False)
            else:
                values[key] = str(value)
        body = urlencode(values).encode("utf-8")
        request = Request(
            self._base_url + str(method),
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(2.0, float(timeout))) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
        except (URLError, TimeoutError, OSError) as exc:
            raise TelegramApiError(
                f"Telegram network error ({type(exc).__name__})."
            ) from None
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise TelegramApiError("Telegram returned an invalid response.") from None
        if not isinstance(result, dict) or not result.get("ok"):
            parameters = result.get("parameters") if isinstance(result, dict) else {}
            retry_after = (
                parameters.get("retry_after", 0)
                if isinstance(parameters, dict) else 0
            )
            description = (
                str(result.get("description") or "Telegram API request failed.")
                if isinstance(result, dict)
                else "Telegram API request failed."
            )
            raise TelegramApiError(
                description[:240],
                error_code=int(result.get("error_code") or 0)
                if isinstance(result, dict) else 0,
                retry_after=int(retry_after or 0),
            )
        return result.get("result")

    def get_me(self) -> dict[str, Any]:
        result = self.request("getMe")
        return result if isinstance(result, dict) else {}

    def get_updates(self, offset: int, timeout: int = 10) -> list[dict[str, Any]]:
        result = self.request(
            "getUpdates",
            {
                "offset": int(offset),
                "timeout": max(0, min(int(timeout), 30)),
                "allowed_updates": ["message"],
            },
            timeout=max(5, int(timeout) + 5),
        )
        return [item for item in (result or []) if isinstance(item, dict)]

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        result = self.request(
            "sendMessage",
            {
                "chat_id": int(chat_id),
                "text": str(text)[:4096],
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
        )
        return result if isinstance(result, dict) else {}

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        return bool(self.request(
            "deleteMessage",
            {"chat_id": int(chat_id), "message_id": int(message_id)},
        ))

    def set_commands(self) -> None:
        self.request(
            "setMyCommands",
            {
                "commands": [
                    {"command": "login", "description": "Войти в Spyon"},
                    {"command": "status", "description": "Статус подключения"},
                    {"command": "notifications", "description": "Последние уведомления"},
                    {"command": "pause", "description": "Приостановить уведомления"},
                    {"command": "resume", "description": "Возобновить уведомления"},
                    {"command": "logout", "description": "Отвязать Telegram"},
                    {"command": "help", "description": "Справка"},
                ]
            },
        )


class TelegramLinkService:
    """Tenant-safe Telegram links and idempotent notification delivery state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection | PostgresConnection:
        conn = connect_database(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _public_link(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        return {
            "user_id": int(value["user_id"]),
            "tenant_id": int(value["tenant_id"])
            if value.get("tenant_id") is not None else None,
            "chat_id": int(value["chat_id"]),
            "telegram_user_id": int(value["telegram_user_id"]),
            "telegram_username": str(value.get("telegram_username") or ""),
            "telegram_display_name": str(
                value.get("telegram_display_name") or ""
            ),
            "is_enabled": bool(value.get("is_enabled")),
            "linked_at": str(value.get("linked_at") or ""),
            "updated_at": str(value.get("updated_at") or ""),
        }

    def link_user(
        self,
        user: dict[str, Any],
        *,
        chat_id: int,
        telegram_user_id: int,
        username: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        user_id = int(user["id"])
        tenant_id = (
            int(user["tenant_id"]) if user.get("tenant_id") is not None else None
        )
        stamp = now_iso()
        conn = self._connect()
        try:
            start_row = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM app_notifications"
            ).fetchone()
            start_id = int(start_row[0] or 0)
            conn.execute(
                "UPDATE telegram_notification_deliveries SET status='failed',last_error='link_replaced',updated_at=? WHERE status IN ('pending','retry') AND (user_id=? OR chat_id=?)",
                (stamp, user_id, int(chat_id)),
            )
            conn.execute(
                "DELETE FROM telegram_user_links WHERE user_id=? OR chat_id=? OR telegram_user_id=?",
                (user_id, int(chat_id), int(telegram_user_id)),
            )
            conn.execute(
                """INSERT INTO telegram_user_links(
                       user_id,tenant_id,chat_id,telegram_user_id,
                       telegram_username,telegram_display_name,is_enabled,
                       notification_start_id,linked_at,updated_at
                   ) VALUES(?,?,?,?,?,?,1,?,?,?)""",
                (
                    user_id, tenant_id, int(chat_id), int(telegram_user_id),
                    str(username or "")[:120], str(display_name or "")[:200],
                    start_id, stamp, stamp,
                ),
            )
            self._event(
                conn, user_id, tenant_id, "telegram_linked", str(chat_id),
                {"telegram_username": str(username or "")[:120]},
            )
            conn.commit()
            return self.status_for_user(user_id, connection=conn) or {}
        finally:
            conn.close()

    def _event(
        self,
        conn: sqlite3.Connection | PostgresConnection,
        user_id: int,
        tenant_id: int | None,
        event_type: str,
        entity_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO app_events(
                   user_id,event_type,entity_type,entity_id,details_json,
                   created_at,tenant_id
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                int(user_id), str(event_type), "telegram", str(entity_id),
                json.dumps(details or {}, ensure_ascii=False), now_iso(), tenant_id,
            ),
        )

    def status_for_user(
        self,
        user_id: int,
        *,
        connection: sqlite3.Connection | PostgresConnection | None = None,
    ) -> dict[str, Any] | None:
        conn = connection or self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM telegram_user_links WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            return self._public_link(row)
        finally:
            if connection is None:
                conn.close()

    def status_for_chat(self, chat_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM telegram_user_links WHERE chat_id=?",
                (int(chat_id),),
            ).fetchone()
            return self._public_link(row)
        finally:
            conn.close()

    def set_enabled(self, user_id: int, enabled: bool) -> bool:
        stamp = now_iso()
        conn = self._connect()
        try:
            if enabled:
                start_row = conn.execute(
                    "SELECT COALESCE(MAX(id),0) FROM app_notifications"
                ).fetchone()
                cursor = conn.execute(
                    """UPDATE telegram_user_links
                       SET is_enabled=1,notification_start_id=?,updated_at=?
                       WHERE user_id=?""",
                    (int(start_row[0] or 0), stamp, int(user_id)),
                )
            else:
                cursor = conn.execute(
                    "UPDATE telegram_user_links SET is_enabled=0,updated_at=? WHERE user_id=?",
                    (stamp, int(user_id)),
                )
                conn.execute(
                    """UPDATE telegram_notification_deliveries
                       SET status='failed',last_error='notifications_paused',updated_at=?
                       WHERE user_id=? AND status IN ('pending','retry')""",
                    (stamp, int(user_id)),
                )
            conn.commit()
            return int(cursor.rowcount) > 0
        finally:
            conn.close()

    def unlink_user(self, user_id: int, *, actor_user_id: int | None = None) -> bool:
        stamp = now_iso()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT tenant_id,chat_id FROM telegram_user_links WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                """UPDATE telegram_notification_deliveries
                   SET status='failed',last_error='link_removed',updated_at=?
                   WHERE user_id=? AND status IN ('pending','retry')""",
                (stamp, int(user_id)),
            )
            cursor = conn.execute(
                "DELETE FROM telegram_user_links WHERE user_id=?",
                (int(user_id),),
            )
            self._event(
                conn,
                int(actor_user_id or user_id),
                int(row["tenant_id"]) if row["tenant_id"] is not None else None,
                "telegram_unlinked",
                str(row["chat_id"]),
            )
            conn.commit()
            return int(cursor.rowcount) > 0
        finally:
            conn.close()

    def unlink_chat(self, chat_id: int) -> bool:
        link = self.status_for_chat(chat_id)
        if not link:
            return False
        return self.unlink_user(int(link["user_id"]))

    def latest_notifications(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(
                """SELECT id,category,event_type,level,title,message,action_url,created_at
                   FROM app_notifications WHERE user_id=?
                   ORDER BY id DESC LIMIT ?""",
                (int(user_id), max(1, min(int(limit), 20))),
            ).fetchall()]
        finally:
            conn.close()

    def unread_count(self, user_id: int) -> int:
        conn = self._connect()
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM app_notifications WHERE user_id=? AND read_at IS NULL",
                (int(user_id),),
            ).fetchone()[0])
        finally:
            conn.close()

    def active_tenant_ids(self) -> list[int]:
        conn = self._connect()
        try:
            return [int(row[0]) for row in conn.execute(
                """SELECT DISTINCT l.tenant_id
                   FROM telegram_user_links l
                   JOIN app_users u ON u.id=l.user_id AND u.is_active=1
                   JOIN tenant_users tu
                     ON tu.tenant_id=l.tenant_id
                    AND tu.user_id=l.user_id
                    AND tu.is_active=1
                   WHERE l.is_enabled=1 AND l.tenant_id IS NOT NULL
                   ORDER BY l.tenant_id"""
            ).fetchall()]
        finally:
            conn.close()

    def enqueue_notifications(self, limit: int = 200) -> int:
        stamp = now_iso()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """INSERT INTO telegram_notification_deliveries(
                       notification_id,user_id,tenant_id,chat_id,status,
                       attempt_count,next_attempt_at,created_at,updated_at
                   )
                   SELECT n.id,n.user_id,n.tenant_id,l.chat_id,'pending',0,?,?,?
                   FROM app_notifications n
                   JOIN telegram_user_links l ON l.user_id=n.user_id
                   JOIN app_users u ON u.id=n.user_id AND u.is_active=1
                    WHERE l.is_enabled=1
                      AND n.id>l.notification_start_id
                      AND (
                          n.category='security' OR COALESCE((
                              SELECT p.telegram_enabled FROM notification_preferences p
                              WHERE p.user_id=n.user_id AND p.category=n.category
                          ),1)=1
                      )
                     AND (
                         l.tenant_id IS NULL OR EXISTS(
                             SELECT 1 FROM tenant_users tu
                             WHERE tu.tenant_id=l.tenant_id
                               AND tu.user_id=l.user_id
                               AND tu.is_active=1
                         )
                     )
                     AND NOT EXISTS(
                         SELECT 1 FROM telegram_notification_deliveries d
                         WHERE d.notification_id=n.id AND d.chat_id=l.chat_id
                     )
                   ORDER BY n.id LIMIT ?
                   ON CONFLICT(notification_id,chat_id) DO NOTHING""",
                (stamp, stamp, stamp, max(1, min(int(limit), 1000))),
            )
            conn.commit()
            return max(0, int(cursor.rowcount))
        finally:
            conn.close()

    def pending_deliveries(self, limit: int = DELIVERY_BATCH_SIZE) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(
                """SELECT d.id,d.notification_id,d.user_id,d.tenant_id,d.chat_id,
                          d.attempt_count,n.level,n.title,n.message,n.action_url,
                          n.created_at
                   FROM telegram_notification_deliveries d
                   JOIN app_notifications n ON n.id=d.notification_id
                   JOIN telegram_user_links l
                     ON l.user_id=d.user_id AND l.chat_id=d.chat_id
                   JOIN app_users u ON u.id=d.user_id AND u.is_active=1
                    WHERE l.is_enabled=1
                      AND (
                          n.category='security' OR COALESCE((
                              SELECT p.telegram_enabled FROM notification_preferences p
                              WHERE p.user_id=n.user_id AND p.category=n.category
                          ),1)=1
                      )
                      AND (
                         l.tenant_id IS NULL OR EXISTS(
                             SELECT 1 FROM tenant_users tu
                             WHERE tu.tenant_id=l.tenant_id
                               AND tu.user_id=l.user_id
                               AND tu.is_active=1
                         )
                     )
                     AND d.status IN ('pending','retry')
                     AND d.next_attempt_at<=?
                   ORDER BY d.id LIMIT ?""",
                (now_iso(), max(1, min(int(limit), 100))),
            ).fetchall()]
        finally:
            conn.close()

    def mark_sent(self, delivery_id: int, telegram_message_id: int | None) -> None:
        stamp = now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE telegram_notification_deliveries
                   SET status='sent',attempt_count=attempt_count+1,
                       telegram_message_id=?,last_error='',updated_at=?,sent_at=?
                   WHERE id=?""",
                (telegram_message_id, stamp, stamp, int(delivery_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_failed(
        self,
        delivery_id: int,
        *,
        error: str,
        retry_after: int = 0,
        permanent: bool = False,
        disable_link: bool = False,
    ) -> None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT attempt_count,user_id FROM telegram_notification_deliveries WHERE id=?",
                (int(delivery_id),),
            ).fetchone()
            if row is None:
                return
            attempts = int(row["attempt_count"] or 0) + 1
            final = bool(permanent or attempts >= 5)
            delay = max(int(retry_after or 0), min(900, 10 * (2 ** min(attempts, 6))))
            next_attempt = (
                datetime.now().astimezone() + timedelta(seconds=delay)
            ).isoformat(timespec="seconds")
            conn.execute(
                """UPDATE telegram_notification_deliveries
                   SET status=?,attempt_count=?,next_attempt_at=?,last_error=?,updated_at=?
                   WHERE id=?""",
                (
                    "failed" if final else "retry", attempts, next_attempt,
                    str(error or "telegram_delivery_failed")[:240], now_iso(),
                    int(delivery_id),
                ),
            )
            if disable_link:
                conn.execute(
                    "UPDATE telegram_user_links SET is_enabled=0,updated_at=? WHERE user_id=?",
                    (now_iso(), int(row["user_id"])),
                )
            conn.commit()
        finally:
            conn.close()

    def bot_offset(self, token_fingerprint: str) -> int:
        conn = self._connect()
        try:
            fingerprint_row = conn.execute(
                "SELECT value FROM metadata WHERE key='telegram_bot_fingerprint'"
            ).fetchone()
            stored = str(fingerprint_row[0]) if fingerprint_row else ""
            if stored != token_fingerprint:
                conn.execute(
                    """INSERT INTO metadata(key,value) VALUES('telegram_bot_fingerprint',?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (str(token_fingerprint),),
                )
                conn.execute(
                    """INSERT INTO metadata(key,value) VALUES('telegram_bot_update_offset','0')
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
                )
                conn.commit()
                return 0
            row = conn.execute(
                "SELECT value FROM metadata WHERE key='telegram_bot_update_offset'"
            ).fetchone()
            return max(0, int(row[0] or 0)) if row else 0
        finally:
            conn.close()

    def save_bot_offset(self, offset: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO metadata(key,value) VALUES('telegram_bot_update_offset',?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (str(max(0, int(offset))),),
            )
            conn.commit()
        finally:
            conn.close()


class TelegramBotWorker:
    def __init__(
        self,
        db_path: Path,
        auth: AuthService,
        token: str,
        *,
        public_url: str = "https://spyon.kz",
        api: TelegramBotApi | None = None,
        notification_sync: Callable[[], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.auth = auth
        self.token_fingerprint = hashlib.sha256(
            str(token).encode("utf-8")
        ).hexdigest()[:16]
        self.api = api or TelegramBotApi(token)
        self.links = TelegramLinkService(self.db_path)
        parsed_url = urlparse(str(public_url or "").strip())
        self.public_url = (
            str(public_url).rstrip("/")
            if parsed_url.scheme in {"http", "https"} and parsed_url.netloc
            else "https://spyon.kz"
        )
        self.bot_username = ""
        self.notification_sync = notification_sync
        self._sessions: dict[int, dict[str, Any]] = {}
        self._attempts: dict[str, list[float]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="spyon-telegram-bot",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=16)
        self._thread = None

    def _run(self) -> None:
        offset = self.links.bot_offset(self.token_fingerprint)
        while not self._stop.is_set():
            try:
                identity = self.api.get_me()
                self.bot_username = str(identity.get("username") or "")
                self.api.set_commands()
                LOGGER.info(
                    "telegram_bot_started username=%s",
                    self.bot_username or "unknown",
                )
                break
            except Exception as exc:
                LOGGER.error(
                    "telegram_bot_start_failed error=%s", self._safe_error(exc)
                )
                self._stop.wait(10)
        while not self._stop.is_set():
            try:
                updates = self.api.get_updates(offset, timeout=10)
                for update in updates:
                    update_id = int(update.get("update_id") or 0)
                    try:
                        self.handle_update(update)
                    except Exception:
                        LOGGER.exception("telegram_update_failed update_id=%s", update_id)
                    offset = max(offset, update_id + 1)
                    self.links.save_bot_offset(offset)
                if self.notification_sync:
                    try:
                        self.notification_sync()
                    except Exception:
                        LOGGER.exception("telegram_notification_sync_failed")
                self.deliver_pending()
            except TelegramApiError as exc:
                LOGGER.warning(
                    "telegram_poll_failed code=%s error=%s",
                    exc.error_code,
                    self._safe_error(exc),
                )
                self._stop.wait(max(2, min(exc.retry_after or 5, 30)))
            except Exception:
                LOGGER.exception("telegram_worker_failed")
                self._stop.wait(5)
        LOGGER.info("telegram_bot_stopped")

    @staticmethod
    def _safe_error(error: BaseException) -> str:
        return str(error or "telegram_error").replace("\r", " ").replace("\n", " ")[:240]

    def _send(self, chat_id: int, text: str, *, html_mode: bool = False) -> dict[str, Any]:
        return self.api.send_message(
            int(chat_id), text, parse_mode="HTML" if html_mode else None
        )

    def _delete_sensitive(self, chat_id: int, message_id: int) -> bool:
        try:
            return bool(self.api.delete_message(int(chat_id), int(message_id)))
        except Exception:
            LOGGER.warning("telegram_sensitive_message_delete_failed chat_id=%s", chat_id)
            return False

    def _active_session(self, chat_id: int) -> dict[str, Any] | None:
        session = self._sessions.get(int(chat_id))
        if not session:
            return None
        if time.monotonic() > float(session.get("expires_at") or 0):
            self._sessions.pop(int(chat_id), None)
            return None
        return session

    def _attempt_key_locked(self, key: str) -> bool:
        current = time.monotonic()
        values = [
            stamp for stamp in self._attempts.get(key, [])
            if current - stamp < AUTH_WINDOW_SECONDS
        ]
        self._attempts[key] = values
        return len(values) >= AUTH_MAX_ATTEMPTS

    def _auth_locked(self, chat_id: int, email: str) -> bool:
        return self._attempt_key_locked(f"chat:{int(chat_id)}") or self._attempt_key_locked(
            f"email:{str(email).strip().casefold()}"
        )

    def _record_auth_failure(self, chat_id: int, email: str) -> None:
        stamp = time.monotonic()
        for key in (
            f"chat:{int(chat_id)}",
            f"email:{str(email).strip().casefold()}",
        ):
            values = [
                item for item in self._attempts.get(key, [])
                if stamp - item < AUTH_WINDOW_SECONDS
            ]
            values.append(stamp)
            self._attempts[key] = values

    def _clear_auth_attempts(self, chat_id: int, email: str) -> None:
        self._attempts.pop(f"chat:{int(chat_id)}", None)
        self._attempts.pop(f"email:{str(email).strip().casefold()}", None)

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat_id = int(chat.get("id") or 0)
        message_id = int(message.get("message_id") or 0)
        if not chat_id:
            return
        if str(chat.get("type") or "") != "private":
            self._send(chat_id, "Для входа используйте личный чат с ботом.")
            return
        text = str(message.get("text") or "").strip()
        if not text:
            self._send(chat_id, "Отправьте текстовую команду. /help — список команд.")
            return
        command_token = text.split(maxsplit=1)[0].casefold()
        command = command_token.split("@", 1)[0] if command_token.startswith("/") else ""
        if command:
            self._handle_command(command, chat_id)
            return

        session = self._active_session(chat_id)
        if not session:
            self._send(chat_id, "Начните безопасную привязку командой /login.")
            return
        if session["state"] == "await_login":
            deleted = self._delete_sensitive(chat_id, message_id)
            email = text.casefold()[:254]
            if "@" not in email or len(email) < 5:
                self._send(chat_id, "Введите логин в формате email или отправьте /cancel.")
                return
            session.update({
                "state": "await_password",
                "email": email,
                "expires_at": time.monotonic() + SESSION_TTL_SECONDS,
            })
            self._send(
                chat_id,
                (
                    "Теперь отправьте пароль Spyon. Сообщение будет сразу удалено и пароль не сохранится."
                    if deleted
                    else "Telegram не позволил удалить логин — удалите его вручную. Теперь отправьте пароль Spyon."
                ),
            )
            return
        if session["state"] != "await_password":
            self._sessions.pop(chat_id, None)
            return

        password_deleted = self._delete_sensitive(chat_id, message_id)
        email = str(session.get("email") or "")
        password = text
        self._sessions.pop(chat_id, None)
        if self._auth_locked(chat_id, email):
            password = ""
            self._send(
                chat_id,
                "Слишком много попыток входа. Повторите через 15 минут.",
            )
            return
        user = self.auth.authenticate(
            email,
            password,
            event_type="telegram_login",
        )
        password = ""

        if user and not user.get("email_verified"):
            self._send(
                chat_id,
                "??????? ??????????? ??????????? ????? "
                "? Spyon, ????? ????????? ????.",
            )
            return

        if not user:
            self._record_auth_failure(chat_id, email)
            warning = (
                ""
                if password_deleted
                else " Telegram не удалил пароль — удалите сообщение вручную."
            )
            self._send(
                chat_id,
                "Неверный логин или пароль. Для новой попытки: /login" + warning,
            )
            return
        self._clear_auth_attempts(chat_id, email)
        display_name = " ".join(
            str(sender.get(key) or "").strip() for key in ("first_name", "last_name")
        ).strip()
        self.links.link_user(
            user,
            chat_id=chat_id,
            telegram_user_id=int(sender.get("id") or chat_id),
            username=str(sender.get("username") or ""),
            display_name=display_name,
        )
        self._send(
            chat_id,
            (
                f"Готово. Telegram привязан к аккаунту {user.get('display_name') or user.get('email')}. Новые уведомления Spyon будут приходить сюда."
                + (
                    " Telegram не удалил сообщение с паролем — удалите его вручную."
                    if not password_deleted else ""
                )
            ),
        )

    def _handle_command(self, command: str, chat_id: int) -> None:
        link = self.links.status_for_chat(chat_id)
        if link:
            linked_user = self.auth.get_user(int(link["user_id"]))
            linked_tenant = (
                int(link["tenant_id"]) if link.get("tenant_id") is not None else None
            )
            current_tenant = (
                int(linked_user["tenant_id"])
                if linked_user and linked_user.get("tenant_id") is not None else None
            )
            if (
                not linked_user
                or not linked_user.get("is_active")
                or linked_tenant != current_tenant
            ):
                self.links.unlink_user(int(link["user_id"]))
                link = None
        if command in {"/start", "/help"}:
            if link:
                self._send(
                    chat_id,
                    "Spyon подключён.\n/status — состояние\n/notifications — последние события\n/pause — пауза\n/resume — возобновить\n/logout — отвязать Telegram",
                )
            else:
                self._send(
                    chat_id,
                    "Бот Spyon отправляет уведомления о синхронизациях, ошибках и подписке. Для привязки используйте /login. Логин — email от Spyon.",
                )
            return
        if command == "/login":
            self._sessions[chat_id] = {
                "state": "await_login",
                "expires_at": time.monotonic() + SESSION_TTL_SECONDS,
            }
            self._send(
                chat_id,
                "Введите логин Spyon (email). Сообщение будет удалено после чтения. /cancel — отмена.",
            )
            return
        if command == "/cancel":
            self._sessions.pop(chat_id, None)
            self._send(chat_id, "Вход отменён.")
            return
        if not link:
            self._send(chat_id, "Telegram ещё не привязан. Используйте /login.")
            return
        user_id = int(link["user_id"])
        if command == "/status":
            unread = self.links.unread_count(user_id)
            status = "включены" if link["is_enabled"] else "приостановлены"
            account = self.auth.get_user(user_id) or {}
            self._send(
                chat_id,
                f"Аккаунт: {account.get('display_name') or account.get('email') or user_id}\nУведомления: {status}\nНепрочитанных в Spyon: {unread}",
            )
            return
        if command == "/notifications":
            items = self.links.latest_notifications(user_id, 5)
            if not items:
                self._send(chat_id, "Уведомлений пока нет.")
                return
            for item in reversed(items):
                self._send(chat_id, self._notification_text(item), html_mode=True)
            return
        if command == "/pause":
            self.links.set_enabled(user_id, False)
            self._send(chat_id, "Автоматические уведомления приостановлены. /resume — включить снова.")
            return
        if command == "/resume":
            self.links.set_enabled(user_id, True)
            self._send(chat_id, "Автоматические уведомления включены. Новые события будут приходить сюда.")
            return
        if command == "/logout":
            self.links.unlink_user(user_id)
            self._sessions.pop(chat_id, None)
            self._send(chat_id, "Telegram отвязан от Spyon. Для повторной привязки: /login")
            return
        self._send(chat_id, "Неизвестная команда. /help — список команд.")

    def _notification_text(self, item: dict[str, Any]) -> str:
        icons = {"success": "✅", "warning": "⚠️", "danger": "❌", "info": "ℹ️"}
        icon = icons.get(str(item.get("level") or "info"), "ℹ️")
        title = html.escape(str(item.get("title") or "Уведомление"))
        message = html.escape(str(item.get("message") or ""))
        parts = [f"{icon} <b>{title}</b>"]
        if message:
            parts.append(message)
        action_url = str(item.get("action_url") or "")
        if action_url.startswith("/"):
            href = html.escape(self.public_url + action_url, quote=True)
            parts.append(f'<a href="{href}">Открыть Spyon</a>')
        return "\n".join(parts)

    def deliver_pending(self) -> int:
        self.links.enqueue_notifications()
        sent = 0
        for item in self.links.pending_deliveries():
            try:
                result = self._send(
                    int(item["chat_id"]),
                    self._notification_text(item),
                    html_mode=True,
                )
                message_id = result.get("message_id") if isinstance(result, dict) else None
                self.links.mark_sent(
                    int(item["id"]),
                    int(message_id) if message_id is not None else None,
                )
                sent += 1
            except TelegramApiError as exc:
                self.links.mark_failed(
                    int(item["id"]),
                    error=f"telegram_api_{exc.error_code or 'error'}",
                    retry_after=exc.retry_after,
                    permanent=exc.error_code in {400, 403},
                    disable_link=exc.error_code == 403,
                )
            except Exception as exc:
                self.links.mark_failed(
                    int(item["id"]),
                    error=type(exc).__name__,
                )
        return sent
