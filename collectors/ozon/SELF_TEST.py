#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ozon_probe_core import parse_catalog_html, parse_product_json
from ozon_validation_core import normalize_for_import, seller_match_status
from registry import Registry, now_iso
from reporting import generate_dashboard

ROOT = Path(__file__).resolve().parent
fixtures = ROOT / "fixtures"

catalog, next_page = parse_catalog_html(
    (fixtures / "category.html").read_text(encoding="utf-8", errors="replace"),
    "https://www.ozon.ru/category/shiny-zimnie-8803/?__rr=1",
)
assert len(catalog) >= 8, len(catalog)
assert next_page, "next page missing"

api = json.loads((fixtures / "api_1668280585.json").read_text(encoding="utf-8"))
product = next(item for item in catalog if item["article"] == "1668280585")
detail = parse_product_json("1668280585", api, product)
assert detail["success"], detail
assert detail["seller_name"] == "ПИН АВТО", detail
assert detail["card_price"] == 6077, detail
assert detail["regular_price"] == 6752, detail
assert detail["tire_size"] == "195/65 R15", detail

detail["detail_success"] = True
detail["detail_status"] = "API_OK"
detail["overall_status"] = "COMPLETE"
detail["seller_match_status"] = seller_match_status(detail, "ПИН АВТО")
normalized = normalize_for_import(detail, "2026-07-24T00:00:00", "selftest")
assert normalized["product_identity_key"], normalized
assert normalized["load_index"] == "95", normalized
assert normalized["speed_index"] == "T", normalized
assert normalized["season"] == "WINTER", normalized
assert normalized["studded"] is False, normalized
assert normalized["xl"] is True, normalized

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    registry = Registry(tmp / "registry.db")
    registry.begin_run("selftest", "discover", "fixture")
    for item in catalog:
        registry.upsert_catalog_product(item, "fixture", "selftest", 1, now_iso())
    assert registry.counts()["products"] >= 8
    registry.update_from_detail(detail, normalized, "selftest", now_iso(), "raw/test.json")
    registry.complete_task("1668280585", "ENRICH")
    counts = registry.counts()
    assert counts["complete_products"] == 1, counts
    assert counts["offers"] == 1, counts
    assert counts["price_points"] == 1, counts
    rows = registry.export_current(tmp / "export.json", tmp / "export.csv")
    row = next(x for x in rows if x["source_product_id"] == "1668280585")
    assert row["source_url"], row
    assert row["price"] == 6077, row
    report = generate_dashboard(tmp / "registry.db", tmp / "report.html")
    assert report.exists() and report.stat().st_size > 1000
    registry.finish_run("selftest", "PASSED", {
        "pages_loaded": 1,
        "items_total": 1,
        "items_success": 1,
        "duration_seconds": 1.0,
    })
    registry.close()

print("SELF TEST: OK")
print(f"Catalog: {len(catalog)} products, pagination detected")
print("SQLite registry: product, link, offer and price history saved")
print("Identity: NEXEN WINGUARD ICE-3 | 195/65 R15 | 95T | WINTER | XL")
