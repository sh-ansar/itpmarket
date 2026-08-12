from __future__ import annotations

import argparse
import csv
import html
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_service import DataService, OPPORTUNITY_STATUSES, RISK_STATUSES, UNSCANNED_STATUSES
from engine.simple_xlsx import write_xlsx
from marketplace_registry import MARKETPLACE_CODES
from storage.postgres_compat import connect_database


def money(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):,.0f} ₸".replace(",", " ")
    except Exception:
        return str(value)


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--ozon-db", default="")
    parser.add_argument("--ozon-kz-db", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--seller-name", default="Unityre")
    parser.add_argument("--seller-id", default="Unityre")
    parser.add_argument("--user-id", type=int, default=0)
    parser.add_argument("--tenant-id", type=int, default=0)
    parser.add_argument("--allowed-platforms", default="")
    parser.add_argument("--codes", default="")
    parser.add_argument("--selection-file", default="")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    ozon_path = Path(args.ozon_db).resolve() if args.ozon_db else None
    ozon_kz_path = Path(args.ozon_kz_db).resolve() if args.ozon_kz_db else None
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    print("[Экспорт] 1/4 Подготовка выборки", flush=True)
    service = DataService(
        db_path, args.seller_name, ozon_path, seller_id=args.seller_id,
        ozon_kz_db_path=ozon_kz_path,
    )
    prefs = service.preferences(args.user_id)
    code_filter = {code.strip() for code in args.codes.split(",") if code.strip()}
    report_scope = "selected" if code_filter else "all"
    if args.selection_file:
        selection_path = Path(args.selection_file).resolve()
        try:
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            raw_codes = selection.get("codes") if isinstance(selection, dict) else []
            if isinstance(raw_codes, list):
                code_filter.update(str(code).strip() for code in raw_codes if str(code).strip())
            raw_scope = str(selection.get("scope") or "selected") if isinstance(selection, dict) else "selected"
            report_scope = raw_scope if raw_scope in {"selected", "filtered"} else "selected"
        finally:
            try:
                selection_path.unlink(missing_ok=True)
            except OSError:
                pass
    allowed_platforms = {
        value.strip() for value in args.allowed_platforms.split(",")
        if value.strip() in MARKETPLACE_CODES
    }
    if args.user_id and not allowed_platforms:
        conn = connect_database(db_path, timeout=30)
        try:
            allowed_platforms = {
                str(row[0]) for row in conn.execute(
                    """SELECT tma.marketplace_code
                       FROM tenant_users tu
                       JOIN tenants t ON t.id=tu.tenant_id
                       JOIN tenant_marketplace_access tma ON tma.tenant_id=tu.tenant_id
                       JOIN tenant_integrations ti ON ti.tenant_id=tma.tenant_id
                        AND ti.integration_code=tma.marketplace_code
                       WHERE tu.user_id=? AND tu.is_active=1
                         AND t.status IN ('active','approved','confirmed')
                         AND tma.is_allowed=1 AND ti.status='active'""",
                    (args.user_id,),
                ).fetchall()
                if str(row[0]) in MARKETPLACE_CODES
            }
        finally:
            conn.close()
    rows = [
        service._apply_user_values(row, prefs)
        for row in service.rows_for_user(args.user_id)
        if not args.user_id or str(row.get("platform") or "") in allowed_platforms
    ]
    if code_filter:
        rows = [row for row in rows if str(row.get("product_code")) in code_filter or str(row.get("source_product_code")) in code_filter]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"itp_market_intelligence_{timestamp}"
    csv_path = output / f"{base}.csv"
    json_path = output / f"{base}.json"
    html_path = output / f"{base}.html"
    xlsx_path = output / f"{base}.xlsx"

    export_rows = []
    for row in rows:
        price_status = str(row.get("price_status") or "")
        export_rows.append({
            "platform": row.get("platform_label"),
            "source_product_code": row.get("source_product_code"),
            "title": row.get("title"),
            "brand": row.get("brand"),
            "model": row.get("model"),
            "size": row.get("size"),
            "product_type": row.get("product_type_label") or row.get("product_type"),
            "tire_width": row.get("tire_width"),
            "tire_profile": row.get("tire_profile"),
            "tire_diameter": row.get("tire_diameter"),
            "load_index": row.get("load_index"),
            "speed_index": row.get("speed_index"),
            "season": row.get("season_label") or row.get("season"),
            "studded": row.get("studded"),
            "runflat": bool(row.get("runflat")),
            "characteristic_group": row.get("characteristic_group_label"),
            "exact_characteristic_key": row.get("exact_characteristic_key"),
            "seller": row.get("seller_name"),
            "product_url": row.get("product_url"),
            "own_price_kzt": row.get("own_price_kzt"),
            "source_price": row.get("price_original"),
            "source_currency": row.get("currency_original"),
            "converted_price_kzt": row.get("price_kzt"),
            "reference_type": row.get("reference_type"),
            "match_method": row.get("match_method"),
            "match_method_label": row.get("match_method_label"),
            "reference_count": row.get("reference_count"),
            "exact_offer_count": row.get("exact_offer_count"),
            "competitor_seller_count": row.get("competitor_seller_count"),
            "legacy_candidate_count": row.get("legacy_candidate_count"),
            "market_min_price_kzt": row.get("market_min_price_kzt"),
            "market_median_price_kzt": row.get("market_median_price_kzt"),
            "market_max_price_kzt": row.get("market_max_price_kzt"),
            "difference_to_median_kzt": row.get("difference_kzt"),
            "difference_to_median_pct": row.get("difference_pct"),
            "status": row.get("status_label"),
            "status_code": price_status,
            "risk_flag": price_status in RISK_STATUSES,
            "review_required": price_status in UNSCANNED_STATUSES,
            "opportunity_flag": price_status in OPPORTUNITY_STATUSES,
            "lowest_product_title": row.get("lowest_product_title"),
            "lowest_product_price_kzt": row.get("lowest_product_price_kzt"),
            "lowest_product_url": row.get("lowest_product_url"),
            "highest_product_title": row.get("highest_product_title"),
            "highest_product_price_kzt": row.get("highest_product_price_kzt"),
            "highest_product_url": row.get("highest_product_url"),
            "price_rank": row.get("price_rank"),
            "price_rank_total": row.get("price_rank_total"),
            "potential_margin_per_unit_kzt": row.get("potential_margin_per_unit_kzt"),
            "expected_monthly_units": row.get("expected_monthly_units"),
            "potential_margin_monthly_kzt": row.get("potential_margin_monthly_kzt"),
            "freshness_status": row.get("freshness_status"),
            "freshness_label": row.get("freshness_label"),
            "watched": bool(row.get("watched")),
            "priority": row.get("priority"),
            "note": row.get("note"),
            "catalog_rating": row.get("catalog_rating"),
            "catalog_reviews": row.get("catalog_reviews"),
            "updated_at": row.get("_updated_sort"),
        })

    print(f"[Экспорт] 2/4 Подготовлено строк: {len(export_rows)}", flush=True)
    fieldnames = list(export_rows[0].keys()) if export_rows else ["platform", "source_product_code", "title"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(export_rows)
    json_path.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"), "preferences": prefs, "rows": export_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    xlsx_headers = [
        "Площадка", "Код", "Товар", "Бренд", "Модель", "Размер", "Тип",
        "Ширина", "Профиль", "Диаметр", "Индекс нагрузки", "Индекс скорости",
        "Сезон", "Шипы", "RunFlat", "Группа характеристик", "Продавец",
        "Цена", "Валюта", "Цена в KZT", "Минимум рынка", "Медиана рынка",
        "Максимум рынка", "Статус", "Риск", "Требует проверки", "Потенциал",
        "Отклонение, %", "Место", "Всего предложений", "Потенциал / ед.",
        "Объём / мес.", "Потенциал / мес.", "Актуальность", "Обновлено",
        "Наблюдение", "Приоритет", "Примечание", "Ссылка",
    ]
    xlsx_rows = [
        [
            row.get("platform"), row.get("source_product_code"), row.get("title"),
            row.get("brand"), row.get("model"), row.get("size"), row.get("product_type"),
            row.get("tire_width"), row.get("tire_profile"), row.get("tire_diameter"),
            row.get("load_index"), row.get("speed_index"), row.get("season"),
            row.get("studded"), row.get("runflat"), row.get("characteristic_group"),
            row.get("seller"), row.get("source_price"), row.get("source_currency"),
            row.get("converted_price_kzt") or row.get("own_price_kzt"),
            row.get("market_min_price_kzt"), row.get("market_median_price_kzt"),
            row.get("market_max_price_kzt"), row.get("status"), row.get("risk_flag"),
            row.get("review_required"), row.get("opportunity_flag"),
            row.get("difference_to_median_pct"), row.get("price_rank"),
            row.get("price_rank_total"), row.get("potential_margin_per_unit_kzt"),
            row.get("expected_monthly_units"), row.get("potential_margin_monthly_kzt"),
            row.get("freshness_label"), row.get("updated_at"), row.get("watched"),
            row.get("priority"), row.get("note"), row.get("product_url"),
        ]
        for row in export_rows
    ]
    write_xlsx(xlsx_path, xlsx_headers, xlsx_rows, sheet_name="Товары")
    print("[Экспорт] 3/4 CSV, JSON и Excel сформированы", flush=True)

    opportunity = sum(float(row.get("potential_margin_monthly_kzt") or 0) for row in rows)
    risks = sum(1 for row in rows if str(row.get("price_status") or "") in RISK_STATUSES)
    review = sum(1 for row in rows if str(row.get("price_status") or "") in UNSCANNED_STATUSES)
    tr = []
    for row in export_rows:
        product_link = f'<a href="{esc(row["product_url"])}" target="_blank" rel="noreferrer">{esc(row["title"])}</a>' if row.get("product_url") else esc(row.get("title"))
        low = f'<a href="{esc(row["lowest_product_url"])}" target="_blank" rel="noreferrer">{money(row["lowest_product_price_kzt"])}</a>' if row.get("lowest_product_url") else money(row.get("market_min_price_kzt"))
        high = f'<a href="{esc(row["highest_product_url"])}" target="_blank" rel="noreferrer">{money(row["highest_product_price_kzt"])}</a>' if row.get("highest_product_url") else money(row.get("market_max_price_kzt"))
        difference_pct = (
            f"{esc(row.get('difference_to_median_pct'))}%"
            if row.get("difference_to_median_pct") is not None
            else "—"
        )
        tr.append(
            "<tr>"
            f"<td><span class='platform {esc(str(row.get('platform')).lower())}'>{esc(row.get('platform'))}</span></td>"
            f"<td>{product_link}<small>{esc(row.get('source_product_code'))} · {esc(row.get('brand'))} · {esc(row.get('size'))}</small></td>"
            f"<td>{money(row.get('own_price_kzt') or row.get('converted_price_kzt'))}</td>"
            f"<td>{low}</td><td>{money(row.get('market_median_price_kzt'))}</td><td>{high}</td>"
            f"<td>{esc(row.get('status'))}<small>{esc(row.get('status_code'))}</small></td>"
            f"<td>{difference_pct}</td>"
            f"<td>{esc(row.get('price_rank') or '—')} / {esc(row.get('price_rank_total') or '—')}</td>"
            f"<td>{money(row.get('potential_margin_per_unit_kzt'))}</td>"
            f"<td>{esc(row.get('expected_monthly_units') or 0)}</td>"
            f"<td>{money(row.get('potential_margin_monthly_kzt'))}</td>"
            "</tr>"
        )
    html_doc = f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Spyon</title><style>
    :root{{--blue:#05a9e8;--navy:#071827;--bg:#f4f7fa;--line:#dce5ec;--orange:#f0642e}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font:14px/1.45 Arial,sans-serif;color:#17212b}}header{{padding:30px 38px;background:linear-gradient(135deg,#061827,#0b2f48);color:#fff;border-bottom:4px solid var(--blue)}}header h1{{margin:0 0 6px;font-size:28px}}header p{{margin:0;color:#b8c8d5}}main{{max-width:1600px;margin:auto;padding:28px}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}}.card,.panel{{background:#fff;border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 24px rgba(14,40,59,.06)}}.card{{padding:20px}}.card b{{display:block;font-size:26px;margin-top:5px}}.card span{{color:#6d7d89;font-size:12px}}.panel{{padding:20px;overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:1200px}}th,td{{padding:12px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{font-size:11px;color:#71808c;text-transform:uppercase}}td small{{display:block;color:#7a8792;margin-top:4px}}a{{color:#067db4;text-decoration:none}}.platform{{display:inline-block;padding:5px 8px;border-radius:999px;font-size:11px;font-weight:700;background:#e8f7fd;color:#087aa8}}.platform.kaspi{{background:#ffecef;color:#b32545}}.platform.ozon{{background:#e9efff;color:#2446cb}}@media(max-width:900px){{.metrics{{grid-template-columns:1fr 1fr}}main{{padding:14px}}}}
    </style></head><body><header><h1>Spyon</h1><p>Сводный exact-only отчёт Kaspi, Ozon.ru и Halyk Market · {datetime.now().strftime('%d.%m.%Y %H:%M')}</p></header><main><section class='metrics'><div class='card'><span>Позиций</span><b>{len(rows)}</b></div><div class='card'><span>Ценовых рисков</span><b>{risks}</b></div><div class='card'><span>Требуют проверки</span><b>{review}</b></div><div class='card'><span>Ценовой потенциал / месяц</span><b>{money(opportunity)}</b></div></section><section class='panel'><table><thead><tr><th>Площадка</th><th>Товар</th><th>Текущая цена</th><th>Минимум</th><th>Медиана</th><th>Максимум</th><th>Позиция</th><th>Отклонение</th><th>Место</th><th>Потенциал / ед.</th><th>Объём / мес.</th><th>Потенциал / мес.</th></tr></thead><tbody>{''.join(tr)}</tbody></table></section></main></body></html>"""
    html_path.write_text(html_doc, encoding="utf-8")
    print("[Экспорт] 4/4 HTML сформирован", flush=True)

    platforms_json = json.dumps(sorted({
        str(row.get("platform") or "") for row in rows if row.get("platform")
    }), ensure_ascii=False)
    conn = connect_database(db_path, timeout=30)
    try:
        conn.execute(
            """INSERT INTO app_reports(
                   report_type,scope,file_name,file_path,rows_count,created_by,
                   tenant_id,platforms_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
            ("market_intelligence_html", report_scope if code_filter else "all", html_path.name, str(html_path), len(rows), args.user_id or None, args.tenant_id or None, platforms_json),
        )
        conn.execute(
            """INSERT INTO app_reports(
                   report_type,scope,file_name,file_path,rows_count,created_by,
                   tenant_id,platforms_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
            ("market_intelligence_csv", report_scope if code_filter else "all", csv_path.name, str(csv_path), len(rows), args.user_id or None, args.tenant_id or None, platforms_json),
        )
        conn.execute(
            """INSERT INTO app_reports(
                   report_type,scope,file_name,file_path,rows_count,created_by,
                   tenant_id,platforms_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
            ("market_intelligence_xlsx", report_scope if code_filter else "all", xlsx_path.name, str(xlsx_path), len(rows), args.user_id or None, args.tenant_id or None, platforms_json),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"HTML: {html_path}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    print(f"Excel: {xlsx_path}")
    print(f"Rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
