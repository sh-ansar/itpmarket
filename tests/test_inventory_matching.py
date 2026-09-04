from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["ITP_DISABLE_SCHEDULER"] = "1"

import app as webapp
from auth_service import AuthService
from inventory_service import InventoryService
from market_intelligence import Candidate, exact_offer_position
from schema import ensure_database
from tenant_security import ROLE_DEFAULT_PERMISSIONS


def offer(code: str, price: float) -> Candidate:
    return Candidate(
        code=code,
        title=code,
        url="",
        price=price,
        brand="Bridgestone",
        tier="premium",
        model="Blizzak DM-V3",
        score=100,
        relation="EXACT",
        reasons=[],
    )


def catalog_rows() -> list[dict[str, object]]:
    base = {
        "title": "Bridgestone Blizzak DM-V3 275/40 R22 107T",
        "brand": "Bridgestone",
        "model": "Blizzak DM-V3",
        "size": "275/40 R22",
        "product_type": "tires",
        "manufacturer_article": "BS-DMV3-27540R22",
        "exact_characteristic_key": "bridgestone|blizzak-dmv3|275/40r22|107t",
    }
    return [
        {
            **base,
            "product_code": "kaspi:s11:180065336",
            "source_product_code": "180065336",
            "platform": "kaspi",
            "platform_label": "Kaspi",
            "price_kzt": 188_600,
        },
        {
            **base,
            "product_code": "ozon:s22:123456",
            "source_product_code": "123456",
            "platform": "ozon",
            "platform_label": "Ozon.ru",
            "price_kzt": 190_000,
        },
    ]


class ExactPriceTieTests(unittest.TestCase):
    def test_all_prices_equal_are_in_market(self) -> None:
        result = exact_offer_position(
            188_600,
            [offer("seller-a", 188_600), offer("seller-b", 188_600)],
            "ok",
        )

        self.assertEqual("EXACT_IN_MARKET", result["price_status"])
        self.assertFalse(result["is_lowest"])
        self.assertFalse(result["is_unique_lowest"])
        self.assertEqual(3, result["lowest_tie_count"])
        self.assertEqual(3, result["price_rank_tie_count"])
        self.assertEqual(0, result["potential_margin_per_unit_kzt"])

    def test_equal_minimum_below_maximum_is_tied_lowest(self) -> None:
        result = exact_offer_position(
            188_600,
            [offer("seller-a", 188_600), offer("seller-b", 190_000)],
            "ok",
        )

        self.assertEqual("EXACT_TIED_LOWEST", result["price_status"])
        self.assertTrue(result["is_lowest"])
        self.assertFalse(result["is_unique_lowest"])

    def test_equal_maximum_above_minimum_is_tied_highest(self) -> None:
        result = exact_offer_position(
            190_000,
            [offer("seller-a", 100_000), offer("seller-b", 190_000)],
            "ok",
        )

        self.assertEqual("EXACT_TIED_HIGHEST", result["price_status"])
        self.assertTrue(result["is_highest"])
        self.assertFalse(result["is_unique_highest"])

    def test_high_side_production_boundaries_respect_tolerance(self) -> None:
        cases = [
            ("108065336", 188_600, [188_550, 188_600, 188_600], "EXACT_IN_MARKET"),
            ("123070844", 52_900, [52_800, 52_900, 52_900], "EXACT_IN_MARKET"),
            ("12719578", 156_700, [152_590, 156_700, 156_700], "EXACT_IN_MARKET"),
            ("135745057", 82_000, [81_995, 82_000, 82_000], "EXACT_IN_MARKET"),
            ("143279516", 105_500, [104_210, 104_210], "EXACT_IN_MARKET"),
            ("120426914", 32_000, [29_600, 29_750, 32_000], "EXACT_TIED_HIGHEST"),
            ("132368483", 105_300, [70_000, 78_000, 105_300], "EXACT_TIED_HIGHEST"),
        ]

        for product_code, own_price, prices, expected in cases:
            with self.subTest(product_code=product_code):
                result = exact_offer_position(
                    own_price,
                    [
                        offer(f"seller-{index}", price)
                        for index, price in enumerate(prices)
                    ],
                    "ok",
                )
                self.assertEqual(expected, result["price_status"])

    def test_price_within_two_percent_tolerance_is_in_market(self) -> None:
        result = exact_offer_position(
            139_490,
            [
                offer("seller-a", 130_000),
                offer("seller-b", 140_245),
                offer("seller-c", 150_490),
            ],
            "ok",
        )

        self.assertEqual("EXACT_IN_MARKET", result["price_status"])

    def test_price_below_tolerance_is_exact_below_without_safe_potential(self) -> None:
        result = exact_offer_position(
            130_000,
            [
                offer("seller-a", 120_000),
                offer("seller-b", 140_000),
                offer("seller-c", 160_000),
            ],
            "ok",
        )

        self.assertEqual("EXACT_BELOW", result["price_status"])
        self.assertEqual(0, result["potential_margin_per_unit_kzt"])

    def test_strictly_lower_price_remains_unique_lowest(self) -> None:
        result = exact_offer_position(
            180_000,
            [offer("seller-a", 188_600), offer("seller-b", 190_000)],
            "ok",
        )

        self.assertEqual("EXACT_LOWEST", result["price_status"])
        self.assertTrue(result["is_unique_lowest"])
        self.assertEqual(1, result["lowest_tie_count"])
        self.assertGreater(result["potential_margin_per_unit_kzt"], 0)


class InventoryMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="inventory_matching_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        self.auth = AuthService(self.db_path)
        self.admin, _ = self.auth.create_initial_admin(
            "inventory@example.test", "Inventory Admin", "StrongPassword123!"
        )
        self.tenant_id = int(self.admin["tenant_id"])
        self.user_id = int(self.admin["id"])
        self.service = InventoryService(self.db_path)
        self.rows = catalog_rows()

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_confirmed_cross_market_match_counts_physical_stock_once(self) -> None:
        source, candidate = self.rows
        self.service.save_inventory(
            self.tenant_id,
            str(source["product_code"]),
            source,
            {
                "internal_sku": "BS-DMV3-01",
                "quantity_on_hand": 4,
                "purchase_price_kzt": 100_000,
                "target_markup_percent": 20,
            },
            self.user_id,
        )
        suggestion = self.service.context(
            self.tenant_id,
            str(source["product_code"]),
            self.rows,
            include_inventory=True,
        )["matching"]["suggestions"][0]
        self.assertEqual("MANUFACTURER_ARTICLE", suggestion["match_method"])

        result = self.service.decide_match(
            self.tenant_id,
            source,
            candidate,
            "confirmed",
            self.user_id,
            match_method=str(suggestion["match_method"]),
            match_score=float(suggestion["match_score"]),
            reason=str(suggestion["match_reason"]),
        )
        context = self.service.context(
            self.tenant_id,
            str(candidate["product_code"]),
            self.rows,
            include_inventory=True,
        )
        summary = self.service.summary(
            self.tenant_id, {str(row["product_code"]) for row in self.rows}
        )

        self.assertEqual(result["inventory_product_id"], context["inventory"]["id"])
        self.assertEqual(2, len(context["inventory"]["linked_listings"]))
        self.assertEqual(1, summary["inventory_products"])
        self.assertEqual(2, summary["linked_listings"])
        self.assertEqual(4, summary["quantity_on_hand"])
        self.assertEqual(400_000, summary["stock_value_kzt"])
        self.assertTrue(summary["stock_value_complete"])
        self.assertEqual(0, summary["unpriced_inventory_products"])
        self.assertEqual(120_000, context["inventory"]["recommended_min_price_kzt"])

    def test_purchase_cost_is_not_returned_without_inventory_permission(self) -> None:
        source = self.rows[0]
        self.service.save_inventory(
            self.tenant_id,
            str(source["product_code"]),
            source,
            {"quantity_on_hand": 2, "purchase_price_kzt": 70_000},
            self.user_id,
        )

        context = self.service.context(
            self.tenant_id,
            str(source["product_code"]),
            self.rows,
            include_inventory=False,
        )

        self.assertTrue(context["has_inventory_link"])
        self.assertIsNone(context["inventory"])

    def test_rejected_pair_is_not_suggested_again(self) -> None:
        source, candidate = self.rows
        self.service.decide_match(
            self.tenant_id,
            source,
            candidate,
            "rejected",
            self.user_id,
            match_method="MANUFACTURER_ARTICLE",
            match_score=100,
            reason="Manual verification",
        )

        context = self.service.context(
            self.tenant_id,
            str(source["product_code"]),
            self.rows,
            include_inventory=True,
        )
        self.assertEqual([], context["matching"]["suggestions"])

    def test_two_populated_inventory_items_are_not_merged_implicitly(self) -> None:
        source, candidate = self.rows
        self.service.save_inventory(
            self.tenant_id,
            str(source["product_code"]),
            source,
            {"quantity_on_hand": 2, "purchase_price_kzt": 70_000},
            self.user_id,
        )
        self.service.save_inventory(
            self.tenant_id,
            str(candidate["product_code"]),
            candidate,
            {"quantity_on_hand": 3, "purchase_price_kzt": 75_000},
            self.user_id,
        )

        with self.assertRaisesRegex(ValueError, "разным складским товарам"):
            self.service.decide_match(
                self.tenant_id,
                source,
                candidate,
                "confirmed",
                self.user_id,
                match_method="MANUFACTURER_ARTICLE",
                match_score=100,
            )


