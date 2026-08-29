from __future__ import annotations

import unittest

from storage.postgres_compat import HybridRow, PostgresConnection, _qmarks_to_psycopg


class PostgresCompatibilityTests(unittest.TestCase):
    def test_hybrid_row_matches_sqlite_access_contract(self) -> None:
        row = HybridRow(("id", "name"), (7, "Spyon"))
        self.assertEqual(7, row[0])
        self.assertEqual("Spyon", row["name"])
        self.assertEqual([7, "Spyon"], list(row))
        self.assertEqual({"id": 7, "name": "Spyon"}, dict(row))

    def test_qmark_translation_ignores_sql_string_literals(self) -> None:
        self.assertEqual(
            "SELECT '?' literal, value FROM items WHERE id=%s AND note='still ?'",
            _qmarks_to_psycopg(
                "SELECT '?' literal, value FROM items WHERE id=? AND note='still ?'"
            ),
        )

    def test_sqlite_upserts_dates_and_nocase_are_translated(self) -> None:
        connection = object.__new__(PostgresConnection)
        connection.schema = "app"
        connection._primary_keys = {"metadata": ("key",)}
        connection._identity_keys = {}

        replace, table = connection.translate(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)"
        )
        self.assertEqual("metadata", table)
        self.assertIn('ON CONFLICT ("key") DO UPDATE', replace)
        self.assertEqual(2, replace.count("%s"))

        ignore, _ = connection.translate(
            "INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)"
        )
        self.assertIn("ON CONFLICT DO NOTHING", ignore)

        select, _ = connection.translate(
            "SELECT datetime('now') FROM app_users WHERE email=? COLLATE NOCASE"
        )
        self.assertIn("TO_CHAR(CURRENT_TIMESTAMP", select)
        self.assertIn("LOWER(email)=LOWER(%s)", select)

        begin, _ = connection.translate("BEGIN IMMEDIATE")
        self.assertEqual("BEGIN", begin)


if __name__ == "__main__":
    unittest.main()
