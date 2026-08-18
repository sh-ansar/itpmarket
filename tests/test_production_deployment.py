from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as webapp
from auth_service import AuthService
from schema import ensure_database


ROOT = Path(__file__).resolve().parents[1]


class ProductionDeploymentTests(unittest.TestCase):
    def test_public_plans_are_available_before_initial_setup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="public_plans_") as folder:
            db_path = Path(folder) / "app.db"
            ensure_database(db_path)
            auth = AuthService(db_path)
            with patch.object(webapp, "AUTH", auth), patch.object(
                webapp, "DB_PATH", db_path
            ):
                response = webapp.app.test_client().get("/api/public/plans")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ok"])

    def test_health_and_database_readiness_are_separate(self) -> None:
        client = webapp.app.test_client()
        live = client.get("/health")
        ready = client.get("/ready")
        self.assertEqual(200, live.status_code)
        self.assertEqual(200, ready.status_code)
        self.assertTrue(live.get_json()["ok"])
        self.assertTrue(ready.get_json()["ok"])

    def test_caddy_proxies_only_to_loopback_and_checks_database(self) -> None:
        caddyfile = (ROOT / "deploy" / "windows" / "Caddyfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("reverse_proxy 127.0.0.1:", caddyfile)
        self.assertIn("health_uri /ready", caddyfile)
        self.assertIn("{$SPYON_DOMAIN:spyon.kz}", caddyfile)
        self.assertNotIn("tls_insecure_skip_verify", caddyfile)

    def test_environment_template_contains_no_real_secret(self) -> None:
        example = (
            ROOT / "deploy" / "windows" / "production.env.example"
        ).read_text(encoding="utf-8")
        self.assertIn("ITP_HOST=127.0.0.1", example)
        self.assertIn("ITP_STORAGE_BACKEND=postgresql", example)
        self.assertIn("ITP_COOKIE_SECURE=1", example)
        self.assertIn("ITP_TRUST_PROXY=1", example)
        self.assertIn("CHANGE_ME", example)
        self.assertNotIn("85.159.27.24", example)

    def test_windows_launcher_enforces_loopback_and_postgresql(self) -> None:
        launcher = (
            ROOT / "deploy" / "windows" / "start-production.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("$env:ITP_HOST = '127.0.0.1'", launcher)
        self.assertIn("$env:ITP_STORAGE_BACKEND -ne 'postgresql'", launcher)
        self.assertIn("postgres_initialize.py", launcher)
        self.assertIn("postgres_initialize.py' --check", launcher)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", launcher)
        self.assertIn("environment_check.py' --check-only", launcher)
        self.assertNotIn("Start-Process", launcher)

        installer = (
            ROOT / "deploy" / "windows" / "install-production.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("requirements.txt", installer)
        self.assertIn("requirements-postgres.txt", installer)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", installer)
        self.assertIn("@('py', '-3.11')", installer)
        self.assertIn("@('py', '-3.10')", installer)

        stopper = (
            ROOT / "deploy" / "windows" / "stop-production.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("ParentProcessId", stopper)
        self.assertIn("verifiedVenvParent", stopper)
        self.assertIn("start-production.ps1", stopper)
        self.assertIn("Refusing to stop", stopper)

    def test_runtime_diagnostic_is_read_only_and_checks_production_contract(self) -> None:
        diagnostic = (ROOT / "scripts" / "diagnose_runtime.ps1").read_text(
            encoding="utf-8-sig"
        )
        for mutation in (
            "New-Item", "Set-Content", "Add-Content", "Out-File",
            "Remove-Item", "Start-Process", "Stop-Process",
        ):
            self.assertNotIn(mutation, diagnostic)
        self.assertIn("postgresql://[REDACTED]", diagnostic)
        self.assertIn("ITP_SESSION_SECRET", diagnostic)
        self.assertIn("Get-ScheduledTask", diagnostic)
        self.assertIn("Spyon Auto Deploy", diagnostic)
        self.assertIn("LocalAddress", diagnostic)
        self.assertIn("@('/health', '/ready', '/')", diagnostic)
        self.assertIn("environment_check.py') --check-only", diagnostic)

    def test_postgres_manifest_covers_every_runtime_schema(self) -> None:
        manifest = json.loads(
            (ROOT / "engine" / "postgres_schema_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        schemas = manifest["schemas"]
        self.assertEqual({"app", "ozon_ru", "ozon_kz"}, set(schemas))
        self.assertEqual(98, sum(len(tables) for tables in schemas.values()))
        self.assertIn("tenants", schemas["app"])
        self.assertIn("products", schemas["ozon_ru"])
        self.assertIn("ozon_kz_products", schemas["ozon_kz"])


if __name__ == "__main__":
    unittest.main()
