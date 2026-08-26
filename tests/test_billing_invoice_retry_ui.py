from __future__ import annotations

import ast
import unittest
from pathlib import Path


class BillingInvoiceRetryUITests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(
        cls,
    ) -> None:
        root = (
            Path(__file__)
            .resolve()
            .parents[1]
        )

        app_path = (
            root
            / "app.py"
        )

        cls.app_source = (
            app_path.read_text(
                encoding="utf-8"
            )
        )

        cls.js_source = (
            root
            / "static"
            / "js"
            / "app.js"
        ).read_text(
            encoding="utf-8"
        )

        tree = ast.parse(
            cls.app_source
        )

        lines = (
            cls.app_source
            .splitlines()
        )

        route = next(
            node
            for node in ast.walk(tree)
            if (
                isinstance(
                    node,
                    ast.FunctionDef,
                )
                and node.name
                == "api_subscription_invoice_create"
            )
        )

        cls.route_source = "\n".join(
            lines[
                route.lineno - 1:
                route.end_lineno
            ]
        )

    def test_missing_invoice_pdf_can_be_retried(
        self,
    ) -> None:
        for token in (
            "retry_pdf",
            "existing_invoice",
            '"awaiting_payment"',
            '"payment_review"',
            '"payment_rejected"',
            "billing.generate_invoice_pdf",
        ):
            self.assertIn(
                token,
                self.route_source,
            )

        self.assertIn(
            "retrySubscriptionInvoicePdf",
            self.js_source,
        )

        self.assertIn(
            "invoice.months_count",
            self.js_source,
        )

        self.assertIn(
            "'/api/subscription/invoice'",
            self.js_source,
        )


if __name__ == "__main__":
    unittest.main()
