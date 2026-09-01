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
from saas_service import SaaSService
from schema import ensure_database


class PlatformCompanyAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="platform_company_admin_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.root, _ = self.auth.create_initial_admin(
            "root-platform@example.com", "Root Platform", "StrongPassword123!"
        )
        conn = sqlite3.connect(self.db_path)
        stamp = conn.execute("SELECT datetime('now')").fetchone()[0]
        self.tenant_b = int(conn.execute(
            """INSERT INTO tenants(
                   name,slug,registration_number,status,plan_code,contact_email,contact_phone,
                   created_at,updated_at,approved_at
               ) VALUES('Company B','company-b','BIN-B','approved','demo',
                        'company-b@example.com','+7 700 000 00 02',?,?,?)""",
            (stamp, stamp, stamp),
        ).lastrowid)
        conn.commit()
        conn.close()
        ensure_database(self.db_path)
        self.company_admin, _ = self.auth.create_user(
            "admin-b@example.com", "Admin B", "StrongPassword456!", "admin",
            int(self.root["id"]), tenant_id=self.tenant_b,
        )
        self.saas = SaaSService(self.db_path)
        self.patchers = [
            patch.object(webapp, "AUTH", self.auth),
            patch.object(webapp, "SAAS", self.saas),
            patch.object(webapp, "DB_PATH", self.db_path),
        ]
        for item in self.patchers:
            item.start()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()

    def tearDown(self) -> None:
        for item in reversed(self.patchers):
            item.stop()
        self.folder.cleanup()

    def login(self, user_id: int) -> None:
        with self.client.session_transaction() as session:
            session["user_id"] = int(user_id)
            session["csrf_token"] = "csrf-platform-test"

    @property
    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": "csrf-platform-test"}

    def test_platform_grants_company_connects_and_admin_edits_user(self) -> None:
        self.login(int(self.root["id"]))
        granted = self.client.put(
            f"/api/platform/tenants/{self.tenant_b}/marketplaces",
            json={"marketplaces": ["ozon_kz"]}, headers=self.headers,
        )
        self.assertEqual(200, granted.status_code)
        access = granted.get_json()["marketplace_access"]
        self.assertTrue(next(item for item in access if item["code"] == "ozon_kz")["is_allowed"])

        self.login(int(self.company_admin["id"]))
        checked = self.client.post(
            "/api/tenant/marketplaces/check",
            json={"marketplace_code": "ozon_kz", "source": "company-b-90210"},
            headers=self.headers,
        )
        self.assertEqual(200, checked.status_code)
        self.assertEqual("ozon_kz", checked.get_json()["result"]["marketplace_code"])
        self.assertEqual(
            "https://ozon.kz/продавец/company-b-90210/",
            checked.get_json()["result"]["seller_url"],
        )
        connected = self.client.post(
            "/api/tenant/marketplaces/connect",
            json={"marketplace_code": "ozon_kz", "source": "company-b-90210"},
            headers=self.headers,
        )
        self.assertEqual(200, connected.status_code)
        self.assertFalse(connected.get_json()["result"]["is_connected"])
        self.assertEqual("pending", connected.get_json()["result"]["approval_status"])

        self.login(int(self.root["id"]))
        with patch("saas_service.verify_ozon_storefront", return_value={
            "canonical_seller_id": "company-b-90210",
            "canonical_seller_url": "https://ozon.kz/seller/company-b-90210/",
            "seller_name": "Company B", "catalogue_empty": "false",
        }):
            reviewed = self.client.post(
                f"/api/platform/tenants/{self.tenant_b}/marketplaces/ozon_kz/approved",
                json={}, headers=self.headers,
            )
        self.assertEqual(200, reviewed.status_code)
        detail = self.client.get(f"/api/platform/tenants/{self.tenant_b}/detail")
        self.assertEqual(200, detail.status_code)
        detail_data = detail.get_json()
        self.assertEqual(6, len(detail_data["integration_catalog"]))
        self.assertIn("permissions", detail_data["users"][0])

        ozon_kz = next(item for item in detail_data["marketplace_access"] if item["code"] == "ozon_kz")
        self.assertTrue(ozon_kz["is_allowed"])
        self.assertTrue(ozon_kz["is_connected"])

        updated = self.client.put(
            f"/api/platform/tenants/{self.tenant_b}/users/{self.company_admin['id']}",
            json={
                "display_name": "Updated Admin B",
                "role": "operator",
                "is_active": True,
                "permissions": {"view_products": True, "run_operations": False},
            },
            headers=self.headers,
        )
        self.assertEqual(200, updated.status_code)
        user = updated.get_json()["user"]
        self.assertEqual("Updated Admin B", user["display_name"])
        self.assertEqual("operator", user["role"])
        self.assertTrue(user["marketplaces"]["ozon_kz"])
        self.assertFalse(user["permissions"]["run_operations"])

        wrong_tenant = self.client.put(
            f"/api/platform/tenants/{self.root['tenant_id']}/users/{self.company_admin['id']}",
            json={"display_name": "Must Not Change"}, headers=self.headers,
        )
        self.assertEqual(404, wrong_tenant.status_code)

    def test_rejected_marketplace_can_be_resubmitted_and_approved(self) -> None:
        self.login(int(self.root["id"]))
        self.client.put(
            f"/api/platform/tenants/{self.tenant_b}/marketplaces",
            json={"marketplaces": ["ozon_kz"]}, headers=self.headers,
        )
        self.login(int(self.company_admin["id"]))
        submitted = self.client.post(
            "/api/tenant/marketplaces/connect",
            json={"marketplace_code": "ozon_kz", "source": "store-first-101"},
            headers=self.headers,
        )
        self.assertEqual("pending", submitted.get_json()["result"]["approval_status"])

        self.login(int(self.root["id"]))
        rejected = self.client.post(
            f"/api/platform/tenants/{self.tenant_b}/marketplaces/ozon_kz/rejected",
            json={"review_note": "Укажите основной магазин"}, headers=self.headers,
        )
        self.assertEqual("rejected", rejected.get_json()["integration"]["approval_status"])

        self.login(int(self.company_admin["id"]))
        resubmitted = self.client.post(
            "/api/tenant/marketplaces/connect",
            json={"marketplace_code": "ozon_kz", "source": "store-correct-202"},
            headers=self.headers,
        )
        self.assertEqual("pending", resubmitted.get_json()["result"]["approval_status"])

    def test_logout_and_split_platform_pages(self) -> None:
        self.login(int(self.root["id"]))
        for page, heading in (
            ("/platform", "Компании"),
            ("/platform/packages", "Пакеты и лимиты"),
            ("/platform/link-rules", "Правила ссылок"),
            ("/platform/payments", "Оплаты и сроки"),
        ):
            response = self.client.get(page)
            self.assertEqual(200, response.status_code)
            self.assertIn(heading, response.get_data(as_text=True))
        response = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/"))
        self.assertEqual(302, self.client.get("/app").status_code)

    def test_platform_company_profile_requires_every_field(self) -> None:
        self.login(int(self.root["id"]))
        rejected = self.client.put(
            f"/api/platform/tenants/{self.tenant_b}",
            json={"name": "Incomplete Company"}, headers=self.headers,
        )
        self.assertEqual(400, rejected.status_code)
        accepted = self.client.put(
            f"/api/platform/tenants/{self.tenant_b}",
            json={
                "name": "Complete Company", "registration_number": "BIN-COMPLETE",
                "contact_email": "complete@example.com", "contact_phone": "+7 700 111 22 33",
                "status": "approved",
            },
            headers=self.headers,
        )
        self.assertEqual(200, accepted.status_code)

    def test_superadmin_can_update_company_addresses(self) -> None:
        self.login(int(self.root["id"]))

        response = self.client.put(
            f"/api/platform/tenants/{self.tenant_b}",
            json={
                "name": "Company B",
                "registration_number": "BIN-B",
                "contact_email":
                    "company-b@example.com",
                "contact_phone":
                    "+7 700 000 00 02",
                "legal_address":
                    "Astana, Legal Street 10",
                "actual_address":
                    "Astana, Office Street 20",
                "status": "approved",
            },
            headers=self.headers,
        )

        self.assertEqual(
            200,
            response.status_code,
        )

        detail = self.client.get(
            f"/api/platform/tenants/"
            f"{self.tenant_b}/detail"
        )

        self.assertEqual(
            200,
            detail.status_code,
        )

        tenant = detail.get_json()["tenant"]

        self.assertEqual(
            "Astana, Legal Street 10",
            tenant["legal_address"],
        )
        self.assertEqual(
            "Astana, Office Street 20",
            tenant["actual_address"],
        )

    def test_company_settings_always_list_the_marketplace_registry(self) -> None:
        self.login(int(self.root["id"]))
        response = self.client.put(
            f"/api/platform/tenants/{self.tenant_b}/marketplaces",
            json={"marketplaces": ["kaspi", "ozon"]},
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code)
        access = response.get_json()["marketplace_access"]
        self.assertEqual(
            {"kaspi", "ozon"},
            {item["code"] for item in access if item["is_allowed"]},
        )
        self.login(int(self.company_admin["id"]))
        tenant = self.client.get("/api/tenant")
        self.assertEqual(200, tenant.status_code)
        tenant_access = tenant.get_json()["marketplace_access"]
        self.assertEqual(6, len(tenant_access))
        self.assertEqual(
            {"kaspi", "ozon"},
            {item["code"] for item in tenant_access if item["is_allowed"]},
        )
        self.assertFalse(any(item["is_connected"] for item in tenant_access))
        page = self.client.get("/app")
        self.assertEqual(200, page.status_code)
        html = page.get_data(as_text=True)
        self.assertIn('<option value="kaspi">Kaspi</option>', html)
        self.assertIn('<option value="ozon">Ozon.ru</option>', html)
        self.assertNotIn('<option value="ozon_kz">', html)
        self.assertNotIn('<option value="halyk_market">', html)
        self.assertNotIn('<option value="forte_market">', html)
        session_user = self.client.get("/api/session").get_json()["user"]
        self.assertEqual({}, session_user["marketplaces"])
        self.assertEqual({"kaspi": True, "ozon": True}, session_user["available_marketplaces"])

    def test_company_can_request_ungranted_marketplace(self) -> None:
        self.login(int(self.root["id"]))

        response = self.client.put(
            f"/api/platform/tenants/{self.tenant_b}/marketplaces",
            json={
                "marketplaces": ["kaspi"],
            },
            headers=self.headers,
        )
        self.assertEqual(
            200,
            response.status_code,
        )

        self.login(
            int(self.company_admin["id"])
        )

        regular = self.client.get(
            "/api/tenant"
        )
        self.assertEqual(
            200,
            regular.status_code,
        )
        self.assertEqual(6, len(regular.get_json()["marketplace_access"]))

        expanded = self.client.get(
            "/api/tenant"
            "?include_unavailable=1"
        )
        self.assertEqual(
            200,
            expanded.status_code,
        )

        access = expanded.get_json()[
            "marketplace_access"
        ]

        self.assertEqual(
            6,
            len(access),
        )

        ozon_kz = next(
            item
            for item in access
            if item["code"] == "ozon_kz"
        )

        self.assertFalse(
            ozon_kz["is_allowed"]
        )

        checked = self.client.post(
            "/api/tenant/marketplaces/check",
            json={
                "marketplace_code":
                    "ozon_kz",
                "source":
                    "future-store-456",
            },
            headers=self.headers,
        )
        self.assertEqual(
            200,
            checked.status_code,
        )

        submitted = self.client.post(
            "/api/tenant/marketplaces/connect",
            json={
                "marketplace_code":
                    "ozon_kz",
                "source":
                    "future-store-456",
            },
            headers=self.headers,
        )
        self.assertEqual(
            200,
            submitted.status_code,
        )
        self.assertEqual(
            "pending",
            submitted.get_json()[
                "result"
            ]["approval_status"],
        )

        self.login(int(self.root["id"]))

        with patch("saas_service.verify_ozon_storefront", return_value={
            "canonical_seller_id": "future-store-456",
            "canonical_seller_url": "https://ozon.kz/seller/future-store-456/",
            "seller_name": "Future Store", "catalogue_empty": "false",
        }):
            approved = self.client.post(
                f"/api/platform/tenants/"
                f"{self.tenant_b}/marketplaces/"
                "ozon_kz/approved",
                json={},
                headers=self.headers,
            )
        self.assertEqual(
            200,
            approved.status_code,
        )

        self.login(
            int(self.company_admin["id"])
        )

        after = self.client.get(
            "/api/tenant"
        )
        self.assertEqual(
            200,
            after.status_code,
        )

        access = after.get_json()[
            "marketplace_access"
        ]

        ozon_kz = next(
            item
            for item in access
            if item["code"] == "ozon_kz"
        )

        self.assertTrue(
            ozon_kz["is_allowed"]
        )
        self.assertTrue(
            ozon_kz["is_connected"]
        )

    def test_operation_commands_use_the_requesting_company_connection(self) -> None:
        self.saas.set_marketplace_access(
            self.tenant_b,
            ["kaspi", "ozon", "ozon_kz", "halyk_market", "forte_market", "wildberries"],
            int(self.root["id"]),
        )
        conn = sqlite3.connect(self.db_path)
        try:
            values = {
                "kaspi": ("Kaspi Company B", "kaspi-company-b", "https://kaspi.kz/shop/m/kaspi-company-b/products"),
                "ozon": ("Ozon RU Company B", "company-b-ru", "https://www.ozon.ru/seller/company-b-ru/"),
                "ozon_kz": ("Ozon KZ Company B", "company-b-kz", "https://ozon.kz/seller/company-b-kz/"),
                "halyk_market": ("Halyk Company B", "halyk-company-b", "https://halykmarket.kz/merchant/halyk-company-b"),
                "forte_market": ("Forte Company B", "forte-company-b", "https://market.forte.kz/merchant/forte-company-b"),
                "wildberries": ("WB Company B", "250000260", "https://global.wildberries.ru/seller/250000260"),
            }
            for code, (seller_name, seller_identifier, seller_url) in values.items():
                conn.execute(
                    """UPDATE tenant_integrations
                       SET seller_name=?,seller_identifier=?,seller_url=?,approval_status='approved',status='active'
                       WHERE tenant_id=? AND integration_code=?""",
                    (seller_name, seller_identifier, seller_url, self.tenant_b, code),
                )
            conn.commit()
        finally:
            conn.close()

        user_id = int(self.company_admin["id"])
        kaspi = webapp.build_action_command("sync_catalog", [], user_id)
        halyk = webapp.build_action_command("halyk_sync_catalog", [], user_id)
        forte = webapp.build_action_command("forte_sync_catalog", [], user_id)

        self.assertEqual(str(self.tenant_b), kaspi[kaspi.index("--tenant-id") + 1])
        self.assertEqual("kaspi-company-b", kaspi[kaspi.index("--seller-id") + 1])
        self.assertEqual(str(self.tenant_b), halyk[halyk.index("--tenant-id") + 1])
        self.assertEqual("Halyk Company B", halyk[halyk.index("--seller-name") + 1])
        self.assertEqual(str(self.tenant_b), forte[forte.index("--tenant-id") + 1])
        self.assertEqual("forte-company-b", forte[forte.index("--merchant-id") + 1])
        ozon = webapp.build_action_command("ozon_catalog_collect", [], user_id)
        self.assertTrue(any("ozon_collector.py" in part for part in ozon))
        self.assertIn("sync-catalog", ozon)
        self.assertFalse(any("seller_api.py" in part for part in ozon))
        self.assertEqual("https://www.ozon.ru/seller/company-b-ru/", ozon[ozon.index("--source-url") + 1])
        self.assertTrue(ozon[ozon.index("--profile-path") + 1].endswith("collectors\\ozon\\chrome_vpn_profile"))
        self.assertEqual("9222", ozon[ozon.index("--debug-port") + 1])
        ozon_full = webapp.build_action_command("ozon_full_sync", [], user_id)
        self.assertTrue(any("ozon_collector.py" in part for part in ozon_full))
        self.assertIn("full-sync", ozon_full)
        self.assertNotIn("--manifest", ozon_full)
        ozon_kz = webapp.build_action_command("ozon_kz_full_sync", [], user_id)
        self.assertTrue(any("ozon_kz_collector.py" in part for part in ozon_kz))
        self.assertFalse(any("collectors\\ozon\\ozon_collector.py" in part for part in ozon_kz))
        ozon_kz_catalog = webapp.build_action_command("ozon_kz_catalog_collect", [], user_id)
        self.assertIn("sync-catalog", ozon_kz_catalog)
        self.assertNotIn("full-sync", ozon_kz_catalog)
        self.assertEqual("https://ozon.kz/seller/company-b-kz/", ozon_kz[ozon_kz.index("--source-url") + 1])
        self.assertTrue(ozon_kz[ozon_kz.index("--profile-path") + 1].endswith("collectors\\ozon\\chrome_kz_profile"))
        self.assertEqual("9333", ozon_kz[ozon_kz.index("--debug-port") + 1])
        wildberries = webapp.build_action_command("wb_catalog_collect", [], user_id)
        self.assertTrue(any("wildberries_collector.py" in part for part in wildberries))
        self.assertEqual("250000260", wildberries[wildberries.index("--seller-id") + 1])
        self.assertEqual(str(self.tenant_b), wildberries[wildberries.index("--tenant-id") + 1])
        complete = webapp.build_action_command("full_sync_all", [], user_id)
        self.assertIn("--manifest", complete)
        manifest_path = Path(complete[complete.index("--manifest") + 1])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        try:
            self.assertEqual(6, len(manifest["steps"]))
            self.assertTrue(all(item["command"] for item in manifest["steps"]))
        finally:
            webapp.cleanup_pending_command(complete)

    def test_revoking_company_grant_disables_connection_without_deleting_seller(self) -> None:
        self.saas.set_marketplace_access(self.tenant_b, ["ozon"], int(self.root["id"]))
        self.saas.connect_marketplace(
            self.tenant_b, "https://www.ozon.ru/seller/company-b-90210/",
            int(self.company_admin["id"]), "ozon",
        )
        self.saas.set_marketplace_access(self.tenant_b, [], int(self.root["id"]))
        access = next(item for item in self.saas.marketplace_access(self.tenant_b) if item["code"] == "ozon")
        self.assertFalse(access["is_allowed"])
        self.assertFalse(access["is_connected"])
        conn = sqlite3.connect(self.db_path)
        seller_count = conn.execute(
            "SELECT COUNT(*) FROM tenant_marketplace_sellers WHERE tenant_id=? AND marketplace_code='ozon'",
            (self.tenant_b,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(1, seller_count)

    def test_superadmin_can_preview_update_and_persist_source_rules(self) -> None:
        self.login(int(self.root["id"]))
        response = self.client.get("/api/platform/marketplace-source-rules")
        self.assertEqual(200, response.status_code)
        rules = response.get_json()["marketplace_source_rules"]
        self.assertEqual(
            {"kaspi", "ozon", "ozon_kz", "halyk_market", "forte_market", "wildberries"},
            set(rules),
        )

        rules["forte_market"]["seller_path_patterns"] = [
            r"/vendor/(?P<seller_id>[^/?#]+)"
        ]
        rules["forte_market"]["seller_url_template"] = (
            "https://market.forte.kz/merchant/{seller_id}?type=all&view=grid"
        )
        preview = self.client.post(
            "/api/platform/marketplace-source-rules/preview",
            json={
                "marketplace_code": "forte_market",
                "source": "https://market.forte.kz/vendor/B8pXMdkk110XZRswXw",
                "marketplace_source_rules": {"marketplaces": rules},
            },
            headers=self.headers,
        )
        self.assertEqual(200, preview.status_code)
        preview_result = preview.get_json()["result"]
        self.assertEqual("B8pXMdkk110XZRswXw", preview_result["seller_identifier"])
        self.assertEqual(
            "https://market.forte.kz/merchant/B8pXMdkk110XZRswXw?type=all&view=grid",
            preview_result["seller_url"],
        )

        saved = self.client.put(
            "/api/platform/marketplace-source-rules",
            json={"marketplaces": rules},
            headers=self.headers,
        )
        self.assertEqual(200, saved.status_code)
        persisted = self.client.get(
            "/api/platform/marketplace-source-rules"
        ).get_json()["marketplace_source_rules"]
        self.assertEqual(
            rules["forte_market"]["seller_url_template"],
            persisted["forte_market"]["seller_url_template"],
        )

        invalid = self.client.put(
            "/api/platform/marketplace-source-rules",
            json={"marketplaces": {"kaspi": {"seller_path_patterns": ["("]}}},
            headers=self.headers,
        )
        self.assertEqual(400, invalid.status_code)

        self.login(int(self.company_admin["id"]))
        self.assertEqual(
            403,
            self.client.get("/api/platform/marketplace-source-rules").status_code,
        )
        self.assertEqual(
            403,
            self.client.put(
                "/api/platform/marketplace-source-rules",
                json={"marketplaces": rules},
                headers=self.headers,
            ).status_code,
        )


if __name__ == "__main__":
    unittest.main()
