from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.five_company_load_test import run_scenario


class FiveCompanyWorkflowTests(unittest.TestCase):
    def test_five_companies_connections_catalogs_filters_and_operations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="five_company_test_") as folder:
            result = run_scenario(
                Path(folder) / "five.db", products_per_company=25, read_rounds=4
            )
        self.assertEqual(5, result["companies"])
        self.assertEqual(125, result["products_total"])
        self.assertEqual(5, result["approved_connections"])
        self.assertEqual(5, result["completed_operations"])
        self.assertEqual("passed", result["isolation"])
        self.assertLess(result["write_seconds"], 15.0)
        self.assertLess(result["parallel_read_seconds"], 15.0)


if __name__ == "__main__":
    unittest.main()
