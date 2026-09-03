from __future__ import annotations

import json
import unittest
from pathlib import Path


class RegistrationCompletionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

        cls.template = (
            cls.root
            / "templates"
            / "registration_complete.html"
        ).read_text(encoding="utf-8")

        cls.css = (
            cls.root
            / "static"
            / "css"
            / "public_onboarding.css"
        ).read_text(encoding="utf-8")

    def test_completion_has_real_application_timeline(self) -> None:
        with open(
            "templates/registration_complete.html",
            encoding="utf-8",
        ) as handle:
            template = handle.read()

        self.assertIn(
            'class="application-timeline"',
            template,
        )

        for key in (
            "completion_step_submitted",
            "completion_step_review",
            "completion_step_payment",
            "completion_step_activation",
        ):
            self.assertIn(
                f'data-pi18n="{key}"',
                template,
            )

        self.assertIn(
            "{% if result.payment_required %}",
            template,
        )

        self.assertNotIn(
            'data-pi18n="completion_step_company_approval"',
            template,
        )

        self.assertNotIn(
            "Review by our team",
            template,
        )

        self.assertNotIn(
            "Company approval",
            template,
        )

        self.assertNotIn(
            "result.recovery_code",
            template,
        )

    def test_completion_displays_selected_plan_without_registration_marketplaces(self) -> None:
        self.assertIn(
            "data-plan-code=",
            self.template,
        )

        self.assertNotIn("data-marketplace-code=", self.template)
        self.assertNotIn("result.marketplaces", self.template)

    def test_completion_displays_account_email(self) -> None:
        self.assertIn(
            "result.user.email",
            self.template,
        )

        self.assertIn(
            "completion_email",
            self.template,
        )

    def test_completion_has_no_damaged_visible_symbols(self) -> None:
        standalone_questions = [
            line
            for line in self.template.splitlines()
            if line.strip() == "?"
        ]

        self.assertFalse(
            standalone_questions,
            "Standalone ? found in completion UI",
        )

        self.assertIn(
            "&middot;",
            self.template,
        )

        self.assertIn(
            "&#10003;",
            self.template,
        )

    def test_completion_is_safe_without_result(self) -> None:
        self.assertIn(
            "{% if result %}",
            self.template,
        )

        self.assertIn(
            "{% if request_id %}",
            self.template,
        )

    def test_completion_has_no_old_public_role_name(self) -> None:
        text = self.template.casefold()

        self.assertNotIn(
            "\u0441\u0443\u043f\u0435\u0440-"
            "\u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
            text,
        )

        self.assertNotIn(
            "super administrator",
            text,
        )

    def test_completion_strings_exist_in_every_locale(self) -> None:
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

        values, _ = (
            json.JSONDecoder().raw_decode(
                source[len(prefix):]
            )
        )

        required = {
            "completion_eyebrow",
            "completion_title",
            "completion_intro",
            "completion_company",
            "completion_request",
            "completion_plan",
            "completion_status",
            "completion_status_review",
            "completion_email",
            "completion_marketplaces",
            "completion_step_submitted",
            "completion_step_review",
            "completion_step_company_approval",
            "completion_step_payment",
            "completion_step_activation",
            "completion_next_action",
            "completion_open_system",
            "completion_home",
        }

        for language in (
            "ru",
            "kk",
            "en",
        ):
            missing = (
                required
                - set(values[language])
            )

            self.assertFalse(
                missing,
                f"{language}: missing {sorted(missing)}",
            )


if __name__ == "__main__":
    unittest.main()
