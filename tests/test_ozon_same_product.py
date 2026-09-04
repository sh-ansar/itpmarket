from __future__ import annotations

import json
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
from ozon_collector import Collector
from ozon_probe_core import (
    extract_best_seller_modal_link,
    parse_other_seller_offers,
)
from registry import Registry


def widget_data(key: str, value: dict[str, object], *, encoded: bool = True) -> dict[str, object]:
    return {
        "widgetStates": {
            key: json.dumps(value, ensure_ascii=False) if encoded else value,
        }
    }


def unavailable_result(*, failed: bool = False) -> dict[str, object]:
    return {
        "available": False,
        "failed": failed,
        "request_made": failed,
        "seller_list_found": False,
        "offers": 0,
        "skipped_own": 0,
        "deduplicated": 0,
        "stale_removed": 0,
        "error": "BLOCKED" if failed else "",
    }


class OzonSameProductParserTests(unittest.TestCase):
    def test_best_seller_capability_requires_positive_count_and_modal_link(self) -> None:
        cases = (
            ({"widgetStates": {}}, ""),
            (widget_data("webBestSeller-a", {"count": "0", "modalLink": "/modal/x"}), ""),
            (widget_data("webBestSeller-b", {"count": "5"}), ""),
            (
                widget_data(
                    "webBestSeller-runtime-id",
                    {
                        "count": "5",
                        "modalLink": (
                            "/modal/otherOffersFromSellers"
                            "?product_id=2946362370&sort=price"
                        ),
                    },
                    encoded=False,
                ),
                "/modal/otherOffersFromSellers?product_id=2946362370&sort=price",
            ),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, extract_best_seller_modal_link(payload))

    def test_five_sellers_are_normalized_with_current_price_priority(self) -> None:
        sellers = [
            {
                "sku": "4069808624",
                "id": "3625806",
                "name": "Vianor&Pirelli",
                "link": "/seller/3625806/",
                "price": {"price": "36\u2009484\u2009₽", "originalPrice": "38\u2009389\u2009₽"},
                "productLink": "https://www.ozon.ru/product/tire-4069808624/",
            },
            {
                "sku": "5004758792",
                "id": "4143072",
                "name": "Король шин",
                "link": "/seller/4143072/",
                "price": {"cardPrice": {"price": "39\u2009910\u2009₽"}},
                "productLink": "/product/tire-5004758792/",
            },
            {"sku": "4994321108", "id": "1894948", "price": {"price": "40 628 ₽"}},
            {"sku": "5550083460", "id": "1374628", "price": {"price": "41 532 ₽"}},
            {"sku": "5069975237", "id": "3696314", "price": {"price": "54 784 ₽"}},
        ]
        payload = widget_data("webSellerList-runtime-id", {"sellers": sellers})

        offers = parse_other_seller_offers(payload, "https://www.ozon.ru", "RUB")

        self.assertEqual(5, len(offers))
        self.assertEqual(36484, offers[0]["card_price"])
        self.assertEqual(38389, offers[0]["original_price"])
        self.assertEqual(39910, offers[1]["card_price"])
        self.assertEqual("https://www.ozon.ru/seller/3625806/", offers[0]["seller_url"])
        self.assertEqual("RUB", offers[0]["currency"])

    def test_original_price_is_never_used_as_current_price(self) -> None:
        payload = widget_data(
            "webSellerList-x",
            {"sellers": [{"sku": "1", "price": {"originalPrice": "88 000 ₸"}}]},
        )
        offer = parse_other_seller_offers(payload, "https://ozon.kz", "KZT")[0]
        self.assertEqual(0, offer["card_price"])
        self.assertEqual(88000, offer["original_price"])
        self.assertEqual("KZT", offer["currency"])


