from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import smtplib
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from config import ROOT, get_secret_key
from schema import ensure_database
from storage.postgres_compat import PostgresConnection, connect_database


LOGGER = logging.getLogger(__name__)
EMAIL_CATEGORIES = ("security", "billing", "marketplaces", "operations")
EMAIL_ADDRESS_RE = re.compile(
    r"(?i)[A-Z0-9._%+\-]+@[A-Z0-9.\-]+"
)


SECURITY_TEMPLATES = {
    "verify_email", "password_reset", "password_changed", "user_invitation",
}
TEMPLATE_SUBJECTS = {
    "verify_email": "Подтвердите email в Spyon",
    "password_reset": "Восстановление пароля Spyon",
    "password_changed": "Пароль Spyon изменён",
    "user_invitation": "Приглашение в Spyon",
    "registration_received": "Регистрация Spyon завершена",
    "company_approved": "Компания одобрена в Spyon",
    "company_rejected": "Решение по заявке Spyon",
    "subscription_expiry": "Срок подписки Spyon заканчивается",
    "marketplace_auth_required": "Требуется авторизация marketplace в Spyon",
    "sync_failed": "Ошибка синхронизации Spyon",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _environment_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def mask_recipient(value: str) -> str:
    address = str(value or "").strip()
    if "@" not in address:
        return "***"
    local, domain = address.split("@", 1)
    return f"{local[:1] or '*'}***@{domain}"


def safe_error(
    value: BaseException | str | None,
) -> str:
    text = (
        str(value or "email_delivery_failed")
        .replace("\r", " ")
        .replace("\n", " ")
    )

    # SMTP servers may echo recipient addresses in rejection messages.
    # Do not persist personal addresses in logs or email_outbox.last_error.
    text = EMAIL_ADDRESS_RE.sub(
        "<redacted-email>",
        text,
    )

    # Never expose authentication/configuration secrets through SMTP errors.
    for marker in (
        "password",
        "token",
        "authorization",
        "cookie",
        "secret",
    ):
        if marker in text.casefold():
            return "sensitive_smtp_error"

    return text[:240]


class EmailConfigurationError(RuntimeError):
    pass


class SmtpTlsRequiredError(EmailConfigurationError):
    pass


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    security: str
    require_tls: bool
    mail_from: str
    mail_from_name: str
    reply_to: str
    public_url: str
    timeout_seconds: float
    max_attempts: int

    @classmethod
    def from_environment(cls) -> "EmailSettings":
        security = str(os.environ.get("ITP_SMTP_SECURITY") or "starttls").strip().casefold()
        if security not in {"starttls", "smtps", "none"}:
            security = "starttls"
        try:
            port = int(os.environ.get("ITP_SMTP_PORT") or 25)
        except ValueError:
            port = 25
        try:
            timeout = float(os.environ.get("ITP_SMTP_TIMEOUT_SECONDS") or 12)
        except ValueError:
            timeout = 12
        try:
            attempts = int(os.environ.get("ITP_EMAIL_MAX_ATTEMPTS") or 5)
        except ValueError:
            attempts = 5
        public_url = str(
            os.environ.get("SPYON_PUBLIC_URL")
            or os.environ.get("SPYON_DOMAIN")
            or "https://spyon.kz"
        ).strip()
        if not public_url.startswith(("https://", "http://")):
            public_url = "https://" + public_url
        return cls(
            enabled=_environment_flag("ITP_EMAIL_ENABLED"),
            host=str(os.environ.get("ITP_SMTP_HOST") or "").strip(),
            port=max(1, min(port, 65535)),
            username=str(os.environ.get("ITP_SMTP_USERNAME") or "").strip(),
            password=str(os.environ.get("ITP_SMTP_PASSWORD") or ""),
            security=security,
            require_tls=_environment_flag("ITP_SMTP_REQUIRE_TLS", True),
            mail_from=str(os.environ.get("ITP_MAIL_FROM") or "").strip(),
            mail_from_name=str(os.environ.get("ITP_MAIL_FROM_NAME") or "Spyon").strip()[:120],
            reply_to=str(os.environ.get("ITP_MAIL_REPLY_TO") or "").strip(),
            public_url=public_url.rstrip("/"),
            timeout_seconds=max(2.0, min(timeout, 60.0)),
            max_attempts=max(1, min(attempts, 10)),
        )

    @property
    def configured(self) -> bool:
        return bool(self.host and self.mail_from)

    def public_status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "security": self.security,
            "require_tls": self.require_tls,
            "host": self.host,
            "port": self.port,
        }


