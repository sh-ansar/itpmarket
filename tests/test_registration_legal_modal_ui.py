from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RegistrationLegalModalUiTests(unittest.TestCase):

    def test_modal_has_single_svg_close_control(self) -> None:
        template = (
            ROOT / "templates" / "register.html"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            1,
            template.count('id="closeLegalModal"'),
        )
        self.assertIn(
            'class="legal-modal-close"',
            template,
        )
        self.assertIn(
            "icons/close.svg",
            template,
        )
        self.assertNotIn(
            'id="closeLegalModalFooter"',
            template,
        )

    def test_modal_does_not_render_permanent_unavailable_message(self) -> None:
        source = (
            ROOT
            / "static"
            / "js"
            / "registration_wizard.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "???????? ???????? ??????????",
            source,
        )
        self.assertNotIn(
            'document.createElement("p")',
            source[source.find("function openLegalModal"):],
        )
        self.assertNotIn(
            "closeLegalModalFooter",
            source,
        )

    def test_close_control_has_dedicated_style(self) -> None:
        source = (
            ROOT
            / "static"
            / "css"
            / "registration_guide.css"
        ).read_text(encoding="utf-8")

        self.assertIn(
            ".legal-modal-close {",
            source,
        )


if __name__ == "__main__":
    unittest.main()