class OzonSameProductBrowserTests(unittest.TestCase):
    def test_composer_modal_uses_runtime_path_and_site_root_for_ru_and_kz(self) -> None:
        modal_link = "/modal/otherOffersFromSellers?product_id=2946362370&sort=price"
        for site_root, host in (
            ("https://www.ozon.ru", "www.ozon.ru"),
            ("https://ozon.kz", "ozon.kz"),
        ):
            with self.subTest(site_root=site_root):
                session = BrowserSession.__new__(BrowserSession)
                session.driver = MagicMock()
                session.site_root = site_root
                session.allowed_hosts = {host, host.removeprefix("www.")}
                session._extract_json = MagicMock(return_value=({}, "", "", ""))

                response = session.load_composer_path(modal_link, 1, 0)

                self.assertTrue(response["ok"])
                session.driver.get.assert_called_once_with(
                    f"{site_root}/api/composer-api.bx/page/json/v2"
                    f"?url={modal_link}&__rr=1"
                )

    def test_modal_request_only_runs_for_advertised_capability(self) -> None:
        session = BrowserSession.__new__(BrowserSession)
        session.site_root = "https://www.ozon.ru"
        session.marketplace_label = "Ozon.ru"
        session.load_composer_path = MagicMock(
            return_value={
                "ok": True,
                "status": "JSON_OK",
                "json": widget_data("webSellerList-any", {"sellers": []}),
                "url": "modal",
            }
        )
        missing = session.load_other_seller_offers("1", {"widgetStates": {}}, 1, 0)
        self.assertFalse(missing["request_made"])
        session.load_composer_path.assert_not_called()

        product = widget_data(
            "webBestSeller-any",
            {"count": "5", "modalLink": "/modal/runtime?product_id=1"},
        )
        available = session.load_other_seller_offers("1", product, 1, 0)
        session.load_composer_path.assert_called_once_with(
            "/modal/runtime?product_id=1", wait_seconds=1, reloads=0
        )
        self.assertTrue(available["request_made"])
        self.assertTrue(available["seller_list_found"])
        self.assertEqual("SAME_PRODUCT_UNAVAILABLE", available["status"])

    def test_missing_seller_list_is_a_nonfatal_unavailable_capability(self) -> None:
        session = BrowserSession.__new__(BrowserSession)
        session.site_root = "https://ozon.kz"
        session.marketplace_label = "Ozon.kz"
        session.load_composer_path = MagicMock(
            return_value={"ok": True, "status": "JSON_OK", "json": {"widgetStates": {}}}
        )
        product = widget_data(
            "webBestSeller-kz",
            {"count": "1", "modalLink": "/modal/kz-runtime"},
        )
        result = session.load_other_seller_offers("kz-1", product, 1, 0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["seller_list_found"])
        self.assertEqual([], result["offers"])


