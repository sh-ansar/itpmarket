from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from auth_service import AuthService
from ozon_browser_runtime import (
    configure_legacy_profiles,
    managed_ozon_session_zero_processes,
    resolve_ozon_runtimes,
)
from saas_service import SaaSService
from schema import ensure_database


ROOT = Path(__file__).resolve().parents[1]


def launcher_module():
    path = ROOT / "scripts" / "open_ozon_browsers.py"
    spec = importlib.util.spec_from_file_location("ozon_launcher_hotfix", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OzonBrowserLauncherTests(unittest.TestCase):
    def sellers(self) -> list[dict]:
        return [
            {"id": 11, "runtime_seller_id": 11, "tenant_id": 1, "marketplace_code": "ozon", "source_url": "https://www.ozon.ru/seller/first-11/"},
            {"id": 12, "runtime_seller_id": 12, "tenant_id": 1, "marketplace_code": "ozon_kz", "source_url": "https://ozon.kz/seller/first-12/"},
            {"id": 13, "runtime_seller_id": 13, "tenant_id": 2, "marketplace_code": "ozon", "source_url": "https://www.ozon.ru/seller/second-13/"},
            {"id": 14, "runtime_seller_id": 14, "tenant_id": 2, "marketplace_code": "ozon_kz", "source_url": "https://ozon.kz/seller/second-14/"},
        ]

    def test_ru_kz_multi_tenant_runtimes_have_unique_profiles_and_ports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_runtimes_") as folder:
            runtimes = resolve_ozon_runtimes(Path(folder), self.sellers())
        self.assertEqual(4, len(runtimes))
        self.assertEqual(4, len({str(item.profile_dir) for item in runtimes.values()}))
        self.assertEqual(4, len({item.debug_port for item in runtimes.values()}))
        self.assertEqual({"ozon", "ozon_kz"}, {item.marketplace_code for item in runtimes.values()})

    def test_only_managed_session_zero_chrome_is_selected_for_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_managed_") as folder:
            root = Path(folder)
            configure_legacy_profiles(root)
            managed = root / ".runtime" / "browser_profiles" / "t1" / "ozon" / "s11"
            rows = [
                {"pid": 10, "session_id": 0, "profile_dir": str(managed)},
                {"pid": 11, "session_id": 0, "profile_dir": str(root / "collectors" / "ozon" / "chrome_vpn_profile")},
                {"pid": 12, "session_id": 0, "profile_dir": str(root / "ordinary-chrome")},
                {"pid": 13, "session_id": 2, "profile_dir": str(managed)},
            ]
            selected = managed_ozon_session_zero_processes(root, rows)
        self.assertEqual({10, 11}, {item["pid"] for item in selected})

    def test_launcher_continues_after_one_seller_failure(self) -> None:
        launcher = launcher_module()
        sellers = self.sellers()[:2]
        with patch.object(launcher, "parse_args", return_value=SimpleNamespace(seller_id=[], dry_run=True, bootstrap=False)), patch.object(launcher, "is_interactive_session", return_value=True), patch.object(launcher, "active_sellers", return_value=sellers), patch.object(launcher, "seller_plan", side_effect=[{"runtime": object()}, {"runtime": object()}]), patch.object(launcher, "start_browser", side_effect=[RuntimeError("first failed"), {"status": "planned"}] ) as start:
            self.assertEqual(2, launcher.main())
        self.assertEqual(2, start.call_count)

    def test_launcher_and_operation_use_same_legacy_seller_set(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_legacy_set_") as folder:
            db_path = Path(folder) / "app.db"
            ensure_database(db_path)
            auth = AuthService(db_path)
            admin, _ = auth.create_initial_admin("owner@example.test", "Owner", "StrongPassword123!")
            tenant_id = int(admin["tenant_id"])
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """UPDATE tenant_integrations SET seller_identifier=?,seller_name=?,seller_url=?,
                       status='active',approval_status='approved' WHERE tenant_id=? AND integration_code='ozon'""",
                    ("legacy-ozon", "Legacy Ozon", "https://www.ozon.ru/seller/legacy-ozon/", tenant_id),
                )
                conn.commit()
            finally:
                conn.close()
            service = SaaSService(db_path)
            seller = service.resolve_seller(tenant_id, "ozon")
            listed = service.ozon_runtime_sellers()
        self.assertTrue(seller["legacy"])
        self.assertEqual(
            {(item["tenant_id"], item["marketplace_code"], item["runtime_seller_id"]) for item in listed},
            {(seller["tenant_id"], seller["marketplace_code"], seller["runtime_seller_id"])},
        )


if __name__ == "__main__":
    unittest.main()
