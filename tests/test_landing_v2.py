from __future__ import annotations

import unittest
from pathlib import Path

import app as webapp


ROOT = Path(__file__).resolve().parents[1]


class LandingV2Tests(unittest.TestCase):
    def test_landing_renders_primary_content_and_dynamic_plan_cta(self) -> None:
        response = webapp.app.test_client().get("/?lang=en")
        page = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("<h1", page)
        self.assertIn("Аналитика и управление продажами на маркетплейсах", page)
        self.assertIn("url_for('registration', plan=plan.code)", (ROOT / "templates" / "landing.html").read_text(encoding="utf-8"))
        self.assertIn('data-li18n="choose_plan"', page)

    def test_landing_has_indexing_and_accessibility_basics(self) -> None:
        source = (ROOT / "templates" / "landing.html").read_text(encoding="utf-8")
        self.assertIn('rel="canonical"', source)
        self.assertIn('hreflang="en"', source)
        self.assertIn('application/ld+json', source)
        self.assertIn('aria-controls="landingNav"', source)
        self.assertIn('<details>', source)
        self.assertIn('spyon-og-v2-1200x630.png', source)
        self.assertIn('og:url', source)
        self.assertIn('data-radar="kaspi"', source)
        self.assertIn('prefers-reduced-motion', (ROOT / "static" / "css" / "landing-v2.css").read_text(encoding="utf-8"))

    def test_public_crawl_files_are_served(self) -> None:
        client = webapp.app.test_client()
        robots = client.get("/robots.txt")
        sitemap = client.get("/sitemap.xml")
        self.assertEqual(200, robots.status_code)
        self.assertIn("Sitemap:", robots.get_data(as_text=True))
        self.assertEqual(200, sitemap.status_code)
        self.assertIn("<urlset", sitemap.get_data(as_text=True))

    def test_landing_locales_and_asset_do_not_include_marketplace_logo_files(self) -> None:
        script = (ROOT / "static" / "js" / "landing-v2.js").read_text(encoding="utf-8")
        for locale in ("const ru", "const en", "const kk"):
            self.assertIn(locale, script)
        self.assertTrue((ROOT / "static" / "images" / "spyon-og-v2-1200x630.png").is_file())
        self.assertNotIn("kaspi-logo", script.casefold())
        self.assertNotIn("wildberries-logo", script.casefold())

    def test_refinement_module_keeps_demo_anonymous_and_removes_demo_badge(self) -> None:
        source = (ROOT / "static" / "js" / "landing-v2-refine.js").read_text(encoding="utf-8")
        self.assertIn("radar-signal span:first-child", source)
        self.assertIn("product-preview", source)
        self.assertIn("spyon-mark-story", source)
        self.assertIn('class="mark-spy"', source)
        self.assertIn('class="mark-on"', source)
        self.assertIn('mark-letter mark-s', source)
        self.assertIn('mark-letter mark-y', source)
        self.assertIn('mark-letter mark-o', source)
        self.assertIn('mark-letter mark-n', source)
        self.assertIn("story-final-mark", source)
        self.assertNotIn("cloneNode", source)
        self.assertIn("product-scope-tabs", source)
        self.assertIn("product-demo-table", source)
        self.assertIn("Download report", source)
        self.assertNotIn("ТОО ", source)
        self.assertNotIn("ИП ", source)

    def test_refinement_restores_bar_chart_and_uses_product_workspace_layout(self) -> None:
        styles = (ROOT / "static" / "css" / "landing-v2.css").read_text(encoding="utf-8")
        self.assertIn(".chart i{display:block", styles)
        self.assertIn(".chart:before{display:none}", styles)
        self.assertIn(".product-demo-command", styles)
        self.assertIn(".demo-range", styles)
        self.assertIn("story-final-brand", styles)
        self.assertIn("story-s-enter 1.05s", styles)
        self.assertIn("story-logo-reveal .65s", styles)
        self.assertIn(".spyon-mark-story .story-spy,.spyon-mark-story .story-on,.spyon-mark-story .story-eye{display:none!important}", styles)
        self.assertIn("mark-s-travel 1.1s", styles)
        self.assertIn("mark-final-eye .43s", styles)

    def test_versioned_legal_page_uses_shared_public_shell(self) -> None:
        source = (ROOT / "templates" / "legal_versioned.html").read_text(encoding="utf-8")
        self.assertIn('class="legal-layout"', source)
        self.assertIn('class="legal-nav"', source)
        self.assertIn('data-public-theme', source)
        self.assertIn("document='consent'", source)

    def test_legal_dark_mode_polish_is_loaded_by_both_templates(self) -> None:
        for filename in ("legal.html", "legal_versioned.html"):
            source = (ROOT / "templates" / filename).read_text(encoding="utf-8")
            self.assertIn("css/legal-v2.css", source)
        styles = (ROOT / "static" / "css" / "legal-v2.css").read_text(encoding="utf-8")
        self.assertIn("html[data-theme=dark]", styles)
        self.assertIn("legal-warning", styles)


if __name__ == "__main__":
    unittest.main()
