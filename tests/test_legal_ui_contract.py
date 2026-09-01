from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LegalUIContractTests(
    unittest.TestCase
):
    def test_user_reacceptance_requires_checkbox_and_server_flag(
        self,
    ) -> None:
        source = (
            ROOT
            / "static"
            / "js"
            / "app.js"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "data-legal-confirm",
            source,
        )

        self.assertIn(
            "accepted:true",
            source,
        )

        self.assertIn(
            "window.location.reload()",
            source,
        )

    def test_dynamic_pdf_is_not_unconditionally_rendered(
        self,
    ) -> None:
        source = (
            ROOT
            / "static"
            / "js"
            / "app.js"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "item.pdf_available",
            source,
        )

    def test_platform_editor_exposes_version_lifecycle_fields(
        self,
    ) -> None:
        template = (
            ROOT
            / "templates"
            / "platform.html"
        ).read_text(
            encoding="utf-8"
        )

        for value in (
            'name="effective_at"',
            'name="requires_acceptance"',
            'id="cancelLegalDraftEdit"',
            'colspan="7"',
        ):
            self.assertIn(
                value,
                template,
            )

    def test_platform_draft_can_be_edited_before_publish(
        self,
    ) -> None:
        source = (
            ROOT
            / "static"
            / "js"
            / "platform.js"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "data-edit-legal",
            source,
        )

        self.assertIn(
            "method:",
            source,
        )

        self.assertIn(
            "?`/api/platform/legal-documents/drafts/${versionId}`",
            source,
        )

    def test_platform_legal_audit_uses_existing_endpoint(
        self,
    ) -> None:
        template = (
            ROOT
            / "templates"
            / "platform.html"
        ).read_text(
            encoding="utf-8"
        )
        source = (
            ROOT
            / "static"
            / "js"
            / "platform.js"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'id="legalAcceptanceModal"',
            template,
        )
        self.assertIn(
            "data-legal-acceptances",
            source,
        )
        self.assertIn(
            "/api/platform/legal-documents/acceptances?version_id=",
            source,
        )
        self.assertNotIn(
            "row.acceptance_text",
            source,
        )

    def test_platform_legal_documents_label_is_consistent(
        self,
    ) -> None:
        template = (
            ROOT
            / "templates"
            / "platform.html"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "{% elif platform_section == 'legal-documents' %}Юридические документы",
            template,
        )
        self.assertIn(
            ">Юридические документы</a>",
            template,
        )


if __name__ == "__main__":
    unittest.main()
