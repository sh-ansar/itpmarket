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

import browser_session
from browser_session import BrowserSession
from ozon_collector import (
    Collector,
    combined_status,
    has_hard_failure,
    materialize_tenant_catalog,
    own_offer_availability,
    result_exit_code,
    structured_result as collector_structured_result,
)
from ozon_probe_core import parse_product_json
from ozon_validation_core import normalize_for_import
from registry import Registry
from collectors.ozon_kz import ozon_kz_collector
from collectors.ozon_kz.ozon_kz_collector import require_complete
from data_service import DataService
from storage.postgres_compat import _schema_for_path
from task_manager import RESULT_MESSAGES, structured_result


class OzonRuntimeContractTests(unittest.TestCase):
    @staticmethod
    def _catalog_articles(count: int, prefix: str = "article") -> set[str]:
        return {f"{prefix}-{number}" for number in range(count)}

    def _run_discovery_with_published_baseline(
        self,
        discovered_articles: set[str],
        published_articles: set[str] | None = None,
        product_limit: int | None = None,
        previous_passed_articles: set[str] | None = None,
    ) -> tuple[dict[str, object], dict[str, object], MagicMock]:
        with tempfile.TemporaryDirectory(prefix="ozon_catalog_shrink_guard_") as folder:
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
            registry = MagicMock()
            published_run_id = "published-catalog" if published_articles is not None else ""
            registry.current_published_catalog_run_id.return_value = published_run_id
            registry.strongest_previous_passed_discovery_run_id.return_value = ""
            if previous_passed_articles is not None:
                registry.strongest_previous_passed_discovery_run_id.return_value = (
                    "previous-passed-discovery"
                )
            registry.catalog_articles.return_value = set(
                published_articles or previous_passed_articles or set()
            )
            registry.upsert_catalog_product.return_value = (True, False)
            collector.registry = registry
            browser = MagicMock()
            browser.load_catalog.return_value = {
                "ok": True,
                "status": "CATALOG_OK",
                "products": [
                    {
                        "article": article,
                        "name": article,
                        "url": f"https://www.ozon.ru/product/{article}/",
                        "catalog_card_price": 100,
                    }
                    for article in sorted(discovered_articles)
                ],
                "next_page": "",
            }
            if product_limit is None:
                result = None
            else:
                result = product_limit
            with patch.object(collector, "ensure_browser", return_value=browser), patch.object(
                collector, "generate_outputs"
            ):
                if result is None:
                    discovery = collector.discover()
                else:
                    discovery = collector.discover(product_limit=result)
            summary = json.loads(
                (Path(discovery["run_dir"]) / "summary.json").read_text(encoding="utf-8")
            )
            return discovery, summary, registry

    def test_catalog_shrink_guard_demotes_truncated_1152_snapshot(self) -> None:
        published = self._catalog_articles(2086)
        discovered = set(sorted(published)[:1151]) | {"new-article"}

        result, summary, registry = self._run_discovery_with_published_baseline(
            discovered, published
        )

        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual("CATALOG_SHRINK_GUARD", result["reason"])
        guard = result["catalog_shrink_guard"]
        self.assertEqual(2086, guard["previous_count"])
        self.assertEqual(1152, guard["discovered_count"])
        self.assertAlmostEqual(1152 / 2086, guard["retained_ratio"], delta=1e-6)
        self.assertAlmostEqual(1151 / 1152, guard["overlap_ratio"], delta=1e-6)
        self.assertEqual("CATALOG_SHRINK_GUARD", summary["reason"])
        self.assertEqual("PARTIAL", summary["status"])
        self.assertEqual("PARTIAL", registry.finish_run.call_args.args[1])
        self.assertEqual("CATALOG_SHRINK_GUARD", registry.finish_run.call_args.args[2]["notes"])
        registry.mark_catalog_published.assert_not_called()
        registry.strongest_previous_passed_discovery_run_id.assert_not_called()

    def test_catalog_shrink_guard_demotes_80_product_subset(self) -> None:
        published = self._catalog_articles(2086)
        discovered = set(sorted(published)[:80])

        result, summary, _registry = self._run_discovery_with_published_baseline(
            discovered, published
        )

        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual("CATALOG_SHRINK_GUARD", result["reason"])
        self.assertEqual(80, result["catalog_shrink_guard"]["discovered_count"])
        self.assertEqual("CATALOG_SHRINK_GUARD", summary["catalog_shrink_guard"]["reason"])

    def test_catalog_shrink_guard_allows_one_product_growth(self) -> None:
        published = self._catalog_articles(2086)
        result, _summary, _registry = self._run_discovery_with_published_baseline(
            published | {"new-article"}, published
        )

        self.assertEqual("PASSED", result["status"])
        self.assertNotIn("catalog_shrink_guard", result)

    def test_catalog_shrink_guard_allows_1900_product_snapshot(self) -> None:
        published = self._catalog_articles(2086)
        result, _summary, _registry = self._run_discovery_with_published_baseline(
            set(sorted(published)[:1900]), published
        )

        self.assertEqual("PASSED", result["status"])
        self.assertNotIn("reason", result)

    def test_catalog_shrink_guard_is_inactive_without_any_safe_baseline(self) -> None:
        result, _summary, registry = self._run_discovery_with_published_baseline(
            self._catalog_articles(80)
        )

        self.assertEqual("PASSED", result["status"])
        registry.current_published_catalog_run_id.assert_called_once_with()
        registry.strongest_previous_passed_discovery_run_id.assert_called_once_with(
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
            result["run_id"],
        )
        registry.catalog_articles.assert_not_called()

    def test_catalog_shrink_guard_uses_previous_passed_discovery_before_first_publication(self) -> None:
        previous = self._catalog_articles(1152)
        discovered = set(sorted(previous)[:48])

        result, _summary, registry = self._run_discovery_with_published_baseline(
            discovered,
            previous_passed_articles=previous,
        )

        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual("CATALOG_SHRINK_GUARD", result["reason"])
        guard = result["catalog_shrink_guard"]
        self.assertEqual("previous_passed_discovery", guard["baseline_source"])
        self.assertEqual("previous-passed-discovery", guard["baseline_run_id"])
        self.assertEqual(1152, guard["baseline_count"])
        self.assertEqual(48, guard["discovered_count"])
        self.assertAlmostEqual(48 / 1152, guard["retained_ratio"], delta=1e-6)
        self.assertEqual(1.0, guard["overlap_ratio"])
        registry.catalog_articles.assert_called_once_with("previous-passed-discovery")

    def test_explicit_discovery_limit_keeps_existing_partial_behavior(self) -> None:
        published = self._catalog_articles(2086)
        result, _summary, registry = self._run_discovery_with_published_baseline(
            set(sorted(published)[:80]), published, product_limit=80
        )

        self.assertEqual("PARTIAL", result["status"])
        self.assertNotIn("CATALOG_SHRINK_GUARD", result.get("reason", ""))
        registry.current_published_catalog_run_id.assert_not_called()
        registry.strongest_previous_passed_discovery_run_id.assert_not_called()

    def test_sync_catalog_does_not_refresh_after_shrink_guard_demotion(self) -> None:
        collector = Collector.__new__(Collector)
        collector.discover = MagicMock(return_value={
            "status": "PARTIAL",
            "reason": "CATALOG_SHRINK_GUARD",
        })
        collector.process = MagicMock()

        result = collector.sync_catalog()

        collector.process.assert_not_called()
        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual("CATALOG_SHRINK_GUARD", result["discovery"]["reason"])

    def _run_catalog_snapshot_timeline(
        self,
        base_url: str,
        delayed_growth_at: float | None = None,
        fail_confirmation: bool = False,
    ) -> tuple[list[dict[str, object]], str, list[dict[str, object]], list[float]]:
        clock = [0.0]

        def catalogue_products() -> list[dict[str, object]]:
            count = 3 if delayed_growth_at is not None and clock[0] >= delayed_growth_at else 2
            return [
                {
                    "article": str(number),
                    "name": f"Product {number}",
                    "url": f"https://www.ozon.ru/product/product-{number}/",
                    "catalog_card_price": number,
                }
                for number in range(1, count + 1)
            ]

        class FakeDriver:
            def __init__(self) -> None:
                self.bottom_calls = 0

            def execute_script(self, script: str) -> object:
                if script == "window.scrollTo(0, 0);":
                    return None
                if "root.scrollTo(0, root.scrollHeight)" in script:
                    self.bottom_calls += 1
                    if fail_confirmation and self.bottom_calls == 1:
                        clock[0] = 10000.0
                        raise RuntimeError("confirmation unavailable")
                    height = 1200 if delayed_growth_at is not None and clock[0] >= delayed_growth_at else 1000
                    return {
                        "before": 0,
                        "after": height,
                        "heightBefore": height,
                        "heightAfter": height,
                        "height": height,
                        "loading": False,
                    }
                height = 1200 if delayed_growth_at is not None and clock[0] >= delayed_growth_at else 1000
                return {"top": 0, "height": height, "loading": False}

        def fake_parse(_page_html: str, _base_url: str, scan: dict[str, object]) -> tuple[list[dict[str, object]], str]:
            if "/seller/" in base_url:
                scan.update(
                    {
                        "selected_strategy": "seller_evidence",
                        "accepted_seller_grid_ids": ["seller-grid"],
                        "accepted_seller_articles": [product["article"] for product in catalogue_products()],
                    }
                )
            else:
                scan.update({"selected_strategy": "market_catalog"})
            return catalogue_products(), "https://www.ozon.ru/page/2/"

        session = BrowserSession.__new__(BrowserSession)
        session.driver = FakeDriver()
        session.snapshot = lambda: ("Catalog", "Catalog", "<html></html>")
        session.blocked_state = lambda *_args: False
        session._safe_dom_text = lambda value: value
        session.dom_catalog_products = lambda _base_url, _scan: []
        events: list[dict[str, object]] = []

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        with patch.object(browser_session, "parse_catalog_html", side_effect=fake_parse), patch(
            "browser_session.time.monotonic", side_effect=monotonic
        ), patch("browser_session.time.sleep", side_effect=sleep):
            products, next_page, _title, _text, _html, _blocked = session._collect_catalog_snapshot(
                base_url, 60, events
            )
        return products, next_page, events, clock

    def test_seller_terminal_confirmation_continues_after_transient_plateau(self) -> None:
        products, next_page, events, _clock = self._run_catalog_snapshot_timeline(
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
            delayed_growth_at=15.0,
        )

        self.assertEqual({"1", "2", "3"}, {str(product["article"]) for product in products})
        self.assertEqual("https://www.ozon.ru/page/2/", next_page)
        self.assertEqual(
            [1, 2, 3, 4],
            [event["stable_cycles"] for event in events if event.get("event") == "scroll_cycle"][:4],
        )
        self.assertTrue(any(event.get("event") == "terminal_confirmation_cancelled" for event in events))
        self.assertTrue(any(event.get("event") == "seller_terminal_confirmed" for event in events))

    def test_seller_terminal_confirmation_ends_after_unchanged_windows(self) -> None:
        products, next_page, events, _clock = self._run_catalog_snapshot_timeline(
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
        )

        self.assertEqual({"1", "2"}, {str(product["article"]) for product in products})
        self.assertEqual("https://www.ozon.ru/page/2/", next_page)
        windows = [event for event in events if event.get("event") == "terminal_confirmation_window"]
        self.assertEqual([1, 2], [event["window"] for event in windows])
        self.assertEqual(1, sum(event.get("event") == "seller_terminal_confirmed" for event in events))

    def test_seller_paginator_is_unavailable_before_terminal_confirmation(self) -> None:
        products, next_page, events, _clock = self._run_catalog_snapshot_timeline(
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
            fail_confirmation=True,
        )

        self.assertEqual({"1", "2"}, {str(product["article"]) for product in products})
        self.assertEqual("", next_page)
        self.assertTrue(any(event.get("next_page") for event in events if event.get("event") == "scroll_cycle"))
        self.assertFalse(any(event.get("event") == "seller_terminal_confirmed" for event in events))
        self.assertTrue(any(event.get("event") == "scroll_safety_deadline" for event in events))

    def test_non_seller_catalog_still_returns_its_paginator(self) -> None:
        products, next_page, events, _clock = self._run_catalog_snapshot_timeline(
            "https://www.ozon.ru/category/tires/",
        )

        self.assertEqual({"1", "2"}, {str(product["article"]) for product in products})
        self.assertEqual("https://www.ozon.ru/page/2/", next_page)
        self.assertFalse(any(event.get("event", "").startswith("terminal_confirmation") for event in events))

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
            collector.registry.current_published_catalog_run_id.return_value = "catalog-run"
            collector.registry.catalog_articles.return_value = {
                f"owner-{number}" for number in range(31)
            }
            collector.registry.finish_market_analysis.return_value = "PASSED"
            collector._run_dir = MagicMock(return_value=Path(folder))
            collector.ensure_browser = MagicMock()
            collector.generate_outputs = MagicMock()

            with patch("ozon_collector.build_search_queries", return_value=[]):
                result = collector.market_search()

        collector.registry.client_products_for_market_search.assert_called_once_with(
            0,
            allowed_articles={f"owner-{number}" for number in range(31)},
            catalog_run_id="catalog-run",
        )
        self.assertEqual(31, result["items_total"])
        self.assertEqual(31, collector.registry.finish_market_search.call_count)
        self.assertEqual("PASSED", result["status"])

    def test_kz_market_search_uses_current_24_item_catalog_without_publication(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_kz_current_catalog_") as folder:
            articles = {f"kz-{number}" for number in range(24)}
            collector = Collector.__new__(Collector)
            collector.settings = SimpleNamespace(
                market_search_batch_limit=30,
                start_url="https://ozon.kz/seller/alfa-tires-3381444/",
            )
            collector.registry = MagicMock()
            collector.registry.current_published_catalog_run_id.return_value = ""
            collector.registry.catalog_articles.return_value = set(articles)
            collector.registry.client_products_for_market_search.return_value = [
                {"article": article} for article in sorted(articles)
            ]
            collector.registry.finish_market_analysis.return_value = "PASSED"
            collector._run_dir = MagicMock(return_value=Path(folder))
            collector.ensure_browser = MagicMock()
            collector.generate_outputs = MagicMock()

            with patch("ozon_collector.build_search_queries", return_value=[]):
                result = collector.market_search(
                    catalog_run_id="current-kz-discovery",
                )

        collector.registry.current_published_catalog_run_id.assert_not_called()
        collector.registry.client_products_for_market_search.assert_called_once_with(
            0,
            allowed_articles=articles,
            catalog_run_id="current-kz-discovery",
        )
        self.assertEqual(24, result["items_total"])
        self.assertEqual("PASSED", result["status"])

    def test_market_search_uses_ru_or_kz_host_from_start_url(self) -> None:
        for start_url, expected_origin in (
            ("https://www.ozon.ru/seller/example/", "https://www.ozon.ru/search/"),
            ("https://ozon.kz/seller/example/", "https://ozon.kz/search/"),
        ):
            with self.subTest(start_url=start_url), tempfile.TemporaryDirectory(
                prefix="ozon_market_host_"
            ) as folder:
                collector = Collector.__new__(Collector)
                collector.settings = SimpleNamespace(
                    market_search_batch_limit=30,
                    market_search_max_pages=1,
                    market_search_candidate_limit=10,
                    market_search_detail_limit=10,
                    market_search_delay_seconds=(0, 0),
                    catalog_wait_seconds=1,
                    page_reloads=0,
                    start_url=start_url,
                )
                collector.registry = MagicMock()
                collector.registry.catalog_articles.return_value = {"owner"}
                collector.registry.client_products_for_market_search.return_value = [
                    {"article": "owner", "title": "Test"}
                ]
                collector.registry.finish_market_analysis.return_value = "PASSED"
                collector._run_dir = MagicMock(return_value=Path(folder))
                browser = MagicMock()
                browser.load_catalog.return_value = {
                    "ok": True,
                    "products": [],
                    "next_page": "",
                }
                collector.ensure_browser = MagicMock(return_value=browser)
                collector.generate_outputs = MagicMock()

                with patch(
                    "ozon_collector.build_search_queries", return_value=["test tire"]
                ), patch("ozon_collector.sleep_range"):
                    collector.market_search(catalog_run_id="catalog-run")

                search_url = browser.load_catalog.call_args.args[0]
                self.assertTrue(search_url.startswith(expected_origin), search_url)

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

    def test_partial_or_failed_discovery_never_starts_own_price_refresh(self) -> None:
        for status in ("PARTIAL", "FAILED", "BLOCKED", "INTERRUPTED"):
            collector = Collector.__new__(Collector)
            collector.discover = MagicMock(return_value={"status": status})
            collector.process = MagicMock()

            result = collector.sync_catalog()

            self.assertTrue(has_hard_failure({"status": status}))
            collector.process.assert_not_called()
            self.assertEqual(status, result["status"])

    def test_catalog_details_must_select_every_article_from_discovery_run(self) -> None:
        collector = Collector.__new__(Collector)
        collector.discover = MagicMock(return_value={
            "status": "PASSED", "run_id": "catalog-run", "items_total": 24,
        })
        collector.process = MagicMock(return_value={
            "status": "PASSED", "run_id": "refresh-run", "items_total": 23,
        })

        result = collector.sync_catalog()

        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual(24, result["details"]["expected_catalog_articles"])
        self.assertEqual(23, result["details"]["selected_catalog_articles"])

    @staticmethod
    def _own_catalog_item(article: str, price: int, availability: str = "AVAILABLE") -> tuple[dict[str, object], dict[str, object]]:
        product_url = f"https://www.ozon.ru/product/{article}/"
        return (
            {
                "article": article,
                "seller_id": "3381444",
                "seller_name": "Alfa Tires",
                "seller_link": "https://www.ozon.ru/seller/alfa-tires-3381444/",
            },
            {
                "source_url": product_url,
                "title": f"Tyre {article}",
                "image_url": "",
                "price": price,
                "catalog_price": price,
                "regular_price": price,
                "original_price": price,
                "currency": "RUB",
                "availability_status": availability,
                "identity_completeness_percent": 0,
            },
        )

    @staticmethod
    def _own_catalog_settings(registry_path: Path) -> SimpleNamespace:
        source = "https://www.ozon.ru/seller/alfa-tires-3381444/"
        return SimpleNamespace(
            start_url=source,
            start_urls=(source,),
            expected_seller="Alfa Tires",
            database_path=registry_path,
        )

    def test_current_refresh_snapshot_replaces_old_own_price(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_fresh_own_price_") as folder:
            registry = Registry(Path(folder) / "registry.db")
            try:
                source = "https://www.ozon.ru/seller/alfa-tires-3381444/"
                article = "A"
                registry.upsert_catalog_product(
                    {"article": article, "name": "Old", "url": "https://www.ozon.ru/product/A/", "catalog_card_price": 100},
                    source, "old-discovery", 1, "2026-09-02T12:00:00",
                )
                item, normalized = self._own_catalog_item(article, 100)
                registry.update_from_detail(item, normalized, "old-refresh", "2026-09-02T12:01:00")
                registry.upsert_catalog_product(
                    {"article": article, "name": "New", "url": "https://www.ozon.ru/product/A/", "catalog_card_price": 120},
                    source, "new-discovery", 1, "2026-09-02T13:00:00",
                )
                item, normalized = self._own_catalog_item(article, 120, "OUT_OF_STOCK")
                registry.update_from_detail(item, normalized, "new-refresh", "2026-09-02T13:01:00")
                settings = self._own_catalog_settings(registry.path)
                with patch("ozon_collector.CatalogConfigurationService") as service:
                    service.return_value.replace_catalog_products.return_value = 1
                    self.assertEqual(1, materialize_tenant_catalog(
                        settings, 1, str(Path(folder) / "app.db"), "ozon",
                        catalog_run_id="new-discovery", refresh_run_id="new-refresh",
                    ))
                    products = service.return_value.replace_catalog_products.call_args.args[2]
                self.assertEqual(120, products[0]["price"])
                self.assertEqual("OUT_OF_STOCK", products[0]["availability"])
                self.assertEqual("2026-09-02T13:01:00", products[0]["updated_at"])
            finally:
                registry.close()

    def test_failed_current_refresh_cannot_publish_stale_own_price(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_stale_own_price_") as folder:
            registry = Registry(Path(folder) / "registry.db")
            try:
                source = "https://www.ozon.ru/seller/alfa-tires-3381444/"
                article = "A"
                registry.upsert_catalog_product(
                    {"article": article, "name": "Old", "url": "https://www.ozon.ru/product/A/", "catalog_card_price": 100},
                    source, "old-discovery", 1, "2026-09-02T12:00:00",
                )
                item, normalized = self._own_catalog_item(article, 100)
                registry.update_from_detail(item, normalized, "old-refresh", "2026-09-02T12:01:00")
                settings = self._own_catalog_settings(registry.path)
                published: list[list[dict[str, object]]] = []
                with patch("ozon_collector.CatalogConfigurationService") as service:
                    service.return_value.replace_catalog_products.side_effect = (
                        lambda _tenant, _marketplace, rows, **_kwargs: published.append(rows) or len(rows)
                    )
                    materialize_tenant_catalog(
                        settings, 1, str(Path(folder) / "app.db"), "ozon",
                        catalog_run_id="old-discovery", refresh_run_id="old-refresh",
                    )
                    registry.upsert_catalog_product(
                        {"article": article, "name": "Current", "url": "https://www.ozon.ru/product/A/", "catalog_card_price": 120},
                        source, "new-discovery", 1, "2026-09-02T13:00:00",
                    )
                    with self.assertRaisesRegex(RuntimeError, "fresh own offer is missing"):
                        materialize_tenant_catalog(
                            settings, 1, str(Path(folder) / "app.db"), "ozon",
                            catalog_run_id="new-discovery", refresh_run_id="failed-refresh",
                        )
                self.assertEqual(1, len(published))
                self.assertEqual(100, published[0][0]["price"])
            finally:
                registry.close()

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

    def test_successful_own_pdp_without_out_of_stock_widget_is_available(self) -> None:
        self.assertEqual(
            "AVAILABLE",
            own_offer_availability({"success": True, "availability_status": "UNKNOWN"}),
        )
        self.assertEqual(
            "OUT_OF_STOCK",
            own_offer_availability({"success": True, "availability_status": "OUT_OF_STOCK"}),
        )

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

    def test_refresh_limit_zero_selects_every_article_from_catalog_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_limit_zero_") as folder:
            registry = Registry(Path(folder) / "registry.db")
            try:
                source = "https://www.ozon.ru/seller/alfa-tires-3381444/"
                for number in range(24):
                    registry.upsert_catalog_product(
                        {
                            "article": f"article-{number}",
                            "name": f"Article {number}",
                            "url": f"https://www.ozon.ru/product/article-{number}/",
                            "catalog_card_price": 100,
                        },
                        source, "catalog-run", 1, "2026-09-02T12:00:00",
                    )
                allowed = registry.articles_for_sources([source], catalog_run_id="catalog-run")
                selected = registry.select_articles(
                    "refresh-prices", 0, allowed_articles=allowed
                )
                self.assertEqual(24, len(allowed))
                self.assertEqual(24, len(selected))
                self.assertEqual(set(selected), allowed)
            finally:
                registry.close()

    def test_market_queue_is_current_catalog_scoped_without_a_tire_size_cap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_market_queue_") as folder:
            registry = Registry(Path(folder) / "registry.db")
            try:
                source = "https://www.ozon.ru/seller/alfa-tires-3381444/"
                for article, run_id in (("removed", "old-catalog"), ("tube", "current-catalog")):
                    registry.upsert_catalog_product(
                        {"article": article, "name": article, "url": f"https://www.ozon.ru/product/{article}/", "catalog_card_price": 100},
                        source, run_id, 1, "2026-09-02T12:00:00",
                    )
                    item, normalized = self._own_catalog_item(article, 100)
                    normalized["brand"] = "Michelin"
                    registry.update_from_detail(item, normalized, f"refresh-{article}", "2026-09-02T12:01:00")
                selected = registry.client_products_for_market_search(
                    0, catalog_run_id="current-catalog"
                )
                self.assertEqual(["tube"], [row["article"] for row in selected])
            finally:
                registry.close()

    @staticmethod
    def _market_fixture_registry(folder: str) -> tuple[Registry, Path]:
        root = Path(folder) / "collectors" / "ozon"
        data_dir = root / "data"
        data_dir.mkdir(parents=True)
        (root / "EXPECTED_SELLER.txt").write_text("Alfa Tires\n", encoding="utf-8")
        (root / "START_URLS.txt").write_text(
            "https://www.ozon.ru/seller/alfa-tires-3381444/\n", encoding="utf-8"
        )
        registry = Registry(data_dir / "registry.db")
        source = "https://www.ozon.ru/seller/alfa-tires-3381444/"
        registry.begin_run("catalog", "discover", source)
        registry.upsert_catalog_product(
            {"article": "A", "name": "Owner", "url": "https://www.ozon.ru/product/A/", "catalog_card_price": 1000},
            source, "catalog", 1, "2026-09-02T12:00:00",
        )
        item, normalized = OzonRuntimeContractTests._own_catalog_item("A", 1000)
        normalized.update({"brand": "Michelin", "model": "Pilot", "tire_size": "205/55R16"})
        registry.update_from_detail(item, normalized, "own-refresh", "2026-09-02T12:01:00")
        registry.finish_run("catalog", "PASSED", {"items_total": 1, "items_success": 1})
        registry.mark_catalog_published("catalog")
        registry.upsert_catalog_product(
            {"article": "B", "name": "Foreign", "url": "https://www.ozon.ru/product/B/", "catalog_card_price": 1400},
            "https://www.ozon.ru/search/?text=michelin", "candidate", 1, "2026-09-02T12:02:00",
        )
        return registry, registry.path

    @staticmethod
    def _publish_market_result(registry: Registry, run_id: str, price: int | None, status: str = "PASSED") -> str:
        registry.begin_market_analysis(run_id, "catalog", 1)
        if price is not None:
            registry.save_market_candidate(
                "A", "B", "Michelin Pilot", "https://www.ozon.ru/search/", 1,
                {"level": "EXACT", "score": 100, "method": "OZON_SAME_ARTICLE", "reason": "same card", "reasons": []},
                run_id,
                candidate={"article": "B", "title": "Foreign", "canonical_url": "https://www.ozon.ru/product/B/"},
                offer={"seller_id": "foreign", "seller_name": "Foreign", "seller_url": "https://www.ozon.ru/seller/foreign/", "card_price": price, "currency": "RUB", "availability_status": "AVAILABLE"},
            )
        registry.record_market_analysis_product(
            run_id, "A", "COMPLETED" if price is not None else "NO_MATCH", "q", "u", 1 if price else 0, 1 if price else 0, 0
        )
        return registry.finish_market_analysis(run_id, status, {"items_success": 1 if status == "PASSED" else 0})

    def test_completed_zero_competitor_snapshot_replaces_old_competitor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_market_current_") as folder:
            registry, path = self._market_fixture_registry(folder)
            try:
                self.assertEqual("PASSED", self._publish_market_result(registry, "market-old", 1400))
                service = DataService(Path(folder) / "app.db", "unused", path)
                before = service._ozon_rows()[0]
                self.assertEqual(1400, before["market_min_price_original"])
                self.assertEqual(1, before["exact_candidate_count"])
                self.assertEqual("OZON_SAME_ARTICLE", before["match_method"])
                self.assertEqual("PASSED", self._publish_market_result(registry, "market-new", None))
                after = service._ozon_rows()[0]
                self.assertEqual(0, after["candidate_count"])
                self.assertIsNone(after["market_min_price_original"])
                self.assertEqual("NO_OTHER_SELLERS", after["price_status"])
                self.assertEqual("market-new", after["market_run_id"])
            finally:
                registry.close()

    def test_partial_market_run_keeps_previous_completed_snapshot_current(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_market_partial_") as folder:
            registry, path = self._market_fixture_registry(folder)
            try:
                self.assertEqual("PASSED", self._publish_market_result(registry, "market-old", 100))
                self.assertEqual("PARTIAL", self._publish_market_result(registry, "market-new", 120, "PARTIAL"))
                row = DataService(Path(folder) / "app.db", "unused", path)._ozon_rows()[0]
                self.assertEqual(100, row["market_min_price_original"])
                self.assertEqual("market-old", row["market_run_id"])
            finally:
                registry.close()

    def test_failed_blocked_or_interrupted_market_run_never_publishes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_market_failed_") as folder:
            registry, path = self._market_fixture_registry(folder)
            try:
                self.assertEqual("PASSED", self._publish_market_result(registry, "market-old", 100))
                for status in ("FAILED", "BLOCKED", "INTERRUPTED"):
                    self.assertEqual(status, self._publish_market_result(registry, f"market-{status}", 120, status))
                    row = DataService(Path(folder) / "app.db", "unused", path)._ozon_rows()[0]
                    self.assertEqual(100, row["market_min_price_original"])
                    self.assertEqual("market-old", row["market_run_id"])
            finally:
                registry.close()

    def test_new_published_catalog_makes_old_market_snapshot_not_analyzed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_market_stale_") as folder:
            registry, path = self._market_fixture_registry(folder)
            try:
                self.assertEqual("PASSED", self._publish_market_result(registry, "market-old", 100))
                source = "https://www.ozon.ru/seller/alfa-tires-3381444/"
                registry.begin_run("catalog-new", "discover", source)
                registry.upsert_catalog_product(
                    {"article": "A", "name": "Owner updated", "url": "https://www.ozon.ru/product/A/", "catalog_card_price": 1100},
                    source, "catalog-new", 1, "2026-09-02T13:00:00",
                )
                registry.finish_run("catalog-new", "PASSED", {"items_total": 1, "items_success": 1})
                registry.mark_catalog_published("catalog-new")
                row = DataService(Path(folder) / "app.db", "unused", path)._ozon_rows()[0]
                self.assertEqual("NOT_ANALYZED", row["price_status"])
                self.assertEqual("", row["market_run_id"])
            finally:
                registry.close()

    def test_own_seller_is_excluded_from_current_market_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_market_own_") as folder:
            registry, path = self._market_fixture_registry(folder)
            try:
                registry.begin_market_analysis("market-own", "catalog", 1)
                registry.save_market_candidate(
                    "A", "B", "q", "u", 1,
                    {"level": "EXACT", "score": 100, "method": "OZON_SAME_ARTICLE", "reason": "same", "reasons": []},
                    "market-own", candidate={"title": "Owner"},
                    offer={"seller_id": "3381444", "seller_name": "Alfa Tires", "card_price": 900, "currency": "RUB"},
                )
                registry.record_market_analysis_product("market-own", "A", "COMPLETED", "q", "u", 1, 1, 0)
                self.assertEqual("PASSED", registry.finish_market_analysis("market-own", "PASSED", {"items_success": 1}))
                row = DataService(Path(folder) / "app.db", "unused", path)._ozon_rows()[0]
                self.assertEqual(0, row["candidate_count"])
                self.assertEqual("NO_OTHER_SELLERS", row["price_status"])
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
                "discovery": {"run_id": "catalog-run"},
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
        market_search.assert_called_once_with(100, catalog_run_id="catalog-run")

        self.assertEqual("PASSED", result["status"])
        self.assertNotIn("prices", result)
        self.assertIsNotNone(result["market"])

    def test_kz_full_sync_passes_current_discovery_run_to_market_search(self) -> None:
        collector = Collector.__new__(Collector)
        collector.settings = SimpleNamespace(
            start_url="https://ozon.kz/seller/alfa-tires-3381444/"
        )

        with patch.object(
            collector,
            "sync_catalog",
            return_value={
                "status": "PASSED",
                "discovery": {
                    "run_id": "current-kz-discovery",
                    "items_total": 24,
                },
                "details": {"status": "PASSED", "items_total": 24},
            },
        ), patch.object(
            collector,
            "market_search",
            return_value={"status": "PASSED", "items_total": 24},
        ) as market_search:
            result = collector.full_sync()

        market_search.assert_called_once_with(
            None,
            catalog_run_id="current-kz-discovery",
        )
        self.assertEqual("PASSED", result["status"])
        self.assertEqual(24, result["market"]["items_total"])

    def test_kz_refresh_prices_uses_own_price_refresh_not_market_search(self) -> None:
        args = SimpleNamespace(
            action="refresh-prices",
            limit=0,
            articles="kz-1,kz-2",
            tenant_id=3,
            tenant_seller_id=11,
            app_db="app.db",
            db="kz.db",
        )
        settings = SimpleNamespace(database_path=Path("kz.db"))
        collector = MagicMock()
        collector.process.return_value = {"status": "PASSED"}

        with patch.object(
            ozon_kz_collector, "build_parser"
        ) as parser, patch.object(
            ozon_kz_collector, "build_settings", return_value=settings
        ), patch.object(
            ozon_kz_collector, "ensure_schema"
        ), patch.object(
            ozon_kz_collector, "Collector", return_value=collector
        ), patch("builtins.print"):
            parser.return_value.parse_args.return_value = args
            exit_code = ozon_kz_collector.main()

        self.assertEqual(0, exit_code)
        collector.process.assert_called_once_with(
            "refresh-prices", None, {"kz-1", "kz-2"}
        )
        collector.market_search.assert_not_called()
        collector.registry.mark_catalog_published.assert_not_called()

    def test_kz_catalog_publication_marks_only_complete_current_run(self) -> None:
        args = SimpleNamespace(
            action="sync-catalog",
            limit=0,
            articles="",
            tenant_id=3,
            tenant_seller_id=11,
            app_db="app.db",
            db="kz.db",
        )
        settings = SimpleNamespace(
            database_path=Path("kz.db"),
            start_url="https://ozon.kz/seller/alfa-tires-3381444/",
        )

        for status, expected_exit_code in (("PASSED", 0), ("PARTIAL", 1)):
            with self.subTest(status=status):
                collector = MagicMock()
                collector.sync_catalog.return_value = {
                    "status": status,
                    "discovery": {
                        "run_id": "current-kz-discovery",
                        "items_total": 24,
                    },
                }
                error_connection = MagicMock()
                with patch.object(
                    ozon_kz_collector, "build_parser"
                ) as parser, patch.object(
                    ozon_kz_collector, "build_settings", return_value=settings
                ), patch.object(
                    ozon_kz_collector, "ensure_schema"
                ), patch.object(
                    ozon_kz_collector, "Collector", return_value=collector
                ), patch.object(
                    ozon_kz_collector,
                    "mirror_public_registry",
                    return_value={"products": 24, "offers": 24},
                ) as mirror, patch.object(
                    ozon_kz_collector,
                    "materialize_tenant_catalog",
                    return_value=24,
                ) as materialize, patch.object(
                    ozon_kz_collector, "connect", return_value=error_connection
                ), patch("builtins.print"):
                    parser.return_value.parse_args.return_value = args
                    exit_code = ozon_kz_collector.main()

                self.assertEqual(expected_exit_code, exit_code)
                if status == "PASSED":
                    mirror.assert_called_once_with(
                        settings, catalog_run_id="current-kz-discovery"
                    )
                    self.assertEqual(
                        "current-kz-discovery",
                        materialize.call_args.kwargs["catalog_run_id"],
                    )
                    collector.registry.mark_catalog_published.assert_called_once_with(
                        "current-kz-discovery"
                    )
                else:
                    mirror.assert_not_called()
                    materialize.assert_not_called()
                    collector.registry.mark_catalog_published.assert_not_called()

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
