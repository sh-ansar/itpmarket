from __future__ import annotations

import unittest
from pathlib import Path


class BillingPaymentProofUITests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls) -> None:
        root = (
            Path(__file__)
            .resolve()
            .parents[1]
        )

        cls.source = (
            root
            / "static"
            / "js"
            / "app.js"
        ).read_text(
            encoding="utf-8"
        )

    def test_api_preserves_form_data(
        self,
    ) -> None:
        self.assertIn(
            "opts.body instanceof FormData",
            self.source,
        )

        self.assertIn(
            "!formDataBody",
            self.source,
        )

    def test_payment_proof_upload_contract(
        self,
    ) -> None:
        self.assertIn(
            "subscriptionPaymentProofFile",
            self.source,
        )

        self.assertIn(
            "form.append(",
            self.source,
        )

        self.assertIn(
            "'file'",
            self.source,
        )

        self.assertIn(
            "/payment-proof`",
            self.source,
        )

        self.assertIn(
            "10*1024*1024",
            self.source,
        )

    def test_payment_proof_ui_handles_review_and_rejection(
        self,
    ) -> None:
        self.assertIn(
            "payment_review",
            self.source,
        )

        self.assertIn(
            "payment_rejected",
            self.source,
        )

        self.assertIn(
            "paymentProof.review_note",
            self.source,
        )

        self.assertIn(
            "downloadSubscriptionPaymentProof",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
