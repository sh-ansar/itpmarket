from __future__ import annotations

import unittest
from pathlib import Path

from data_service import DataService, OPPORTUNITY_STATUSES, RISK_STATUSES
from market_intelligence import STATUS_INFO


ROOT = Path(__file__).resolve().parents[1]


class RiskOpportunityContractTests(unittest.TestCase):
    def test_exact_below_is_a_red_risk_not_an_opportunity(self) -> None:
        self.assertIn("EXACT_BELOW", RISK_STATUSES)
        self.assertNotIn("EXACT_BELOW", OPPORTUNITY_STATUSES)
        self.assertEqual("danger", STATUS_INFO["EXACT_BELOW"]["tone"])
        self.assertFalse(DataService._is_opportunity({"price_status": "EXACT_BELOW"}))

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
            ("EXACT_ABOVE", 0), ("EXACT_BELOW", 0), ("DATA_ERROR", 0),
            ("EXACT_LOWEST", 50), ("EXACT_TIED_LOWEST", 10),
            ("EXACT_IN_MARKET", 0), ("COMPARABLE_HIGHEST", 0),
            ("NOT_ANALYZED", 0),
        ]
        ordered = sorted(
            ({"price_status": status, "potential_margin_per_unit_kzt": potential}
             for status, potential in reversed(statuses)),
            key=DataService._business_priority,
        )
        self.assertEqual([status for status, _ in statuses], [item["price_status"] for item in ordered])
        self.assertFalse(DataService._is_opportunity({"price_status": "EXACT_BELOW", "potential_margin_per_unit_kzt": 999}))
        self.assertFalse(DataService._is_opportunity({"price_status": "NOT_ANALYZED", "potential_margin_per_unit_kzt": 999}))


class PostReleaseFrontendContractTests(unittest.TestCase):
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
