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

    def test_payment_proof_can_be_replaced_during_review(
        self,
    ) -> None:
        guard_start = self.source.index(
            "const canUploadProof="
        )

        guard_end = self.source.index(
            "const proofStatusLabel=",
            guard_start,
        )

        guard = self.source[
            guard_start:
            guard_end
        ]

        self.assertIn(
            "billingStatus==='awaiting_payment'",
            guard,
        )

        self.assertIn(
            "billingStatus==='payment_review'",
            guard,
        )

        self.assertIn(
            "billingStatus==='payment_rejected'",
            guard,
        )

        self.assertIn(
            r"\u0417\u0430\u043c\u0435\u043d\u0438\u0442\u044c \u043f\u043b\u0430\u0442\u0451\u0436\u043d\u044b\u0439 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442",
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