class InventoryPermissionAndUiTests(unittest.TestCase):
    def test_role_defaults_separate_inventory_and_matching(self) -> None:
        self.assertNotIn("view_inventory", ROLE_DEFAULT_PERMISSIONS["viewer"])
        self.assertIn("view_inventory", ROLE_DEFAULT_PERMISSIONS["operator"])
        self.assertIn("manage_inventory", ROLE_DEFAULT_PERMISSIONS["operator"])
        self.assertNotIn("manage_product_matching", ROLE_DEFAULT_PERMISSIONS["operator"])
        self.assertIn("manage_product_matching", ROLE_DEFAULT_PERMISSIONS["admin"])

    def test_registration_uses_styled_fields_and_bottom_guide(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "register.html").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "registration_guide.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("registration_guide.css", template)
        self.assertEqual(
            3,
            template.count("data-registration-step"),
        )
        for step in ("company", "plan", "account"):
            self.assertIn(f'data-step-code="{step}"', template)
        self.assertNotIn('data-step-code="marketplaces"', template)
        self.assertIn('id="registrationGuide"', template)
        self.assertIn(".registration-fields input", css)
        self.assertIn("position: sticky", css)

    def test_help_content_loads_before_ui_runtime_and_covers_every_page(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "app.html").read_text(encoding="utf-8")
        help_js = (root / "static" / "js" / "help_content.js").read_text(
            encoding="utf-8"
        )
        self.assertLess(template.index("help_content.js"), template.index("ui_core.js"))
        for language in ("ru:", "kk:", "en:"):
            self.assertIn(language, help_js)
        for page in (
            "dashboard:", "products:", "operations:", "reports:",
            "schedules:", "users:", "settings:",
        ):
            self.assertGreaterEqual(help_js.count(page), 3)

    def test_postgres_migration_is_additive_and_seeds_new_role_defaults(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "20260818_inventory_matching_v1.sql"
        ).read_text(encoding="utf-8")
        upper = migration.upper()
        self.assertNotRegex(upper, r"(?m)^\s*(DROP|DELETE|TRUNCATE)\b")
        for table in (
            "tenant_inventory_products", "tenant_product_listings",
            "tenant_product_match_decisions", "tenant_inventory_events",
        ):
            self.assertIn(table, migration)
        self.assertIn("('admin','manage_product_matching')", migration)
        self.assertIn("('operator','manage_inventory')", migration)
        self.assertIn("ON CONFLICT(tenant_id,role_code,permission_code) DO NOTHING", migration)


class FakeAuth:
    def __init__(self, users: dict[int, dict[str, object]]) -> None:
        self.users = users

    def get_user(self, user_id: int):
        user = self.users.get(int(user_id))
        return dict(user) if user else None

    def has_users(self) -> bool:
        return True


class FakeSubscription:
    def entitlement(self, tenant_id: int) -> dict[str, object]:
        return {
            "active": True,
            "status": "active",
            "features": {
                "products": True,
                "operations": True,
                "reports": True,
                "dynamic_filters": True,
                "team_management": True,
            },
            "marketplaces": {
                "kaspi": {"enabled": True},
                "ozon": {"enabled": True},
            },
        }


