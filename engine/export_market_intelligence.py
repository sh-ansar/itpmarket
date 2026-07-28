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

from data_service import DataService


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
    parser.add_argument("--output", required=True)
    parser.add_argument("--seller-name", default="Unityre")
    parser.add_argument("--seller-id", default="Unityre")
    parser.add_argument("--user-id", type=int, default=0)
    parser.add_argument("--codes", default="")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    ozon_path = Path(args.ozon_db).resolve() if args.ozon_db else None
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    service = DataService(db_path, args.seller_name, ozon_path, seller_id=args.seller_id)
    prefs = service.preferences(args.user_id)
    code_filter = {code.strip() for code in args.codes.split(",") if code.strip()}
    rows = [service._apply_user_values(row, prefs) for row in service.rows(0)]
    if code_filter:
        rows = [row for row in rows if str(row.get("product_code")) in code_filter or str(row.get("source_product_code")) in code_filter]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"itp_market_intelligence_{timestamp}"
    csv_path = output / f"{base}.csv"
    json_path = output / f"{base}.json"
    html_path = output / f"{base}.html"

    export_rows = []
    for row in rows:
        export_rows.append({
            "platform": row.get("platform_label"),
            "source_product_code": row.get("source_product_code"),
            "title": row.get("title"),
            "brand": row.get("brand"),
            "model": row.get("model"),
            "size": row.get("size"),
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
            "status_code": row.get("price_status"),
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
            "updated_at": row.get("_updated_sort"),
        })

    fieldnames = list(export_rows[0].keys()) if export_rows else ["platform", "source_product_code", "title"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(export_rows)
    json_path.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"), "preferences": prefs, "rows": export_rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    opportunity = sum(float(row.get("potential_margin_monthly_kzt") or 0) for row in rows)
    risks = sum(1 for row in rows if row.get("status_tone") in {"warning", "danger"} and row.get("price_status") not in {"REVIEW_REQUIRED"})
    review = sum(1 for row in rows if row.get("price_status") == "REVIEW_REQUIRED")
    tr = []
    for row in export_rows:
        product_link = f'<a href="{esc(row["product_url"])}" target="_blank" rel="noreferrer">{esc(row["title"])}</a>' if row.get("product_url") else esc(row.get("title"))
        low = f'<a href="{esc(row["lowest_product_url"])}" target="_blank" rel="noreferrer">{money(row["lowest_product_price_kzt"])}</a>' if row.get("lowest_product_url") else money(row.get("market_min_price_kzt"))
        high = f'<a href="{esc(row["highest_product_url"])}" target="_blank" rel="noreferrer">{money(row["highest_product_price_kzt"])}</a>' if row.get("highest_product_url") else money(row.get("market_max_price_kzt"))
        tr.append(
            "<tr>"
            f"<td><span class='platform {esc(str(row.get('platform')).lower())}'>{esc(row.get('platform'))}</span></td>"
            f"<td>{product_link}<small>{esc(row.get('source_product_code'))} · {esc(row.get('brand'))} · {esc(row.get('size'))}</small></td>"
            f"<td>{money(row.get('own_price_kzt') or row.get('converted_price_kzt'))}</td>"
            f"<td>{low}</td><td>{money(row.get('market_median_price_kzt'))}</td><td>{high}</td>"
            f"<td>{esc(row.get('status'))}</td>"
            f"<td>{money(row.get('potential_margin_monthly_kzt'))}</td>"
            "</tr>"
        )
    html_doc = f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ITP Market Intelligence</title><style>
    :root{{--blue:#05a9e8;--navy:#071827;--bg:#f4f7fa;--line:#dce5ec;--orange:#f0642e}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font:14px/1.45 Arial,sans-serif;color:#17212b}}header{{padding:30px 38px;background:linear-gradient(135deg,#061827,#0b2f48);color:#fff;border-bottom:4px solid var(--blue)}}header h1{{margin:0 0 6px;font-size:28px}}header p{{margin:0;color:#b8c8d5}}main{{max-width:1600px;margin:auto;padding:28px}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}}.card,.panel{{background:#fff;border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 24px rgba(14,40,59,.06)}}.card{{padding:20px}}.card b{{display:block;font-size:26px;margin-top:5px}}.card span{{color:#6d7d89;font-size:12px}}.panel{{padding:20px;overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:1200px}}th,td{{padding:12px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{font-size:11px;color:#71808c;text-transform:uppercase}}td small{{display:block;color:#7a8792;margin-top:4px}}a{{color:#067db4;text-decoration:none}}.platform{{display:inline-block;padding:5px 8px;border-radius:999px;font-size:11px;font-weight:700;background:#e8f7fd;color:#087aa8}}.platform.kaspi{{background:#ffecef;color:#b32545}}.platform.ozon{{background:#e9efff;color:#2446cb}}@media(max-width:900px){{.metrics{{grid-template-columns:1fr 1fr}}main{{padding:14px}}}}
    </style></head><body><header><h1>ITP Market Intelligence</h1><p>Сводный exact-only отчёт Kaspi и реестр Ozon · {datetime.now().strftime('%d.%m.%Y %H:%M')}</p></header><main><section class='metrics'><div class='card'><span>Позиций</span><b>{len(rows)}</b></div><div class='card'><span>Ценовых рисков</span><b>{risks}</b></div><div class='card'><span>Требуют проверки</span><b>{review}</b></div><div class='card'><span>Ценовой потенциал Kaspi / месяц</span><b>{money(opportunity)}</b></div></section><section class='panel'><table><thead><tr><th>Площадка</th><th>Товар</th><th>Текущая цена</th><th>Минимум</th><th>Медиана</th><th>Максимум</th><th>Позиция</th><th>Ценовой потенциал</th></tr></thead><tbody>{''.join(tr)}</tbody></table></section></main></body></html>"""
    html_path.write_text(html_doc, encoding="utf-8")

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "INSERT INTO app_reports(report_type,scope,file_name,file_path,rows_count,created_by,created_at) VALUES(?,?,?,?,?,?,datetime('now'))",
            ("market_intelligence_html", "selected" if code_filter else "all", html_path.name, str(html_path), len(rows), args.user_id or None),
        )
        conn.execute(
            "INSERT INTO app_reports(report_type,scope,file_name,file_path,rows_count,created_by,created_at) VALUES(?,?,?,?,?,?,datetime('now'))",
            ("market_intelligence_csv", "selected" if code_filter else "all", csv_path.name, str(csv_path), len(rows), args.user_id or None),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"HTML: {html_path}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    print(f"Rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
