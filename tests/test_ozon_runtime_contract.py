from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OZON_ROOT = ROOT / "collectors" / "ozon"
if str(OZON_ROOT) not in sys.path:
    sys.path.insert(0, str(OZON_ROOT))

from ozon_collector import combined_status, result_exit_code


class OzonRuntimeContractTests(unittest.TestCase):
    def test_partial_and_blocked_results_fail_the_background_job(self) -> None:
        self.assertEqual("PARTIAL", combined_status(
            {"status": "PASSED"}, {"status": "PARTIAL"}
        ))
        self.assertEqual("BLOCKED", combined_status(
            {"status": "PARTIAL"}, {"status": "BLOCKED"}
        ))
        self.assertEqual(0, result_exit_code({"status": "PASSED"}))
        self.assertEqual(0, result_exit_code({"status": "READY"}))
        self.assertEqual(2, result_exit_code({"status": "PARTIAL"}))
        self.assertEqual(2, result_exit_code({"status": "BLOCKED"}))

    def test_postgresql_market_search_query_avoids_group_by(self) -> None:
        source = (OZON_ROOT / "registry.py").read_text(encoding="utf-8-sig")
        method = source[source.index("def client_products_for_market_search"):]
        method = method[:method.index("def primary_offer")]
        self.assertIn("EXISTS(", method)
        self.assertNotIn("GROUP BY p.article", method)

    def test_self_test_starts_without_manual_pythonpath(self) -> None:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, str(OZON_ROOT / "SELF_TEST.py")],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("SELF TEST: OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
