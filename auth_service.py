from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from schema import ensure_database

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ROLES = {"admin", "operator", "viewer"}
PLATFORM_ROLES = {"", "superadmin", "support", "technical"}
MARKETPLACE_CODES = {"kaspi", "ozon", "forte_market", "halyk_market"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_email(value: str) -> str:
    return (value or "").strip().casefold()


def validate_password(password: str) -> None:
    value = password or ""
    if len(value) < 10:
        raise ValueError("Пароль должен содержать не менее 10 символов.")
    if not any(char.isalpha() for char in value) or not any(char.isdigit() for char in value):
        raise ValueError("Пароль должен содержать буквы и цифры.")


def generate_recovery_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "UNITYRE-" + "-".join(groups)


class AuthService:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        ensure_database(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
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
            "tenant_role": value.get("tenant_role") or value.get("role") or "viewer",
            "marketplaces": value.get("marketplaces") if isinstance(value.get("marketplaces"), dict) else {},
            "created_at": value.get("created_at"),
            "updated_at": value.get("updated_at"),
            "last_login_at": value.get("last_login_at"),
        }

    @staticmethod
    def _user_select() -> str:
        return """
            SELECT u.*,tu.tenant_id,tu.tenant_role,t.name AS tenant_name,t.slug AS tenant_slug
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
        access = {code: False for code in MARKETPLACE_CODES}
        tenant_id = value.get("tenant_id")
        if tenant_id is not None:
            rows = conn.execute(
                """
                SELECT marketplace_code,is_enabled
                FROM user_marketplace_access
                WHERE tenant_id=? AND user_id=?
                """,
                (int(tenant_id), int(value["id"])),
            ).fetchall()
            for item in rows:
                code = str(item["marketplace_code"])
                if code in access:
                    access[code] = bool(item["is_enabled"])
        value["marketplaces"] = access
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
        return self.create_user(email, display_name, password, "admin", actor_user_id=None, tenant_id=None, platform_role="superadmin")

    def create_user(
        self,
        email: str,
        display_name: str,
        password: str,
        role: str,
        actor_user_id: int | None,
        tenant_id: int | None = None,
        platform_role: str = "",
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
        validate_password(password)
        recovery_code = generate_recovery_code()
        stamp = now_iso()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO app_users(
                    email,display_name,password_hash,recovery_hash,role,platform_role,is_active,
                    created_at,updated_at,password_changed_at
                ) VALUES(?,?,?,?,?,?,1,?,?,?)
                """,
                (
                    email_value,
                    name_value,
                    generate_password_hash(password),
                    generate_password_hash(recovery_code),
                    role_value,
                    platform_role_value,
                    stamp,
                    stamp,
                    stamp,
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
                integrations = conn.execute(
                    "SELECT integration_code,status FROM tenant_integrations WHERE tenant_id=?",
                    (resolved_tenant_id,),
                ).fetchall()
                for integration in integrations:
                    enabled = 1 if str(integration["status"]) in {"active", "setup"} else 0
                    conn.execute(
                        """
                        INSERT INTO user_marketplace_access(
                            tenant_id,user_id,marketplace_code,is_enabled,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(tenant_id,user_id,marketplace_code) DO UPDATE SET
                            is_enabled=excluded.is_enabled,updated_at=excluded.updated_at
                        """,
                        (
                            resolved_tenant_id,user_id,str(integration["integration_code"]),
                            enabled,stamp,stamp,
                        ),
                    )
            self._event(conn, actor_user_id or user_id, "user_created", "user", str(user_id), {
                "email": email_value,
                "role": role_value,
            })
            conn.commit()
            row = conn.execute(self._user_select()+" WHERE u.id=? LIMIT 1", (user_id,)).fetchone()
            return self.public_user(self._attach_marketplaces(conn, row)) or {}, recovery_code
        except sqlite3.IntegrityError as exc:
            raise ValueError("Пользователь с такой почтой уже существует.") from exc
        finally:
            conn.close()

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        value = self.get_user_by_email(email, include_secret=True)
        if not value or not bool(value.get("is_active")):
            return None
        if not check_password_hash(str(value["password_hash"]), password or ""):
            return None
        stamp = now_iso()
        conn = self._connect()
        try:
            conn.execute("UPDATE app_users SET last_login_at=?,updated_at=? WHERE id=?", (stamp, stamp, value["id"]))
            self._event(conn, int(value["id"]), "login", "user", str(value["id"]), {})
            conn.commit()
        finally:
            conn.close()
        return self.public_user(value)

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
        access_changes: dict[str, bool] | None = None
        if "marketplaces" in changes:
            raw = changes.get("marketplaces")
            if isinstance(raw, dict):
                access_changes = {
                    code: bool(raw.get(code, False)) for code in MARKETPLACE_CODES
                }
            else:
                enabled = {
                    str(value).strip() for value in (raw or [])
                    if str(value).strip() in MARKETPLACE_CODES
                }
                access_changes = {code: code in enabled for code in MARKETPLACE_CODES}
        if not fields and access_changes is None:
            return target
        conn = self._connect()
        try:
            stamp = now_iso()
            if fields:
                fields.append("updated_at=?")
                params.append(stamp)
                params.append(int(user_id))
                conn.execute(f"UPDATE app_users SET {', '.join(fields)} WHERE id=?", params)
            if "role" in changes:
                conn.execute(
                    "UPDATE tenant_users SET tenant_role=? WHERE user_id=?",
                    (str(changes["role"] or "viewer").casefold(), int(user_id)),
                )
            if access_changes is not None:
                tenant_id = target.get("tenant_id")
                if tenant_id is None:
                    raise ValueError("Пользователь не связан с компанией.")
                for code, enabled in access_changes.items():
                    conn.execute(
                        """
                        INSERT INTO user_marketplace_access(
                            tenant_id,user_id,marketplace_code,is_enabled,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(tenant_id,user_id,marketplace_code) DO UPDATE SET
                            is_enabled=excluded.is_enabled,updated_at=excluded.updated_at
                        """,
                        (int(tenant_id),int(user_id),code,1 if enabled else 0,stamp,stamp),
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

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        validate_password(new_password)
        conn = self._connect()
        try:
            row = conn.execute("SELECT password_hash FROM app_users WHERE id=? AND is_active=1", (int(user_id),)).fetchone()
            if row is None or not check_password_hash(row["password_hash"], current_password or ""):
                raise ValueError("Текущий пароль указан неверно.")
            stamp = now_iso()
            conn.execute(
                "UPDATE app_users SET password_hash=?,password_changed_at=?,updated_at=? WHERE id=?",
                (generate_password_hash(new_password), stamp, stamp, int(user_id)),
            )
            self._event(conn, int(user_id), "password_changed", "user", str(user_id), {})
            conn.commit()
        finally:
            conn.close()

    def reset_password_with_recovery(self, email: str, recovery_code: str, new_password: str) -> None:
        validate_password(new_password)
        value = self.get_user_by_email(email, include_secret=True)
        if not value or not bool(value.get("is_active")):
            raise ValueError("Не удалось подтвердить данные восстановления.")
        if not check_password_hash(str(value["recovery_hash"]), (recovery_code or "").strip().upper()):
            raise ValueError("Не удалось подтвердить данные восстановления.")
        stamp = now_iso()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE app_users SET password_hash=?,password_changed_at=?,updated_at=? WHERE id=?",
                (generate_password_hash(new_password), stamp, stamp, int(value["id"])),
            )
            self._event(conn, int(value["id"]), "password_recovered", "user", str(value["id"]), {})
            conn.commit()
        finally:
            conn.close()

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
                (generate_password_hash(code), stamp, int(user_id)),
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
        conn.execute(
            """
            INSERT INTO app_events(user_id,event_type,entity_type,entity_id,details_json,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                user_id,
                event_type,
                entity_type,
                entity_id,
                json.dumps(details, ensure_ascii=False, separators=(",", ":")),
                now_iso(),
            ),
        )
