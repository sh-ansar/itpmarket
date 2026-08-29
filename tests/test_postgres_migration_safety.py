from __future__ import annotations

import unittest

from engine.postgres_migrations import _statement_command, split_sql
from engine.postgres_migration import verify_metadata_values


class PostgresMigrationSafetyTests(unittest.TestCase):
    def test_foreign_key_delete_actions_are_ddl_not_destructive_commands(self) -> None:
        for action in ("CASCADE", "SET NULL", "RESTRICT"):
            statement = (
                "ALTER TABLE child ADD CONSTRAINT fk_parent FOREIGN KEY(parent_id) "
                f"REFERENCES parent(id) ON DELETE {action}"
            )
            self.assertEqual("ALTER", _statement_command(statement))

    def test_top_level_destructive_commands_are_detected(self) -> None:
        cases = {
            "DELETE FROM users": "DELETE",
            "DROP TABLE users": "DROP",
            "TRUNCATE TABLE users": "TRUNCATE",
            "VACUUM": "VACUUM",
            "REINDEX TABLE users": "REINDEX",
            "WITH doomed AS (SELECT id FROM users) DELETE FROM users": "DELETE",
        }
        for statement, command in cases.items():
            self.assertEqual(command, _statement_command(statement))

    def test_quoted_function_body_does_not_become_a_delete_command(self) -> None:
        statement = "CREATE FUNCTION f() RETURNS void AS $$ DELETE FROM users; $$ LANGUAGE sql"
        self.assertEqual("CREATE", _statement_command(statement))

    def test_comments_are_removed_before_top_level_classification(self) -> None:
        statements = split_sql("/* comment */ DELETE FROM users;")
        self.assertEqual(["DELETE FROM users"], statements)
        self.assertEqual("DELETE", _statement_command(statements[0]))

    def test_metadata_verifier_allows_only_declared_schema_rows(self) -> None:
        result = verify_metadata_values(
            {"legacy": "preserved"},
            {"legacy": "preserved", "schema_marker": "canonical"},
            {"schema_marker"},
        )
        self.assertTrue(result["count_ok"])
        self.assertTrue(result["digest_ok"])
        self.assertEqual(["schema_marker"], result["allowed_schema_rows"])
        self.assertEqual([], result["unexpected_target_rows"])

    def test_metadata_verifier_rejects_unknown_target_rows_and_legacy_changes(self) -> None:
        result = verify_metadata_values(
            {"legacy": "source"},
            {"legacy": "target", "unknown": "value"},
            set(),
        )
        self.assertEqual(["legacy"], result["changed"])
        self.assertEqual(["unknown"], result["unexpected_target_rows"])
        self.assertFalse(result["count_ok"])

    def test_metadata_verifier_gives_schema_owned_source_key_canonical_precedence(self) -> None:
        result = verify_metadata_values(
            {"schema_marker": "sqlite-time", "legacy": "preserved"},
            {"schema_marker": "postgres-time", "legacy": "preserved"},
            {"schema_marker"},
        )
        self.assertTrue(result["digest_ok"])
        self.assertEqual([], result["changed"])


if __name__ == "__main__":
    unittest.main()
