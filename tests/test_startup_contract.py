from __future__ import annotations

import unittest
from pathlib import Path


class StartupContractTests(unittest.TestCase):
    def test_runtime_check_covers_cryptography_and_app_has_no_vault_import(self) -> None:
        root = Path(__file__).resolve().parents[1]
        check_env = (root / "CHECK_ENV.bat").read_text(encoding="utf-8")
        environment_check = (root / "environment_check.py").read_text(encoding="utf-8")
        app_source = (root / "app.py").read_text(encoding="utf-8")
        self.assertIn("cryptography", check_env)
        self.assertIn('"cryptography"', environment_check)
        self.assertNotIn("from credential_vault import", app_source)

    def test_startup_is_local_only_and_lan_helpers_are_removed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename in ("START.bat", "START_SERVER.bat"):
            source = (root / filename).read_text(encoding="utf-8")
            self.assertIn('ITP_HOST=127.0.0.1', source)
            self.assertNotIn("CHECK_LAN_ACCESS", source)
            self.assertNotIn("LAN:", source)
        self.assertFalse((root / "CHECK_LAN_ACCESS.bat").exists())
        self.assertFalse((root / "ALLOW_LAN_ACCESS.bat").exists())


if __name__ == "__main__":
    unittest.main()
