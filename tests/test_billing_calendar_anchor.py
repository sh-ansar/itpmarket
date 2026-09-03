from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from billing_service import BillingService
from schema import ensure_database


class BillingCalendarAnchorTests(unittest.TestCase):
    def test_anchor_31_survives_short_month(self) -> None:
        start = datetime.fromisoformat(
            "2027-01-31T12:00:00+05:00"
        )

        february = BillingService._add_calendar_months(
            start,
            1,
            anchor_day=31,
        )

        march = BillingService._add_calendar_months(
            february,
            1,
            anchor_day=31,
        )

        april = BillingService._add_calendar_months(
            march,
            1,
            anchor_day=31,
        )

        self.assertEqual(
            "2027-02-28T12:00:00+05:00",
            february.isoformat(),
        )
        self.assertEqual(
            "2027-03-31T12:00:00+05:00",
            march.isoformat(),
        )
        self.assertEqual(
            "2027-04-30T12:00:00+05:00",
            april.isoformat(),
        )

    def test_anchor_30_returns_to_day_30(self) -> None:
        start = datetime.fromisoformat(
            "2027-01-30T08:15:20+05:00"
        )

        february = BillingService._add_calendar_months(
            start,
            1,
            anchor_day=30,
        )

        march = BillingService._add_calendar_months(
            february,
            1,
            anchor_day=30,
        )

        self.assertEqual(
            "2027-02-28T08:15:20+05:00",
            february.isoformat(),
        )
        self.assertEqual(
            "2027-03-30T08:15:20+05:00",
            march.isoformat(),
        )

    def test_sqlite_schema_contains_billing_anchor_day(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="billing_anchor_"
        ) as folder:
            db_path = (
                Path(folder)
                / "app.db"
            )

            ensure_database(
                db_path
            )

            conn = sqlite3.connect(
                db_path
            )

            try:
                columns = {
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(tenant_subscriptions)"
                    ).fetchall()
                }
            finally:
                conn.close()

            self.assertIn(
                "billing_anchor_day",
                columns,
            )


if __name__ == "__main__":
    unittest.main()
