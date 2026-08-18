from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.backup_database import _postgres_binary, _postgres_binary_major


class PostgresBackupToolSelectionTests(unittest.TestCase):
    @patch("engine.backup_database.subprocess.run")
    def test_binary_major_is_read_from_tool_version(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            ["pg_dump", "--version"], 0, "pg_dump (PostgreSQL) 16.1\n", ""
        )

        self.assertEqual(_postgres_binary_major("pg_dump"), 16)

    @patch("engine.backup_database.Path.glob")
    @patch("engine.backup_database.Path.resolve", autospec=True)
    @patch("engine.backup_database.shutil.which")
    @patch("engine.backup_database._postgres_binary_major")
    def test_matching_server_major_is_selected_over_newer_path_tool(
        self, binary_major, which, resolve, glob
    ) -> None:
        which.return_value = "C:/Program Files/PostgreSQL/17/bin/pg_dump.exe"
        glob.return_value = [
            Path("C:/Program Files/PostgreSQL/17/bin/pg_dump.exe"),
            Path("C:/Program Files/PostgreSQL/16/bin/pg_dump.exe"),
        ]
        resolve.side_effect = lambda value: value
        binary_major.side_effect = (
            lambda value: 16
            if "/16/" in str(value).replace("\\", "/")
            else 17
        )

        selected = _postgres_binary("pg_dump", server_major=16)

        self.assertIn("PostgreSQL\\16\\bin\\pg_dump.exe", selected)

    @patch("engine.backup_database.Path.glob", return_value=[])
    @patch("engine.backup_database.Path.resolve", autospec=True)
    @patch("engine.backup_database.shutil.which", return_value="C:/pg_dump.exe")
    @patch("engine.backup_database._postgres_binary_major", return_value=17)
    def test_missing_matching_client_fails_closed(
        self, _binary_major, _which, _resolve, _glob
    ) -> None:
        _resolve.side_effect = lambda value: value
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL 16"):
            _postgres_binary("pg_dump", server_major=16)


if __name__ == "__main__":
    unittest.main()
