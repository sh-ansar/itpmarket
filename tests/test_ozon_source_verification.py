from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from collectors.ozon.ozon_probe_core import parse_catalog_html
from ozon_source_verification import (
    OzonSourceVerificationError,
    _interactive_snapshot,
    resolve_ozon_snapshot,
)


class OzonSourceVerificationTests(unittest.TestCase):
    @staticmethod
    def _temporary_tab_fixture(*, fail_navigation: bool = False):
        class FakeSwitch:
            def __init__(self, driver):
                self.driver = driver

            def new_window(self, _kind):
                self.driver.handles.append("verification-tab")
                self.driver.current = "verification-tab"

            def window(self, handle):
                if handle not in self.driver.handles:
                    raise RuntimeError("missing window")
                self.driver.current = handle

        class FakeDriver:
            def __init__(self):
                self.handles = ["working-tab"]
                self.current = "working-tab"
                self.current_url = "https://www.ozon.ru/search/?text=working"
                self.get_calls = []
                self.closed = []
                self.switch_to = FakeSwitch(self)

            @property
            def current_window_handle(self):
                return self.current

            @property
            def window_handles(self):
                return list(self.handles)

            def get(self, url):
                self.get_calls.append((self.current, url))
                if fail_navigation:
                    raise RuntimeError("navigation failed")
                self.current_url = url

            def close(self):
                self.closed.append(self.current)
                self.handles.remove(self.current)
                self.current = self.handles[0]

        driver = FakeDriver()

        class FakeSession:
            instance = None

            def __init__(self, _port, _source_url):
                self.driver = driver
                FakeSession.instance = self

            def connect(self):
                return self

            def snapshot(self):
                return (
                    "Store",
                    "",
                    '<div id="state-tileGridDesktop-main" '
                    'data-state="{&quot;items&quot;:['
                    '{&quot;sku&quot;:&quot;101&quot;,'
                    '&quot;action&quot;:{&quot;link&quot;:'
                    '&quot;/product/store-product-101/&quot;},'
                    '&quot;mainState&quot;:[]}]}"/>'
                )

        return driver, FakeSession

    def test_interactive_snapshot_uses_temporary_tab_and_restores_working_tab(self) -> None:
        driver, fake_session = self._temporary_tab_fixture()
        with patch.dict(
            sys.modules,
            {"browser_session": SimpleNamespace(BrowserSession=fake_session)},
        ):
            result = _interactive_snapshot(
                "ozon", "https://www.ozon.ru/seller/store-101/"
            )

        self.assertEqual("https://www.ozon.ru/seller/store-101/", result["final_url"])
        self.assertEqual(
            [("verification-tab", "https://www.ozon.ru/seller/store-101/")],
            driver.get_calls,
        )
        self.assertEqual(["working-tab"], driver.handles)
        self.assertEqual("working-tab", driver.current_window_handle)
        self.assertEqual(["verification-tab"], driver.closed)
        self.assertIsNone(fake_session.instance.driver)

    def test_interactive_snapshot_closes_temporary_tab_after_navigation_error(self) -> None:
        driver, fake_session = self._temporary_tab_fixture(fail_navigation=True)
        with patch.dict(
            sys.modules,
            {"browser_session": SimpleNamespace(BrowserSession=fake_session)},
        ):
            with self.assertRaisesRegex(RuntimeError, "navigation failed"):
                _interactive_snapshot(
                    "ozon", "https://www.ozon.ru/seller/store-101/"
                )

        self.assertEqual(["working-tab"], driver.handles)
        self.assertEqual("working-tab", driver.current_window_handle)
        self.assertEqual(["verification-tab"], driver.closed)
        self.assertIsNone(fake_session.instance.driver)

    def test_seller_grid_pages_accumulate_more_than_sixteen_unique_products(self) -> None:
        seen: set[str] = set()
        for page_no in range(3):
            items = ",".join(
                '{&quot;sku&quot;:&quot;%s&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/seller-%s/&quot;},&quot;mainState&quot;:[]}'
                % (number, number)
                for number in range(page_no * 8 + 1, page_no * 8 + 9)
            )
            page = (
                '<div id="state-tileGridDesktop-seller" data-state='
                '"{&quot;sellerId&quot;:&quot;alfa-tires-3381444&quot;,&quot;items&quot;:[%s]}" />'
            ) % items
            products, _ = parse_catalog_html(
                page, "https://www.ozon.ru/seller/alfa-tires-3381444/"
            )
            seen.update(str(item["article"]) for item in products)
        self.assertEqual(24, len(seen))

    def test_kz_canonical_identity_requires_agreeing_browser_evidence(self) -> None:
        page_html = (
            '<link rel="canonical" '
            'href="https://ozon.kz/\u043f\u0440\u043e\u0434\u0430\u0432\u0435\u0446/alfa-tires-3381444/">'
            '<div id="state-tileGridDesktop-seller" '
            'data-state="{&quot;sellerId&quot;:&quot;alfa-tires-3381444&quot;,'
            '&quot;items&quot;:[{&quot;sku&quot;:&quot;2868562113&quot;,'
            '&quot;action&quot;:{&quot;link&quot;:&quot;/product/michelin-2868562113/&quot;},'
            '&quot;mainState&quot;:[]}]}"/>'
        )

        result = resolve_ozon_snapshot(
            "ozon_kz",
            final_url=(
                "https://ozon.kz/"
                "\u043f\u0440\u043e\u0434\u0430\u0432\u0435\u0446/"
                "alfa-tires-3381444/"
            ),
            page_html=page_html,
            page_text="",
        )

        self.assertEqual("verified", result["verification_state"])
        self.assertEqual(
            "alfa-tires-3381444",
            result["canonical_seller_id"],
        )
        self.assertEqual(
            "https://ozon.kz/seller/alfa-tires-3381444/",
            result["canonical_seller_url"],
        )
        self.assertEqual(1, result["product_count"])

    def test_legacy_seller_path_is_normalized_to_current_storefront_path(self) -> None:
        page_html = (
            '<link rel="canonical" '
            'href="https://www.ozon.ru/seller/alfa-tires-3381444/">'
            '<div id="state-tileGridDesktop-seller" '
            'data-state="{&quot;sellerId&quot;:&quot;alfa-tires-3381444&quot;,'
            '&quot;items&quot;:[{&quot;sku&quot;:&quot;2868562113&quot;,'
            '&quot;action&quot;:{&quot;link&quot;:&quot;/product/michelin-2868562113/&quot;},'
            '&quot;mainState&quot;:[]}]}"/>'
        )

        result = resolve_ozon_snapshot(
            "ozon",
            final_url=(
                "https://www.ozon.ru/"
                "seller/alfa-tires-3381444/"
            ),
            page_html=page_html,
            page_text="",
        )

        self.assertEqual(
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
            result["canonical_seller_url"],
        )
        self.assertEqual(1, result["product_count"])

    def test_kz_real_storefront_without_slug_in_dom_is_verified(self) -> None:
        page_html = (
            '<div id="state-tileGridDesktop-main" '
            'data-state="{&quot;items&quot;:['
            '{&quot;sku&quot;:&quot;2868562113&quot;,'
            '&quot;action&quot;:{&quot;link&quot;:'
            '&quot;/product/michelin-2868562113/&quot;},'
            '&quot;mainState&quot;:[]}]}"/>'
        )

        result = resolve_ozon_snapshot(
            "ozon_kz",
            final_url=(
                "https://ozon.kz/"
                "seller/alfa-tires-3381444/"
            ),
            page_html=page_html,
            page_text="",
            page_title="",
        )

        self.assertEqual("verified", result["verification_state"])
        self.assertEqual(1, result["product_count"])
        self.assertFalse(result["catalogue_empty"])
        self.assertEqual(
            "alfa-tires-3381444",
            result["seller_name"],
        )

    def test_seller_name_without_storefront_evidence_is_not_enough(self) -> None:
        with self.assertRaisesRegex(
            OzonSourceVerificationError,
            "no verified catalogue products",
        ):
            resolve_ozon_snapshot(
                "ozon_kz",
                final_url=(
                    "https://ozon.kz/"
                    "seller/alfa-tires-3381444/"
                ),
                page_html="<h1>Alfa Tires</h1>",
                page_text="Alfa Tires",
                page_title="Alfa Tires",
            )

    def test_short_ru_slug_is_not_a_verified_storefront(self) -> None:
        with self.assertRaises(OzonSourceVerificationError):
            resolve_ozon_snapshot(
                "ozon",
                final_url="https://www.ozon.ru/seller/alfa-tires/",
                page_html='<h1>Alfa Tires</h1>',
                page_text="Alfa Tires",
            )

    def test_cross_marketplace_canonical_is_rejected(self) -> None:
        with self.assertRaises(OzonSourceVerificationError):
            resolve_ozon_snapshot(
                "ozon_kz",
                final_url="https://www.ozon.ru/seller/alfa-tires-3381444/",
                page_html='<link rel="canonical" href="https://www.ozon.ru/seller/alfa-tires-3381444/">',
                page_text="alfa-tires-3381444",
            )

    def test_seller_storefront_single_unscoped_grid_is_safe_fallback(self) -> None:
        page = '''
          <div id="state-tileGridDesktop-main" data-state="{&quot;items&quot;:[{&quot;sku&quot;:&quot;1&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/seller-product-1/&quot;},&quot;mainState&quot;:[]}]}" />
        '''
        scan: dict[str, object] = {}
        products, _ = parse_catalog_html(
            page,
            "https://www.ozon.ru/seller/alfa-tires-3381444/?abt_att=1",
            scan,
        )

        self.assertEqual(["1"], [item["article"] for item in products])
        self.assertEqual(
            {
                "grids_total": 1,
                "grids_recommendation_rejected": 0,
                "grids_seller_matched": 0,
                "grids_unscoped": 1,
                "selected_strategy": "seller_single_unscoped_fallback",
                "accepted_seller_grid_ids": ["state-tileGridDesktop-main"],
                "accepted_seller_articles": ["1"],
                "products_found": 1,
            },
            scan,
        )

    def test_recommendation_grid_is_not_catalogue(self) -> None:
        page = '''
          <div id="state-tileGridDesktop-recommendations" data-state="{&quot;title&quot;:&quot;Рекомендуем&quot;,&quot;items&quot;:[{&quot;sku&quot;:&quot;1&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/recommended-1/&quot;},&quot;mainState&quot;:[]}]}" />
        '''
        scan: dict[str, object] = {}
        products, _ = parse_catalog_html(
            page,
            "https://ozon.kz/продавец/alfa-tires-3381444/",
            scan,
        )
        self.assertEqual([], products)
        self.assertEqual(1, scan["grids_recommendation_rejected"])
        self.assertEqual("none", scan["selected_strategy"])

    def test_seller_evidence_retains_strong_widget_match(self) -> None:
        page = '''
          <div id="state-tileGridDesktop-seller" data-state="{&quot;sellerId&quot;:&quot;alfa-tires-3381444&quot;,&quot;items&quot;:[{&quot;sku&quot;:&quot;10&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/seller-product-10/&quot;},&quot;mainState&quot;:[]}] }" />
          <div id="state-tileGridDesktop-other" data-state="{&quot;items&quot;:[{&quot;sku&quot;:&quot;20&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/other-product-20/&quot;},&quot;mainState&quot;:[]}] }" />
        '''
        scan: dict[str, object] = {}
        products, _ = parse_catalog_html(
            page,
            "https://ozon.kz/продавец/alfa-tires-3381444/",
            scan,
        )
        self.assertEqual(["10"], [item["article"] for item in products])
        self.assertEqual("seller_evidence", scan["selected_strategy"])
        self.assertEqual(1, scan["grids_seller_matched"])

    def test_non_seller_url_does_not_enter_seller_fallback(self) -> None:
        page = '''
          <div id="state-tileGridDesktop-search" data-state="{&quot;items&quot;:[{&quot;sku&quot;:&quot;30&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/search-product-30/&quot;},&quot;mainState&quot;:[]}]}" />
        '''
        scan: dict[str, object] = {}
        products, _ = parse_catalog_html(
            page,
            "https://www.ozon.ru/search/?text=tyres",
            scan,
        )

        self.assertEqual(["30"], [item["article"] for item in products])
        self.assertEqual("none", scan["selected_strategy"])
        self.assertEqual(0, scan["grids_unscoped"])

    def test_multiple_unscoped_seller_grids_are_not_collected(self) -> None:
        page = '''
          <div id="state-tileGridDesktop-main" data-state="{&quot;items&quot;:[{&quot;sku&quot;:&quot;40&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/main-product-40/&quot;},&quot;mainState&quot;:[]}]}" />
          <div id="state-tileGridDesktop-other" data-state="{&quot;items&quot;:[{&quot;sku&quot;:&quot;41&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/other-product-41/&quot;},&quot;mainState&quot;:[]}]}" />
        '''
        scan: dict[str, object] = {}
        products, _ = parse_catalog_html(
            page,
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
            scan,
        )

        self.assertEqual([], products)
        self.assertEqual(2, scan["grids_unscoped"])
        self.assertEqual("none", scan["selected_strategy"])

    def test_kz_storefront_uses_same_single_unscoped_fallback(self) -> None:
        page = '''
          <div id="state-tileGridDesktop-main" data-state="{&quot;items&quot;:[{&quot;sku&quot;:&quot;50&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/kz-product-50/&quot;},&quot;mainState&quot;:[]}]}" />
        '''
        products, _ = parse_catalog_html(
            page,
            "https://ozon.kz/продавец/alfa-tires-3381444/",
        )

        self.assertEqual(["50"], [item["article"] for item in products])


if __name__ == "__main__":
    unittest.main()
