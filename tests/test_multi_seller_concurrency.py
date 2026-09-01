from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

os.environ["ITP_DISABLE_SCHEDULER"] = "1"

import app as webapp
from auth_service import AuthService
from catalog_configuration_service import CatalogConfigurationService
from credential_vault import CredentialVault, CredentialVaultError, generate_master_key
from data_service import DataService
from runtime_scope import SellerRuntimeScope
from saas_service import SaaSService
from scheduler_service import SchedulerService
from schema import ensure_database
from subscription_service import SubscriptionLimitError, SubscriptionService
from task_manager import TaskManager
import storage.postgres_compat as postgres_compat


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OZON_ROOT = PROJECT_ROOT / "collectors" / "ozon"
if str(OZON_ROOT) not in sys.path:
    sys.path.insert(0, str(OZON_ROOT))
from ozon_collector import run_id_for
from engine.exact_offer_refresh import Database as KaspiDatabase, save_success


def add_seller(
    db_path: Path,
    tenant_id: int,
    marketplace: str,
    external_id: str,
    name: str,
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO tenant_marketplace_sellers(
                   tenant_id,marketplace_code,external_seller_id,display_name,
                   source_url,status,approval_status,created_at,updated_at
               ) VALUES(?,?,?,?,?,'active','approved',datetime('now'),datetime('now'))""",
            (
                int(tenant_id), marketplace, external_id, name,
                f"https://example.test/{marketplace}/{external_id}",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def add_approved_tenant(db_path: Path, slug: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO tenants(
                   name,slug,registration_number,status,contact_email,
                   contact_phone,created_at,updated_at
               ) VALUES(?,?,?,'approved',?,?,datetime('now'),datetime('now'))""",
            (slug, slug, f"BIN-{slug}", f"{slug}@example.test", "+7 700 000 00 00"),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


class MultiSellerDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="multi_seller_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.admin, _ = self.auth.create_initial_admin(
            "owner@example.test", "Owner", "StrongPassword123!"
        )
        self.tenant_id = int(self.admin["tenant_id"])
        self.saas = SaaSService(self.db_path)
        self.saas.update_tenant_profile(
            self.tenant_id,
            {
                "name": "Tenant A",
                "registration_number": "BIN-A",
                "contact_email": "a@example.test",
                "contact_phone": "+7 700 000 00 01",
            },
            int(self.admin["id"]),
        )
        self.saas.set_marketplace_access(
            self.tenant_id, ["kaspi", "ozon", "halyk_market"], int(self.admin["id"])
        )
        self.seller_a = add_seller(
            self.db_path, self.tenant_id, "kaspi", "shared-external", "Seller A"
        )
        self.seller_b = add_seller(
            self.db_path, self.tenant_id, "kaspi", "seller-b", "Seller B"
        )
        self.catalog = CatalogConfigurationService(self.db_path)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_same_marketplace_same_sku_isolated_and_replace_is_seller_scoped(self) -> None:
        self.catalog.replace_catalog_products(
            self.tenant_id,
            "kaspi",
            [{"product_id": "SKU-1", "title": "A title", "price": 100}],
            tenant_seller_id=self.seller_a,
        )
        initial_visible = DataService(self.db_path, "unused").rows_for_user(
            int(self.admin["id"])
        )
        self.assertIn(
            f"kaspi:s{self.seller_a}:SKU-1",
            {row["product_code"] for row in initial_visible},
        )
        self.catalog.replace_catalog_products(
            self.tenant_id,
            "kaspi",
            [{"product_id": "SKU-1", "title": "B title", "price": 200}],
            tenant_seller_id=self.seller_b,
        )
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT tenant_seller_id,title,price_amount,active
                   FROM tenant_seller_catalog_products
                   WHERE tenant_id=? AND marketplace_code='kaspi'
                     AND source_product_code='SKU-1' ORDER BY tenant_seller_id""",
                (self.tenant_id,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(2, len(rows))
        self.assertEqual(
            {(self.seller_a, "A title", 100), (self.seller_b, "B title", 200)},
            {(int(row["tenant_seller_id"]), row["title"], row["price_amount"]) for row in rows},
        )

        user = self.auth.get_user(int(self.admin["id"])) or self.admin
        visible = DataService(self.db_path, "unused").rows_for_user(int(user["id"]))
        scoped_codes = {
            row["product_code"] for row in visible if row.get("platform") == "kaspi"
        }
        self.assertEqual(
            {f"kaspi:s{self.seller_a}:SKU-1", f"kaspi:s{self.seller_b}:SKU-1"},
            scoped_codes,
        )

        self.catalog.replace_catalog_products(
            self.tenant_id, "kaspi", [], tenant_seller_id=self.seller_a
        )
        memberships_b = self.catalog.catalog_memberships(
            self.tenant_id, ["kaspi"], self.seller_b
        )
        self.assertEqual({("kaspi", "SKU-1")}, memberships_b)

    def test_same_external_seller_id_in_different_tenants_does_not_collide(self) -> None:
        tenant_b = add_approved_tenant(self.db_path, "tenant-b")
        ensure_database(self.db_path)
        seller_b = add_seller(
            self.db_path, tenant_b, "kaspi", "shared-external", "Tenant B seller"
        )
        self.catalog.replace_catalog_products(
            self.tenant_id,
            "kaspi",
            [{"product_id": "SAME", "title": "Tenant A"}],
            tenant_seller_id=self.seller_a,
        )
        self.catalog.replace_catalog_products(
            tenant_b,
            "kaspi",
            [{"product_id": "SAME", "title": "Tenant B"}],
            tenant_seller_id=seller_b,
        )
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT tenant_id,title FROM tenant_seller_catalog_products
                   WHERE marketplace_code='kaspi' AND source_product_code='SAME'
                   ORDER BY tenant_id"""
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            [(self.tenant_id, "Tenant A"), (tenant_b, "Tenant B")], rows
        )

    def test_credentials_are_tenant_and_seller_scoped(self) -> None:
        previous = os.environ.get("ITP_CREDENTIAL_MASTER_KEY")
        os.environ["ITP_CREDENTIAL_MASTER_KEY"] = generate_master_key()
        self.addCleanup(
            lambda: (
                os.environ.pop("ITP_CREDENTIAL_MASTER_KEY", None)
                if previous is None
                else os.environ.__setitem__("ITP_CREDENTIAL_MASTER_KEY", previous)
            )
        )
        vault = CredentialVault(self.db_path)
        reference = vault.put(
            self.tenant_id,
            "kaspi",
            "browser-session",
            {"cookie": "seller-a-secret"},
            int(self.admin["id"]),
            tenant_seller_id=self.seller_a,
        )
        self.assertEqual(
            "seller-a-secret",
            vault.get(
                self.tenant_id, reference, tenant_seller_id=self.seller_a
            )["cookie"],
        )
        with self.assertRaises(CredentialVaultError):
            vault.get(self.tenant_id, reference, tenant_seller_id=self.seller_b)

        tenant_b = add_approved_tenant(self.db_path, "vault-b")
        ensure_database(self.db_path)
        with self.assertRaises(CredentialVaultError):
            vault.get(tenant_b, reference)
        metadata = vault.metadata(
            self.tenant_id, tenant_seller_id=self.seller_a
        )
        self.assertEqual(self.seller_a, int(metadata[0]["tenant_seller_id"]))
        self.assertNotIn("ciphertext", metadata[0])

    def test_kaspi_offer_snapshots_are_written_to_selected_seller(self) -> None:
        self.catalog.replace_catalog_products(
            self.tenant_id,
            "kaspi",
            [{"product_id": "SKU-OFFERS", "title": "Scoped offers"}],
            tenant_seller_id=self.seller_a,
        )
        database = KaspiDatabase(self.db_path)
        try:
            result = save_success(
                database,
                run_id="seller-a-run",
                item={"product_code": "SKU-OFFERS", "title": "Scoped offers"},
                detail={},
                offers=[
                    {
                        "merchantId": "shared-external",
                        "merchantName": "Seller A",
                        "price": 120,
                    },
                    {
                        "merchantId": "competitor",
                        "merchantName": "Competitor",
                        "price": 110,
                    },
                ],
                seller_id="shared-external",
                seller_name="Seller A",
                duration=0.1,
                tenant_id=self.tenant_id,
                tenant_seller_id=self.seller_a,
            )
        finally:
            database.conn.close()
        self.assertEqual((2, 1, 110.0, 110.0), result)
        conn = sqlite3.connect(self.db_path)
        try:
            snapshot_count = int(conn.execute(
                """SELECT COUNT(*) FROM tenant_seller_offer_snapshots
                   WHERE tenant_id=? AND tenant_seller_id=?
                     AND source_product_code='SKU-OFFERS'""",
                (self.tenant_id, self.seller_a),
            ).fetchone()[0])
            scan = conn.execute(
                """SELECT status,offers_count,competitor_count
                   FROM tenant_seller_offer_scans
                   WHERE tenant_id=? AND tenant_seller_id=?
                     AND source_product_code='SKU-OFFERS'""",
                (self.tenant_id, self.seller_a),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(2, snapshot_count)
        self.assertEqual(("ok", 2, 1), scan)

    def test_capacity_check_is_atomic_across_two_sellers(self) -> None:
        subscription = SubscriptionService(self.db_path)
        entitlement = subscription.entitlement(self.tenant_id)
        plan_id = int(entitlement["subscription"]["plan_id"])
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO subscription_plan_marketplace_limits(
                       plan_id,marketplace_code,is_enabled,position_limit,
                       daily_operation_limit
                   ) VALUES(?,'kaspi',1,1,NULL)
                   ON CONFLICT(plan_id,marketplace_code) DO UPDATE SET
                       position_limit=1,is_enabled=1""",
                (plan_id,),
            )
            conn.commit()
        finally:
            conn.close()
        marketplace_entitlement = SubscriptionService(self.db_path).entitlement(
            self.tenant_id
        )["marketplaces"]["kaspi"]
        barrier = threading.Barrier(3)

        def replace(seller_id: int, code: str) -> str:
            barrier.wait()
            try:
                self.catalog.replace_catalog_products(
                    self.tenant_id,
                    "kaspi",
                    [{"product_id": code, "title": code}],
                    tenant_seller_id=seller_id,
                )
                return "saved"
            except SubscriptionLimitError:
                return "limited"

        with patch.object(
            self.catalog,
            "_position_entitlement",
            return_value=marketplace_entitlement,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(replace, self.seller_a, "A"),
                    executor.submit(replace, self.seller_b, "B"),
                ]
                barrier.wait()
                results = [future.result(timeout=10) for future in futures]
        self.assertEqual(["limited", "saved"], sorted(results))
        conn = sqlite3.connect(self.db_path)
        try:
            active = int(conn.execute(
                """SELECT COUNT(*) FROM tenant_seller_catalog_products
                   WHERE tenant_id=? AND marketplace_code='kaspi' AND active=1""",
                (self.tenant_id,),
            ).fetchone()[0])
        finally:
            conn.close()
        self.assertEqual(1, active)

    def test_schedule_claim_is_single_winner_and_keeps_seller(self) -> None:
        schedule = self.saas.create_schedule(
            self.tenant_id,
            {
                "name": "Seller A schedule",
                "action": "kaspi_catalog_collect",
                "tenant_seller_id": self.seller_a,
                "recurrence_type": "interval",
                "interval_minutes": 60,
            },
            int(self.admin["id"]),
        )
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE operation_schedules SET next_run_at='2020-01-01T00:00:00+00:00' WHERE id=?",
                (int(schedule["id"]),),
            )
            conn.commit()
        finally:
            conn.close()
        due = SaaSService(self.db_path).schedule(int(schedule["id"]), self.tenant_id)
        assert due
        barrier = threading.Barrier(3)

        def claim() -> int | None:
            barrier.wait()
            return SaaSService(self.db_path).begin_schedule_run(dict(due))

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(claim), executor.submit(claim)]
            barrier.wait()
            claims = [future.result(timeout=10) for future in futures]
        self.assertEqual(1, sum(value is not None for value in claims))
        runs = self.saas.schedule_runs(self.tenant_id)
        self.assertEqual(1, len(runs))
        self.assertEqual(self.seller_a, int(runs[0]["tenant_seller_id"]))

    def test_acceptance_topology_keeps_five_parallel_seller_results_separate(self) -> None:
        seller_a3 = add_seller(
            self.db_path, self.tenant_id, "ozon", "a3", "Ozon A3"
        )
        tenant_b = add_approved_tenant(self.db_path, "acceptance-b")
        ensure_database(self.db_path)
        seller_b1 = add_seller(
            self.db_path, tenant_b, "kaspi", "b1", "Kaspi B1"
        )
        seller_b2 = add_seller(
            self.db_path, tenant_b, "halyk_market", "b2", "Halyk B2"
        )
        targets = [
            (self.tenant_id, "kaspi", self.seller_a, "A1"),
            (self.tenant_id, "kaspi", self.seller_b, "A2"),
            (self.tenant_id, "ozon", seller_a3, "A3"),
            (tenant_b, "kaspi", seller_b1, "B1"),
            (tenant_b, "halyk_market", seller_b2, "B2"),
        ]
        barrier = threading.Barrier(len(targets) + 1)

        def store(target: tuple[int, str, int, str]) -> int:
            tenant_id, marketplace, seller_id, label = target
            barrier.wait()
            return CatalogConfigurationService(self.db_path).replace_catalog_products(
                tenant_id,
                marketplace,
                [{"product_id": "SHARED-SKU", "title": label}],
                tenant_seller_id=seller_id,
            )

        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            futures = [executor.submit(store, target) for target in targets]
            barrier.wait()
            self.assertEqual([1] * len(targets), [item.result(timeout=20) for item in futures])
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT tenant_id,marketplace_code,tenant_seller_id,title
                   FROM tenant_seller_catalog_products
                   WHERE source_product_code='SHARED-SKU' AND active=1"""
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            {
                (tenant_id, marketplace, seller_id, label)
                for tenant_id, marketplace, seller_id, label in targets
            },
            set(rows),
        )

    def test_cross_tenant_seller_idor_is_rejected_by_direct_api(self) -> None:
        tenant_b = add_approved_tenant(self.db_path, "api-b")
        ensure_database(self.db_path)
        foreign_seller = add_seller(
            self.db_path, tenant_b, "kaspi", "foreign", "Foreign seller"
        )
        patched_user = self.auth.get_user(int(self.admin["id"])) or self.admin
        data = DataService(self.db_path, "unused")
        patchers = [
            patch.object(webapp, "AUTH", self.auth),
            patch.object(webapp, "DB_PATH", self.db_path),
            patch.object(webapp, "SAAS", self.saas),
            patch.object(webapp, "CATALOG", self.catalog),
            patch.object(webapp, "DATA", data),
        ]
        for item in patchers:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(patchers)])
        webapp.app.config.update(TESTING=True)
        client = webapp.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = int(patched_user["id"])
            session["csrf_token"] = "csrf-test"
        response = client.post(
            "/api/tasks/start",
            json={
                "action": "kaspi_catalog_collect",
                "scope": "all",
                "tenant_seller_id": foreign_seller,
            },
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(403, response.status_code)
        invalid = client.post(
            "/api/tasks/start",
            json={
                "action": "kaspi_catalog_collect",
                "scope": "all",
                "tenant_seller_id": "not-an-integer",
            },
            headers={"X-CSRF-Token": "csrf-test"},
        )
        self.assertEqual(400, invalid.status_code)
        tenant_view = client.get("/api/tenant").get_json()
        kaspi = next(
            item for item in tenant_view["marketplace_access"]
            if item["code"] == "kaspi"
        )
        self.assertNotIn(foreign_seller, {int(item["id"]) for item in kaspi["sellers"]})
        html = client.get("/app").get_data(as_text=True)
        self.assertIn('id="operationSeller"', html)
        self.assertIn('id="scheduleSeller"', html)
        self.assertNotIn(f'"id": {foreign_seller}', html)


class TaskAndRuntimeIsolationTests(unittest.TestCase):
    def test_single_seller_selector_is_hidden_everywhere(self) -> None:
        javascript = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "sellerField.hidden=sellers.length<=1||platform==='system'",
            javascript,
        )
        self.assertIn("field.hidden=sellers.length<=1", javascript)

    def test_runtime_paths_and_resources_are_tenant_seller_scoped(self) -> None:
        root = Path("C:/test-root")
        scopes = [
            SellerRuntimeScope(root, 1, "kaspi", 10),
            SellerRuntimeScope(root, 1, "kaspi", 11),
            SellerRuntimeScope(root, 2, "kaspi", 10),
            SellerRuntimeScope(root, 1, "ozon", 10),
        ]
        self.assertEqual(4, len({str(item.profile_dir) for item in scopes}))
        self.assertEqual(4, len({str(item.registry_path) for item in scopes}))
        resources = [tuple(item.task_resources(["kaspi_browser"])) for item in scopes]
        self.assertEqual(4, len(set(resources)))

    def test_ozon_marketplace_lock_serializes_ru_but_not_kz(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_marketplace_lock_") as folder:
            root = Path(folder)
            release = root / "release"
            manager = TaskManager(root, root / "logs", root / "tasks.json", 6)
            command = [
                sys.executable, "-c",
                "from pathlib import Path;import sys,time;p=Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.01)",
                str(release),
            ]
            ru = manager.start("ozon_catalog_collect", "RU", command, ["ozon_browser"])
            try:
                with self.assertRaises(RuntimeError):
                    manager.start("ozon_catalog_collect", "RU other seller", command, ["ozon_browser"])
                kz = manager.start("ozon_kz_catalog_collect", "KZ", command, ["ozon_kz"])
                self.assertEqual("running", manager.state(str(kz["id"]))["status"])
            finally:
                release.touch()
                for task in (ru, locals().get("kz")):
                    if task:
                        manager.processes[str(task["id"])].wait(timeout=10)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and any(
                    manager.state(str(task["id"])).get("status") == "running"
                    for task in (ru, locals().get("kz")) if task
                ):
                    threading.Event().wait(0.01)

    def test_ozon_marketplace_profile_is_shared_by_all_sellers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_legacy_profile_") as folder:
            root = Path(folder)
            first = SellerRuntimeScope(root, 8, "ozon", 17)
            second = SellerRuntimeScope(root, 8, "ozon", 23)
            sellers = [
                {
                    "id": 17,
                    "source_url": "https://www.ozon.ru/seller/alfa-tires-3381444/",
                },
                {"id": 23, "source_url": "https://www.ozon.ru/seller/other/"},
            ]
            expected = root / "collectors" / "ozon" / "chrome_vpn_profile"
            self.assertEqual(expected.resolve(), webapp.browser_profile_for_seller(first, "ozon", sellers))
            self.assertEqual(expected.resolve(), webapp.browser_profile_for_seller(second, "ozon", sellers))

    def test_ozon_kz_uses_its_permanent_marketplace_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_empty_profile_") as folder:
            root = Path(folder)
            runtime = SellerRuntimeScope(root, 8, "ozon_kz", 19)
            self.assertEqual(
                (root / "collectors" / "ozon" / "chrome_kz_profile").resolve(),
                webapp.browser_profile_for_seller(
                    runtime, "ozon_kz", [{"id": 19, "source_url": "https://ozon.kz/seller/alfa/"}],
                ),
            )

    def test_ozon_profile_does_not_depend_on_current_seller_tab(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_profile_bootstrap_") as folder:
            root = Path(folder)
            runtime = SellerRuntimeScope(root, 10, "ozon_kz", 19)
            sellers = [
                {"id": 3, "source_url": "https://ozon.kz/seller/ridial/"},
                {
                    "id": 19,
                    "source_url": "https://ozon.kz/seller/alfa-tires-3381444/",
                },
            ]
            self.assertEqual(
                (root / "collectors" / "ozon" / "chrome_kz_profile").resolve(),
                webapp.browser_profile_for_seller(runtime, "ozon_kz", sellers),
            )

    def test_legacy_profile_owner_is_global_across_tenants(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ozon_profile_owner_") as folder:
            db_path = Path(folder) / "app.db"
            ensure_database(db_path)
            auth = AuthService(db_path)
            root_user, _ = auth.create_initial_admin(
                "root-owner@example.test", "Root", "StrongPassword123!"
            )
            first_tenant = int(root_user["tenant_id"])
            second_tenant = add_approved_tenant(db_path, "profile-owner-second")
            first_seller = add_seller(
                db_path, first_tenant, "ozon", "first-global", "First"
            )
            second_seller = add_seller(
                db_path, second_tenant, "ozon", "second-global", "Second"
            )
            service = SaaSService(db_path)
            self.assertEqual(
                [first_seller, second_seller],
                [item["id"] for item in service.active_seller_sources("ozon")],
            )

    def test_two_managers_allow_six_sellers_reject_duplicate_and_isolate_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task_multi_seller_") as folder:
            root = Path(folder)
            logs = root / "logs"
            state = root / "tasks.json"
            release = root / "release"
            first = TaskManager(root, logs, state, max_parallel=6)
            second = TaskManager(root, logs, state, max_parallel=6)
            exit_codes = [0, 7, 0, 9, 0, 0]
            tasks: list[dict[str, object]] = []
            managers: list[TaskManager] = []
            for index, exit_code in enumerate(exit_codes, 1):
                manager = first if index % 2 else second
                command = [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path;import sys,time;"
                        "p=Path(sys.argv[1]);"
                        "\nwhile not p.exists(): time.sleep(0.01);"
                        "\nsys.exit(int(sys.argv[2]))"
                    ),
                    str(release),
                    str(exit_code),
                ]
                task = manager.start(
                    "mock_sync",
                    f"Seller {index}",
                    command,
                    [f"seller:1:kaspi:{index}"],
                    metadata={
                        "tenant_id": 1,
                        "tenant_seller_id": index,
                        "seller_id": f"external-{index}",
                        "platform": "kaspi",
                    },
                )
                tasks.append(task)
                managers.append(manager)
                if index == 4:
                    with self.assertRaises(RuntimeError):
                        second.start(
                            "duplicate",
                            "Duplicate seller",
                            command,
                            ["seller:1:kaspi:1"],
                        )
            self.assertEqual(6, len({str(task["id"]) for task in tasks}))
            with self.assertRaises(RuntimeError):
                first.start(
                    "seventh",
                    "Seventh seller",
                    [sys.executable, "-c", "pass"],
                    ["seller:1:kaspi:7"],
                )
            for task in tasks:
                first_line = Path(str(task["log_file"])).read_text(
                    encoding="utf-8"
                ).splitlines()[0]
                context = json.loads(first_line.removeprefix("[JOB_CONTEXT] "))
                self.assertEqual(task["id"], context["job_id"])
                self.assertEqual("kaspi", context["marketplace"])

            processes = [
                manager.processes[str(task["id"])]
                for manager, task in zip(managers, tasks)
            ]
            release.touch()
            for process in processes:
                process.wait(timeout=10)
            deadline = time.monotonic() + 10
            statuses: dict[str, str] = {}
            while time.monotonic() < deadline:
                statuses = {
                    str(item["id"]): str(item["status"])
                    for item in first.raw_states()
                }
                if all(statuses.get(str(task["id"])) != "running" for task in tasks):
                    break
                threading.Event().wait(0.01)
            self.assertEqual(
                ["completed", "failed", "completed", "failed", "completed", "completed"],
                [statuses[str(task["id"])] for task in tasks],
            )

            cleanup_deadline = time.monotonic() + 10
            while time.monotonic() < cleanup_deadline:
                if all(
                    not manager.processes and not manager.log_handles
                    for manager in (first, second)
                ):
                    break
                threading.Event().wait(0.01)
            self.assertFalse(first.processes)
            self.assertFalse(second.processes)
            self.assertFalse(first.log_handles)
            self.assertFalse(second.log_handles)

    def test_http_remains_available_while_seller_job_runs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="http_during_job_") as folder:
            root = Path(folder)
            db_path = root / "app.db"
            ensure_database(db_path)
            release = root / "release"
            manager = TaskManager(root, root / "logs", root / "tasks.json", 6)
            task = manager.start(
                "mock_sync",
                "Seller background sync",
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path;import sys,time;"
                        "p=Path(sys.argv[1]);"
                        "\nwhile not p.exists(): time.sleep(0.01)"
                    ),
                    str(release),
                ],
                ["seller:1:kaspi:1"],
                metadata={
                    "tenant_id": 1, "tenant_seller_id": 1, "platform": "kaspi"
                },
            )
            try:
                with patch.object(webapp, "DB_PATH", db_path), patch.object(
                    webapp, "SUBSCRIPTIONS", SubscriptionService(db_path)
                ):
                    client = webapp.app.test_client()
                    for path in ("/health", "/ready", "/", "/api/public/plans"):
                        response = client.get(path)
                        self.assertEqual(200, response.status_code, path)
                    self.assertEqual("running", manager.state(str(task["id"]))["status"])
            finally:
                release.touch()
                manager.processes[str(task["id"])].wait(timeout=10)
                deadline = time.monotonic() + 5
                while (
                    manager.state(str(task["id"])).get("status") == "running"
                    and time.monotonic() < deadline
                ):
                    threading.Event().wait(0.01)
                self.assertNotEqual(
                    "running", manager.state(str(task["id"])).get("status")
                )

    def test_dead_process_is_recovered_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task_recovery_") as folder:
            root = Path(folder)
            state = root / "tasks.json"
            state.write_text(
                json.dumps({
                    "tasks": [{
                        "id": "orphan", "name": "sync", "status": "running",
                        "running": True, "pid": 99999999,
                        "started_at": "2026-01-01T00:00:00+00:00",
                        "resources": ["seller:1:kaspi:1"], "metadata": {},
                    }]
                }),
                encoding="utf-8",
            )
            manager = TaskManager(root, root / "logs", state, max_parallel=6)
            task = manager.state("orphan")
            self.assertEqual("interrupted", task["status"])
            self.assertFalse(task["running"])

    def test_scheduler_and_manual_job_for_same_seller_conflict(self) -> None:
        class FakeSaaS:
            def __init__(self) -> None:
                self.finished: list[tuple[int, int, str, str]] = []

            def due_schedules(self):
                return [{
                    "id": 12,
                    "tenant_id": 1,
                    "tenant_seller_id": 7,
                    "action": "kaspi_catalog_collect",
                    "created_by": 3,
                }]

            def begin_schedule_run(self, _schedule):
                return 55

            def finish_schedule_run(self, *args):
                self.finished.append(args)

            def attach_task_to_run(self, *_args):
                return None

        with tempfile.TemporaryDirectory(prefix="scheduler_manual_") as folder:
            root = Path(folder)
            release = root / "release"
            manager = TaskManager(root, root / "logs", root / "tasks.json", 6)
            command = [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path;import sys,time;"
                    "p=Path(sys.argv[1]);"
                    "\nwhile not p.exists(): time.sleep(0.01)"
                ),
                str(release),
            ]
            manual = manager.start(
                "kaspi_catalog_collect",
                "Manual",
                command,
                ["seller:1:kaspi:7"],
                metadata={
                    "tenant_id": 1, "tenant_seller_id": 7, "platform": "kaspi"
                },
            )
            fake = FakeSaaS()
            scheduler = SchedulerService(
                fake,
                manager,
                {
                    "kaspi_catalog_collect": {
                        "label": "Kaspi sync",
                        "resource": ["kaspi_browser"],
                        "platform": "kaspi",
                    }
                },
                lambda *_args: command,
                user_loader=lambda _user_id: {
                    "id": 3,
                    "tenant_id": 1,
                    "is_active": True,
                    "tenant_status": "approved",
                    "tenant_profile_complete": True,
                    "role": "operator",
                    "platform_role": "",
                    "permissions": {"run_operations": True},
                    "marketplaces": {"kaspi": True},
                    "available_marketplaces": {"kaspi": True},
                    "marketplace_permissions": {"kaspi": True},
                },
            )
            scheduler.run_due_once()
            scheduled = next(
                task for task in manager.raw_states()
                if task.get("id") != manual.get("id")
            )
            self.assertEqual("queued", scheduled["status"])
            process = manager.processes[str(manual["id"])]
            release.touch()
            process.wait(timeout=10)
            deadline = time.monotonic() + 5
            while manager.state(str(manual["id"])).get("status") == "running" and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            deadline = time.monotonic() + 5
            while manager.state(str(scheduled["id"])).get("status") in {"queued", "running"} and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            self.assertEqual("completed", manager.state(str(scheduled["id"]))["status"])

    def test_ozon_run_ids_are_collision_resistant(self) -> None:
        values = {run_id_for("sync") for _ in range(1000)}
        self.assertEqual(1000, len(values))


