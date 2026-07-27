from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import ensure_database
try:
    from .kaspi_market_v9_1 import Database, enriched_comparison_rows
except ImportError:
    from kaspi_market_v9_1 import Database, enriched_comparison_rows


FIELDS = [
    "product_code", "title", "brand", "product_type", "size", "own_price_kzt",
    "exact_competitor_min_price_kzt", "exact_competitor_name", "exact_difference_kzt",
    "exact_difference_pct", "analog_min_price_kzt", "analog_difference_kzt",
    "analog_difference_pct", "price_status", "review_candidate_count", "product_url",
    "v9_updated_at", "last_price_update_at", "watched", "priority", "note",
]

LABELS = {
    "product_code": "Код Kaspi",
    "title": "Товар Unityre",
    "brand": "Бренд",
    "product_type": "Тип",
    "size": "Размер",
    "own_price_kzt": "Цена Unityre, ₸",
    "exact_competitor_min_price_kzt": "Мин. цена конкурента, ₸",
    "exact_competitor_name": "Конкурент",
    "exact_difference_kzt": "Разница с конкурентом, ₸",
    "exact_difference_pct": "Разница с конкурентом, %",
    "analog_min_price_kzt": "Мин. цена аналога, ₸",
    "analog_difference_kzt": "Разница с аналогом, ₸",
    "analog_difference_pct": "Разница с аналогом, %",
    "price_status": "Статус",
    "review_candidate_count": "На ручную проверку",
    "product_url": "Ссылка Kaspi",
    "v9_updated_at": "Анализ обновлён",
    "last_price_update_at": "Цены обновлены",
    "watched": "В наблюдении",
    "priority": "Приоритет",
    "note": "Комментарий",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Экспорт отчёта ITP Market Intelligence")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seller-name", default="Unityre")
    parser.add_argument("--codes", default="")
    parser.add_argument("--user-id", type=int, default=0)
    return parser.parse_args()


def main(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    ensure_database(db_path)
    db = Database(db_path)
    try:
        rows = [dict(row) for row in enriched_comparison_rows(db, args.seller_name)]
    finally:
        db.conn.close()

    selected = [value.strip() for value in str(args.codes or "").split(",") if value.strip()]
    if selected:
        by_code = {str(row.get("product_code")): row for row in rows}
        rows = [by_code[code] for code in selected if code in by_code]
        scope = "selected"
    else:
        scope = "all"

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        states = {
            row["product_code"]: dict(row)
            for row in conn.execute("SELECT product_code,watched,priority,note FROM app_product_state")
        }
        for row in rows:
            state = states.get(str(row.get("product_code")), {})
            row["watched"] = "Да" if state.get("watched") else "Нет"
            row["priority"] = state.get("priority") or "normal"
            row["note"] = state.get("note") or ""

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"unityre_kaspi_{scope}_{stamp}"
        csv_path = output / f"{base}.csv"
        json_path = output / f"{base}.json"

        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[LABELS[field] for field in FIELDS])
            writer.writeheader()
            for row in rows:
                writer.writerow({LABELS[field]: row.get(field) for field in FIELDS})
        json_path.write_text(
            json.dumps([{field: row.get(field) for field in FIELDS} for row in rows], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        created_at = now_iso()
        for file_path, report_type in ((csv_path, "csv"), (json_path, "json")):
            conn.execute(
                """
                INSERT INTO app_reports(report_type,scope,file_name,file_path,rows_count,created_by,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    report_type,
                    scope,
                    file_path.name,
                    str(file_path.resolve()),
                    len(rows),
                    int(args.user_id) or None,
                    created_at,
                ),
            )
        conn.execute(
            """
            INSERT INTO app_events(user_id,event_type,entity_type,entity_id,details_json,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                int(args.user_id) or None,
                "report_exported",
                "report",
                base,
                json.dumps({"scope": scope, "rows": len(rows)}, ensure_ascii=False),
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"[Экспорт] 1/2 CSV: {csv_path}")
    print(f"[Экспорт] 2/2 JSON: {json_path}")
    print(f"[Экспорт] Готово. Строк: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
