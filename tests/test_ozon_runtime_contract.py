from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
OZON_ROOT = ROOT / "collectors" / "ozon"
if str(OZON_ROOT) not in sys.path:
    sys.path.insert(0, str(OZON_ROOT))

from browser_session import BrowserSession
from ozon_collector import combined_status, result_exit_code
from storage.postgres_compat import _schema_for_path


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

    def test_seller_scoped_ozon_registry_uses_ozon_ru_schema(self) -> None:
        registry = Path(
            "C:/Spyon/current/.runtime/marketplaces/t10/ozon/s17/data/registry.db"
        )
        self.assertEqual("ozon_ru", _schema_for_path(registry))

    def test_existing_profile_debugger_is_reused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_profile_port_") as folder:
            profile = Path(folder)
            session = BrowserSession(
                58792,
                "https://ozon.kz/seller/example-1/",
                profile,
            )
            process_output = (
                '"chrome.exe" --remote-debugging-port=54160 '
                f'--user-data-dir="{profile}" --profile-directory=Default'
            )
            self.assertEqual(
                [54160], session._ports_from_process_output(process_output)
            )
            checked_ports: list[int] = []

            def debugger_ready(*_args: object, **_kwargs: object) -> bool:
                checked_ports.append(session.debug_port)
                return session.debug_port == 54160

            with patch.object(
                session, "_debugger_ready", side_effect=debugger_ready
            ), patch.object(
                session, "_running_profile_debug_ports", return_value=[54160]
            ), patch.object(session, "_launch_debug_browser") as launch:
                session.ensure_debug_browser()

            self.assertEqual(54160, session.debug_port)
            self.assertEqual([58792, 54160], checked_ports)
            self.assertEqual(
                "54160",
                profile.joinpath(".spyon_devtools_port").read_text(encoding="ascii"),
            )
            launch.assert_not_called()

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
