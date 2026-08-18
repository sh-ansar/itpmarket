from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from data_service import DataService


def cache_only_service() -> DataService:
    service = DataService.__new__(DataService)
    service.lock = threading.RLock()
    service._rows_cache = []
    service._rows_cached_at = 0.0
    service._rows_signature = None
    service._rows_refreshing = False
    service._cache_generation = 0
    service._tenant_snapshot_cache = {}
    service._tenant_snapshot_locks = {}
    return service


class TenantSnapshotCacheTests(unittest.TestCase):
    def test_parallel_identical_loads_share_one_database_snapshot(self) -> None:
        service = cache_only_service()
        loader_started = threading.Event()
        release_loader = threading.Event()
        call_count = 0
        count_lock = threading.Lock()

        def load_snapshot(tenant_id: int, marketplaces=None):
            nonlocal call_count
            with count_lock:
                call_count += 1
            loader_started.set()
            self.assertTrue(release_loader.wait(timeout=2))
            return [{"tenant_id": tenant_id, "platform": "kaspi"}]

        with patch.object(service, "_load_tenant_catalog_snapshot", load_snapshot):
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(service._tenant_catalog_snapshot, 7, {"kaspi"})
                    for _ in range(4)
                ]
                self.assertTrue(loader_started.wait(timeout=2))
                release_loader.set()
                results = [future.result(timeout=2) for future in futures]

        self.assertEqual(1, call_count)
        self.assertTrue(all(result == results[0] for result in results))

    def test_different_tenants_do_not_share_a_loading_lock(self) -> None:
        service = cache_only_service()
        entered = threading.Barrier(2)

        def load_snapshot(tenant_id: int, marketplaces=None):
            entered.wait(timeout=2)
            return [{"tenant_id": tenant_id}]

        with patch.object(service, "_load_tenant_catalog_snapshot", load_snapshot):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(service._tenant_catalog_snapshot, 1, None)
                second = executor.submit(service._tenant_catalog_snapshot, 2, None)
                self.assertEqual(1, first.result(timeout=3)[0]["tenant_id"])
                self.assertEqual(2, second.result(timeout=3)[0]["tenant_id"])

    def test_invalidate_clears_shared_and_tenant_caches(self) -> None:
        service = cache_only_service()
        service._rows_cache = [{"product_code": "1"}]
        service._rows_cached_at = time.monotonic()
        service._rows_signature = (1,)
        service._tenant_snapshot_cache[(1, ())] = (
            time.monotonic() + 10,
            [{"product_code": "1"}],
        )
        service._tenant_snapshot_locks[(1, ())] = threading.Lock()

        service.invalidate()

        self.assertEqual([], service._rows_cache)
        self.assertIsNone(service._rows_signature)
        self.assertEqual({}, service._tenant_snapshot_cache)
        self.assertEqual({}, service._tenant_snapshot_locks)

    def test_invalidation_during_load_does_not_repopulate_tenant_cache(self) -> None:
        service = cache_only_service()

        def load_snapshot(tenant_id: int, marketplaces=None):
            service.invalidate()
            return [{"tenant_id": tenant_id}]

        with patch.object(service, "_load_tenant_catalog_snapshot", load_snapshot):
            result = service._tenant_catalog_snapshot(3, None)

        self.assertEqual([{"tenant_id": 3}], result)
        self.assertEqual({}, service._tenant_snapshot_cache)


class ProductDetailReuseTests(unittest.TestCase):
    def test_product_detail_reuses_route_catalog_rows(self) -> None:
        service = cache_only_service()
        service.preferences = lambda _user_id: {
            "rub_to_kzt": 5.5,
            "default_monthly_units": 1,
        }
        row = {
            "product_code": "wb:42",
            "source_product_code": "42",
            "platform": "wildberries",
            "title": "Test product",
            "_tenant_catalog_only": True,
            "_tenant_attributes": [],
        }

        with patch.object(
            service,
            "rows_for_user",
            side_effect=AssertionError("catalog must not be loaded twice"),
        ):
            result = service.product("wb:42", user_id=5, rows=[row])

        self.assertIsNotNone(result)
        self.assertEqual("Test product", result["title"])
        self.assertEqual([], result["offers"])


class SharedCatalogRefreshTests(unittest.TestCase):
    def test_expired_cache_is_served_while_default_refresh_runs_in_background(self) -> None:
        service = cache_only_service()
        service._rows_cache = [{"product_code": "stale-complete"}]
        service._rows_cached_at = time.monotonic() - 120
        service._rows_signature = (1,)
        loader_started = threading.Event()
        release_loader = threading.Event()

        def refresh_rows():
            loader_started.set()
            self.assertTrue(release_loader.wait(timeout=2))
            return [{"product_code": "fresh"}]

        with (
            patch.object(service, "_source_signature", return_value=(1,)),
            patch.object(service, "_materialize_shared_rows", refresh_rows),
        ):
            result = service.rows()
            self.assertEqual([{"product_code": "stale-complete"}], result)
            self.assertTrue(loader_started.wait(timeout=2))
            refresh_thread = next(
                thread
                for thread in threading.enumerate()
                if thread.name == "spyon-shared-catalog-refresh"
            )
            release_loader.set()
            refresh_thread.join(timeout=2)
            self.assertFalse(refresh_thread.is_alive())

        self.assertFalse(service._rows_refreshing)
        self.assertEqual([{"product_code": "fresh"}], service._rows_cache)

    def test_zero_ttl_forces_synchronous_refresh(self) -> None:
        service = cache_only_service()
        service._rows_cache = [{"product_code": "old"}]
        service._rows_cached_at = time.monotonic()
        service._rows_signature = (1,)

        with (
            patch.object(service, "_source_signature", return_value=(1,)),
            patch.object(
                service,
                "_materialize_shared_rows",
                return_value=[{"product_code": "fresh"}],
            ) as materialize,
        ):
            result = service.rows(ttl_seconds=0)

        materialize.assert_called_once_with()
        self.assertEqual([{"product_code": "fresh"}], result)


class CatalogUiPerformanceContractTests(unittest.TestCase):
    def test_catalog_requests_cancel_stale_work_and_keep_loaders_visible(self) -> None:
        root = Path(__file__).resolve().parents[1]
        javascript = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "app.css").read_text(encoding="utf-8")

        self.assertIn("productsRequestController?.abort()", javascript)
        self.assertIn("requestSerial!==productsRequestSerial", javascript)
        self.assertIn("productSkeletonRows", javascript)
        self.assertIn("drawerLoadingMarkup", javascript)
        self.assertIn("if(state.page==='dashboard')loadOverview()", javascript)
        self.assertIn(".product-skeleton-row", css)
        self.assertIn(".table-card.is-loading:before", css)


if __name__ == "__main__":
    unittest.main()
