from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ozon_browser_runtime import (
    browser_is_eligible,
    configure_marketplace_profiles,
    managed_ozon_session_zero_processes,
    resolve_ozon_runtime,
)


ROOT = Path(__file__).resolve().parents[1]


def launcher_module():
    path = ROOT / "scripts" / "open_ozon_browsers.py"
    spec = importlib.util.spec_from_file_location("ozon_marketplace_launcher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OzonMarketplaceBrowserTests(unittest.TestCase):
    def test_marketplace_profiles_are_constant_across_sellers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_marketplace_runtime_") as folder:
            root = Path(folder)
            configure_marketplace_profiles(root)
            ru_first = resolve_ozon_runtime(root, "ozon", "https://www.ozon.ru/seller/alfa-tires-3381444/")
            ru_second = resolve_ozon_runtime(root, "ozon", "https://www.ozon.ru/seller/another-store-1/")
            kz_first = resolve_ozon_runtime(root, "ozon_kz", "https://ozon.kz/seller/alfa-tires-3381444/")
            kz_second = resolve_ozon_runtime(root, "ozon_kz", "https://ozon.kz/seller/another-store-2/")
        self.assertEqual(ru_first.profile_dir, ru_second.profile_dir)
        self.assertEqual(kz_first.profile_dir, kz_second.profile_dir)
        self.assertNotEqual(ru_first.profile_dir, kz_first.profile_dir)
        self.assertEqual(9222, ru_first.debug_port)
        self.assertEqual(9333, kz_first.debug_port)

    def test_session_zero_is_rejected_and_interactive_session_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_session_") as folder:
            runtime = resolve_ozon_runtime(Path(folder), "ozon")
            session0 = {"profile_dir": str(runtime.profile_dir), "debug_port": runtime.debug_port, "session_id": 0}
            session2 = {"profile_dir": str(runtime.profile_dir), "debug_port": runtime.debug_port, "session_id": 2}
        self.assertFalse(browser_is_eligible(runtime, session0, production=True))
        self.assertTrue(browser_is_eligible(runtime, session2, production=True))

    def test_bootstrap_selects_only_top_level_managed_marketplace_chrome(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_managed_") as folder:
            root = Path(folder)
            ru = resolve_ozon_runtime(root, "ozon")
            kz = resolve_ozon_runtime(root, "ozon_kz")
            rows = [
                {"pid": 10, "session_id": 0, "profile_dir": str(ru.profile_dir), "debug_port": ru.debug_port},
                {"pid": 11, "session_id": 0, "profile_dir": str(kz.profile_dir), "debug_port": kz.debug_port},
                {"pid": 12, "session_id": 0, "profile_dir": str(ru.profile_dir), "debug_port": 45555},
                {"pid": 13, "session_id": 0, "profile_dir": str(root / "ordinary-chrome"), "debug_port": 9222},
                {"pid": 14, "session_id": 2, "profile_dir": str(ru.profile_dir), "debug_port": ru.debug_port},
            ]
            selected = managed_ozon_session_zero_processes(root, rows)
        self.assertEqual({10, 11}, {item["pid"] for item in selected})

    def test_launcher_uses_marketplaces_not_seller_list(self) -> None:
        launcher = launcher_module()
        runtimes = {
            "ozon": SimpleNamespace(marketplace_code="ozon"),
            "ozon_kz": SimpleNamespace(marketplace_code="ozon_kz"),
        }
        with patch.object(launcher, "parse_args", return_value=SimpleNamespace(dry_run=True, bootstrap=False)), patch.object(launcher, "is_interactive_session", return_value=True), patch.object(launcher, "active_marketplaces", return_value=["ozon", "ozon_kz"]), patch.object(launcher, "resolve_ozon_runtime", side_effect=lambda _root, code: runtimes[code]), patch.object(launcher, "start_browser", return_value={"status": "PLANNED"}) as start:
            self.assertEqual(0, launcher.main())
        self.assertEqual(2, start.call_count)
        self.assertFalse(hasattr(launcher, "active_sellers"))

    def test_ru_failure_does_not_block_kz_launcher(self) -> None:
        launcher = launcher_module()
        runtimes = {
            "ozon": SimpleNamespace(marketplace_code="ozon"),
            "ozon_kz": SimpleNamespace(marketplace_code="ozon_kz"),
        }
        with patch.object(launcher, "parse_args", return_value=SimpleNamespace(dry_run=True, bootstrap=False)), patch.object(launcher, "is_interactive_session", return_value=True), patch.object(launcher, "active_marketplaces", return_value=["ozon", "ozon_kz"]), patch.object(launcher, "resolve_ozon_runtime", side_effect=lambda _root, code: runtimes[code]), patch.object(launcher, "start_browser", side_effect=[RuntimeError("RU failed"), {"status": "PLANNED"}]) as start:
            self.assertEqual(2, launcher.main())
        self.assertEqual(2, start.call_count)


if __name__ == "__main__":
    unittest.main()
