from __future__ import annotations

import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["ITP_DISABLE_SCHEDULER"] = "1"

import app as webapp
from auth_service import AuthService
from catalog_configuration_service import CatalogConfigurationService
from data_service import DataService
from saas_service import SaaSService
from schema import ensure_database


class FakeTasks:
    def states(self):
        return [
            {
                "id": "halyk-task", "name": "halyk_full_sync", "status": "running",
                "command": ["python", "collector.py", "--api-key", "secret"],
                "log_file": "C:/private/task.log",
                "metadata": {"tenant_id": 1, "platform": "halyk_market", "requested_by_id": 2},
            },
            {
                "id": "ozon-task", "name": "ozon_full_sync", "status": "running",
                "command": ["python", "ozon.py"],
                "log_file": "C:/private/ozon.log",
                "metadata": {"tenant_id": 1, "platform": "ozon", "requested_by_id": 2},
            },
        ]


class SecurityMarketplaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="security_marketplaces_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        admin, _ = self.auth.create_initial_admin(
            "root@example.com", "Root Admin", "StrongPassword123!"
        )
        self.admin = admin
        self.tenant_id = int(admin["tenant_id"])
        SaaSService(self.db_path).update_tenant_profile(
            self.tenant_id,
            {
                "name": "Security Tenant",
                "registration_number": "BIN-SECURITY-1",
                "contact_email": "company@example.com",
                "contact_phone": "+7 700 000 00 01",
            },
            int(admin["id"]),
        )
        operator, _ = self.auth.create_user(
            "halyk@example.com", "Halyk Operator", "StrongPassword456!",
            "operator", int(admin["id"]), tenant_id=self.tenant_id,
        )
        SaaSService(self.db_path).set_marketplace_access(
            self.tenant_id, ["halyk_market"], int(admin["id"])
        )
        self.operator = self.auth.get_user(int(operator["id"])) or operator
        self.data = DataService(self.db_path, "Unityre")
        self.catalog = CatalogConfigurationService(self.db_path)
        self.catalog.upsert_catalog_product(
            self.tenant_id, "halyk_market",
            {"product_id": "123", "title": "Tenant Halyk product"},
        )
        self.saas = SaaSService(self.db_path)
        self.patchers = [
            patch.object(webapp, "AUTH", self.auth),
            patch.object(webapp, "DB_PATH", self.db_path),
            patch.object(webapp, "DATA", self.data),
            patch.object(webapp, "CATALOG", self.catalog),
            patch.object(webapp, "SAAS", self.saas),
            patch.object(webapp, "TASKS", FakeTasks()),
        ]
        for item in self.patchers:
            item.start()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = int(self.operator["id"])
            session["csrf_token"] = "csrf-test"

    def tearDown(self) -> None:
        for item in reversed(self.patchers):
            item.stop()
        self.folder.cleanup()

    def test_direct_product_and_state_api_deny_unassigned_marketplace(self) -> None:
        response = self.client.get("/api/products/ozon:123")
        self.assertEqual(403, response.status_code)

        response = self.client.put(
            "/api/products/state",
            json={"codes": ["ozon:123"], "watched": True},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(403, response.status_code)

        response = self.client.put(
            "/api/products/state",
            json={"codes": ["halyk:123"], "watched": True, "note": "tenant note"},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.get_json()["updated"])

    def test_backend_denies_check_and_connection_for_ungranted_marketplace(self) -> None:
        payload = {
            "marketplace_code": "ozon",
            "seller_url": "https://www.ozon.ru/seller/forbidden-store-101/",
        }
        checked = self.client.post(
            "/api/tenant/marketplaces/check",
            json=payload,
            headers={"X-CSRF-Token": "csrf-test"},
        )
        connected = self.client.post(
            "/api/tenant/marketplaces/connect",
            json=payload,
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(403, checked.status_code)
        self.assertEqual(403, connected.status_code)
        self.assertFalse(
            next(
                item for item in self.saas.marketplace_access(self.tenant_id)
                if item["code"] == "ozon"
            )["is_connected"]
        )

    def test_direct_task_api_filters_marketplace_and_redacts_server_state(self) -> None:
        response = self.client.get("/api/tasks")
        self.assertEqual(200, response.status_code)
        tasks = response.get_json()["tasks"]
        self.assertEqual(["halyk-task"], [item["id"] for item in tasks])
        self.assertNotIn("command", tasks[0])
        self.assertNotIn("log_file", tasks[0])

        denied = self.client.post(
            "/api/tasks/start",
            json={"action": "ozon_full_sync", "scope": "all"},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(403, denied.status_code)
        self.assertNotIn(
            "actual-secret",
            webapp.redact_log_text("client_secret=actual-secret token: second-secret"),
        )

    def test_permission_override_blocks_direct_operation_api(self) -> None:
        self.auth.update_user(
            int(self.operator["id"]),
            {"permissions": {"view_products": True, "run_operations": False}},
            int(self.operator["id"]),
        )
        response = self.client.post(
            "/api/tasks/start",
            json={"action": "halyk_full_sync", "scope": "all"},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(403, response.status_code)

    def test_employee_marketplace_override_blocks_ui_and_direct_api(self) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = int(self.admin["id"])
        response = self.client.put(
            f"/api/users/{self.operator['id']}",
            json={"marketplaces": {"halyk_market": False}},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(200, response.status_code)
        saved = response.get_json()["user"]
        self.assertTrue(saved["available_marketplaces"]["halyk_market"])
        self.assertFalse(saved["marketplace_permissions"]["halyk_market"])
        self.assertNotIn("halyk_market", saved["marketplaces"])

        with self.client.session_transaction() as session:
            session["user_id"] = int(self.operator["id"])
        self.assertEqual(403, self.client.get("/api/products/halyk:123").status_code)
        denied = self.client.post(
            "/api/tasks/start",
            json={"action": "halyk_full_sync", "scope": "all"},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(403, denied.status_code)
        html = self.client.get("/app").get_data(as_text=True)
        self.assertNotIn('<option value="halyk_market">', html)

    def test_pending_company_blocks_direct_operation_api(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE tenants SET status='pending' WHERE id=?", (self.tenant_id,))
        conn.commit()
        conn.close()
        response = self.client.post(
            "/api/tasks/start",
            json={"action": "halyk_full_sync", "scope": "all"},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(403, response.status_code)
        self.assertIn("не подтверждена", response.get_json()["error"].casefold())

    def test_global_collector_config_is_hidden_from_tenant_admins(self) -> None:
        admin_user = self.auth.get_user_by_email("root@example.com")
        assert admin_user
        # Remove platform privilege while preserving the tenant admin role.
        self.auth.set_platform_role(int(admin_user["id"]), "", int(admin_user["id"]))
        with self.client.session_transaction() as session:
            session["user_id"] = int(admin_user["id"])
        response = self.client.get("/api/settings")
        self.assertEqual(200, response.status_code)
        self.assertIsNone(response.get_json()["config"])

    def test_frontend_bootstrap_exposes_only_effective_marketplace_access(self) -> None:
        response = self.client.get("/app")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        encoded_user = html.split("window.ITP_USER=", 1)[1].split(
            ";window.ITP_VERSION", 1
        )[0]
        bootstrap_user = json.loads(encoded_user)
        self.assertEqual({"halyk_market": True}, bootstrap_user["marketplaces"])
        self.assertNotIn('data-quick-action="kaspi_price_actualize"', html)
        self.assertNotIn('data-quick-action="ozon_price_actualize"', html)
        self.assertNotIn('<option value="kaspi">', html)
        self.assertNotIn('<option value="ozon">', html)
        self.assertIn('<option value="halyk_market">Halyk Market</option>', html)
        self.assertNotIn("password_hash", bootstrap_user)
        self.assertNotIn("recovery_hash", bootstrap_user)

    def test_reports_and_schedules_are_filtered_by_marketplace(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            for index, platforms in enumerate((["halyk_market"], ["ozon"]), 1):
                conn.execute(
                    """INSERT INTO app_reports(
                           report_type,scope,file_name,file_path,rows_count,created_by,
                           tenant_id,platforms_json,created_at
                       ) VALUES('test','all',?,?,1,?,?,?,datetime('now'))""",
                    (
                        f"report-{index}.csv", str(Path(self.folder.name) / f"report-{index}.csv"),
                        int(self.operator["id"]), self.tenant_id,
                        json.dumps(platforms),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        reports = self.client.get("/api/reports").get_json()["reports"]
        self.assertEqual(["report-1.csv"], [item["file_name"] for item in reports])

        denied = self.client.post(
            "/api/schedules",
            json={"name": "Ozon.ru denied", "action": "ozon_full_sync", "recurrence_type": "daily"},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(403, denied.status_code)
        allowed = self.client.post(
            "/api/schedules",
            json={"name": "Halyk allowed", "action": "halyk_full_sync", "recurrence_type": "daily"},
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(200, allowed.status_code)
        actions = self.client.get("/api/schedules").get_json()["actions"]
        action_codes = {item["code"] for item in actions}
        self.assertIn("halyk_full_sync", action_codes)
        self.assertNotIn("ozon_full_sync", action_codes)

    def test_product_state_is_isolated_by_tenant(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            stamp = conn.execute("SELECT datetime('now')").fetchone()[0]
            tenant_two = int(conn.execute(
                """INSERT INTO tenants(name,slug,status,plan_code,contact_email,created_at,updated_at)
                   VALUES('Tenant Two','tenant-two','active','demo','',?,?)""",
                (stamp, stamp),
            ).lastrowid)
            conn.commit()
        finally:
            conn.close()
        admin = self.auth.get_user_by_email("root@example.com")
        assert admin
        second, _ = self.auth.create_user(
            "second@example.com", "Second User", "StrongPassword789!",
            "operator", int(admin["id"]), tenant_id=tenant_two,
        )
        ensure_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            active_tenants = {
                int(row[0]) for row in conn.execute(
                    "SELECT tenant_id FROM tenant_users WHERE user_id=? AND is_active=1",
                    (int(second["id"]),),
                ).fetchall()
            }
        finally:
            conn.close()
        self.assertEqual({tenant_two}, active_tenants)
        refreshed_second = self.auth.get_user(int(second["id"]))
        assert refreshed_second
        self.assertFalse(any(refreshed_second["marketplaces"].values()))
        self.assertFalse(self.data.user_owns_shared_catalog(int(second["id"])))
        self.assertEqual([], self.data.rows_for_user(int(second["id"])))
        self.catalog.upsert_catalog_product(
            self.tenant_id, "halyk_market",
            {"product_id": "shared", "title": "Shared external ID / first tenant"},
        )
        self.catalog.upsert_catalog_product(
            tenant_two, "halyk_market",
            {"product_id": "shared", "title": "Shared external ID / second tenant"},
        )
        self.data.set_product_state(
            ["halyk:shared"], True, "high", "first tenant", int(self.operator["id"])
        )
        self.data.set_product_state(
            ["halyk:shared"], False, "low", "second tenant", int(second["id"])
        )
        first_row = self.data._with_tenant_state(
            [{"product_code": "halyk:shared"}], int(self.operator["id"])
        )[0]
        second_row = self.data._with_tenant_state(
            [{"product_code": "halyk:shared"}], int(second["id"])
        )[0]
        self.assertEqual("first tenant", first_row["note"])
        self.assertEqual("second tenant", second_row["note"])
        self.assertTrue(first_row["watched"])
        self.assertFalse(second_row["watched"])


if __name__ == "__main__":
    unittest.main()
