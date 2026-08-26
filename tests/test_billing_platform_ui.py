from __future__ import annotations

import unittest
from pathlib import Path


class BillingPlatformUITests(
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

        cls.template = (
            root
            / "templates"
            / "platform.html"
        ).read_text(
            encoding="utf-8"
        )

        cls.javascript = (
            root
            / "static"
            / "js"
            / "platform.js"
        ).read_text(
            encoding="utf-8"
        )

        cls.css = (
            root
            / "static"
            / "css"
            / "platform.css"
        ).read_text(
            encoding="utf-8"
        )

    def test_payment_review_queue_exists(
        self,
    ) -> None:
        self.assertIn(
            'id="paymentReviewTableBody"',
            self.template,
        )

        self.assertIn(
            "payment_review_items",
            self.javascript,
        )

        self.assertIn(
            "/api/platform/billing/payments",
            self.javascript,
        )

    def test_payment_documents_use_platform_routes(
        self,
    ) -> None:
        self.assertIn(
            "/pdf",
            self.javascript,
        )

        self.assertIn(
            "/payment-proof",
            self.javascript,
        )

        self.assertIn(
            "billing-document-link",
            self.javascript,
        )

    def test_payment_decision_contract(
        self,
    ) -> None:
        self.assertIn(
            'data-billing-action="confirm"',
            self.javascript,
        )

        self.assertIn(
            'data-billing-action="reject"',
            self.javascript,
        )

        self.assertIn(
            "review_note:note",
            self.javascript,
        )

        self.assertIn(
            "billingDecisionForm",
            self.javascript,
        )

        self.assertIn(
            'id="billingDecisionModal"',
            self.template,
        )

    def test_payment_review_styles_exist(
        self,
    ) -> None:
        for token in (
            ".billing-review-table",
            ".billing-status",
            ".billing-review-actions",
            ".billing-decision-actions",
        ):
            self.assertIn(
                token,
                self.css,
            )


if __name__ == "__main__":
    unittest.main()