class OzonSameProductCollectorTests(unittest.TestCase):
    @staticmethod
    def _settings() -> SimpleNamespace:
        return SimpleNamespace(
            market_search_batch_limit=30,
            market_search_max_pages=1,
            market_search_candidate_limit=10,
            market_search_detail_limit=10,
            market_search_delay_seconds=(0, 0),
            catalog_wait_seconds=1,
            page_reloads=0,
            request_wait_seconds=1,
            product_reloads=0,
            start_url="https://www.ozon.ru/seller/alfa-tires-3381444/",
            expected_seller="Alfa Tires",
        )

    def test_filtering_saves_only_valid_unique_foreign_offers_without_enrichment(self) -> None:
        collector = Collector.__new__(Collector)
        collector.settings = self._settings()
        collector.registry = MagicMock()
        collector.registry.reconcile_same_product_candidates.return_value = 2
        collector._write_trace = MagicMock()
        collector._enrich_market_candidate = MagicMock()
        browser = MagicMock()
        browser.load_product_api.return_value = {"ok": True, "status": "JSON_OK", "json": {}}
        valid = {
            "candidate_article": "FOREIGN-1",
            "seller_id": "900",
            "seller_name": "Foreign",
            "seller_url": "https://www.ozon.ru/seller/900/",
            "product_url": "https://www.ozon.ru/product/foreign-1/",
            "card_price": 1000,
            "regular_price": 1000,
            "original_price": 1200,
            "currency": "RUB",
            "availability_status": "AVAILABLE",
        }
        browser.load_other_seller_offers.return_value = {
            "ok": True,
            "status": "SAME_PRODUCT_AVAILABLE",
            "request_made": True,
            "seller_list_found": True,
            "seller_count": 7,
            "modal_link": "/modal/runtime",
            "url": "https://www.ozon.ru/api/composer?url=/modal/runtime",
            "offers": [
                valid,
                dict(valid),
                {**valid, "seller_id": "910", "seller_name": "Foreign Two"},
                {**valid, "candidate_article": "OWN-SKU", "seller_id": "901"},
                {**valid, "candidate_article": "FOREIGN-OWN", "seller_id": "3381444"},
                {**valid, "candidate_article": "", "seller_id": "902"},
                {**valid, "candidate_article": "ZERO", "seller_id": "903", "card_price": 0},
            ],
        }
        collector.ensure_browser = MagicMock(return_value=browser)

        result = collector._collect_same_product_offers(
            {"article": "OWNER", "title": "Owner"},
            "market-run",
            Path("."),
            {"OWNER", "OWN-SKU"},
        )

        self.assertTrue(result["available"])
        self.assertEqual(2, result["offers"])
        self.assertEqual(2, result["skipped_own"])
        self.assertEqual(1, result["deduplicated"])
        self.assertEqual(2, result["stale_removed"])
        self.assertEqual(2, collector.registry.upsert_catalog_product.call_count)
        self.assertFalse(collector.registry.upsert_catalog_product.call_args.kwargs["queue_detail"])
        saved_match = collector.registry.save_market_candidate.call_args.args[5]
        self.assertEqual("EXACT", saved_match["level"])
        self.assertEqual(100, saved_match["score"])
        self.assertEqual("OZON_SAME_PRODUCT_GROUP", saved_match["method"])
        collector._enrich_market_candidate.assert_not_called()

    def _market_collector(self, folder: str, owners: list[dict[str, object]]) -> Collector:
        collector = Collector.__new__(Collector)
        collector.settings = self._settings()
        collector.registry = MagicMock()
        collector.registry.catalog_articles.return_value = {
            str(owner["article"]) for owner in owners
        }
        collector.registry.client_products_for_market_search.return_value = owners
        collector.registry.known_market_candidates.return_value = []
        collector.registry.finish_market_analysis.side_effect = (
            lambda _run_id, status, _metrics: status
        )
        collector._run_dir = MagicMock(return_value=Path(folder))
        collector.generate_outputs = MagicMock()
        collector._market_snapshot_counts = MagicMock(return_value=(1, 1, 0))
        collector._enrich_market_candidate = MagicMock(return_value=True)
        return collector

    def test_available_same_product_skips_known_refresh_search_and_enrichment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_same_product_available_") as folder:
            collector = self._market_collector(folder, [{"article": "OWNER", "title": "Owner"}])
            collector._collect_same_product_offers = MagicMock(
                return_value={
                    "available": True,
                    "failed": False,
                    "request_made": True,
                    "seller_list_found": True,
                    "offers": 2,
                    "articles": ["C1", "C2"],
                    "skipped_own": 0,
                    "deduplicated": 0,
                    "stale_removed": 0,
                    "modal_link": "/modal/runtime",
                    "modal_url": "https://www.ozon.ru/modal/runtime",
                }
            )
            browser = MagicMock()
            collector.ensure_browser = MagicMock(return_value=browser)

            result = collector.market_search(catalog_run_id="catalog")

            browser.load_catalog.assert_not_called()
            collector.registry.known_market_candidates.assert_not_called()
            collector._enrich_market_candidate.assert_not_called()
            self.assertEqual(1, result["same_product_search_skipped"])
            self.assertEqual(2, result["same_product_offers"])
            summary = json.loads(
                (Path(folder) / "summary.json").read_text(encoding="utf-8")
            )
            for metric in (
                "same_product_probe",
                "same_product_available",
                "same_product_unavailable",
                "same_product_requests",
                "same_product_success",
                "same_product_failed",
                "same_product_offers",
                "same_product_skipped_own",
                "same_product_deduplicated",
                "same_product_search_skipped",
                "same_product_fallback_search",
            ):
                self.assertIn(metric, summary)

    def test_unavailable_same_product_runs_known_then_normal_search(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_same_product_fallback_") as folder:
            collector = self._market_collector(folder, [{"article": "OWNER", "title": "Owner"}])
            collector._collect_same_product_offers = MagicMock(return_value=unavailable_result())
            browser = MagicMock()
            browser.load_catalog.return_value = {"ok": True, "products": [], "next_page": ""}
            collector.ensure_browser = MagicMock(return_value=browser)
            with patch("ozon_collector.build_search_queries", return_value=["owner"]), patch(
                "ozon_collector.sleep_range"
            ):
                result = collector.market_search(catalog_run_id="catalog")

            collector.registry.known_market_candidates.assert_called_once_with("OWNER")
            browser.load_catalog.assert_called_once()
            self.assertEqual(1, result["same_product_unavailable"])
            self.assertEqual(1, result["same_product_fallback_search"])

    def test_kz_missing_capability_falls_back_without_run_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_kz_same_product_fallback_") as folder:
            collector = self._market_collector(folder, [{"article": "KZ-OWNER", "title": "Owner"}])
            collector.settings.start_url = "https://ozon.kz/seller/example-1234/"
            collector._collect_same_product_offers = MagicMock(return_value=unavailable_result())
            browser = MagicMock()
            browser.load_catalog.return_value = {"ok": True, "products": [], "next_page": ""}
            collector.ensure_browser = MagicMock(return_value=browser)
            with patch("ozon_collector.build_search_queries", return_value=["owner"]), patch(
                "ozon_collector.sleep_range"
            ):
                result = collector.market_search(catalog_run_id="catalog")

            self.assertEqual("PASSED", result["status"])
            self.assertTrue(
                browser.load_catalog.call_args.args[0].startswith("https://ozon.kz/search/")
            )

    def test_same_product_sku_is_not_enriched_later_in_the_same_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_same_product_run_set_") as folder:
            collector = self._market_collector(
                folder,
                [{"article": "OWNER-1"}, {"article": "OWNER-2", "title": "Second"}],
            )
            collector._collect_same_product_offers = MagicMock(
                side_effect=[
                    {
                        "available": True,
                        "failed": False,
                        "request_made": True,
                        "seller_list_found": True,
                        "offers": 1,
                        "articles": ["SAME-SKU"],
                        "skipped_own": 0,
                        "deduplicated": 0,
                        "stale_removed": 0,
                    },
                    unavailable_result(),
                ]
            )
            browser = MagicMock()
            browser.load_catalog.return_value = {
                "ok": True,
                "products": [{"article": "SAME-SKU", "name": "Same"}],
                "next_page": "",
            }
            collector.ensure_browser = MagicMock(return_value=browser)
            with patch("ozon_collector.build_search_queries", return_value=["second"]), patch(
                "ozon_collector.sleep_range"
            ):
                collector.market_search(catalog_run_id="catalog")

            collector._enrich_market_candidate.assert_not_called()

    def test_failed_modal_marks_partial_and_preserves_warm_rows(self) -> None:
        collector = Collector.__new__(Collector)
        collector.settings = self._settings()
        collector.registry = MagicMock()
        collector._write_trace = MagicMock()
        browser = MagicMock()
        browser.load_product_api.return_value = {"ok": True, "status": "JSON_OK", "json": {}}
        browser.load_other_seller_offers.return_value = {
            "ok": False,
            "status": "BLOCKED_CHALLENGE",
            "request_made": True,
            "modal_link": "/modal/runtime",
        }
        collector.ensure_browser = MagicMock(return_value=browser)

        result = collector._collect_same_product_offers(
            {"article": "OWNER"}, "market-run", Path("."), {"OWNER"}
        )

        self.assertTrue(result["failed"])
        collector.registry.save_market_candidate.assert_not_called()
        collector.registry.reconcile_same_product_candidates.assert_not_called()

    def test_failed_modal_does_not_refresh_inherited_same_product_candidate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_same_product_failed_warm_") as folder:
            collector = self._market_collector(folder, [{"article": "OWNER", "title": "Owner"}])
            collector._collect_same_product_offers = MagicMock(
                return_value=unavailable_result(failed=True)
            )
            collector.registry.known_market_candidates.return_value = [
                {
                    "candidate_article": "WARM-SKU",
                    "match_method": "OZON_SAME_PRODUCT_GROUP",
                }
            ]
            browser = MagicMock()
            browser.load_catalog.return_value = {"ok": True, "products": [], "next_page": ""}
            collector.ensure_browser = MagicMock(return_value=browser)
            with patch("ozon_collector.build_search_queries", return_value=["owner"]), patch(
                "ozon_collector.sleep_range"
            ):
                result = collector.market_search(catalog_run_id="catalog")

            self.assertEqual("PARTIAL", result["status"])
            collector._enrich_market_candidate.assert_not_called()
            collector.registry.save_market_candidate.assert_not_called()


class OzonSameProductRegistryTests(unittest.TestCase):
    def test_successful_modal_reconciles_only_stale_same_product_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_same_product_registry_") as folder:
            registry = Registry(Path(folder) / "registry.db")
            try:
                stamp = "2026-09-04T12:00:00"
                for article in ("OWNER", "KEEP", "STALE", "SEARCH"):
                    registry.upsert_catalog_product(
                        {"article": article, "name": article, "catalog_card_price": 100},
                        "https://www.ozon.ru/seller/source/",
                        "catalog",
                        1,
                        stamp,
                        queue_detail=False,
                    )
                registry.begin_market_analysis("market", "catalog", 1)
                same_match = {
                    "level": "EXACT",
                    "score": 100,
                    "method": "OZON_SAME_PRODUCT_GROUP",
                    "reason": "same",
                    "reasons": [],
                }
                search_match = {**same_match, "method": "TITLE_SEARCH"}
                for candidate, seller in (("KEEP", "seller-1"), ("STALE", "seller-2")):
                    registry.save_market_candidate(
                        "OWNER", candidate, "modal", "modal", 1, same_match, "market",
                        candidate={"article": candidate, "name": candidate},
                        offer={"seller_id": seller, "card_price": 100},
                        replace_analysis_candidate=False,
                    )
                registry.save_market_candidate(
                    "OWNER", "SEARCH", "search", "search", 1, search_match, "market",
                    candidate={"article": "SEARCH", "name": "SEARCH"},
                    offer={"seller_id": "seller-3", "card_price": 100},
                )

                removed = registry.reconcile_same_product_candidates(
                    "market", "OWNER", {("KEEP", "seller-1")}
                )

                self.assertEqual(1, removed)
                current = registry.conn.execute(
                    "SELECT candidate_article,match_method FROM market_analysis_candidates "
                    "WHERE market_run_id='market' ORDER BY candidate_article"
                ).fetchall()
                self.assertEqual(
                    [("KEEP", "OZON_SAME_PRODUCT_GROUP"), ("SEARCH", "TITLE_SEARCH")],
                    [(row[0], row[1]) for row in current],
                )
                active = registry.conn.execute(
                    "SELECT active FROM market_search_candidates "
                    "WHERE client_article='OWNER' AND candidate_article='STALE'"
                ).fetchone()[0]
                self.assertEqual(0, active)
            finally:
                registry.close()


if __name__ == "__main__":
    unittest.main()
