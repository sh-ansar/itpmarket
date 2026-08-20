from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash
from storage.postgres_compat import connect_database, integrity_error_types

from schema import ensure_database
from marketplace_registry import MARKETPLACE_CODES
from security_hygiene import redact_sensitive
from tenant_security import (
    PERMISSION_DEFINITIONS,
    canonical_company_status,
    company_is_approved,
    company_status_label,
    permission_map,
)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ROLES = {"admin", "operator", "viewer"}
PLATFORM_ROLES = {"", "superadmin", "support", "technical"}
PASSWORD_HASH_METHOD = "scrypt"
PASSWORD_MIN_LENGTH = 12


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_email(value: str) -> str:
    return (value or "").strip().casefold()


def validate_password(password: str, email: str = "", display_name: str = "") -> None:
    value = password or ""
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError("Пароль должен содержать не менее 12 символов.")
    checks = (
        any(char.islower() for char in value),
        any(char.isupper() for char in value),
        any(char.isdigit() for char in value),
        any(not char.isalnum() for char in value),
    )
    if sum(1 for passed in checks if passed) < 3:
        raise ValueError("Пароль должен содержать минимум три типа символов: строчные, заглавные, цифры или спецсимволы.")
    lowered = value.casefold()
    local_part = normalize_email(email).split("@", 1)[0]
    if local_part and len(local_part) >= 4 and local_part in lowered:
        raise ValueError("Пароль не должен содержать email.")
    for token in re.findall(r"[\wА-Яа-яЁё]{4,}", display_name or ""):
        if token.casefold() in lowered:
            raise ValueError("Пароль не должен содержать имя пользователя.")


def hash_secret(value: str) -> str:
    return generate_password_hash(value, method=PASSWORD_HASH_METHOD)


def hash_needs_upgrade(value: str) -> bool:
    return not str(value or "").startswith(f"{PASSWORD_HASH_METHOD}:")


def generate_recovery_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "SPYON-" + "-".join(groups)


def hash_auth_token(value: str) -> str:
    """A deterministic lookup hash for a high-entropy, one-use URL token."""
    return __import__("hashlib").sha256(str(value).encode("utf-8")).hexdigest()


def parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


