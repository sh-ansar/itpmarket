from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

import app as webapp


ROOT = Path(__file__).resolve().parents[1]


class LandingV2Tests(unittest.TestCase):
    def test_public_i18n_key_parity_covers_landing_and_legal_shells(self) -> None:
        landing_source = (ROOT / "static" / "js" / "landing-v2.js").read_text(encoding="utf-8")
        landing_probe = """const fs=require('fs'),vm=require('vm');const node={textContent:'',setAttribute(){},addEventListener(){},classList:{add(){},toggle(){return false}}};const document={documentElement:{lang:'ru'},querySelector:()=>node,querySelectorAll:()=>[],addEventListener(){}};const context={window:{ITP_PUBLIC_LOCALE:'ru'},document,matchMedia:()=>({matches:true}),localStorage:{getItem:()=>null},console};vm.runInNewContext(fs.readFileSync('static/js/landing-v2.js','utf8').replace(/\}\)\(\);\s*$/,'globalThis.__locales={ru,en,kk};})();'),context);console.log(JSON.stringify(context.__locales));"""
        landing_locales = json.loads(subprocess.check_output(["node", "-e", landing_probe], cwd=ROOT, text=True, encoding="utf-8"))
        self.assertEqual(set(landing_locales["ru"]), set(landing_locales["kk"]))
        self.assertEqual(set(landing_locales["ru"]), set(landing_locales["en"]))
        template = (ROOT / "templates" / "landing.html").read_text(encoding="utf-8")
        required = set(re.findall(r'data-li18n(?:-aria)?="([^"]+)"', template))
        self.assertTrue(required <= set(landing_locales["ru"]))

        public_source = (ROOT / "static" / "js" / "public_i18n.js").read_text(encoding="utf-8")
        public_match = re.search(r'window\.ITP_PUBLIC_LOCALES=(\{.*\});\s*$', public_source, re.S)
        self.assertIsNotNone(public_match)
        public_locales = json.loads(public_match.group(1))
        self.assertEqual(set(public_locales["ru"]), set(public_locales["kk"]))
        self.assertEqual(set(public_locales["ru"]), set(public_locales["en"]))
        runtime_probe = """const fs=require('fs'),vm=require('vm');const source=fs.readFileSync('static/js/public_i18n_runtime.js','utf8').replace('const locales=window.ITP_PUBLIC_LOCALES=window.ITP_PUBLIC_LOCALES||{};','globalThis.__extra=extra;return;const locales=window.ITP_PUBLIC_LOCALES=window.ITP_PUBLIC_LOCALES||{};');const context={window:{},document:{},localStorage:{},console};vm.runInNewContext(source,context);console.log(JSON.stringify(context.__extra));"""
        extra = json.loads(subprocess.check_output(["node", "-e", runtime_probe], cwd=ROOT, text=True, encoding="utf-8"))
        self.assertEqual(set(extra["ru"]), set(extra["kk"]))
        self.assertEqual(set(extra["ru"]), set(extra["en"]))
        public_keys = set(public_locales["ru"]) | set(extra["ru"])
        for filename in ("landing.html", "legal.html", "legal_versioned.html", "register.html", "registration_complete.html"):
            source = (ROOT / "templates" / filename).read_text(encoding="utf-8")
            self.assertTrue(set(re.findall(r'data-pi18n="([^"]+)"', source)) <= public_keys)

        refine_probe = """const fs=require('fs'),vm=require('vm');const source=fs.readFileSync('static/js/landing-v2-refine.js','utf8').replace('  const catalog = [','  globalThis.__words=words; return;\\n  const catalog = [');const context={window:{},globalThis:{},document:{querySelector(){return null},querySelectorAll(){return []},addEventListener(){}},localStorage:{getItem:()=>null},console};vm.runInNewContext(source,context);console.log(JSON.stringify(context.globalThis.__words));"""
        refine_words = json.loads(subprocess.check_output(["node", "-e", refine_probe], cwd=ROOT, text=True, encoding="utf-8"))
        self.assertEqual(refine_words["ru"], refine_words["ru"])
        self.assertEqual(set(refine_words["ru"]), set(refine_words["kk"]))
        self.assertEqual(set(refine_words["ru"]), set(refine_words["en"]))
        for nested in ("statusLabels", "signals", "recommendations"):
            self.assertEqual(set(refine_words["ru"][nested]), set(refine_words["kk"][nested]))
            self.assertEqual(set(refine_words["ru"][nested]), set(refine_words["en"][nested]))
    def test_landing_renders_primary_content_and_dynamic_plan_cta(self) -> None:
        response = webapp.app.test_client().get("/?lang=en")
        page = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("<h1", page)
        self.assertIn("\u0410\u043d\u0430\u043b\u0438\u0442\u0438\u043a\u0430 \u0438 \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u043f\u0440\u043e\u0434\u0430\u0436\u0430\u043c\u0438 \u043d\u0430 \u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441\u0430\u0445", page)
        self.assertNotIn("\u0420\u0452\u0420\u0405\u0420\u00b0", page)
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

    def test_landing_utf8_fallback_copy_renders_for_all_public_locales(self) -> None:
        client = webapp.app.test_client()
        for locale in ("ru", "kk", "en"):
            response = client.get(f"/?lang={locale}")
            page = response.get_data(as_text=True)
            self.assertEqual(200, response.status_code)
            self.assertIn("\u041c\u0435\u043d\u044e", page)
            self.assertIn("\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u044c", page)
            self.assertNotIn("\u0420\u045a\u0420\u00b5\u0420\u0405", page)
            self.assertNotIn("\u0420\u0459\u0420\u043e\u0420\u0405", page)

    def test_demo_price_position_semantics_and_copy_are_consistent(self) -> None:
        source = (ROOT / "static" / "js" / "landing-v2-refine.js").read_text(encoding="utf-8")
        self.assertIn("rank: '8 / 8'", source)
        self.assertIn("rank: '6 / 6'", source)
        self.assertIn("rank: '1 / 7'", source)
        self.assertIn("rank: '3 / 5'", source)
        self.assertIn("pricePosition", source)
        self.assertIn("1 = best price position", source)
        self.assertIn("rankContext", source)

    def test_landing_polish_keeps_brand_motion_isolated_and_adds_demo_interactions(self) -> None:
        template = (ROOT / "templates" / "landing.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "landing-v2-refine.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "css" / "landing-v2-polish.css").read_text(encoding="utf-8")
        self.assertIn("css/landing-v2-polish.css", template)
        self.assertNotIn("spyon-v7-", styles)
        self.assertIn("renderPipeline", script)
        self.assertIn("openProductModal", script)
        self.assertIn("data-product-index", script)
        self.assertIn("product-demo-cards", styles)
        self.assertIn("prefers-reduced-motion", styles)

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
        self.assertIn("document='personal-data-consent'", source)

    def test_legal_dark_mode_polish_is_loaded_by_both_templates(self) -> None:
        for filename in ("legal.html", "legal_versioned.html"):
            source = (ROOT / "templates" / filename).read_text(encoding="utf-8")
            self.assertIn("css/legal-v2.css", source)
        styles = (ROOT / "static" / "css" / "legal-v2.css").read_text(encoding="utf-8")
        self.assertIn("html[data-theme=dark]", styles)
        self.assertIn("legal-warning", styles)


if __name__ == "__main__":
    unittest.main()
