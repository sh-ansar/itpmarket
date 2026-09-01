from __future__ import annotations

import unittest

from marketplace_source_rules import (
    DEFAULT_MARKETPLACE_SOURCE_RULES,
    parse_marketplace_source,
    validate_marketplace_source_rules,
)


class MarketplaceSourceRuleTests(unittest.TestCase):
    def test_real_examples_extract_seller_and_product_identifiers(self) -> None:
        kaspi = parse_marketplace_source(
            "https://kaspi.kz/shop/m/12917020/products?productCode=123271857"
            "&masterSku=123271857&merchantSku=123271857_850884586",
            "kaspi",
        )
        self.assertEqual("12917020", kaspi["seller_identifier"])
        self.assertEqual("123271857", kaspi["product_id"])
        self.assertEqual("https://kaspi.kz/shop/m/12917020/products", kaspi["seller_url"])

        forte = parse_marketplace_source(
            "https://market.forte.kz/merchant/B8pXMdkk110XZRswXw"
            "?productId=c681a9d9-6ef7-11ed-9013-92962dec7f6b&type=all",
            "forte_market",
        )
        self.assertEqual("B8pXMdkk110XZRswXw", forte["seller_identifier"])
        self.assertEqual("c681a9d9-6ef7-11ed-9013-92962dec7f6b", forte["product_id"])
        self.assertEqual(
            "https://market.forte.kz/merchant/B8pXMdkk110XZRswXw?type=all",
            forte["seller_url"],
        )

        halyk = parse_marketplace_source(
            "https://halykmarket.kz/merchant/24955?f=merchantName%3AMechta.kz",
            "halyk_market",
        )
        self.assertEqual("24955", halyk["seller_identifier"])
        self.assertEqual("Mechta.kz", halyk["seller_name"])

        wildberries = parse_marketplace_source(
            "https://global.wildberries.ru/seller/250000260", "wildberries"
        )
        self.assertEqual("250000260", wildberries["seller_identifier"])
        self.assertEqual(
            "https://global.wildberries.ru/seller/250000260",
            wildberries["seller_url"],
        )

    def test_bare_ids_and_scheme_less_urls_are_supported(self) -> None:
        values = {
            "kaspi": "12917020",
            "ozon": "ridial",
            "ozon_kz": "ridial",
            "halyk_market": "24955",
            "forte_market": "B8pXMdkk110XZRswXw",
            "wildberries": "250000260",
        }
        for code, value in values.items():
            with self.subTest(code=code):
                result = parse_marketplace_source(value, code)
                self.assertEqual(value, result["seller_identifier"])
                self.assertEqual("seller_id", result["input_type"])
                self.assertTrue(result["seller_url"].startswith("https://"))

        ozon = parse_marketplace_source("www.ozon.ru/seller/ridial/", "ozon")
        self.assertEqual("ridial", ozon["seller_identifier"])
        self.assertEqual("url", ozon["input_type"])

    def test_current_ozon_storefront_path_is_canonical_and_legacy_path_is_accepted(self) -> None:
        for code, source, expected in (
            (
                "ozon",
                "https://www.ozon.ru/продавец/alfa-tires-3381444/",
                "https://www.ozon.ru/продавец/alfa-tires-3381444/",
            ),
            (
                "ozon_kz",
                "https://ozon.kz/seller/alfa-tires-3381444/",
                "https://ozon.kz/продавец/alfa-tires-3381444/",
            ),
        ):
            with self.subTest(code=code):
                result = parse_marketplace_source(source, code)
                self.assertEqual("alfa-tires-3381444", result["seller_identifier"])
                self.assertEqual(expected, result["seller_url"])

    def test_product_page_fallback_and_cross_marketplace_rejection(self) -> None:
        product = parse_marketplace_source(
            "https://market.forte.kz/items/noutbuk-asus-rog-strix-g15-602890",
            "forte_market",
        )
        self.assertEqual("product", product["source_scope"])
        self.assertEqual(
            "product:noutbuk-asus-rog-strix-g15-602890",
            product["seller_identifier"],
        )
        with self.assertRaisesRegex(ValueError, "другой площадке"):
            parse_marketplace_source("https://ozon.kz/seller/ridial/", "ozon")

    def test_custom_rule_changes_recognition_and_canonical_url(self) -> None:
        rules = {code: dict(rule) for code, rule in DEFAULT_MARKETPLACE_SOURCE_RULES.items()}
        rules["forte_market"] = {
            **rules["forte_market"],
            "seller_path_patterns": [r"/vendor/(?P<seller_id>[^/?#]+)"],
            "seller_url_template": "https://market.forte.kz/merchant/{seller_id}?type=all&view=grid",
        }
        validated = validate_marketplace_source_rules({"marketplaces": rules})
        result = parse_marketplace_source(
            "https://market.forte.kz/vendor/CustomSeller42", "forte_market", validated
        )
        self.assertEqual("CustomSeller42", result["seller_identifier"])
        self.assertEqual(
            "https://market.forte.kz/merchant/CustomSeller42?type=all&view=grid",
            result["seller_url"],
        )

    def test_invalid_regex_and_external_template_host_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_marketplace_source_rules({
                "kaspi": {"seller_path_patterns": ["("]},
            })
        with self.assertRaises(ValueError):
            validate_marketplace_source_rules({
                "kaspi": {"seller_url_template": "https://evil.example/{seller_id}"},
            })


if __name__ == "__main__":
    unittest.main()
