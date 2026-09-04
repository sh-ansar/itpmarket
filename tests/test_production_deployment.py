from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as webapp
from auth_service import AuthService
from engine.postgres_migrations import validate_pending_migration
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
        self.assertIn("ITP_TELEGRAM_BOT_ENABLED=0", example)
        self.assertIn("ITP_TELEGRAM_BOT_TOKEN=CHANGE_ME", example)
        self.assertIn("SPYON_OZON_BROWSER_USER=DOMAIN\\username", example)
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
        self.assertIn("ITP_TELEGRAM_BOT_TOKEN", launcher)
        self.assertNotIn("Start-Process", launcher)

        installer = (
            ROOT / "deploy" / "windows" / "install-production.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("requirements.txt", installer)
        self.assertIn("requirements-postgres.txt", installer)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", installer)
        self.assertIn("@('py', '-3.11')", installer)
        self.assertIn("@('py', '-3.10')", installer)
        self.assertIn("TelegramBotToken", installer)

        stopper = (
            ROOT / "deploy" / "windows" / "stop-production.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("ParentProcessId", stopper)
        self.assertIn("verifiedVenvParent", stopper)
        self.assertIn("start-production.ps1", stopper)
        self.assertIn("Refusing to stop", stopper)

    def test_windows_stopper_is_safe_for_noninteractive_deploys(self) -> None:
        stopper = (
            ROOT / "deploy" / "windows" / "stop-production.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Refusing to stop", stopper)
        self.assertIn("verifiedPython", stopper)
        self.assertIn("verifiedVenvParent", stopper)
        self.assertIn("Stop-Process -Id $serverPid -Force -Confirm:$false", stopper)
        self.assertIn("Wait-Process -Id $serverPid", stopper)

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
        self.assertIn("scripts\\check_postgres.py", diagnostic)
        self.assertNotIn("psycopg.connect(os.environ", diagnostic)

    def test_post_update_reconciles_ozon_task_without_launching_a_gui_browser(self) -> None:
        post_update = (
            ROOT / "deploy" / "windows" / "post-update-production.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("ensure_ozon_browser_task.ps1", post_update)
        self.assertIn("OZON: ", post_update)
        self.assertIn("Ozon browser task registration failed.", post_update)
        self.assertNotIn("open_ozon_browsers.py", post_update)
        self.assertNotIn("Start-Process", post_update)
        self.assertIn("stop-production.ps1", post_update)
        self.assertIn("previousServerPid", post_update)
        self.assertIn("newServerPid", post_update)
        self.assertIn("Previous Spyon PID", post_update)
        self.assertLess(
            post_update.index("-File $stopScript"),
            post_update.index('/End `'),
        )

    def test_postgres_manifest_covers_every_runtime_schema(self) -> None:
        manifest = json.loads(
            (ROOT / "engine" / "postgres_schema_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        schemas = manifest["schemas"]
        self.assertEqual({"app", "ozon_ru", "ozon_kz"}, set(schemas))
        self.assertEqual(111, sum(len(tables) for tables in schemas.values()))
        self.assertIn("auth_tokens", schemas["app"])
        self.assertIn("email_outbox", schemas["app"])
        self.assertIn("notification_preferences", schemas["app"])
        self.assertIn("billing_sequences", schemas["app"])
        self.assertIn("subscription_invoices", schemas["app"])
        self.assertIn("subscription_payment_proofs", schemas["app"])
        self.assertIn("tenants", schemas["app"])
        self.assertIn("tenant_inventory_products", schemas["app"])
        self.assertIn("tenant_product_listings", schemas["app"])
        self.assertIn("tenant_product_match_decisions", schemas["app"])
        self.assertIn("tenant_inventory_events", schemas["app"])
        self.assertIn("telegram_user_links", schemas["app"])
        self.assertIn("telegram_notification_deliveries", schemas["app"])
        self.assertIn("products", schemas["ozon_ru"])
        self.assertIn("ozon_kz_products", schemas["ozon_kz"])

    def test_self_service_billing_migration_is_safe_for_auto_deploy(self) -> None:
        migration = (
            ROOT / "migrations" / "20260827_self_service_billing_v1.sql"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("SPYON-AUTO-MIGRATION", migration)
        self.assertTrue(migration.lstrip().startswith("-- SPYON-AUTO-MIGRATION"))
        self.assertIn("BEGIN;", migration)
        self.assertTrue(migration.rstrip().endswith("COMMIT;"))
        for table in (
            "subscription_invoices",
            "subscription_payment_proofs",
            "billing_sequences",
        ):
            self.assertIn(f"app.{table}", migration)
        validate_pending_migration(
            ROOT / "migrations" / "20260827_self_service_billing_v1.sql",
            migration,
        )


if __name__ == "__main__":
    unittest.main()
