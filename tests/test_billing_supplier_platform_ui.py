import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BillingSupplierPlatformUiTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        cls.app = (
            ROOT / "app.py"
        ).read_text(
            encoding="utf-8-sig"
        )

        cls.template = (
            ROOT / "templates" / "platform.html"
        ).read_text(
            encoding="utf-8-sig"
        )

        cls.js = (
            ROOT / "static" / "js" / "platform.js"
        ).read_text(
            encoding="utf-8-sig"
        )

        cls.css = (
            ROOT / "static" / "css" / "platform.css"
        ).read_text(
            encoding="utf-8-sig"
        )

    def test_supplier_api_exists(self):
        self.assertIn(
            "/api/platform/billing/supplier-settings",
            self.app,
        )

        self.assertIn(
            "api_platform_billing_supplier_settings_get",
            self.app,
        )

        self.assertIn(
            "api_platform_billing_supplier_settings_put",
            self.app,
        )

        self.assertIn(
            'platform_roles_required("superadmin")',
            self.app,
        )

        self.assertIn(
            ".update_supplier_settings(",
            self.app,
        )

    def test_supplier_form_is_superadmin_only(self):
        self.assertIn(
            'id="billingSupplierForm"',
            self.template,
        )

        self.assertIn(
            'current_user.platform_role == "superadmin"',
            self.template,
        )

    def test_supplier_form_contains_business_fields(self):
        for name in (
            "name",
            "registration_number",
            "legal_address",
            "iban",
            "bank_name",
            "bic",
            "kbe",
            "payment_purpose_code",
            "invoice_prefix",
            "invoice_due_days",
            "service_name",
            "agreement_basis",
            "executor_name",
            "vat_enabled",
            "vat_rate",
        ):
            self.assertIn(
                f'name="{name}"',
                self.template,
            )

        for name in (
            "name",
            "registration_number",
            "legal_address",
            "iban",
            "bank_name",
            "bic",
            "kbe",
        ):
            self.assertIn(
                f'name="{name}"',
                self.template,
            )

        self.assertIn(
            'name="iban" autocomplete="off" disabled',
            self.template,
        )

    def test_supplier_ui_submits_only_invoice_configuration(self):
        self.assertIn(
            "const editableFields=[",
            self.js,
        )
        self.assertIn(
            "'payment_purpose_code'",
            self.js,
        )

        editable_fields = self.js.split(
            "const editableFields=[",
            1,
        )[1].split(
            "];",
            1,
        )[0]

        self.assertNotIn(
            "'iban'",
            editable_fields,
        )

    def test_supplier_ui_uses_platform_api(self):
        self.assertIn(
            "loadBillingSupplierSettings",
            self.js,
        )

        self.assertIn(
            "/api/platform/billing/supplier-settings",
            self.js,
        )

        self.assertIn(
            "missing_fields",
            self.js,
        )

        self.assertIn(
            "Spyon billing supplier settings UI",
            self.css,
        )


if __name__ == "__main__":
    unittest.main()
