#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OZON_ROOT = ROOT / "collectors" / "ozon"
if str(OZON_ROOT) not in sys.path:
    sys.path.insert(0, str(OZON_ROOT))

from data_service import DataService
from engine.kaspi_market_v9_1 import Database
from ozon_probe_core import parse_catalog_html, parse_product_json
from ozon_validation_core import normalize_for_import, seller_match_status
from registry import Registry, now_iso


def assert_database_integrity(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        status = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert status == "ok", status
        catalog = conn.execute("SELECT COUNT(*) FROM catalog_products").fetchone()[0]
        details = conn.execute("SELECT COUNT(*) FROM product_details").fetchone()[0]
        assert catalog == 890, catalog
        assert details == 890, details
    finally:
        conn.close()


def assert_deterministic_matching(source_db: Path) -> None:
    script = r"""
import json
from collections import Counter
from pathlib import Path
from data_service import DataService
service = DataService(Path(r'__DB__'), 'Unityre', seller_id='Unityre')
rows = service.rows(ttl_seconds=0)
print(json.dumps(dict(sorted(Counter(str(row.get('price_status')) for row in rows).items())), sort_keys=True))
""".replace("__DB__", str(source_db))
    outputs: list[str] = []
    for seed in ("11", "987654"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        outputs.append(completed.stdout.strip())
    assert outputs[0] == outputs[1], outputs


def build_ozon_fixture(path: Path) -> None:
    fixtures = OZON_ROOT / "fixtures"
    catalog_html = (fixtures / "category.html").read_text(encoding="utf-8", errors="replace")
    catalog, next_page = parse_catalog_html(
        catalog_html,
        "https://www.ozon.ru/category/shiny-zimnie-8803/?__rr=1",
    )
    assert len(catalog) >= 8
    assert next_page

    registry = Registry(path)
    try:
        run_id = "mvp-selftest"
        registry.begin_run(run_id, "discover", "fixture")
        stamp = now_iso()
        for item in catalog:
            registry.upsert_catalog_product(item, "fixture", run_id, 1, stamp)

        api = json.loads((fixtures / "api_1668280585.json").read_text(encoding="utf-8"))
        product = next(item for item in catalog if item["article"] == "1668280585")
        detail = parse_product_json("1668280585", api, product)
        assert detail["success"]
        detail["detail_success"] = True
        detail["detail_status"] = "API_OK"
        detail["overall_status"] = "COMPLETE"
        detail["seller_match_status"] = seller_match_status(detail, "ПИН АВТО")
        normalized = normalize_for_import(detail, stamp, run_id)
        registry.update_from_detail(detail, normalized, run_id, stamp, "raw/selftest.json")
        registry.complete_task("1668280585", "ENRICH")
        registry.finish_run(
            run_id,
            "PASSED",
            {
                "pages_loaded": 1,
                "items_total": 1,
                "items_success": 1,
                "duration_seconds": 1,
            },
        )
    finally:
        registry.close()


def main() -> int:
    source_db = ROOT / "data" / "unityre_kaspi.db"
    assert source_db.exists(), source_db
    assert_database_integrity(source_db)
    assert_deterministic_matching(source_db)

    with tempfile.TemporaryDirectory(prefix="itp_market_selftest_") as tmp_name:
        tmp = Path(tmp_name)
        kaspi_db = tmp / "kaspi.db"
        ozon_db = tmp / "ozon.db"
        shutil.copy2(source_db, kaspi_db)
        build_ozon_fixture(ozon_db)
        conn = sqlite3.connect(kaspi_db)
        conn.execute("DELETE FROM app_events")
        conn.execute("DELETE FROM app_user_preferences")
        conn.execute("DELETE FROM app_users")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='app_users'")
        stamp = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO app_users(
                email,display_name,password_hash,recovery_hash,role,is_active,
                created_at,updated_at,password_changed_at
            ) VALUES(?,?,?,?,?,1,?,?,?)
            """,
            ("selftest@example.local", "Self Test", "unused", "unused", "admin", stamp, stamp, stamp),
        )
        test_user_id = int(cursor.lastrowid)
        conn.commit()
        conn.close()

        service = DataService(kaspi_db, "Unityre", ozon_db, seller_id="Unityre")
        rows = service.rows(ttl_seconds=0)
        kaspi_rows = [row for row in rows if row.get("platform") == "kaspi"]
        ozon_rows = [row for row in rows if row.get("platform") == "ozon"]
        assert len(kaspi_rows) == 890, len(kaspi_rows)
        assert len(ozon_rows) >= 8, len(ozon_rows)

        continental = next(
            row for row in kaspi_rows
            if str(row.get("source_product_code")) == "105775186"
        )
        assert continental["price_status"] == "NOT_ANALYZED", continental
        assert continental.get("market_median_price_kzt") is None, continental
        assert continental.get("difference_pct") is None, continental
        assert float(continental.get("potential_margin_per_unit_kzt") or 0) == 0, continental
        assert int(continental.get("legacy_candidate_count") or 0) > 0, continental
        assert continental.get("legacy_candidates_used_in_analytics") is False, continental

        # Exact-only matching: same Kaspi product_code, different sellers.
        conn = sqlite3.connect(kaspi_db)
        exact_rows = [
            ("105775186", "105775186", "Unityre", "Unityre", "own", 137300, 5.0, 100, now_iso()),
            ("105775186", "105775186", "seller-a", "Шины Плюс", "sku-a", 135000, 4.9, 500, now_iso()),
            ("105775186", "105775186", "seller-b", "Колеса KZ", "sku-b", 142000, 4.8, 200, now_iso()),
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO market_seller_offers VALUES(?,?,?,?,?,?,?,?,?)",
            exact_rows,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO exact_offer_scans(
                product_code,status,offers_count,competitor_count,min_price_kzt,max_price_kzt,
                duration_seconds,error,checked_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            ("105775186", "ok", 3, 2, 135000, 142000, 2.1, None, now_iso()),
        )
        conn.commit()
        conn.close()
        service.invalidate()
        exact_continental = next(
            row for row in service.rows(ttl_seconds=0)
            if str(row.get("source_product_code")) == "105775186"
        )
        assert exact_continental["match_method"] == "KASPI_PRODUCT_CODE", exact_continental
        assert exact_continental["reference_count"] == 2, exact_continental
        assert exact_continental["market_min_price_kzt"] == 135000, exact_continental
        assert exact_continental["market_max_price_kzt"] == 142000, exact_continental
        assert exact_continental["price_status"] == "EXACT_IN_MARKET", exact_continental

        product = service.product("105775186", user_id=test_user_id)
        assert product is not None
        specifications = product.get("specifications")
        assert isinstance(specifications, list) and specifications
        assert {"name", "value"}.issubset(specifications[0]), specifications[0]
        assert not isinstance(specifications[0].get("value"), (dict, list)), specifications[0]

        ozon = next(
            row for row in ozon_rows
            if str(row.get("source_product_code")) == "1668280585"
        )
        assert ozon["seller_name"] == "ПИН АВТО", ozon
        assert int(ozon["price_original"]) == 6077, ozon
        assert ozon["currency_original"] == "RUB", ozon
        assert ozon["size"] == "195/65 R15", ozon

        saved = service.save_preferences(
            test_user_id,
            {
                "locale": "kk",
                "display_currency": "KZT",
                "rub_to_kzt": 5.75,
                "usd_to_kzt": 520,
                "eur_to_kzt": 565,
                "default_monthly_units": 4,
            },
        )
        assert saved["locale"] == "kk"
        assert service.preferences(test_user_id)["rub_to_kzt"] == 5.75
        applied = service._apply_user_values(ozon, service.preferences(test_user_id))
        assert applied["price_kzt"] == round(6077 * 5.75, 2)

        filtered = service.products(
            page=1,
            page_size=50,
            filters={"platform": "ozon", "query": "Nexen"},
            user_id=test_user_id,
        )
        assert filtered["total"] >= 1
        assert all(item["platform"] == "ozon" for item in filtered["items"])

        report_dir = tmp / "reports"
        command = [
            sys.executable,
            str(ROOT / "engine" / "export_market_intelligence.py"),
            "--db", str(kaspi_db),
            "--ozon-db", str(ozon_db),
            "--output", str(report_dir),
            "--seller-name", "Unityre",
            "--seller-id", "Unityre",
            "--user-id", str(test_user_id),
            "--codes", "105775186,ozon:1668280585",
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert completed.returncode == 0, completed.stderr or completed.stdout
        assert list(report_dir.glob("*.html"))
        assert list(report_dir.glob("*.csv"))
        assert list(report_dir.glob("*.json"))

        db = Database(kaspi_db)
        try:
            assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            db.conn.close()

    print("ITP MARKET INTELLIGENCE MVP SELF TEST: OK")
    print("Kaspi: 890 products and 890 detail cards")
    print("Matching: deterministic across independent Python hash seeds")
    print("Continental 105775186: exact-only by Kaspi product_code; cross-brand analogs excluded")
    print("Ozon: fixture registry, seller, price, link and RUB/KZT conversion verified")
    print("Reports: combined HTML, CSV and JSON generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
