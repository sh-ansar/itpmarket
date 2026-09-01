from __future__ import annotations

import unittest

from collectors.ozon.ozon_probe_core import parse_catalog_html
from ozon_source_verification import OzonSourceVerificationError, resolve_ozon_snapshot


class OzonSourceVerificationTests(unittest.TestCase):
    def test_kz_canonical_identity_requires_agreeing_browser_evidence(self) -> None:
        result = resolve_ozon_snapshot(
            "ozon_kz",
            final_url="https://ozon.kz/продавец/alfa-tires-3381444/",
            page_html=(
                '<link rel="canonical" href="https://ozon.kz/продавец/alfa-tires-3381444/">'
                '<h1>Alfa Tires</h1><script>{"sellerId":"alfa-tires-3381444"}</script>'
            ),
            page_text="Alfa Tires alfa-tires-3381444",
        )
        self.assertEqual("verified", result["verification_state"])
        self.assertEqual("alfa-tires-3381444", result["canonical_seller_id"])
        self.assertEqual("https://ozon.kz/seller/alfa-tires-3381444/", result["canonical_seller_url"])

    def test_legacy_seller_path_is_normalized_to_current_storefront_path(self) -> None:
        result = resolve_ozon_snapshot(
            "ozon",
            final_url="https://www.ozon.ru/seller/alfa-tires-3381444/",
            page_html=(
                '<link rel="canonical" href="https://www.ozon.ru/seller/alfa-tires-3381444/">'
                '<h1>Alfa Tires</h1><script>{"sellerId":"alfa-tires-3381444"}</script>'
            ),
            page_text="Alfa Tires alfa-tires-3381444",
        )
        self.assertEqual(
            "https://www.ozon.ru/seller/alfa-tires-3381444/",
            result["canonical_seller_url"],
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

    def test_recommendation_grid_is_not_catalogue(self) -> None:
        page = '''
          <div id="state-tileGridDesktop-recommendations" data-state="{&quot;title&quot;:&quot;Рекомендуем&quot;,&quot;items&quot;:[{&quot;sku&quot;:&quot;1&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/recommended-1/&quot;},&quot;mainState&quot;:[]}]}" />
        '''
        products, _ = parse_catalog_html(page, "https://ozon.kz/продавец/alfa-tires-3381444/")
        self.assertEqual([], products)

    def test_seller_catalogue_requires_positive_seller_widget_context(self) -> None:
        page = '''
          <div id="state-tileGridDesktop-seller" data-state="{&quot;sellerId&quot;:&quot;alfa-tires-3381444&quot;,&quot;items&quot;:[{&quot;sku&quot;:&quot;10&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/seller-product-10/&quot;},&quot;mainState&quot;:[]}] }" />
          <div id="state-tileGridDesktop-other" data-state="{&quot;items&quot;:[{&quot;sku&quot;:&quot;20&quot;,&quot;action&quot;:{&quot;link&quot;:&quot;/product/other-product-20/&quot;},&quot;mainState&quot;:[]}] }" />
        '''
        products, _ = parse_catalog_html(page, "https://ozon.kz/продавец/alfa-tires-3381444/")
        self.assertEqual(["10"], [item["article"] for item in products])


if __name__ == "__main__":
    unittest.main()
