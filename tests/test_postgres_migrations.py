from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
