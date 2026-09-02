from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
OZON_ROOT = ROOT / "collectors" / "ozon"
if str(OZON_ROOT) not in sys.path:
    sys.path.insert(0, str(OZON_ROOT))

from browser_session import BrowserSession
from ozon_collector import (
    Collector,
    combined_status,
    result_exit_code,
    structured_result as collector_structured_result,
)
from ozon_probe_core import parse_product_json
from ozon_validation_core import normalize_for_import
from registry import Registry
from collectors.ozon_kz.ozon_kz_collector import require_complete
from storage.postgres_compat import _schema_for_path
from task_manager import RESULT_MESSAGES, structured_result


class OzonRuntimeContractTests(unittest.TestCase):
    def test_ru_and_kz_catalogue_scroll_waits_for_delayed_virtual_grid_growth(self) -> None:
        def catalogue_html(count: int) -> str:
            items = ",".join(
                '{&quot;sku&quot;:&quot;%s&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/seller-%s/&quot;},&quot;mainState&quot;:[]}'
                % (number, number)
                for number in range(1, count + 1)
            )
            return (
                '<div id="state-tileGridDesktop-seller" data-state="{&quot;sellerId&quot;:&quot;alfa-tires-3381444&quot;,&quot;items&quot;:[%s]}" />'
                % items
            )

        product_counts = [8, 8, 8, 8, 16, 24] + [24] * 6
        heights = [1000, 1000, 1100, 1200, 1200, 2000] + [2000] * 6

        class FakeDriver:
            def __init__(self) -> None:
                self.poll = 0

            def execute_script(self, script: str) -> object:
                if script == "window.scrollTo(0, 0);":
                    return None
                index = min(self.poll, len(product_counts) - 1)
                self.poll += 1
                return {
                    "before": 0,
                    "after": heights[index],
                    "height": heights[index],
                    "nearBottom": True,
                }

        for base_url in (
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
            "https://ozon.kz/seller/alfa-tires-3381444/",
        ):
            session = BrowserSession.__new__(BrowserSession)
            session.driver = FakeDriver()
            session.snapshot = lambda session=session: (
                "Alfa Tires",
                "Alfa Tires",
                catalogue_html(product_counts[min(session.driver.poll, len(product_counts) - 1)]),
            )
            session.blocked_state = lambda *_args: False
            session._safe_dom_text = lambda value: value
            clock = [0.0]

            def monotonic() -> float:
                return clock[0]

            def sleep(seconds: float) -> None:
                clock[0] += seconds

            events: list[dict[str, object]] = []
            with patch("browser_session.time.monotonic", side_effect=monotonic), patch(
                "browser_session.time.sleep", side_effect=sleep
            ):
                products, _next_page, _title, _text, _html, blocked = (
                    session._collect_catalog_snapshot(base_url, 60, events)
                )

            self.assertFalse(blocked)
            self.assertEqual(24, len(products))
            self.assertTrue(any(event.get("height_grew") for event in events))
            self.assertTrue(any(event.get("unique_products") == 24 for event in events))

    def test_virtualized_seller_dom_cards_accumulate_without_recommendations(self) -> None:
        def catalogue_html() -> str:
            seller_items = ",".join(
                '{&quot;sku&quot;:&quot;%s&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/seller-%s/&quot;},&quot;mainState&quot;:[]}'
                % (number, number)
                for number in range(1, 9)
            )
            recommendation_items = (
                '{&quot;sku&quot;:&quot;900&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/recommendation-900/&quot;},&quot;mainState&quot;:[]}'
            )
            return (
                '<section id="seller-container">'
                '<div id="state-tileGridDesktop-seller" '
                'data-state="{&quot;sellerId&quot;:&quot;alfa-tires-3381444&quot;,&quot;items&quot;:[%s]}" />'
                '</section>'
                '<aside id="recommendations">'
                '<div id="state-tileGridDesktop-recommendations" '
                'data-state="{&quot;title&quot;:&quot;Рекомендуем&quot;,&quot;items&quot;:[%s]}" />'
                '</aside>'
            ) % (seller_items, recommendation_items)

        visible_seller_cycles = [
            list(range(1, 9)),
            list(range(9, 17)),
            list(range(17, 25)),
            list(range(25, 33)),
        ]
        heights = [1000, 1800, 2600, 3400]

        class FakeDriver:
            def __init__(self) -> None:
                self.batch = 0

            def execute_script(self, script: str) -> object:
                if script == "window.scrollTo(0, 0);":
                    return None
                index = self.batch
                if "root.scrollTo(0, height)" in script:
                    # The next virtualized seller batch appears only after the
                    # collector reaches the current document bottom.
                    if self.batch < len(visible_seller_cycles) - 1:
                        self.batch += 1
                    return {
                        "before": heights[index] - 600,
                        "after": heights[index],
                        "heightBefore": heights[index],
                        "heightAfter": heights[index],
                        "loading": False,
                    }
                return {
                    "top": heights[index] - 600,
                    "height": heights[index],
                    "loading": False,
                }

        session = BrowserSession.__new__(BrowserSession)
        session.driver = FakeDriver()
        session.snapshot = lambda: ("Alfa Tires", "Alfa Tires", catalogue_html())
        session.blocked_state = lambda *_args: False
        session._safe_dom_text = lambda value: value
        dom_scans: list[dict[str, object]] = []

        def seller_dom_products(_base_url: str, scan: dict[str, object]) -> list[dict[str, object]]:
            dom_scans.append(dict(scan))
            # The fake mirrors a scoped seller container.  Its sibling
            # recommendation/cross-sell cards are intentionally absent.
            index = session.driver.batch
            return [
                {
                    "article": str(number),
                    "name": f"Seller {number}",
                    "url": f"https://www.ozon.ru/product/seller-{number}/",
                    "catalog_card_price": number,
                }
                for number in visible_seller_cycles[index]
            ]

        session.dom_catalog_products = seller_dom_products
        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        events: list[dict[str, object]] = []
        with patch("browser_session.time.monotonic", side_effect=monotonic), patch(
            "browser_session.time.sleep", side_effect=sleep
        ):
            products, _next_page, _title, _text, _html, blocked = (
                session._collect_catalog_snapshot(
                    "https://www.ozon.ru/seller/alfa-tires-3381444/", 60, events
                )
            )

        self.assertFalse(blocked)
        self.assertEqual({str(number) for number in range(1, 33)}, {
            str(product["article"]) for product in products
        })
        self.assertNotIn("900", {str(product["article"]) for product in products})
        self.assertTrue(all(
            scan["selected_strategy"] == "seller_evidence"
            and scan["accepted_seller_grid_ids"] == ["state-tileGridDesktop-seller"]
            for scan in dom_scans
        ))
        scroll_cycles = [event for event in events if event.get("event") == "scroll_cycle"]
        self.assertEqual([8, 8, 8], [event["new_unique"] for event in scroll_cycles[:3]])
        self.assertEqual([16, 24, 32], [event["total_unique"] for event in scroll_cycles[:3]])
        self.assertTrue(all(event["structured_products"] == 8 for event in scroll_cycles))
        self.assertTrue(all(event["seller_dom_products"] == 8 for event in scroll_cycles))
        self.assertEqual(4, scroll_cycles[-1]["stable_cycles"])

    def test_dom_fallback_requires_parser_proven_seller_grid(self) -> None:
        class FakeDriver:
            def __init__(self) -> None:
                self.script = ""
                self.arguments: tuple[object, ...] = ()

            def execute_script(self, script: str, *arguments: object) -> object:
                self.script = script
                self.arguments = arguments
                return [{
                    "url": "https://www.ozon.ru/product/seller-1/",
                    "name": "Seller product",
                    "image_url": "",
                    "price_text": "1 000 ₽",
                }]

        session = BrowserSession.__new__(BrowserSession)
        session.driver = FakeDriver()
        self.assertEqual([], session.dom_catalog_products(
            "https://www.ozon.ru/seller/alfa-tires-3381444/", {}
        ))
        products = session.dom_catalog_products(
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
            {
                "selected_strategy": "seller_evidence",
                "accepted_seller_grid_ids": ["state-tileGridDesktop-seller"],
                "accepted_seller_articles": ["1"],
            },
        )
        self.assertEqual(["1"], [product["article"] for product in products])
        self.assertEqual(
            (["state-tileGridDesktop-seller"], ["1"]),
            session.driver.arguments,
        )
        self.assertIn("document.getElementById(stateId)", session.driver.script)
        self.assertNotIn("document.querySelectorAll('a[href*=\"/product/\"]')", session.driver.script)

    def test_market_search_full_sync_is_not_total_capped_by_batch_size(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_market_search_") as folder:
            collector = Collector.__new__(Collector)
            collector.settings = SimpleNamespace(
                market_search_batch_limit=30,
                start_url="https://www.ozon.ru/seller/alfa-tires-3381444/",
            )
            collector.registry = MagicMock()
            collector.registry.client_products_for_market_search.return_value = [
                {"article": f"owner-{number}"} for number in range(31)
            ]
            collector._run_dir = MagicMock(return_value=Path(folder))
            collector.ensure_browser = MagicMock()
            collector.generate_outputs = MagicMock()

            with patch("ozon_collector.build_search_queries", return_value=[]):
                result = collector.market_search()

        collector.registry.client_products_for_market_search.assert_called_once_with(
            0, allowed_articles=None
        )
        self.assertEqual(31, result["items_total"])
        self.assertEqual(31, collector.registry.finish_market_search.call_count)
        self.assertEqual("PARTIAL", result["status"])

    def test_catalog_details_cover_every_product_from_this_discovery(self) -> None:
        collector = Collector.__new__(Collector)
        collector.discover = MagicMock(return_value={
            "status": "PASSED", "run_id": "catalog-run",
        })
        collector.process = MagicMock(return_value={"status": "PASSED"})

        result = collector.sync_catalog(limit=7)

        collector.process.assert_called_once_with(
            "refresh-prices", 0, catalog_run_id="catalog-run"
        )
        self.assertEqual("PASSED", result["status"])

    def test_catalog_run_filter_excludes_old_source_membership(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_catalog_run_") as folder:
            registry = Registry(Path(folder) / "registry.db")
            try:
                source = "https://www.ozon.ru/seller/alfa-tires-3381444/"
                for article, run_id in (("old", "old-run"), ("current", "new-run")):
                    registry.upsert_catalog_product(
                        {
                            "article": article,
                            "name": article,
                            "url": f"https://www.ozon.ru/product/{article}-1/",
                            "catalog_card_price": 100,
                        },
                        source,
                        run_id,
                        1,
                        "2026-09-02T12:00:00",
                    )
                self.assertEqual(
                    {"current"},
                    registry.articles_for_sources([source], catalog_run_id="new-run"),
                )
            finally:
                registry.close()

    def test_kz_partial_stage_cannot_be_publication_authority(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "PARTIAL"):
            require_complete({"status": "PARTIAL"}, "sync-catalog")
        self.assertEqual(
            {"status": "PASSED"},
            require_complete({"status": "PASSED"}, "sync-catalog"),
        )

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

    def test_partial_result_fails_the_job_while_retaining_its_warning_reason(self) -> None:
        self.assertEqual(
            "PARTIAL",
            combined_status(
                {"status": "PASSED"},
                {"status": "PARTIAL"},
            ),
        )
        self.assertEqual(
            "BLOCKED",
            combined_status(
                {"status": "PARTIAL"},
                {"status": "BLOCKED"},
            ),
        )

        self.assertEqual(
            0,
            result_exit_code({"status": "PASSED"}),
        )
        self.assertEqual(
            0,
            result_exit_code({"status": "READY"}),
        )
        self.assertEqual(
            2,
            result_exit_code({"status": "PARTIAL"}),
        )
        self.assertEqual(
            2,
            result_exit_code({"status": "BLOCKED"}),
        )
        self.assertEqual(
            2,
            result_exit_code({"status": "FAILED"}),
        )

        partial = collector_structured_result(
            {"status": "PARTIAL"}
        )

        self.assertEqual(
            {
                "ok": False,
                "reason": "partial_success",
            },
            partial,
        )

        self.assertIn(
            "\u0447\u0430\u0441\u0442\u0438\u0447\u043d\u043e",
            RESULT_MESSAGES["partial_success"].casefold(),
        )

    def test_full_sync_runs_market_only_after_complete_catalog(self) -> None:
        collector = Collector.__new__(Collector)

        with patch.object(
            collector,
            "sync_catalog",
            return_value={
                "status": "PASSED",
                "items_total": 100,
                "items_success": 100,
                "items_failed": 0,
            },
        ) as sync_catalog, patch.object(
            collector,
            "market_search",
            return_value={"status": "PASSED"},
        ) as market_search:
            result = collector.full_sync(100)

        sync_catalog.assert_called_once_with(100)
        market_search.assert_called_once_with(100)

        self.assertEqual("PASSED", result["status"])
        self.assertNotIn("prices", result)
        self.assertIsNotNone(result["market"])

    def test_full_sync_does_not_start_market_after_partial_catalog(self) -> None:
        collector = Collector.__new__(Collector)
        with patch.object(
            collector, "sync_catalog", return_value={"status": "PARTIAL"}
        ), patch.object(collector, "market_search") as market_search:
            result = collector.full_sync()

        market_search.assert_not_called()
        self.assertEqual("PARTIAL", result["status"])
        self.assertIsNone(result["market"])

    def test_empty_failed_seller_source_is_not_reported_as_partial_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_empty_source_") as folder:
            runtime = Path(folder)
            collector = Collector.__new__(Collector)
            collector.settings = SimpleNamespace(
                start_urls=("https://www.ozon.ru/seller/alfa-tires-3381444/",),
                start_url="https://www.ozon.ru/seller/alfa-tires-3381444/",
                catalog_product_limit=0,
                catalog_max_pages=1,
                catalog_wait_seconds=1,
                page_reloads=0,
                page_delay_seconds=(0, 0),
                runs_dir=runtime / "runs",
                reports_dir=runtime / "reports",
                exports_dir=runtime / "exports",
            )
            collector.registry = MagicMock()
            browser = MagicMock()
            browser.load_catalog.return_value = {
                "ok": False, "status": "NO_CATALOG", "events": [],
            }
            with patch.object(collector, "ensure_browser", return_value=browser), patch.object(
                collector, "generate_outputs"
            ):
                result = collector.discover()

        self.assertEqual("FAILED", result["status"])
        self.assertEqual(2, result_exit_code(result))
        self.assertEqual(
            {"ok": False, "reason": "collector_failed"},
            collector_structured_result(result),
        )

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
            runtime = SimpleNamespace(marketplace_code="ozon", profile_dir=profile, debug_port=43111, source_url="https://www.ozon.ru/seller/example/")
            process = {"profile_dir": str(profile), "debug_port": 43111, "session_id": 2}
            with patch.object(launcher, "debugger_ready", return_value=True), patch.object(
                launcher, "running_chrome_processes", return_value=[process]
            ):
                result = launcher.start_browser(runtime, dry_run=False)
        self.assertEqual("READY", result["status"])
        self.assertEqual(43111, result["port"])
        self.assertEqual(2, result["session_id"])
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
