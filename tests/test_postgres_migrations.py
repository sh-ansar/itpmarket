from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.postgres_initialize import provisioned_initialize
from engine.postgres_migrations import (
    AUTO_MARKER,
    BASELINE_MARKERS,
    MigrationError,
    migration_files,
    split_sql,
    validate_pending_migration,
)


ROOT = Path(__file__).resolve().parents[1]


class PostgresMigrationRunnerTests(
    unittest.TestCase
):
    def test_ozon_kz_metadata_seed_and_runtime_default_are_postgres_safe(
        self,
    ) -> None:
        from collectors.ozon_kz.storage import SCHEMA as ozon_kz_schema
        from engine.postgres_schema import (
            OZON_KZ_POSTGRES_RUNTIME_SCHEMA,
            _statements,
        )

        self.assertIn(
            "INSERT OR IGNORE INTO ozon_kz_connector_metadata(id, updated_at)",
            ozon_kz_schema,
        )
        statements = list(_statements(ozon_kz_schema))
        seed = next(
            statement for statement in statements
            if statement.startswith("INSERT INTO ozon_kz_connector_metadata")
        )
        self.assertIn("updated_at", seed)
        self.assertIn("CURRENT_TIMESTAMP", seed)
        self.assertTrue(seed.endswith("ON CONFLICT DO NOTHING"))
        self.assertIn(
            "ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP::text",
            OZON_KZ_POSTGRES_RUNTIME_SCHEMA,
        )

    def test_sql_splitter_preserves_do_blocks(
        self,
    ) -> None:
        sql = """
        BEGIN;

        DO $$
        BEGIN
            IF 1 = 1 THEN
                PERFORM 1;
            END IF;
        END
        $$;

        COMMIT;
        """

        statements = split_sql(sql)

        self.assertEqual(
            3,
            len(statements),
        )

        self.assertEqual(
            "BEGIN",
            statements[0],
        )

        self.assertIn(
            "PERFORM 1;",
            statements[1],
        )

        self.assertEqual(
            "COMMIT",
            statements[2],
        )

    def test_destructive_auto_migration_is_blocked(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = (
                Path(folder)
                / "20260820_bad.sql"
            )

            text = (
                "-- "
                + AUTO_MARKER
                + "\n"
                + "BEGIN;\n"
                + "DROP TABLE app.tenants;\n"
                + "COMMIT;\n"
            )

            with self.assertRaises(
                MigrationError
            ):
                validate_pending_migration(
                    path,
                    text,
                )

    def test_pending_migration_requires_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = (
                Path(folder)
                / "20260820_unmarked.sql"
            )

            with self.assertRaises(
                MigrationError
            ):
                validate_pending_migration(
                    path,
                    (
                        "BEGIN;\n"
                        "CREATE TABLE x(id integer);\n"
                        "COMMIT;\n"
                    ),
                )
    def test_current_migrations_are_known_baseline_or_auto(
        self,
    ) -> None:
        for path in migration_files(ROOT):
            text = path.read_text(
                encoding="utf-8-sig"
            )

            self.assertTrue(
                (
                    path.name
                    in BASELINE_MARKERS
                )
                or (
                    AUTO_MARKER
                    in text
                ),
                path.name,
            )

            if path.name not in BASELINE_MARKERS:
                validate_pending_migration(
                    path,
                    text,
                )

    def test_provisioned_initialize_refreshes_a_ready_schema(self) -> None:
        ready = {
            "ready": True,
            "empty": False,
            "expected_tables": 111,
            "present_tables": 111,
            "missing": {},
        }
        with patch(
            "engine.postgres_initialize.initialization_state",
            side_effect=[ready, ready],
        ), patch(
            "engine.postgres_initialize.provision_schema",
            return_value={"ok": True, "schemas": {"app": 1}},
        ) as provision:
            result = provisioned_initialize(ROOT, "postgresql://example.test/db")

        self.assertTrue(result["ready"])
        self.assertFalse(result["initialized"])
        provision.assert_called_once_with("postgresql://example.test/db")


if __name__ == "__main__":
    unittest.main()
