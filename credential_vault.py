from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from storage.postgres_compat import connect_database

from marketplace_registry import MARKETPLACE_CODES
from schema import ensure_database


MASTER_KEY_ENV = "ITP_CREDENTIAL_MASTER_KEY"


class CredentialVaultError(RuntimeError):
    pass


def generate_master_key() -> str:
    """Generate a key for an external secret manager; never persist it here."""
    return Fernet.generate_key().decode("ascii")


def _fernet_from_environment() -> tuple[Fernet, str]:
    raw = str(os.environ.get(MASTER_KEY_ENV) or "").strip()
    if not raw:
        raise CredentialVaultError(
            f"Ключ шифрования не задан во внешней переменной {MASTER_KEY_ENV}."
        )
    try:
        encoded = raw.encode("ascii")
        cipher = Fernet(encoded)
    except (UnicodeEncodeError, ValueError) as exc:
        raise CredentialVaultError("Некорректный master key credential vault.") from exc
    key_id = hashlib.sha256(encoded).hexdigest()[:16]
    return cipher, key_id


class CredentialVault:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        ensure_database(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = connect_database(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def put(
        self,
        tenant_id: int,
        marketplace_code: str,
        credential_name: str,
        secret_payload: dict[str, Any],
        actor_user_id: int | None = None,
    ) -> str:
        marketplace = str(marketplace_code or "").strip()
        name = str(credential_name or "").strip()
        if marketplace not in MARKETPLACE_CODES:
            raise ValueError("Неизвестная площадка credential vault.")
        if not name or len(name) > 120:
            raise ValueError("Некорректное имя credential.")
        if not isinstance(secret_payload, dict) or not secret_payload:
            raise ValueError("Credential payload должен быть непустым объектом.")
        cipher, key_id = _fernet_from_environment()
        plaintext = json.dumps(
            secret_payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        ciphertext = cipher.encrypt(plaintext).decode("ascii")
        credential_ref = "cred_" + secrets.token_urlsafe(24)
        conn = self._connect()
        try:
            existing = conn.execute(
                """SELECT credential_ref FROM encrypted_credentials
                   WHERE tenant_id=? AND marketplace_code=? AND credential_name=?""",
                (int(tenant_id), marketplace, name),
            ).fetchone()
            if existing:
                credential_ref = str(existing["credential_ref"])
            conn.execute(
                """INSERT INTO encrypted_credentials(
                       credential_ref,tenant_id,marketplace_code,credential_name,
                       ciphertext,key_id,created_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,datetime('now'),datetime('now'))
                   ON CONFLICT(tenant_id,marketplace_code,credential_name) DO UPDATE SET
                       ciphertext=excluded.ciphertext,key_id=excluded.key_id,
                       updated_at=excluded.updated_at""",
                (
                    credential_ref, int(tenant_id), marketplace, name,
                    ciphertext, key_id, actor_user_id,
                ),
            )
            conn.commit()
            return credential_ref
        finally:
            conn.close()

    def get(self, tenant_id: int, credential_ref: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT ciphertext,key_id FROM encrypted_credentials
                   WHERE tenant_id=? AND credential_ref=?""",
                (int(tenant_id), str(credential_ref)),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            raise CredentialVaultError("Credential не найден.")
        cipher, key_id = _fernet_from_environment()
        if str(row["key_id"]) != key_id:
            raise CredentialVaultError("Credential зашифрован другим master key.")
        try:
            payload = json.loads(cipher.decrypt(
                str(row["ciphertext"]).encode("ascii")
            ).decode("utf-8"))
        except (InvalidToken, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise CredentialVaultError("Credential не удалось расшифровать.") from exc
        if not isinstance(payload, dict):
            raise CredentialVaultError("Credential payload повреждён.")
        return payload

    def metadata(self, tenant_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(
                """SELECT credential_ref,marketplace_code,credential_name,key_id,
                          created_at,updated_at
                   FROM encrypted_credentials WHERE tenant_id=?
                   ORDER BY marketplace_code,credential_name""",
                (int(tenant_id),),
            ).fetchall()]
        finally:
            conn.close()
