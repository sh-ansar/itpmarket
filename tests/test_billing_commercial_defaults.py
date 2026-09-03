from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from billing_service import BillingService
from schema import ensure_database
from subscription_service import SubscriptionService


class BillingCommercialDefaultsTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.folder = (
            tempfile.TemporaryDirectory(
                prefix="billing_defaults_"
            )
        )

        self.db_path = (
            Path(self.folder.name)
            / "app.db"
        )

        ensure_database(
            self.db_path
        )

        self.subscriptions = (
            SubscriptionService(
                self.db_path
            )
        )

        self.billing = (
            BillingService(
                self.db_path
            )
        )

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_default_vat_is_kazakhstan_16_percent(
        self,
    ) -> None:
        supplier = (
            self.billing
            .supplier_settings()
        )

        self.assertTrue(
            supplier["vat_enabled"]
        )

        self.assertEqual(
            16.0,
            float(
                supplier["vat_rate"]
            ),
        )

    def test_trial_remains_three_days(
        self,
    ) -> None:
        plans = {
            item["code"]: item
            for item
            in self.subscriptions.plans(
                public_only=True
            )
        }

        trial = plans["trial"]

        self.assertEqual(
            "day",
            trial[
                "billing_period_unit"
            ],
        )

        self.assertEqual(
            3,
            trial[
                "billing_period_count"
            ],
        )

    def test_paid_default_plans_are_monthly(
        self,
    ) -> None:
        plans = {
            item["code"]: item
            for item
            in self.subscriptions.plans(
                public_only=True
            )
        }

        for code in (
            "starter",
            "growth",
            "business",
        ):
            with self.subTest(
                plan=code
            ):
                self.assertEqual(
                    "month",
                    plans[code][
                        "billing_period_unit"
                    ],
                )

                self.assertEqual(
                    1,
                    plans[code][
                        "billing_period_count"
                    ],
                )


if __name__ == "__main__":
    unittest.main()
