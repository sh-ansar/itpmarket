from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from engine.catalog_sync import (
    complete_seller_snapshot,
    materialize_verified_tenant_snapshot,
    root_api_url,
)


class KaspiCatalogContractTests(unittest.TestCase):
    def test_root_request_is_seller_scoped_without_foreign_product_skus(self) -> None:
        query = parse_qs(urlparse(root_api_url("unityre", "750000000")).query)

        self.assertEqual([":listingType:merchantListing:allMerchants:unityre"], query["q"])
        self.assertEqual(["750000000"], query["c"])
        self.assertFalse({"productCode", "masterSku", "merchantSku", "tabId"} & set(query))

    def test_only_exact_positive_total_is_a_complete_snapshot(self) -> None:
        self.assertFalse(complete_seller_snapshot(0, 100))
        self.assertFalse(complete_seller_snapshot(10, 9))
        self.assertFalse(complete_seller_snapshot(10, 11))
        self.assertTrue(complete_seller_snapshot(10, 10))

    def test_unverified_or_partial_snapshot_does_not_touch_active_catalog(self) -> None:
        args = SimpleNamespace(tenant_id=7, tenant_seller_id=11)
        products = {"seller-product": {"product_id": "seller-product"}}

        with patch("engine.catalog_sync.CatalogConfigurationService") as service:
            saved = materialize_verified_tenant_snapshot(
                Path("staging.sqlite"), args, products, {"seller-product"}, is_complete=False
            )

        self.assertEqual(0, saved)
        service.assert_not_called()

    def test_verified_snapshot_uses_atomic_seller_replace(self) -> None:
        args = SimpleNamespace(tenant_id=7, tenant_seller_id=11)
        products = {"seller-product": {"product_id": "seller-product"}}

        with patch("engine.catalog_sync.CatalogConfigurationService") as service:
            service.return_value.replace_catalog_products.return_value = 1
            saved = materialize_verified_tenant_snapshot(
                Path("staging.sqlite"), args, products, {"seller-product"}, is_complete=True
            )

        self.assertEqual(1, saved)
        call = service.return_value.replace_catalog_products.call_args
        self.assertEqual((7, "kaspi"), call.args[:2])
        self.assertEqual([{"product_id": "seller-product"}], list(call.args[2]))
        self.assertEqual(11, call.kwargs["tenant_seller_id"])


if __name__ == "__main__":
    unittest.main()
