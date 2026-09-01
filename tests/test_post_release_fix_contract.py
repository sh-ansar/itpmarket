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

    def test_tied_lowest_requires_proven_positive_headroom(self) -> None:
        self.assertFalse(DataService._is_opportunity({
            "price_status": "EXACT_TIED_LOWEST", "potential_margin_per_unit_kzt": 0,
        }))
        self.assertTrue(DataService._is_opportunity({
            "price_status": "EXACT_TIED_LOWEST", "potential_margin_per_unit_kzt": 1,
        }))


class PostReleaseFrontendContractTests(unittest.TestCase):
    def test_marketplace_source_actions_use_spyon_modal_not_browser_dialogs(self) -> None:
        source = (ROOT / "static" / "js" / "marketplace_settings.js").read_text(encoding="utf-8")
        self.assertIn("marketplaceSourceModal", source)
        self.assertIn("data-modal-verify", source)
        self.assertNotIn("window.prompt", source)
        self.assertNotIn("window.confirm", source)

    def test_first_products_view_defaults_to_risks(self) -> None:
        source = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "app.html").read_text(encoding="utf-8")
        self.assertIn("scope:'risks'", source)
        self.assertIn('class="active" data-scope="risks"', template)

    def test_static_assets_use_the_deployment_sha_pipeline(self) -> None:
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        deploy_source = (ROOT / "deploy" / "windows" / "post-update-production.ps1").read_text(encoding="utf-8")
        self.assertIn("deployment_asset_version", app_source)
        self.assertIn("deployment-sha", app_source)
        self.assertIn("deployment-sha", deploy_source)
        self.assertIn("$TargetSha", deploy_source)


if __name__ == "__main__":
    unittest.main()