class FakeCatalogData:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def rows_for_user(self, user_id: int, marketplaces=None):
        return [
            dict(row) for row in self.rows
            if not marketplaces or str(row["platform"]) in marketplaces
        ]

    def product(self, code: str, user_id: int | None = None, rows=None):
        product = next(
            (dict(row) for row in self.rows if str(row["product_code"]) == code),
            None,
        )
        if product:
            product.update({
                "specifications": [],
                "offers": [],
                "history": [],
                "status_tone": "neutral",
                "price_status": "NOT_ANALYZED",
                "potential_margin_monthly_kzt": 0,
                "potential_margin_per_unit_kzt": 0,
                "reference_count": 0,
            })
        return product


class InventoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="inventory_api_")
        self.db_path = Path(self.folder.name) / "app.db"
        ensure_database(self.db_path)
        real_auth = AuthService(self.db_path)
        actor, _ = real_auth.create_initial_admin(
            "api-actor@example.test", "API Actor", "StrongPassword123!"
        )
        self.user_id = int(actor["id"])
        self.tenant_id = int(actor["tenant_id"])
        permissions = {
            code: code in ROLE_DEFAULT_PERMISSIONS["admin"]
            for code in ROLE_DEFAULT_PERMISSIONS["admin"]
        }
        self.user = {
            "id": self.user_id,
            "tenant_id": self.tenant_id,
            "display_name": "Tenant Admin",
            "email": "tenant-admin@example.test",
            "role": "admin",
            "platform_role": "",
            "is_active": True,
            "tenant_status": "approved",
            "tenant_profile_complete": True,
            "permissions": permissions,
            "marketplaces": {"kaspi": True, "ozon": True},
            "available_marketplaces": {"kaspi": True, "ozon": True},
            "marketplace_permissions": {"kaspi": True, "ozon": True},
        }
        self.rows = catalog_rows()
        self.patchers = [
            patch.object(webapp, "AUTH", FakeAuth({self.user_id: self.user})),
            patch.object(webapp, "DB_PATH", self.db_path),
            patch.object(webapp, "DATA", FakeCatalogData(self.rows)),
            patch.object(webapp, "subscription_service", lambda: FakeSubscription()),
        ]
        for patcher in self.patchers:
            patcher.start()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id
            session["csrf_token"] = "inventory-csrf"

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.folder.cleanup()

    def request(self, method: str, url: str, payload=None):
        return getattr(self.client, method)(
            url,
            json=payload,
            headers={"X-CSRF-Token": "inventory-csrf"},
        )

    def test_inventory_and_matching_endpoints_share_one_stock_item(self) -> None:
        source_code = str(self.rows[0]["product_code"])
        candidate_code = str(self.rows[1]["product_code"])

        saved = self.request(
            "put",
            f"/api/products/{source_code}/inventory",
            {"quantity_on_hand": 5, "purchase_price_kzt": 90_000},
        )
        matched = self.request(
            "post",
            f"/api/products/{source_code}/match",
            {"candidate_code": candidate_code, "decision": "confirmed"},
        )
        summary = self.request("get", "/api/inventory/summary")

        self.assertEqual(200, saved.status_code)
        self.assertEqual(200, matched.status_code)
        self.assertEqual(200, summary.status_code)
        values = summary.get_json()["summary"]
        self.assertEqual(1, values["inventory_products"])
        self.assertEqual(2, values["linked_listings"])
        self.assertEqual(5, values["quantity_on_hand"])
        self.assertEqual(450_000, values["stock_value_kzt"])

    def test_manage_inventory_permission_is_enforced(self) -> None:
        self.user["permissions"] = {
            **self.user["permissions"],
            "manage_inventory": False,
        }
        source_code = str(self.rows[0]["product_code"])

        response = self.request(
            "put",
            f"/api/products/{source_code}/inventory",
            {"quantity_on_hand": 1, "purchase_price_kzt": 10_000},
        )

        self.assertEqual(403, response.status_code)

    def test_match_api_rejects_candidate_outside_marketplace_access(self) -> None:
        self.user["marketplaces"] = {"kaspi": True}
        self.user["available_marketplaces"] = {"kaspi": True}
        self.user["marketplace_permissions"] = {"kaspi": True}
        source_code = str(self.rows[0]["product_code"])

        response = self.request(
            "post",
            f"/api/products/{source_code}/match",
            {
                "candidate_code": str(self.rows[1]["product_code"]),
                "decision": "confirmed",
            },
        )

        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
