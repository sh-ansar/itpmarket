from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage.database_backend import DatabaseSettings
from storage.postgres_compat import (
    configure_connection,
    connect_database,
    table_exists,
    transaction,
)


class RuntimeDatabaseContractTests(unittest.TestCase):
    def test_sqlite_fixture_has_portable_rows_transactions_ids_and_upserts(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"ITP_STORAGE_BACKEND": "sqlite", "ITP_ENV": "test"}, clear=False
        ):
            path = Path(folder) / "contract.db"
            conn = configure_connection(connect_database(path), foreign_keys=True, busy_timeout=1000)
            try:
                conn.execute(
                    "CREATE TABLE items(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE,enabled INTEGER,created_at TEXT)"
                )
                with transaction(conn, immediate=True):
                    cursor = conn.execute(
                        "INSERT INTO items(name,enabled,created_at) VALUES(?,?,datetime('now'))",
                        ("one", True),
                    )
                    self.assertGreater(cursor.lastrowid, 0)
                conn.executemany(
                    "INSERT OR IGNORE INTO items(name,enabled,created_at) VALUES(?,?,datetime('now'))",
                    [("one", True), ("two", False)],
                )
                conn.commit()
                row = conn.execute("SELECT * FROM items WHERE name=?", ("one",)).fetchone()
                self.assertEqual(row[0], row["id"])
                self.assertEqual("one", dict(row)["name"])
                self.assertTrue(table_exists(conn, "items"))
                with self.assertRaises(RuntimeError):
                    with transaction(conn):
                        conn.execute("INSERT INTO items(name,enabled) VALUES(?,?)", ("rollback", True))
                        raise RuntimeError("rollback")
                self.assertIsNone(conn.execute("SELECT id FROM items WHERE name=?", ("rollback",)).fetchone())
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
            finally:
                conn.close()

    def test_production_like_environment_requires_explicit_backend(self) -> None:
        with patch.dict(os.environ, {"ITP_ENV": "production"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ITP_STORAGE_BACKEND=postgresql"):
                DatabaseSettings.from_environment()


if __name__ == "__main__":
    unittest.main()
