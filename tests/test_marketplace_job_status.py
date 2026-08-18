from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.forte import forte_collector
from collectors.halyk import halyk_collector
from engine.exact_offer_refresh import result_exit_code as kaspi_result_exit_code


class MarketplaceJobStatusTests(unittest.TestCase):
    def test_kaspi_exact_offer_partial_errors_fail_the_job(self) -> None:
        self.assertEqual(0, kaspi_result_exit_code(
            {"ok": 2, "no_competitors": 1, "error": 0}
        ))
        self.assertEqual(2, kaspi_result_exit_code(
            {"ok": 2, "no_competitors": 1, "error": 1}
        ))
        self.assertEqual(2, kaspi_result_exit_code(
            {"ok": 0, "no_competitors": 0, "error": 0}
        ))

    def test_halyk_partial_refresh_is_persisted_and_fails_the_job(self) -> None:
        with tempfile.TemporaryDirectory(prefix="halyk_status_") as folder:
            db_path = Path(folder) / "app.db"
            args = halyk_collector.parse_args([
                "refresh-offers", "--db", str(db_path), "--sleep", "0"
            ])
            with patch.object(
                halyk_collector, "refresh_market_offers", return_value=(3, 1, 2)
            ), patch.object(
                halyk_collector, "materialize_tenant_catalog", return_value=0
            ):
                exit_code = halyk_collector.run(args)
            conn = sqlite3.connect(db_path)
            try:
                status = conn.execute(
                    "SELECT status FROM halyk_sync_runs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(2, exit_code)
        self.assertEqual("partial", status)

    def test_forte_partial_refresh_is_persisted_and_fails_the_job(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forte_status_") as folder:
            db_path = Path(folder) / "app.db"
            args = forte_collector.parse_args([
                "refresh-offers", "--db", str(db_path), "--sleep", "0"
            ])
            with patch.object(
                forte_collector, "refresh_market_offers", return_value=(3, 1, 2)
            ), patch.object(
                forte_collector, "materialize_tenant_catalog", return_value=0
            ):
                exit_code = forte_collector.run(args)
            conn = sqlite3.connect(db_path)
            try:
                status = conn.execute(
                    "SELECT status FROM forte_sync_runs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(2, exit_code)
        self.assertEqual("partial", status)


if __name__ == "__main__":
    unittest.main()
