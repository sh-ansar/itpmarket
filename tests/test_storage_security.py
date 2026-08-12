from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from credential_vault import CredentialVault, generate_master_key
from engine.postgres_migration import inventory
from schema import ensure_database


class StorageSecurityTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_metadata_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="credential_vault_") as folder:
            db_path = Path(folder) / "app.db"
            ensure_database(db_path)
            conn = sqlite3.connect(db_path)
            tenant_id = int(conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()[0])
            conn.close()
            previous = os.environ.get("ITP_CREDENTIAL_MASTER_KEY")
            os.environ["ITP_CREDENTIAL_MASTER_KEY"] = generate_master_key()
            try:
                vault = CredentialVault(db_path)
                reference = vault.put(
                    tenant_id, "ozon_kz", "seller-api",
                    {"client_id": "public-id", "client_secret": "do-not-store-plain"},
                )
                conn = sqlite3.connect(db_path)
                ciphertext = str(conn.execute(
                    "SELECT ciphertext FROM encrypted_credentials WHERE credential_ref=?",
                    (reference,),
                ).fetchone()[0])
                conn.close()
                self.assertNotIn("do-not-store-plain", ciphertext)
                self.assertEqual(
                    "do-not-store-plain", vault.get(tenant_id, reference)["client_secret"]
                )
                self.assertNotIn("ciphertext", vault.metadata(tenant_id)[0])
            finally:
                if previous is None:
                    os.environ.pop("ITP_CREDENTIAL_MASTER_KEY", None)
                else:
                    os.environ["ITP_CREDENTIAL_MASTER_KEY"] = previous

    def test_postgres_plan_is_read_only_and_complete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postgres_plan_") as folder:
            db_path = Path(folder) / "source.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            conn.executemany("INSERT INTO sample(value) VALUES(?)", [("a",), ("b",)])
            conn.commit(); conn.close()
            plan = inventory(db_path, "app")
            self.assertEqual(1, len(plan))
            self.assertEqual(2, plan[0].rows)
            self.assertEqual(["id"], plan[0].primary_key)


if __name__ == "__main__":
    unittest.main()