class AuthService:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        ensure_database(db_path)
        conn = self._connect()
        try:
            # PostgreSQL's plain UNIQUE(email) is case-sensitive, while every
            # login and registration treats email case-insensitively.
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_email_normalized
                   ON app_users(lower(email))"""
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = connect_database(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def has_users(self) -> bool:
        conn = self._connect()
        try:
            return bool(conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0])
        finally:
            conn.close()

    @staticmethod
    def public_user(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        return {
            "id": int(value["id"]),
            "email": value["email"],
            "display_name": value["display_name"],
            "role": value["role"],
            "platform_role": value.get("platform_role") or "",
            "is_platform_user": bool(value.get("platform_role")),
            "is_active": bool(value["is_active"]),
            "tenant_id": int(value["tenant_id"]) if value.get("tenant_id") is not None else None,
            "tenant_name": value.get("tenant_name") or "",
            "tenant_slug": value.get("tenant_slug") or "",
            "tenant_status": canonical_company_status(value.get("tenant_status")),
            "tenant_status_label": company_status_label(value.get("tenant_status")),
            "tenant_profile_complete": all(
                str(value.get(key) or "").strip()
                for key in (
                    "tenant_name", "tenant_registration_number",
                    "tenant_contact_email", "tenant_contact_phone",
                )
            ),
            "tenant_role": value.get("tenant_role") or value.get("role") or "viewer",
            "marketplaces": value.get("marketplaces") if isinstance(value.get("marketplaces"), dict) else {},
            "available_marketplaces": value.get("available_marketplaces") if isinstance(value.get("available_marketplaces"), dict) else {},
            "marketplace_permissions": value.get("marketplace_permissions") if isinstance(value.get("marketplace_permissions"), dict) else {},
            "permissions": value.get("permissions") if isinstance(value.get("permissions"), dict) else {},
            "created_at": value.get("created_at"),
            "updated_at": value.get("updated_at"),
            "last_login_at": value.get("last_login_at"),
            "email_verified_at": value.get("email_verified_at"),
            "email_verified": bool(value.get("email_verified_at")),
            "session_version": int(value.get("session_version") or 0),
        }

    @staticmethod
    def _user_select() -> str:
        return """
            SELECT u.*,tu.tenant_id,tu.tenant_role,t.name AS tenant_name,
                   t.slug AS tenant_slug,t.status AS tenant_status,
                   t.registration_number AS tenant_registration_number,
                   t.contact_email AS tenant_contact_email,
                   t.contact_phone AS tenant_contact_phone
            FROM app_users u
            LEFT JOIN tenant_users tu ON tu.user_id=u.id AND tu.is_active=1 AND tu.is_primary=1
            LEFT JOIN tenants t ON t.id=tu.tenant_id
        """

    @staticmethod
    def _attach_marketplaces(
        conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        # Tenant-facing user payloads are intentionally sparse: a marketplace
        # that was not granted must not be advertised by the backend at all.
        company_access: dict[str, bool] = {}
        access: dict[str, bool] = {}
        available: dict[str, bool] = {}
        marketplace_permissions: dict[str, bool] = {}
        tenant_id = value.get("tenant_id")
        status = canonical_company_status(value.get("tenant_status"))
        if tenant_id is not None and company_is_approved(status):
            rows = conn.execute(
                """
                SELECT tma.marketplace_code,tma.is_allowed,
                       ti.status AS integration_status
                FROM tenant_marketplace_access tma
                JOIN tenant_integrations ti
                  ON ti.tenant_id=tma.tenant_id
                 AND ti.integration_code=tma.marketplace_code
                WHERE tma.tenant_id=?
                """,
                (int(tenant_id),),
            ).fetchall()
            for item in rows:
                code = str(item["marketplace_code"])
                if code in MARKETPLACE_CODES and bool(item["is_allowed"]):
                    available[code] = True
                    if str(item["integration_status"]) == "active":
                        company_access[code] = True
            overrides = {
                str(item["marketplace_code"]): bool(item["is_allowed"])
                for item in conn.execute(
                    """SELECT marketplace_code,is_allowed
                       FROM tenant_user_marketplace_access
                       WHERE tenant_id=? AND user_id=?""",
                    (int(tenant_id), int(value["id"])),
                ).fetchall()
                if str(item["marketplace_code"]) in MARKETPLACE_CODES
            }
            marketplace_permissions = {
                code: overrides.get(code, True) for code in available
            }
            access = {
                code: True for code in company_access
                if marketplace_permissions.get(code, False)
            }
        permissions: set[str] = set()
        if str(value.get("platform_role") or "") == "superadmin":
            permissions = set(PERMISSION_DEFINITIONS)
        elif tenant_id is not None:
            role_code = str(value.get("tenant_role") or value.get("role") or "viewer")
            permissions = {
                str(item["permission_code"])
                for item in conn.execute(
                    """SELECT permission_code FROM tenant_role_permissions
                       WHERE tenant_id=? AND role_code=? AND is_enabled=1""",
                    (int(tenant_id), role_code),
                ).fetchall()
                if str(item["permission_code"]) in PERMISSION_DEFINITIONS
            }
            for item in conn.execute(
                """SELECT permission_code,is_enabled FROM tenant_user_permissions
                   WHERE tenant_id=? AND user_id=?""",
                (int(tenant_id), int(value["id"])),
            ).fetchall():
                code = str(item["permission_code"])
                if code not in PERMISSION_DEFINITIONS:
                    continue
                if bool(item["is_enabled"]):
                    permissions.add(code)
                else:
                    permissions.discard(code)
        value["marketplaces"] = access
        value["available_marketplaces"] = available
        value["marketplace_permissions"] = marketplace_permissions
        value["tenant_status"] = status
        value["permissions"] = permission_map(permissions)
        return value

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(self._user_select()+" WHERE u.id=? LIMIT 1",(int(user_id),)).fetchone()
            return self.public_user(self._attach_marketplaces(conn, row))
        finally:
            conn.close()

    def get_user_by_email(self, email: str, include_secret: bool = False) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                self._user_select()+" WHERE u.email=? COLLATE NOCASE LIMIT 1",
                (normalize_email(email),),
            ).fetchone()
            if row is None:
                return None
            value = self._attach_marketplaces(conn, row)
            return value if include_secret else self.public_user(value)
        finally:
            conn.close()

    def create_initial_admin(self, email: str, display_name: str, password: str) -> tuple[dict[str, Any], str]:
        if self.has_users():
            raise ValueError("Первичная настройка уже выполнена.")
        # The first local deployment administrator is created during a trusted
        # setup flow, before transactional email can be configured.
        return self.create_user(
            email, display_name, password, "admin", actor_user_id=None,
            tenant_id=None, platform_role="superadmin", email_verified=True,
        )

    def create_user(
        self,
        email: str,
        display_name: str,
        password: str,
        role: str,
        actor_user_id: int | None,
        tenant_id: int | None = None,
        platform_role: str = "",
        email_verified: bool = False,
    ) -> tuple[dict[str, Any], str]:
        email_value = normalize_email(email)
        name_value = (display_name or "").strip()
        role_value = (role or "operator").strip().casefold()
        if not EMAIL_RE.match(email_value):
            raise ValueError("Укажите корректный адрес электронной почты.")
        if len(name_value) < 2:
            raise ValueError("Укажите имя пользователя.")
        if role_value not in ROLES:
            raise ValueError("Неизвестная роль пользователя.")
        platform_role_value = str(platform_role or "").casefold()
        if platform_role_value not in PLATFORM_ROLES:
            raise ValueError("Неизвестная платформенная роль.")
        validate_password(password, email_value, name_value)
        recovery_code = generate_recovery_code()
        stamp = now_iso()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO app_users(
                    email,display_name,password_hash,recovery_hash,role,platform_role,is_active,
                    created_at,updated_at,password_changed_at,email_verified_at,session_version
                ) VALUES(?,?,?,?,?,?,1,?,?,?,?,0)
                """,
                (
                    email_value,
                    name_value,
                    hash_secret(password),
                    hash_secret(recovery_code),
                    role_value,
                    platform_role_value,
                    stamp,
                    stamp,
                    stamp,
                    stamp if email_verified else None,
                ),
            )
            user_id = int(cursor.lastrowid)
            resolved_tenant_id = tenant_id
            if resolved_tenant_id is None and actor_user_id is not None:
                membership = conn.execute(
                    "SELECT tenant_id FROM tenant_users WHERE user_id=? AND is_active=1 ORDER BY is_primary DESC,tenant_id LIMIT 1",
                    (int(actor_user_id),),
                ).fetchone()
                resolved_tenant_id = int(membership["tenant_id"]) if membership else None
            if resolved_tenant_id is None:
                tenant_row = conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()
                resolved_tenant_id = int(tenant_row["id"]) if tenant_row else None
            if resolved_tenant_id is not None:
                conn.execute(
                    """
                    INSERT INTO tenant_users(tenant_id,user_id,tenant_role,is_primary,is_active,created_at)
                    VALUES(?,?,?,?,1,?)
                    ON CONFLICT(tenant_id,user_id) DO UPDATE SET tenant_role=excluded.tenant_role,is_active=1
                    """,
                    (resolved_tenant_id,user_id,role_value,1,stamp),
                )
            self._event(conn, actor_user_id or user_id, "user_created", "user", str(user_id), {
                "email": email_value,
                "role": role_value,
            })
            conn.commit()
            row = conn.execute(self._user_select()+" WHERE u.id=? LIMIT 1", (user_id,)).fetchone()
            return self.public_user(self._attach_marketplaces(conn, row)) or {}, recovery_code
        except integrity_error_types() as exc:
            raise ValueError("Пользователь с такой почтой уже существует.") from exc
        finally:
            conn.close()

    def authenticate(
        self,
        email: str,
        password: str,
        *,
        event_type: str = "login",
    ) -> dict[str, Any] | None:
        value = self.get_user_by_email(email, include_secret=True)
        if not value or not bool(value.get("is_active")):
            return None
        if not check_password_hash(
            str(value["password_hash"]),
            password or "",
        ):
            return None

        public_value = self.public_user(value)

        if not public_value:
            return None

        if not public_value.get("email_verified"):
            return public_value

        stamp = now_iso()
        conn = self._connect()
        try:
            if hash_needs_upgrade(str(value["password_hash"])):
                conn.execute(
                    "UPDATE app_users SET password_hash=?,last_login_at=?,updated_at=? WHERE id=?",
                    (hash_secret(password or ""), stamp, stamp, value["id"]),
                )
            else:
                conn.execute("UPDATE app_users SET last_login_at=?,updated_at=? WHERE id=?", (stamp, stamp, value["id"]))
            auth_event = (
                str(event_type) if str(event_type) in {"login", "telegram_login"}
                else "login"
            )
            self._event(conn, int(value["id"]), auth_event, "user", str(value["id"]), {})
            conn.commit()
        finally:
            conn.close()
        return self.public_user(value)

    def issue_auth_token(
        self,
        user_id: int,
        purpose: str,
        *,
        expires_minutes: int,
        request_ip: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        purpose_value = str(purpose or "").strip()
        if purpose_value not in {"verify_email", "password_reset", "user_invitation", "email_change"}:
            raise ValueError("Неизвестное назначение токена.")
        user = self.get_user(int(user_id))
        if not user or not user.get("is_active"):
            raise ValueError("Пользователь не найден или отключён.")
        raw_token = secrets.token_urlsafe(32)
        stamp = now_iso()
        expires = (datetime.now().astimezone() + timedelta(
            minutes=max(1, min(int(expires_minutes), 7 * 24 * 60))
        )).isoformat(timespec="seconds")
        conn = self._connect()
        try:
            if not isinstance(conn, type(None)) and not hasattr(conn, "raw"):
                conn.execute("BEGIN IMMEDIATE")
            # One current token per purpose keeps resends and reset requests
            # deterministic while preserving an audit trail of consumed tokens.
            conn.execute(
                """UPDATE auth_tokens SET consumed_at=?
                   WHERE user_id=? AND purpose=? AND consumed_at IS NULL""",
                (stamp, int(user_id), purpose_value),
            )
            conn.execute(
                """INSERT INTO auth_tokens(
                       user_id,purpose,token_hash,expires_at,consumed_at,created_at,
                       request_ip,metadata_json
                   ) VALUES(?,?,?,?,NULL,?,?,?)""",
                (
                    int(user_id), purpose_value, hash_auth_token(raw_token), expires,
                    stamp, str(request_ip or "")[:96],
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            self._event(conn, int(user_id), f"{purpose_value}_issued", "user", str(user_id), {})
            conn.commit()
        finally:
            conn.close()
        return raw_token

    def _auth_token_status_with_conn(
        self,
        conn: Any,
        raw_token: str,
        purpose: str,
        *,
        lock: bool = False,
    ) -> dict[str, Any] | None:
        if not raw_token or not purpose:
            return None

        query = """
            SELECT *
            FROM auth_tokens
            WHERE token_hash=? AND purpose=?
            ORDER BY id DESC
            LIMIT 1
        """

        # PostgreSQL row locking prevents two workers from completing
        # the same one-use security action concurrently. SQLite uses
        # BEGIN IMMEDIATE in the write paths below.
        if lock and hasattr(conn, "raw"):
            query += " FOR UPDATE"

        row = conn.execute(
            query,
            (
                hash_auth_token(raw_token),
                str(purpose),
            ),
        ).fetchone()

        if not row or row["consumed_at"] is not None:
            return None

        expires = parse_iso(row["expires_at"])

        if (
            not expires
            or expires <= datetime.now().astimezone()
        ):
            return None

        value = dict(row)

        try:
            value["metadata"] = json.loads(
                value.pop("metadata_json") or "{}"
            )
        except json.JSONDecodeError:
            value["metadata"] = {}

        return value

    def auth_token_status(
        self,
        raw_token: str,
        purpose: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()

        try:
            return self._auth_token_status_with_conn(
                conn,
                raw_token,
                purpose,
            )
        finally:
            conn.close()

    def consume_auth_token(
        self,
        raw_token: str,
        purpose: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()

        try:
            if not hasattr(conn, "raw"):
                conn.execute("BEGIN IMMEDIATE")

            status = self._auth_token_status_with_conn(
                conn,
                raw_token,
                purpose,
                lock=True,
            )

            if not status:
                conn.rollback()
                return None

            stamp = now_iso()

            cursor = conn.execute(
                """
                UPDATE auth_tokens
                SET consumed_at=?
                WHERE id=?
                  AND purpose=?
                  AND token_hash=?
                  AND consumed_at IS NULL
                """,
                (
                    stamp,
                    int(status["id"]),
                    str(purpose),
                    hash_auth_token(raw_token),
                ),
            )

            if int(cursor.rowcount) != 1:
                conn.rollback()
                return None

            self._event(
                conn,
                int(status["user_id"]),
                f"{purpose}_consumed",
                "user",
                str(status["user_id"]),
                {},
            )

            conn.commit()

            user_id = int(status["user_id"])

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        return self.get_user(user_id)

    def verify_email(
        self,
        raw_token: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()

        try:
            if not hasattr(conn, "raw"):
                conn.execute("BEGIN IMMEDIATE")

            status = self._auth_token_status_with_conn(
                conn,
                raw_token,
                "verify_email",
                lock=True,
            )

            if not status:
                conn.rollback()
                return None

            stamp = now_iso()
            user_id = int(status["user_id"])

            consumed = conn.execute(
                """
                UPDATE auth_tokens
                SET consumed_at=?
                WHERE id=?
                  AND purpose='verify_email'
                  AND token_hash=?
                  AND consumed_at IS NULL
                """,
                (
                    stamp,
                    int(status["id"]),
                    hash_auth_token(raw_token),
                ),
            )

            if int(consumed.rowcount) != 1:
                conn.rollback()
                return None

            updated = conn.execute(
                """
                UPDATE app_users
                SET email_verified_at=COALESCE(
                        email_verified_at,
                        ?
                    ),
                    updated_at=?
                WHERE id=?
                  AND is_active=1
                """,
                (
                    stamp,
                    stamp,
                    user_id,
                ),
            )

            if int(updated.rowcount) != 1:
                conn.rollback()
                return None

            self._event(
                conn,
                user_id,
                "verify_email_consumed",
                "user",
                str(user_id),
                {},
            )

            self._event(
                conn,
                user_id,
                "email_verified",
                "user",
                str(user_id),
                {},
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        return self.get_user(user_id)

    def request_password_reset(
        self,
        email: str,
        request_ip: str = "",
    ) -> str | None:
        user = self.get_user_by_email(
            email,
            include_secret=True,
        )

        if not user or not user.get("is_active"):
            return None

        return self.issue_auth_token(
            int(user["id"]),
            "password_reset",
            expires_minutes=30,
            request_ip=request_ip,
        )

    def reset_password_from_token(
        self,
        raw_token: str,
        new_password: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()

        try:
            if not hasattr(conn, "raw"):
                conn.execute("BEGIN IMMEDIATE")

            status = self._auth_token_status_with_conn(
                conn,
                raw_token,
                "password_reset",
                lock=True,
            )

            if not status:
                conn.rollback()
                return None

            user_id = int(status["user_id"])

            user_query = """
                SELECT email,display_name,is_active
                FROM app_users
                WHERE id=?
                LIMIT 1
            """

            if hasattr(conn, "raw"):
                user_query += " FOR UPDATE"

            user = conn.execute(
                user_query,
                (user_id,),
            ).fetchone()

            if not user or not bool(user["is_active"]):
                conn.rollback()
                return None

            validate_password(
                new_password,
                str(user["email"] or ""),
                str(user["display_name"] or ""),
            )

            new_password_hash = hash_secret(
                new_password
            )

            stamp = now_iso()

            consumed = conn.execute(
                """
                UPDATE auth_tokens
                SET consumed_at=?
                WHERE id=?
                  AND purpose='password_reset'
                  AND token_hash=?
                  AND consumed_at IS NULL
                """,
                (
                    stamp,
                    int(status["id"]),
                    hash_auth_token(raw_token),
                ),
            )

            if int(consumed.rowcount) != 1:
                conn.rollback()
                return None

            updated = conn.execute(
                """
                UPDATE app_users
                SET password_hash=?,
                    password_changed_at=?,
                    session_version=
                        COALESCE(session_version,0)+1,
                    updated_at=?
                WHERE id=?
                  AND is_active=1
                """,
                (
                    new_password_hash,
                    stamp,
                    stamp,
                    user_id,
                ),
            )

            if int(updated.rowcount) != 1:
                conn.rollback()
                return None

            # Invalidate any other reset token that may still exist.
            conn.execute(
                """
                UPDATE auth_tokens
                SET consumed_at=?
                WHERE user_id=?
                  AND purpose='password_reset'
                  AND consumed_at IS NULL
                """,
                (
                    stamp,
                    user_id,
                ),
            )

            self._event(
                conn,
                user_id,
                "password_reset_consumed",
                "user",
                str(user_id),
                {},
            )

            self._event(
                conn,
                user_id,
                "password_reset",
                "user",
                str(user_id),
                {},
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        return self.get_user(user_id)

    def create_invitation(
        self,
        *,
        email: str,
        display_name: str,
        role: str,
        tenant_id: int,
        actor_user_id: int,
        request_ip: str = "",
    ) -> tuple[dict[str, Any], str]:
        existing = self.get_user_by_email(email)
        if existing:
            raise ValueError("Пользователь с такой почтой уже существует.")
        tenant = int(tenant_id)
        conn = self._connect()
        try:
            exists = conn.execute("SELECT id FROM tenants WHERE id=?", (tenant,)).fetchone()
        finally:
            conn.close()
        if not exists:
            raise ValueError("Компания не найдена.")
        name = str(display_name or "").strip() or normalize_email(email).split("@", 1)[0]
        user, _ = self.create_user(
            email, name, secrets.token_urlsafe(48), role, actor_user_id,
            tenant_id=tenant, email_verified=False,
        )
        token = self.issue_auth_token(
            int(user["id"]), "user_invitation", expires_minutes=7 * 24 * 60,
            request_ip=request_ip, metadata={"tenant_id": tenant},
        )
        return user, token

    def accept_invitation(
        self,
        raw_token: str,
        new_password: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()

        try:
            if not hasattr(conn, "raw"):
                conn.execute("BEGIN IMMEDIATE")

            status = self._auth_token_status_with_conn(
                conn,
                raw_token,
                "user_invitation",
                lock=True,
            )

            if not status:
                conn.rollback()
                return None

            metadata = (
                status.get("metadata")
                if isinstance(
                    status.get("metadata"),
                    dict,
                )
                else {}
            )

            try:
                tenant_id = int(
                    metadata.get("tenant_id") or 0
                )
            except (TypeError, ValueError):
                conn.rollback()
                return None

            if tenant_id <= 0:
                conn.rollback()
                return None

            user_id = int(status["user_id"])

            user_query = """
                SELECT
                    u.email,
                    u.display_name,
                    u.is_active,
                    tu.tenant_id
                FROM app_users u
                JOIN tenant_users tu
                  ON tu.user_id=u.id
                 AND tu.tenant_id=?
                 AND tu.is_active=1
                WHERE u.id=?
                LIMIT 1
            """

            if hasattr(conn, "raw"):
                user_query += " FOR UPDATE"

            user = conn.execute(
                user_query,
                (
                    tenant_id,
                    user_id,
                ),
            ).fetchone()

            if (
                not user
                or not bool(user["is_active"])
            ):
                conn.rollback()
                return None

            validate_password(
                new_password,
                str(user["email"] or ""),
                str(user["display_name"] or ""),
            )

            new_password_hash = hash_secret(
                new_password
            )

            stamp = now_iso()

            consumed = conn.execute(
                """
                UPDATE auth_tokens
                SET consumed_at=?
                WHERE id=?
                  AND purpose='user_invitation'
                  AND token_hash=?
                  AND consumed_at IS NULL
                """,
                (
                    stamp,
                    int(status["id"]),
                    hash_auth_token(raw_token),
                ),
            )

            if int(consumed.rowcount) != 1:
                conn.rollback()
                return None

            updated = conn.execute(
                """
                UPDATE app_users
                SET password_hash=?,
                    password_changed_at=?,
                    email_verified_at=?,
                    session_version=
                        COALESCE(session_version,0)+1,
                    updated_at=?
                WHERE id=?
                  AND is_active=1
                  AND email_verified_at IS NULL
                """,
                (
                    new_password_hash,
                    stamp,
                    stamp,
                    stamp,
                    user_id,
                ),
            )

            if int(updated.rowcount) != 1:
                conn.rollback()
                return None

            self._event(
                conn,
                user_id,
                "user_invitation_consumed",
                "user",
                str(user_id),
                {},
            )

            self._event(
                conn,
                user_id,
                "invitation_accepted",
                "user",
                str(user_id),
                {},
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        return self.get_user(user_id)

    def list_users(self, tenant_id: int | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            query = self._user_select()
            params: list[Any] = []
            if tenant_id is not None:
                query += " WHERE tu.tenant_id=?"
                params.append(int(tenant_id))
            query += " ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'operator' THEN 1 ELSE 2 END,u.display_name"
            return [
                self.public_user(self._attach_marketplaces(conn, row)) or {}
                for row in conn.execute(query,params).fetchall()
            ]
        finally:
            conn.close()

    def set_platform_role(self,user_id:int,platform_role:str,actor_user_id:int)->dict[str,Any]:
        value=str(platform_role or "").casefold()
        if value not in PLATFORM_ROLES: raise ValueError("Неизвестная платформенная роль.")
        conn=self._connect()
        try:
            conn.execute("UPDATE app_users SET platform_role=?,updated_at=? WHERE id=?",(value,now_iso(),int(user_id)))
            self._event(conn,actor_user_id,"platform_role_updated","user",str(user_id),{"platform_role":value}); conn.commit()
            row=conn.execute(self._user_select()+" WHERE u.id=? LIMIT 1",(int(user_id),)).fetchone(); return self.public_user(self._attach_marketplaces(conn,row)) or {}
        finally:conn.close()

    def update_user(self, user_id: int, changes: dict[str, Any], actor_user_id: int) -> dict[str, Any]:
        target = self.get_user(user_id)
        if not target:
            raise ValueError("Пользователь не найден.")
        fields: list[str] = []
        params: list[Any] = []
        if "display_name" in changes:
            value = str(changes["display_name"] or "").strip()
            if len(value) < 2:
                raise ValueError("Укажите имя пользователя.")
            fields.append("display_name=?")
            params.append(value)
        if "role" in changes:
            value = str(changes["role"] or "").casefold()
            if value not in ROLES:
                raise ValueError("Неизвестная роль пользователя.")
            fields.append("role=?")
            params.append(value)
        if "is_active" in changes:
            active = 1 if bool(changes["is_active"]) else 0
            if int(user_id) == int(actor_user_id) and active == 0:
                raise ValueError("Нельзя отключить собственную учётную запись.")
            fields.append("is_active=?")
            params.append(active)
        marketplace_changes: dict[str, bool] | None = None
        if "marketplaces" in changes:
            raw_marketplaces = changes.get("marketplaces")
            if isinstance(raw_marketplaces, dict):
                requested_marketplaces = {
                    code: bool(raw_marketplaces.get(code, False))
                    for code in MARKETPLACE_CODES
                }
            else:
                enabled_marketplaces = {
                    str(value).strip() for value in (raw_marketplaces or [])
                    if str(value).strip() in MARKETPLACE_CODES
                }
                requested_marketplaces = {
                    code: code in enabled_marketplaces for code in MARKETPLACE_CODES
                }
            available_marketplaces = {
                code for code, enabled in (target.get("available_marketplaces") or {}).items()
                if code in MARKETPLACE_CODES and bool(enabled)
            }
            forbidden = {
                code for code, enabled in requested_marketplaces.items()
                if enabled and code not in available_marketplaces
            }
            if forbidden:
                raise ValueError(
                    "Нельзя выдать сотруднику недоступные компании площадки: "
                    + ", ".join(sorted(forbidden)) + "."
                )
            marketplace_changes = {
                code: requested_marketplaces.get(code, False)
                for code in available_marketplaces
            }
        permission_changes: dict[str, bool] | None = None
        if "permissions" in changes:
            raw_permissions = changes.get("permissions")
            if isinstance(raw_permissions, dict):
                permission_changes = {
                    code: bool(raw_permissions.get(code, False))
                    for code in PERMISSION_DEFINITIONS
                }
            else:
                enabled_permissions = {
                    str(value).strip() for value in (raw_permissions or [])
                    if str(value).strip() in PERMISSION_DEFINITIONS
                }
                permission_changes = {
                    code: code in enabled_permissions for code in PERMISSION_DEFINITIONS
                }
        if not fields and permission_changes is None and marketplace_changes is None:
            return target
        conn = self._connect()
        try:
            stamp = now_iso()
            tenant_id = target.get("tenant_id")
            if fields:
                fields.append("updated_at=?")
                params.append(stamp)
                params.append(int(user_id))
                conn.execute(f"UPDATE app_users SET {', '.join(fields)} WHERE id=?", params)
            if "role" in changes and tenant_id is not None:
                conn.execute(
                    "UPDATE tenant_users SET tenant_role=? WHERE user_id=? AND tenant_id=?",
                    (str(changes["role"] or "viewer").casefold(), int(user_id), int(tenant_id)),
                )
                if (
                    permission_changes is None
                    and str(changes.get("role") or "viewer").casefold()
                    != str(target.get("role") or "viewer").casefold()
                ):
                    conn.execute(
                        "DELETE FROM tenant_user_permissions WHERE tenant_id=? AND user_id=?",
                        (int(tenant_id), int(user_id)),
                    )
            if permission_changes is not None:
                if tenant_id is None:
                    raise ValueError("Пользователь не связан с компанией.")
                for code, enabled in permission_changes.items():
                    conn.execute(
                        """
                        INSERT INTO tenant_user_permissions(
                            tenant_id,user_id,permission_code,is_enabled,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(tenant_id,user_id,permission_code) DO UPDATE SET
                            is_enabled=excluded.is_enabled,updated_at=excluded.updated_at
                        """,
                        (int(tenant_id),int(user_id),code,1 if enabled else 0,stamp,stamp),
                    )
            if marketplace_changes is not None:
                if tenant_id is None:
                    raise ValueError("Пользователь не связан с компанией.")
                conn.execute(
                    "DELETE FROM tenant_user_marketplace_access WHERE tenant_id=? AND user_id=?",
                    (int(tenant_id), int(user_id)),
                )
                for code, enabled in marketplace_changes.items():
                    conn.execute(
                        """INSERT INTO tenant_user_marketplace_access(
                               tenant_id,user_id,marketplace_code,is_allowed,updated_by,
                               created_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            int(tenant_id), int(user_id), code, 1 if enabled else 0,
                            int(actor_user_id), stamp, stamp,
                        ),
                    )
            self._event(conn, actor_user_id, "user_updated", "user", str(user_id), changes)
            conn.commit()
            row = conn.execute(
                self._user_select()+" WHERE u.id=? LIMIT 1", (int(user_id),)
            ).fetchone()
            return self.public_user(self._attach_marketplaces(conn, row)) or {}
        finally:
            conn.close()

    def delete_user(self, user_id: int, actor_user_id: int) -> None:
        target = self.get_user(user_id)
        if not target:
            raise ValueError("Пользователь не найден.")
        if int(user_id) == int(actor_user_id):
            raise ValueError("Нельзя удалить собственную учётную запись.")
        conn = self._connect()
        try:
            if target.get("role") == "admin" and bool(target.get("is_active")):
                tenant_id = target.get("tenant_id")
                if tenant_id is not None:
                    active_admins = int(conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM app_users u
                        JOIN tenant_users tu ON tu.user_id=u.id AND tu.is_active=1
                        WHERE tu.tenant_id=? AND u.role='admin' AND u.is_active=1
                        """,
                        (int(tenant_id),),
                    ).fetchone()[0])
                else:
                    active_admins = int(conn.execute(
                        "SELECT COUNT(*) FROM app_users WHERE role='admin' AND is_active=1"
                    ).fetchone()[0])
                if active_admins <= 1:
                    raise ValueError("Нельзя удалить последнего активного администратора.")
            conn.execute("UPDATE app_product_state SET updated_by=NULL WHERE updated_by=?", (int(user_id),))
            conn.execute("UPDATE app_events SET user_id=NULL WHERE user_id=?", (int(user_id),))
            conn.execute("UPDATE app_reports SET created_by=NULL WHERE created_by=?", (int(user_id),))
            conn.execute("UPDATE market_match_overrides SET updated_by=NULL WHERE updated_by=?", (int(user_id),))
            self._event(conn, actor_user_id, "user_deleted", "user", str(user_id), {
                "email": target.get("email"),
                "role": target.get("role"),
            })
            conn.execute("DELETE FROM app_users WHERE id=?", (int(user_id),))
            conn.commit()
        finally:
            conn.close()

    def change_password(self, user_id: int, current_password: str, new_password: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT email,display_name,password_hash FROM app_users WHERE id=? AND is_active=1", (int(user_id),)).fetchone()
            if row is None or not check_password_hash(row["password_hash"], current_password or ""):
                raise ValueError("Текущий пароль указан неверно.")
            validate_password(new_password, str(row["email"] or ""), str(row["display_name"] or ""))
            stamp = now_iso()
            conn.execute(
                """UPDATE app_users SET password_hash=?,password_changed_at=?,
                   session_version=COALESCE(session_version,0)+1,updated_at=? WHERE id=?""",
                (hash_secret(new_password), stamp, stamp, int(user_id)),
            )
            self._event(conn, int(user_id), "password_changed", "user", str(user_id), {})
            conn.commit()
        finally:
            conn.close()
        return self.get_user(int(user_id)) or {}

    def reset_password_with_recovery(self, email: str, recovery_code: str, new_password: str) -> dict[str, Any]:
        value = self.get_user_by_email(email, include_secret=True)
        if not value or not bool(value.get("is_active")):
            raise ValueError("Не удалось подтвердить данные восстановления.")
        if not check_password_hash(str(value["recovery_hash"]), (recovery_code or "").strip().upper()):
            raise ValueError("Не удалось подтвердить данные восстановления.")
        validate_password(new_password, str(value.get("email") or ""), str(value.get("display_name") or ""))
        stamp = now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE app_users SET password_hash=?,password_changed_at=?,
                   session_version=COALESCE(session_version,0)+1,updated_at=? WHERE id=?""",
                (hash_secret(new_password), stamp, stamp, int(value["id"])),
            )
            self._event(conn, int(value["id"]), "password_recovered", "user", str(value["id"]), {})
            conn.commit()
        finally:
            conn.close()
        return self.get_user(int(value["id"])) or {}

    def regenerate_recovery(self, user_id: int, actor_user_id: int) -> str:
        code = generate_recovery_code()
        stamp = now_iso()
        conn = self._connect()
        try:
            row = conn.execute("SELECT id FROM app_users WHERE id=?", (int(user_id),)).fetchone()
            if row is None:
                raise ValueError("Пользователь не найден.")
            conn.execute(
                "UPDATE app_users SET recovery_hash=?,updated_at=? WHERE id=?",
                (hash_secret(code), stamp, int(user_id)),
            )
            self._event(conn, actor_user_id, "recovery_regenerated", "user", str(user_id), {})
            conn.commit()
            return code
        finally:
            conn.close()

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        user_id: int | None,
        event_type: str,
        entity_type: str | None,
        entity_id: str | None,
        details: dict[str, Any],
    ) -> None:
        tenant_id = None
        if user_id is not None:
            membership = conn.execute(
                """SELECT tenant_id FROM tenant_users
                   WHERE user_id=? AND is_active=1
                   ORDER BY is_primary DESC,tenant_id LIMIT 1""",
                (int(user_id),),
            ).fetchone()
            tenant_id = int(membership["tenant_id"]) if membership else None
        conn.execute(
            """
            INSERT INTO app_events(
                user_id,tenant_id,event_type,entity_type,entity_id,details_json,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                user_id,
                tenant_id,
                event_type,
                entity_type,
                entity_id,
                json.dumps(redact_sensitive(details), ensure_ascii=False, separators=(",", ":")),
                now_iso(),
            ),
        )