class PostgreSQLPoolBoundTests(unittest.TestCase):
    def test_pool_size_is_a_hard_checked_out_connection_limit(self) -> None:
        class FakeRaw:
            def __init__(self) -> None:
                self.closed = False

            def execute(self, *_args, **_kwargs):
                return None

            def commit(self) -> None:
                return None

            def rollback(self) -> None:
                return None

            def close(self) -> None:
                self.closed = True

        created: list[FakeRaw] = []

        def connect(*_args, **_kwargs) -> FakeRaw:
            raw = FakeRaw()
            created.append(raw)
            return raw

        fake_psycopg = types.SimpleNamespace(connect=connect)
        key = ("postgresql://test", "app")
        with patch.dict(sys.modules, {"psycopg": fake_psycopg}), patch.object(
            postgres_compat, "_POOL_SIZE", 2
        ):
            postgres_compat._CONNECTION_POOLS.clear()
            postgres_compat._METADATA_CACHE[key] = ({}, {})
            first = postgres_compat.PostgresConnection(*key, timeout=0)
            second = postgres_compat.PostgresConnection(*key, timeout=0)
            with self.assertRaises(TimeoutError):
                postgres_compat.PostgresConnection(*key, timeout=0)
            first.close()
            third = postgres_compat.PostgresConnection(*key, timeout=0)
            self.assertEqual(2, len(created))
            second.close()
            third.close()
            postgres_compat._CONNECTION_POOLS.clear()
            postgres_compat._METADATA_CACHE.pop(key, None)


if __name__ == "__main__":
    unittest.main()
