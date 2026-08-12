from __future__ import annotations

import unittest
from pathlib import Path

import app as webapp


ROOT = Path(__file__).resolve().parents[1]


class ProductionDeploymentTests(unittest.TestCase):
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
        self.assertNotIn("Start-Process", launcher)

        installer = (
            ROOT / "deploy" / "windows" / "install-production.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("requirements.txt", installer)
        self.assertIn("requirements-postgres.txt", installer)


if __name__ == "__main__":
    unittest.main()
