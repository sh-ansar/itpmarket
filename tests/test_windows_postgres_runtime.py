from __future__ import annotations

import re
import unittest
from pathlib import Path


class WindowsPostgresRuntimeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.script = (
            cls.root / "scripts" / "postgres_runtime.ps1"
        ).read_text(encoding="utf-8-sig")

    def test_existing_cluster_selects_matching_postgres_major(self) -> None:
        self.assertIn("PG_VERSION", self.script)
        self.assertIn("RequiredMajorVersion", self.script)
        self.assertRegex(
            self.script,
            re.compile(
                r"Find-PostgresBin\s+`\s*"
                r"-RequiredMajorVersion\s+\$requiredMajor",
                re.MULTILINE,
            ),
        )
        self.assertIn(
            "Existing cluster will not be started with",
            self.script,
        )

    def test_runtime_rejects_incomplete_postgres_installation(self) -> None:
        self.assertIn('share\\timezone', self.script)
        for executable in (
            "postgres.exe",
            "pg_ctl.exe",
            "pg_isready.exe",
            "psql.exe",
            "createdb.exe",
            "initdb.exe",
        ):
            self.assertIn(executable, self.script)

    def test_runtime_waits_for_database_readiness(self) -> None:
        self.assertIn("Wait-PostgresReady", self.script)
        self.assertIn("pg_isready.exe", self.script)
        self.assertIn("-p $Port", self.script)
        self.assertIn("Start-Sleep -Seconds 1", self.script)
        self.assertIn("${activeMajor}:", self.script)
        self.assertNotIn("$activeMajor:", self.script)


if __name__ == "__main__":
    unittest.main()

