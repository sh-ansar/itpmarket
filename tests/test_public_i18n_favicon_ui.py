from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicI18nFaviconUiTests(unittest.TestCase):

    def test_registration_login_uses_public_i18n(self) -> None:
        template = (
            ROOT / "templates" / "register.html"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'data-pi18n="public_login"',
            template,
        )

    def test_public_login_exists_in_all_locales(self) -> None:
        source = (
            ROOT
            / "static"
            / "js"
            / "public_i18n_runtime.js"
        ).read_text(encoding="utf-8")

        expected = {
            "ru": "\u0412\u043e\u0439\u0442\u0438",
            "kk": "\u041a\u0456\u0440\u0443",
            "en": "Login",
        }

        for lang, value in expected.items():
            marker = f"Object.assign(extra.{lang}, {{"
            start = source.find(marker)
            self.assertNotEqual(
                -1,
                start,
                f"missing locale block: {lang}",
            )

            end = source.find("});", start)
            self.assertNotEqual(-1, end)

            block = source[start:end]

            self.assertIn(
                f"public_login:'{value}'",
                block,
            )

    def test_standalone_templates_have_favicon(self) -> None:
        templates = [
            "legal_versioned.html",
            "platform.html",
            "register.html",
            "registration_complete.html",
        ]

        for filename in templates:
            source = (
                ROOT / "templates" / filename
            ).read_text(encoding="utf-8")

            self.assertIn(
                'rel="icon"',
                source,
                filename,
            )

            self.assertIn(
                "images/spyon-logo.svg",
                source,
                filename,
            )


if __name__ == "__main__":
    unittest.main()
