from __future__ import annotations

import json
import unittest
from pathlib import Path


class RegistrationWizardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.template = (
            cls.root / "templates" / "register.html"
        ).read_text(encoding="utf-8")
        cls.script = (
            cls.root
            / "static"
            / "js"
            / "registration_wizard.js"
        ).read_text(encoding="utf-8")
        cls.css = (
            cls.root
            / "static"
            / "css"
            / "registration_guide.css"
        ).read_text(encoding="utf-8")

    def test_registration_has_company_plan_and_account_steps_only(self) -> None:
        self.assertEqual(
            3,
            self.template.count(
                "data-registration-step"
            ),
        )

        company = self.template.index(
            'data-step-code="company"'
        )
        plan = self.template.index(
            'data-step-code="plan"'
        )
        account = self.template.index(
            'data-step-code="account"'
        )

        self.assertNotIn('data-step-code="marketplaces"', self.template)
        self.assertNotIn('name="marketplaces"', self.template)
        self.assertLess(company, plan)
        self.assertLess(plan, account)

    def test_required_and_optional_ui_contract(self) -> None:
        self.assertIn(
            "required-fields-note",
            self.template,
        )
        self.assertIn(
            "register_optional",
            self.template,
        )
        self.assertIn(
            "input[required]",
            self.css,
        )
        self.assertIn(
            ".wizard-section[hidden]",
            self.css,
        )

    def test_company_ux_controls_exist(self) -> None:
        self.assertIn(
            'class="phone-composite"',
            self.template,
        )
        self.assertIn(
            'id="registrationSameAddress"',
            self.template,
        )
        self.assertIn(
            'id="registrationAccountEmail"',
            self.template,
        )

    def test_password_visibility_controls_exist(self) -> None:
        self.assertEqual(
            2,
            self.template.count(
                "data-toggle-password="
            ),
        )
        self.assertIn(
            "icons/eye.svg",
            self.template,
        )
        self.assertIn(
            "register_show_password",
            self.script,
        )

    def test_plan_recommendation_is_catalog_based(self) -> None:
        self.assertIn(
            "data-position-limit",
            self.template,
        )
        self.assertIn(
            "updateRecommendation",
            self.script,
        )
        self.assertIn(
            "is-recommended",
            self.script,
        )
        self.assertNotIn("selectedMarketplaceCount", self.script)
        self.assertNotIn("marketplaceValid", self.script)
        self.assertIn("!planTouched", self.script)

    def test_wizard_submits_only_after_all_steps_validate(self) -> None:
        self.assertIn(
            "validateAll",
            self.script,
        )
        self.assertIn(
            "form.requestSubmit()",
            self.script,
        )

    def test_public_locales_have_new_wizard_strings(self) -> None:
        source = (
            self.root
            / "static"
            / "js"
            / "public_i18n.js"
        ).read_text(encoding="utf-8")

        prefix = "window.ITP_PUBLIC_LOCALES="
        self.assertTrue(
            source.startswith(prefix)
        )

        values, _ = json.JSONDecoder().raw_decode(
            source[len(prefix):]
        )

        required = {
            "register_required_fields_note",
            "register_optional",
            "register_same_address",
            "register_plan_recommendation_title",
            "register_plan_recommendation_wait",
            "register_plan_recommendation_fit",
            "register_plan_recommendation_extra",
            "register_recommended_badge",
            "register_account_email",
            "register_account_email_hint",
            "register_show_password",
            "register_hide_password",
            "register_password_mismatch",
            "register_guide_step",
            "register_guide_next",
        }

        for language in ("ru", "kk", "en"):
            missing = sorted(
                required - set(values[language])
            )
            self.assertFalse(
                missing,
                f"{language}: missing {missing}",
            )

        ru_text = " ".join(
            str(value).casefold()
            for value in values["ru"].values()
        )

        self.assertNotIn(
            "\u0441\u0443\u043f\u0435\u0440-\u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
            ru_text,
        )


if __name__ == "__main__":
    unittest.main()
