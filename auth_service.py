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
            "is_active": bool(value["is_active"]),
            "created_at": value.get("created_at"),
            "updated_at": value.get("updated_at"),
            "last_login_at": value.get("last_login_at"),
        }

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM app_users WHERE id=?", (int(user_id),)).fetchone()
            return self.public_user(row)
        finally:
            conn.close()

    def get_user_by_email(self, email: str, include_secret: bool = False) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM app_users WHERE email=? COLLATE NOCASE",
                (normalize_email(email),),
            ).fetchone()
            if row is None:
                return None
            return dict(row) if include_secret else self.public_user(row)
        finally:
            conn.close()

    def create_initial_admin(self, email: str, display_name: str, password: str) -> tuple[dict[str, Any], str]:
        if self.has_users():
            raise ValueError("Первичная настройка уже выполнена.")
        return self.create_user(email, display_name, password, "admin", actor_user_id=None)

    def create_user(
        self,
        email: str,
        display_name: str,
        password: str,
        role: str,
        actor_user_id: int | None,
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
        validate_password(password)
        recovery_code = generate_recovery_code()
        stamp = now_iso()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO app_users(
                    email,display_name,password_hash,recovery_hash,role,is_active,
                    created_at,updated_at,password_changed_at
                ) VALUES(?,?,?,?,?,1,?,?,?)
                """,
                (
                    email_value,
                    name_value,
                    generate_password_hash(password),
                    generate_password_hash(recovery_code),
                    role_value,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            user_id = int(cursor.lastrowid)
            self._event(conn, actor_user_id or user_id, "user_created", "user", str(user_id), {
                "email": email_value,
                "role": role_value,
            })
            conn.commit()
            row = conn.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
            return self.public_user(row) or {}, recovery_code
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

    def list_users(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM app_users ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'operator' THEN 1 ELSE 2 END, display_name"
            ).fetchall()
            return [self.public_user(row) or {} for row in rows]
        finally:
            conn.close()

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
        if not fields:
            return target
        fields.append("updated_at=?")
        params.append(now_iso())
        params.append(int(user_id))
        conn = self._connect()
        try:
            conn.execute(f"UPDATE app_users SET {', '.join(fields)} WHERE id=?", params)
            self._event(conn, actor_user_id, "user_updated", "user", str(user_id), changes)
            conn.commit()
            row = conn.execute("SELECT * FROM app_users WHERE id=?", (int(user_id),)).fetchone()
            return self.public_user(row) or {}
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