class EmailService:
    """Persistent transactional mail outbox with safe SMTP delivery."""

    def __init__(self, db_path: Path, settings: EmailSettings | None = None):
        self.db_path = Path(db_path)
        self.settings = settings or EmailSettings.from_environment()
        ensure_database(self.db_path)
        self.templates = Environment(
            loader=FileSystemLoader(str(ROOT / "templates" / "email")),
            autoescape=select_autoescape(("html", "xml")),
            undefined=StrictUndefined,
        )

    def _connect(self):
        conn = connect_database(self.db_path, timeout=30)
        conn.row_factory = __import__("sqlite3").Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _category_for_template(template_key: str) -> str:
        if template_key in SECURITY_TEMPLATES:
            return "security"
        if template_key == "subscription_expiry":
            return "billing"
        if template_key == "marketplace_auth_required":
            return "marketplaces"
        return "operations"

    @staticmethod
    def _is_security_template(template_key: str) -> bool:
        return template_key in SECURITY_TEMPLATES

    def _payload_cipher(self) -> Fernet:
        secret = str(os.environ.get("ITP_EMAIL_OUTBOX_KEY") or get_secret_key())
        material = hashlib.sha256(
            b"spyon-email-outbox-v1\x00" + secret.encode("utf-8")
        ).digest()
        return Fernet(base64.urlsafe_b64encode(material))

    def _encrypt_payload(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._payload_cipher().encrypt(raw).decode("ascii")

    def _decrypt_payload(self, payload: str) -> dict[str, Any]:
        try:
            value = json.loads(self._payload_cipher().decrypt(str(payload).encode("ascii")).decode("utf-8"))
        except (InvalidToken, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise EmailConfigurationError("email_outbox_payload_unavailable") from exc
        if not isinstance(value, dict):
            raise EmailConfigurationError("email_outbox_payload_invalid")
        return value

    def _context(self, template_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        context = dict(payload)
        context.setdefault("public_url", self.settings.public_url)
        context.setdefault(
            "logo_url",
            str(
                os.environ.get("ITP_BRAND_LOGO_URL")
                or (
                    self.settings.public_url
                    + "/static/images/spyon-logo.svg"
                )
            ).strip(),
        )
        context.setdefault("support_email", self.settings.reply_to or self.settings.mail_from)
        context.setdefault("recipient_name", "")
        context.setdefault("company_name", "")
        context.setdefault("marketplace", "")
        context.setdefault("action_url", "")
        context.setdefault("action_label", "Открыть Spyon")
        context.setdefault("security_notice", template_key in SECURITY_TEMPLATES)
        context.setdefault("subject", TEMPLATE_SUBJECTS.get(template_key, "Уведомление Spyon"))
        return context

    def render(self, template_key: str, payload: dict[str, Any]) -> tuple[str, str, str]:
        if template_key not in TEMPLATE_SUBJECTS:
            raise ValueError("Неизвестный шаблон email.")
        context = self._context(template_key, payload)
        try:
            html = self.templates.get_template(f"{template_key}.html").render(**context)
            plain = self.templates.get_template(f"{template_key}.txt").render(**context)
        except Exception as exc:
            raise EmailConfigurationError("email_template_render_failed") from exc
        return str(context["subject"])[:240], plain.strip(), html.strip()

    def _email_allowed(self, conn: Any, user_id: int | None, category: str, security: bool) -> bool:
        if security or user_id is None:
            return True
        row = conn.execute(
            "SELECT email_enabled FROM notification_preferences WHERE user_id=? AND category=?",
            (int(user_id), str(category)),
        ).fetchone()
        return True if row is None else bool(row[0])

    def queue(
        self,
        *,
        recipient: str,
        template_key: str,
        payload: dict[str, Any],
        dedupe_key: str,
        tenant_id: int | None = None,
        user_id: int | None = None,
        category: str | None = None,
        security: bool | None = None,
    ) -> int | None:
        address = str(recipient or "").strip().casefold()
        if not address or "@" not in address or len(address) > 320:
            raise ValueError("Укажите корректный адрес электронной почты.")
        key = str(dedupe_key or "").strip()
        if not key:
            raise ValueError("Для email требуется dedupe key.")
        subject, _, _ = self.render(template_key, payload)
        category_value = category or self._category_for_template(template_key)
        is_security = self._is_security_template(template_key) if security is None else bool(security)
        stamp = now_iso()
        conn = self._connect()
        try:
            if (
                template_key == "password_reset"
                and user_id is not None
            ):
                conn.execute(
                    """UPDATE email_outbox
                       SET status='failed',
                           last_error='superseded_by_new_password_reset',
                           updated_at=?
                       WHERE user_id=?
                         AND template_key='password_reset'
                         AND status IN ('pending','retry')""",
                    (
                        stamp,
                        int(user_id),
                    ),
                )

            if not self._email_allowed(conn, user_id, category_value, is_security):
                return None
            cursor = conn.execute(
                """INSERT INTO email_outbox(
                       tenant_id,user_id,recipient,template_key,subject,payload_json,status,
                       attempt_count,next_attempt_at,last_error,dedupe_key,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,? ,0,?,'',?,?,?)
                   ON CONFLICT(dedupe_key) DO NOTHING""",
                (
                    int(tenant_id) if tenant_id is not None else None,
                    int(user_id) if user_id is not None else None,
                    address, template_key, subject, self._encrypt_payload(payload), "pending",
                    stamp, key[:500], stamp, stamp,
                ),
            )
            if int(cursor.rowcount) == 0:
                existing = conn.execute(
                    "SELECT id FROM email_outbox WHERE dedupe_key=?", (key[:500],)
                ).fetchone()
                conn.commit()
                return int(existing[0]) if existing else None
            delivery_id = int(cursor.lastrowid)
            conn.commit()
            LOGGER.info(
                "email_queued email_outbox_id=%s template=%s recipient=%s",
                delivery_id, template_key, mask_recipient(address),
            )
            return delivery_id
        finally:
            conn.close()

    def queue_for_user(
        self,
        *,
        user_id: int,
        template_key: str,
        payload: dict[str, Any],
        dedupe_key: str,
        tenant_id: int | None = None,
        category: str | None = None,
        security: bool | None = None,
    ) -> int | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT email,is_active FROM app_users WHERE id=?", (int(user_id),)
            ).fetchone()
        finally:
            conn.close()
        if not row or not bool(row["is_active"]):
            return None
        return self.queue(
            recipient=str(row["email"]), template_key=template_key, payload=payload,
            dedupe_key=dedupe_key, tenant_id=tenant_id, user_id=user_id,
            category=category, security=security,
        )

    def queue_notification(
        self,
        *,
        tenant_id: int | None,
        user_id: int,
        category: str,
        event_type: str,
        title: str,
        message: str,
        action_url: str,
        dedupe_key: str,
    ) -> int | None:
        template_key = {
            "subscription_expiry": "subscription_expiry",
            "marketplace_auth_required": "marketplace_auth_required",
        }.get(event_type)
        if event_type in {"task_failed", "task_interrupted"}:
            template_key = "sync_failed"
        if not template_key:
            return None
        action = str(action_url or "").strip()
        if action.startswith("/"):
            action = self.settings.public_url + action
        return self.queue_for_user(
            user_id=int(user_id), tenant_id=tenant_id, category=category,
            template_key=template_key,
            payload={
                "title": str(title)[:240], "message": str(message)[:1200],
                "action_url": action, "action_label": "Открыть Spyon",
            },
            dedupe_key=f"notification-email:{dedupe_key}", security=False,
        )

    def diagnostic(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT status,COUNT(*) AS count FROM email_outbox GROUP BY status"
            ).fetchall()
        except Exception:
            # A feature branch can be imported before the production migration is
            # applied. This must not make /health or app startup fail.
            rows = []
        finally:
            conn.close()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return self.settings.public_status() | {
            "pending": counts.get("pending", 0) + counts.get("retry", 0) + counts.get("sending", 0),
            "failed": counts.get("failed", 0),
        }

    def _validate_configuration(self) -> None:
        settings = self.settings
        if not settings.configured:
            raise EmailConfigurationError("smtp_not_configured")
        if settings.require_tls and settings.security == "none":
            raise SmtpTlsRequiredError("smtp_tls_required")
        if settings.password and not settings.username:
            raise EmailConfigurationError("smtp_username_required")
        if settings.username and not settings.password:
            raise EmailConfigurationError("smtp_password_required")

    def _smtp_send(self, recipient: str, subject: str, plain: str, html: str) -> str:
        self._validate_configuration()
        settings = self.settings
        message = EmailMessage()
        message["From"] = formataddr((settings.mail_from_name or "Spyon", settings.mail_from))
        message["To"] = recipient
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=settings.mail_from.rsplit("@", 1)[-1])
        if settings.reply_to:
            message["Reply-To"] = settings.reply_to
        message.set_content(plain, subtype="plain", charset="utf-8")
        message.add_alternative(html, subtype="html", charset="utf-8")

        context = ssl.create_default_context()
        smtp: smtplib.SMTP | smtplib.SMTP_SSL
        if settings.security == "smtps":
            smtp = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout_seconds, context=context)
        else:
            smtp = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout_seconds)
        try:
            smtp.ehlo_or_helo_if_needed()
            if settings.security == "starttls":
                if not smtp.has_extn("starttls"):
                    raise SmtpTlsRequiredError("smtp_starttls_not_supported")
                smtp.starttls(context=context)
                smtp.ehlo_or_helo_if_needed()
            if settings.username:
                if settings.require_tls and settings.security == "none":
                    raise SmtpTlsRequiredError("smtp_credentials_require_tls")
                smtp.login(settings.username, settings.password)
            smtp.send_message(message)
            return str(message["Message-ID"])
        finally:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass

    @staticmethod
    def classify_error(error: BaseException) -> str:
        if isinstance(error, (SmtpTlsRequiredError, EmailConfigurationError, smtplib.SMTPAuthenticationError)):
            return "permanent"
        if isinstance(error, smtplib.SMTPRecipientsRefused):
            return "permanent"
        if isinstance(error, smtplib.SMTPResponseException):
            return "retry" if int(error.smtp_code or 0) < 500 else "permanent"
        if isinstance(error, (TimeoutError, socket.timeout, OSError, smtplib.SMTPException)):
            return "retry"
        return "retry"

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()
        except (TypeError, ValueError):
            return None

    def _reclaim_stale(
        self,
        conn: Any,
    ) -> None:
        cutoff = (
            datetime.now().astimezone()
            - timedelta(minutes=15)
        )

        query = """
            SELECT id,updated_at
            FROM email_outbox
            WHERE status='sending'
            ORDER BY id
            LIMIT 100
        """

        if hasattr(conn, "raw"):
            query += " FOR UPDATE SKIP LOCKED"

        rows = conn.execute(query).fetchall()
        stamp = now_iso()

        for row in rows:
            updated = self._parse_time(
                row["updated_at"]
            )

            if updated and updated <= cutoff:
                conn.execute(
                    """
                    UPDATE email_outbox
                    SET status='retry',
                        next_attempt_at=?,
                        last_error='worker_restart_recovered',
                        updated_at=?
                    WHERE id=?
                      AND status='sending'
                    """,
                    (
                        stamp,
                        stamp,
                        int(row["id"]),
                    ),
                )

    def _claim_due(
        self,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self.settings.enabled:
            return []

        batch_size = max(
            1,
            min(int(limit), 100),
        )

        conn = self._connect()

        try:
            # SQLite has no row-level SKIP LOCKED. Serialize the short
            # select-and-claim transaction instead.
            if not hasattr(conn, "raw"):
                conn.execute("BEGIN IMMEDIATE")

            self._reclaim_stale(conn)

            stamp = now_iso()

            query = """
                SELECT *
                FROM email_outbox
                WHERE status IN ('pending','retry')
                  AND next_attempt_at<=?
                ORDER BY
                    CASE
                        WHEN template_key IN (
                            'password_reset',
                            'verify_email',
                            'user_invitation',
                            'password_changed'
                        )
                        THEN 0
                        ELSE 1
                    END,
                    id
                LIMIT ?
            """

            # PostgreSQL workers claim different rows instead of competing
            # for the same leading batch.
            if hasattr(conn, "raw"):
                query += " FOR UPDATE SKIP LOCKED"

            rows = conn.execute(
                query,
                (
                    stamp,
                    batch_size,
                ),
            ).fetchall()

            claimed: list[dict[str, Any]] = []

            for row in rows:
                cursor = conn.execute(
                    """
                    UPDATE email_outbox
                    SET status='sending',
                        updated_at=?
                    WHERE id=?
                      AND status IN ('pending','retry')
                    """,
                    (
                        stamp,
                        int(row["id"]),
                    ),
                )

                if int(cursor.rowcount) == 1:
                    claimed.append(dict(row))

            conn.commit()
            return claimed

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

    def _mark_sent(self, delivery_id: int, attempts: int, message_id: str) -> None:
        conn = self._connect()
        try:
            stamp = now_iso()
            conn.execute(
                """UPDATE email_outbox SET status='sent',attempt_count=?,last_error='',
                       sent_at=?,updated_at=? WHERE id=? AND status='sending'""",
                (int(attempts), stamp, stamp, int(delivery_id)),
            )
            conn.commit()
        finally:
            conn.close()
        LOGGER.info("email_sent email_outbox_id=%s message_id=%s", delivery_id, message_id)

    def _mark_error(self, delivery_id: int, attempts: int, error: BaseException) -> None:
        classification = self.classify_error(error)
        final = classification == "permanent" or attempts >= self.settings.max_attempts
        delay = min(3600, 30 * (2 ** max(0, attempts - 1)))
        next_attempt = (datetime.now().astimezone() + timedelta(seconds=delay)).isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE email_outbox SET status=?,attempt_count=?,next_attempt_at=?,
                       last_error=?,updated_at=? WHERE id=? AND status='sending'""",
                (
                    "failed" if final else "retry", int(attempts), next_attempt,
                    safe_error(error), now_iso(), int(delivery_id),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        LOGGER.warning(
            "email_delivery_failed email_outbox_id=%s attempt=%s result=%s error=%s",
            delivery_id, attempts, "failed" if final else "retry", safe_error(error),
        )

    def deliver_due_once(self, limit: int = 1) -> int:
        delivered = 0
        for row in self._claim_due(limit):
            delivery_id = int(row["id"])
            attempts = int(row.get("attempt_count") or 0) + 1
            try:
                payload = self._decrypt_payload(str(row["payload_json"]))
                _, plain, html = self.render(str(row["template_key"]), payload)
                message_id = self._smtp_send(str(row["recipient"]), str(row["subject"]), plain, html)
                self._mark_sent(delivery_id, attempts, message_id)
                delivered += 1
            except Exception as exc:
                self._mark_error(delivery_id, attempts, exc)
        return delivered

    def smtp_probe(self) -> dict[str, Any]:
        """Perform an unauthenticated capability probe; never sends credentials."""
        settings = self.settings
        if not settings.host:
            raise EmailConfigurationError("smtp_not_configured")
        smtp = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout_seconds)
        try:
            code, response = smtp.ehlo()
            features = {str(key).casefold(): str(value) for key, value in smtp.esmtp_features.items()}
            return {
                "host": settings.host,
                "port": settings.port,
                "ehlo_code": int(code),
                "starttls": "starttls" in features,
                "features": sorted(features),
                "response": bytes(response).decode("utf-8", "replace")[:240],
            }
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()


class EmailOutboxWorker:
    def __init__(self, service: EmailService, interval_seconds: float = 5.0):
        self.service = service
        self.interval_seconds = max(1.0, min(float(interval_seconds), 60.0))
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
            target=self._run, name="spyon-email-outbox", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.service.deliver_due_once()
            except Exception:
                LOGGER.exception("email_outbox_worker_failed")
            self._stop.wait(self.interval_seconds)
