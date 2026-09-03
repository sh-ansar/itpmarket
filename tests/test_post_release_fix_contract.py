from __future__ import annotations

import sqlite3
import statistics
import unittest
from pathlib import Path

from data_service import (
    DataService,
    OPPORTUNITY_STATUSES,
    RISK_STATUSES,
    _exact_offer_price_status_sql,
)
from market_intelligence import Candidate, STATUS_INFO, exact_offer_position


ROOT = Path(__file__).resolve().parents[1]


class RiskOpportunityContractTests(unittest.TestCase):
    def test_exact_below_is_an_info_opportunity_not_a_risk(self) -> None:
        self.assertNotIn("EXACT_BELOW", RISK_STATUSES)
        self.assertIn("EXACT_BELOW", OPPORTUNITY_STATUSES)
        self.assertEqual("info", STATUS_INFO["EXACT_BELOW"]["tone"])
        self.assertTrue(DataService._is_opportunity({"price_status": "EXACT_BELOW"}))
        self.assertNotIn("EXACT_IN_MARKET", RISK_STATUSES)
        self.assertNotIn("EXACT_IN_MARKET", OPPORTUNITY_STATUSES)

    def test_lowest_statuses_require_proven_positive_headroom(self) -> None:
        self.assertFalse(DataService._is_opportunity({
            "price_status": "EXACT_LOWEST", "potential_margin_per_unit_kzt": 0,
        }))
        self.assertTrue(DataService._is_opportunity({
            "price_status": "EXACT_LOWEST", "potential_margin_per_unit_kzt": 1,
        }))
        self.assertFalse(DataService._is_opportunity({
            "price_status": "EXACT_TIED_LOWEST", "potential_margin_per_unit_kzt": 0,
        }))
        self.assertTrue(DataService._is_opportunity({
            "price_status": "EXACT_TIED_LOWEST", "potential_margin_per_unit_kzt": 1,
        }))

    def test_all_business_order_keeps_risks_before_positive_potential(self) -> None:
        statuses = [
            ("EXACT_HIGHEST", 0), ("EXACT_TIED_HIGHEST", 0),
            ("EXACT_ABOVE", 0), ("DATA_ERROR", 0),
            ("EXACT_LOWEST", 50), ("EXACT_TIED_LOWEST", 10),
            ("EXACT_BELOW", 0),
            ("EXACT_IN_MARKET", 0), ("COMPARABLE_HIGHEST", 0),
            ("NOT_ANALYZED", 0),
        ]
        ordered = sorted(
            ({"price_status": status, "potential_margin_per_unit_kzt": potential}
             for status, potential in reversed(statuses)),
            key=DataService._business_priority,
        )
        self.assertEqual([status for status, _ in statuses], [item["price_status"] for item in ordered])
        self.assertTrue(DataService._is_opportunity({"price_status": "EXACT_BELOW", "potential_margin_per_unit_kzt": 0}))
        self.assertFalse(DataService._is_opportunity({"price_status": "NOT_ANALYZED", "potential_margin_per_unit_kzt": 999}))

    def test_sql_projection_and_drawer_classifier_have_the_same_status(self) -> None:
        cases = [
            (167_950, [167_950, 167_950], "EXACT_IN_MARKET"),
            (200_000, [100_000, 200_000], "EXACT_TIED_HIGHEST"),
            (100_000, [100_000, 200_000], "EXACT_TIED_LOWEST"),
            (139_490, [130_000, 140_245, 150_490], "EXACT_IN_MARKET"),
            (130_000, [120_000, 140_000, 160_000], "EXACT_BELOW"),
            (188_600, [188_550, 188_600, 188_600], "EXACT_IN_MARKET"),
            (52_900, [52_800, 52_900, 52_900], "EXACT_IN_MARKET"),
            (156_700, [152_590, 156_700, 156_700], "EXACT_IN_MARKET"),
            (82_000, [81_995, 82_000, 82_000], "EXACT_IN_MARKET"),
            (105_500, [104_210, 104_210], "EXACT_IN_MARKET"),
            (32_000, [29_600, 29_750, 32_000], "EXACT_TIED_HIGHEST"),
            (105_300, [70_000, 78_000, 105_300], "EXACT_TIED_HIGHEST"),
        ]
        conn = sqlite3.connect(":memory:")
        conn.create_function("GREATEST", -1, max)
        sql_status = _exact_offer_price_status_sql().replace(
            "a.references", "a.reference_count"
        )
        try:
            for own_price, prices, expected in cases:
                competitors = [
                    Candidate(
                        code=f"seller-{index}", title="", url="", price=price,
                        brand="", tier="", model="", score=100,
                        relation="EXACT", reasons=[],
                    )
                    for index, price in enumerate(prices)
                ]
                drawer_status = exact_offer_position(
                    own_price, competitors, "ok"
                )["price_status"]
                projection_status = conn.execute(
                    f"""WITH b(price_amount,scan_status) AS (VALUES (?,'ok')),
                               a(reference_count,market_min,market_max,market_median)
                               AS (VALUES (?,?,?,?))
                        SELECT {sql_status} FROM b CROSS JOIN a""",
                    (
                        own_price,
                        len(prices),
                        min(prices),
                        max(prices),
                        statistics.median(prices),
                    ),
                ).fetchone()[0]
                self.assertEqual(expected, drawer_status)
                self.assertEqual(drawer_status, projection_status)
        finally:
            conn.close()


class PostReleaseFrontendContractTests(unittest.TestCase):
    def test_export_uses_backend_risk_and_opportunity_semantics(self) -> None:
        source = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            "const risk=['EXACT_ABOVE','EXACT_HIGHEST','EXACT_TIED_HIGHEST','DATA_ERROR']",
            source,
        )
        self.assertIn("const opportunity=priceStatus==='EXACT_BELOW'", source)

    def test_marketplace_source_actions_use_spyon_modal_not_browser_dialogs(self) -> None:
        source = (ROOT / "static" / "js" / "marketplace_settings.js").read_text(encoding="utf-8")
        self.assertIn("marketplaceSourceModal", source)
        self.assertIn("data-modal-verify", source)
        self.assertNotIn("window.prompt", source)
        self.assertNotIn("window.confirm", source)

    def test_first_products_view_defaults_to_all(self) -> None:
        source = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "app.html").read_text(encoding="utf-8")
        self.assertIn("scope:'all'", source)
        self.assertIn('class="active" data-scope="all"', template)

    def test_marketplace_source_actions_use_dedicated_design_classes(self) -> None:
        source = (ROOT / "static" / "js" / "marketplace_settings.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
        self.assertIn("marketplace-source-actions", source)
        self.assertIn("marketplace-source-action--replace", source)
        self.assertIn("marketplace-source-action--remove", source)
        self.assertNotIn('class="decline" data-marketplace-remove', source)
        self.assertIn(".marketplace-source-action:focus-visible", css)

    def test_static_assets_use_the_deployment_sha_pipeline(self) -> None:
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        deploy_source = (ROOT / "deploy" / "windows" / "post-update-production.ps1").read_text(encoding="utf-8")
        self.assertIn("deployment_asset_version", app_source)
        self.assertIn("deployment-sha", app_source)
        self.assertIn("deployment-sha", deploy_source)
        self.assertIn("$TargetSha", deploy_source)


if __name__ == "__main__":
    unittest.main()
