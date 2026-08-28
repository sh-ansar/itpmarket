from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
OZON_ROOT = ROOT / "collectors" / "ozon"
if str(OZON_ROOT) not in sys.path:
    sys.path.insert(0, str(OZON_ROOT))

from browser_session import BrowserSession
from ozon_collector import combined_status, result_exit_code
from ozon_probe_core import parse_product_json
from ozon_validation_core import normalize_for_import
from registry import Registry
from storage.postgres_compat import _schema_for_path
from task_manager import RESULT_MESSAGES, structured_result


class OzonRuntimeContractTests(unittest.TestCase):
    def test_out_of_stock_widget_is_a_valid_exact_product_response(self) -> None:
        article = "2946348346"
        data = {
            "widgetStates": {
                "webOutOfStock-1": json.dumps(
                    {
                        "sku": article,
                        "skuName": "MICHELIN PILOT SPORT 4S 245/40 R20 99Y",
                        "price": "36\u2009824\u2009₽",
                        "sellerName": "Alfa Tires",
                        "sellerLink": "https://www.ozon.ru/seller/alfa-tires-3381444/",
                        "productLink": f"/product/{article}/?oos_search=false",
                        "deliveryMessage": "Доставка недоступна",
                    },
                    ensure_ascii=False,
                )
            }
        }
        item = parse_product_json(
            article,
            data,
            {"article": article, "url": f"https://www.ozon.ru/product/{article}/"},
        )
        self.assertTrue(item["success"])
        self.assertEqual(36824, item["regular_price"])
        self.assertEqual("3381444", item["seller_id"])
        self.assertEqual("OUT_OF_STOCK", item["availability_status"])
        normalized = normalize_for_import(item, "2026-08-19T12:00:00", "run")
        self.assertEqual(36824, normalized["price"])
        self.assertEqual("OUT_OF_STOCK", normalized["availability_status"])

    def test_price_queue_is_limited_to_the_selected_seller_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_source_queue_") as folder:
            registry = Registry(Path(folder) / "registry.db")
            try:
                registry.upsert_catalog_product(
                    {
                        "article": "alpha-tyre",
                        "name": "Alpha tyre",
                        "url": "https://www.ozon.ru/product/alpha-tyre-1/",
                        "catalog_card_price": 100,
                    },
                    "https://www.ozon.ru/seller/alfa-tires-3381444/",
                    "alpha-run",
                    1,
                    "2026-08-19T12:00:00",
                )
                registry.upsert_catalog_product(
                    {
                        "article": "foreign-detergent",
                        "name": "Foreign detergent",
                        "url": "https://www.ozon.ru/product/foreign-detergent-2/",
                        "catalog_card_price": 200,
                    },
                    "https://ozon.kz/seller/foreign-shop/",
                    "foreign-run",
                    1,
                    "2026-08-19T12:00:00",
                )
                allowed = registry.articles_for_sources(
                    ["https://www.ozon.ru/seller/alfa-tires-3381444/"]
                )
                self.assertEqual({"alpha-tyre"}, allowed)
                self.assertEqual(
                    ["alpha-tyre"],
                    registry.select_articles(
                        "refresh-prices", 100, allowed_articles=allowed
                    ),
                )
            finally:
                registry.close()

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

    def test_structured_challenge_result_is_user_safe(self) -> None:
        value = structured_result(
            'technical detail\nSPYON_RESULT {"ok":false,"reason":"ozon_challenge"}\n'
        )
        self.assertEqual("ozon_challenge", value["reason"])
        self.assertNotIn("code 1", RESULT_MESSAGES[value["reason"]].casefold())
        self.assertNotIn("BLOCKED_CHALLENGE", RESULT_MESSAGES[value["reason"]])

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

    def test_production_never_auto_opens_a_session_zero_browser(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_production_browser_") as folder:
            session = BrowserSession(58792, "https://www.ozon.ru/seller/example/", Path(folder))
            with patch.dict(os.environ, {"ITP_ENV": "production"}, clear=True), patch.object(
                session, "_debugger_ready", return_value=False
            ), patch.object(session, "_adopt_profile_debugger", return_value=False):
                with self.assertRaises(RuntimeError):
                    session.ensure_debug_browser()

    def test_interactive_launcher_reuses_only_its_exact_interactive_browser(self) -> None:
        import importlib.util

        launcher_path = ROOT / "scripts" / "open_ozon_browsers.py"
        spec = importlib.util.spec_from_file_location("open_ozon_browsers", launcher_path)
        self.assertIsNotNone(spec)
        launcher = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = launcher
        spec.loader.exec_module(launcher)
        with tempfile.TemporaryDirectory(prefix="ozon_launcher_") as folder:
            profile = Path(folder)
            runtime = SimpleNamespace(profile_dir=profile, debug_port=43111, source_url="https://www.ozon.ru/seller/example/")
            process = {"profile_dir": str(profile), "debug_port": 43111, "session_id": 2}
            with patch.object(launcher, "debugger_ready", return_value=True), patch.object(
                launcher, "running_chrome_processes", return_value=[process]
            ):
                result = launcher.start_browser(runtime, dry_run=False)
        self.assertEqual("reused", result["status"])
        self.assertEqual(43111, result["port"])
        self.assertNotIn("--headless", launcher_path.read_text(encoding="utf-8"))

    def test_production_rejects_session_zero_even_when_debugger_answers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_hidden_") as folder:
            profile = Path(folder)
            session = BrowserSession(51665, "https://www.ozon.ru/seller/example/", profile)
            process = {"profile_dir": str(profile), "debug_port": 51665, "session_id": 0}
            with patch.dict(os.environ, {"ITP_ENV": "production"}, clear=True), patch(
                "browser_session.sys.platform", "win32"
            ), patch("browser_session.running_chrome_processes", return_value=[process]), patch.object(
                session, "_debugger_ready", return_value=True
            ):
                with self.assertRaisesRegex(RuntimeError, "фоновой сессии"):
                    session.ensure_debug_browser()

    def test_production_reuses_matching_interactive_browser(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_interactive_") as folder:
            profile = Path(folder)
            session = BrowserSession(51665, "https://www.ozon.ru/seller/example/", profile)
            process = {"profile_dir": str(profile), "debug_port": 51665, "session_id": 2}
            with patch.dict(os.environ, {"ITP_ENV": "production"}, clear=True), patch(
                "browser_session.sys.platform", "win32"
            ), patch("browser_session.running_chrome_processes", return_value=[process]), patch.object(
                session, "_debugger_ready", return_value=True
            ):
                session.ensure_debug_browser()

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
