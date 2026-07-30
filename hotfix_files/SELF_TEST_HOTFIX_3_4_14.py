from __future__ import annotations

import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def syntax_checks() -> None:
    for name in ("app.py", "data_service.py", "public_product_service.py"):
        py_compile.compile(str(ROOT / name), doraise=True)
        print(f"[OK] Python syntax: {name}")
    node = shutil.which("node")
    if node:
        for name in ("static/js/app.js", "static/js/help_content.js", "static/js/public_i18n.js"):
            subprocess.run([node, "--check", str(ROOT / name)], check=True)
            print(f"[OK] JavaScript syntax: {name}")
    else:
        print("[SKIP] node is not available; JavaScript syntax check skipped")


def static_ui_checks() -> None:
    app_html = text("templates/app.html")
    app_js = text("static/js/app.js")
    app_css = text("static/css/app.css")
    help_js = text("static/js/help_content.js")
    login_html = text("templates/login.html")
    auth_css = text("static/css/auth.css")
    legal_html = text("templates/legal.html")
    public_css = text("static/css/public.css")

    for element_id in (
        "platformFilter", "brandFilter", "statusFilter", "freshnessFilter",
        "productTypeFilter", "sizeFilter", "seasonFilter", "characteristicGroupFilter",
        "reportPlatforms", "reportBrand", "reportProductType", "reportSize",
        "reportSeason", "reportFreshness", "reportCharacteristicGroup",
    ):
        marker = f'id="{element_id}" multiple data-multi-select'
        check(marker in app_html, f"multi-select enabled: {element_id}")

    check("function initMultiSelect" in app_js and "multiValues('#sizeFilter')" in app_js,
          "multi-select controller and product payload are connected")
    check("queueReportLoad=debounce(loadReports" in app_js and "state.reportRequest" in app_js,
          "report refresh is debounced and protected from stale responses")
    check("report-loading-state" in app_html and "report-spinner" in app_css,
          "report loading state is present")
    check(app_html.index("generated-reports") < app_html.index("report-preview-panel"),
          "report table preview is at the bottom after generated files")
    check("FILTERED DATA" not in app_html and "MARKET POSITION" not in app_html,
          "redundant English report captions are removed")
    check("???" not in help_js and "\ufffd" not in help_js,
          "help content contains no corrupted replacement text")
    check("helpReturnFocus" in app_js and "helpFocusable" in app_js,
          "help drawer restores focus and traps keyboard navigation")
    check('localStorage.getItem("itp_theme")||"light"' in login_html,
          "login defaults to light while reading the shared theme preference")
    check('html[data-theme="light"] .auth-card' in auth_css,
          "light authentication design is defined")
    check("rel=\"icon\"" in legal_html and "class=\"site-header\"" in legal_html,
          "legal pages use favicon and the shared landing header")
    check("legal-document-head" in public_css and "language-select" in public_css,
          "legal and language controls have public-page styling")


def data_filter_checks() -> None:
    from data_service import DataService

    db_path = ROOT / "data" / "unityre_kaspi.db"
    ozon_path = ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db"
    if not db_path.exists():
        print("[SKIP] catalog database is not present in this source snapshot; real-data filter checks skipped")
        return
    check(db_path.exists(), "catalog database is available")
    service = DataService(db_path, "Unityre", ozon_path, seller_id="", halyk_seller_name="Unityre")
    rows = service.rows()
    check(len(rows) > 200, "catalog contains enough rows for pagination and filtering tests")

    brands: list[str] = []
    sizes: list[str] = []
    for row in rows:
        brand = str(row.get("brand") or "").strip()
        size = str(row.get("size") or "").strip()
        if brand and brand.casefold() not in {item.casefold() for item in brands} and len(brands) < 2:
            brands.append(brand)
        if size and size.casefold() not in {item.casefold() for item in sizes} and len(sizes) < 2:
            sizes.append(size)
        if len(brands) == 2 and len(sizes) == 2:
            break
    check(len(brands) == 2 and len(sizes) == 2, "test brands and sizes were selected")

    filters_list = {
        "platforms": ["kaspi", "ozon"],
        "brand": brands,
        "size": sizes,
        "scope": "all",
    }
    list_matches = [row for row in rows if service._matches(row, filters_list)]
    check(all(str(row.get("platform")) in {"kaspi", "ozon"} for row in list_matches),
          "multiple marketplace filter is enforced")
    check(all(str(row.get("brand") or "").casefold() in {item.casefold() for item in brands} for row in list_matches),
          "multiple brand filter is enforced")
    check(all(str(row.get("size") or "").casefold() in {item.casefold() for item in sizes} for row in list_matches),
          "multiple size filter is enforced")

    filters_csv = {
        "platforms": "kaspi,ozon",
        "brand": ",".join(brands),
        "size": ",".join(sizes),
        "scope": "all",
    }
    csv_codes = [str(row.get("product_code")) for row in rows if service._matches(row, filters_csv)]
    list_codes = [str(row.get("product_code")) for row in list_matches]
    check(csv_codes == list_codes, "URL comma-separated filters match array filters")


def legal_checks() -> None:
    from public_product_service import CONSENT_VERSION, PublicProductService

    service = PublicProductService(ROOT / "data" / "unityre_kaspi.db")
    for lang in ("ru", "kk", "en"):
        for code in ("privacy", "terms", "cookies", "consent", "offer"):
            doc = service.legal_document(code, lang)
            combined = " ".join([doc["title"], doc["lead"]] + [p for _, ps in doc["sections"] for p in ps])
            check("{" not in combined and "}" not in combined,
                  f"legal placeholders resolved: {lang}/{code}")
            check(doc["consent_version"] == CONSENT_VERSION and bool(doc["effective_date"]),
                  f"legal version and effective date present: {lang}/{code}")
    privacy = service.legal_document("privacy", "ru")
    terms = service.legal_document("terms", "ru")
    check(len(privacy["sections"]) >= 9, "privacy policy covers key processing topics")
    check(len(terms["sections"]) >= 9, "terms cover access, analytics, external services and liability")
    check(isinstance(privacy["publication_ready"], bool) and isinstance(privacy["missing_fields"], list),
          "legal publication readiness is calculated")


def main() -> int:
    check(text("VERSION.txt").strip() == "3.4.14", "version is 3.4.14")
    syntax_checks()
    static_ui_checks()
    data_filter_checks()
    legal_checks()
    print("SELF TEST 3.4.14: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SELF TEST 3.4.14: FAILED: {exc}", file=sys.stderr)
        raise
