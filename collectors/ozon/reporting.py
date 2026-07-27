#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def generate_dashboard(db_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    counts = {
        "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "active": conn.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0],
        "complete": conn.execute("SELECT COUNT(*) FROM products WHERE detail_status='COMPLETE'").fetchone()[0],
        "offers": conn.execute("SELECT COUNT(*) FROM offers WHERE active=1").fetchone()[0],
        "history": conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0],
        "pending": conn.execute("SELECT COUNT(*) FROM crawl_queue WHERE status IN ('PENDING','RUNNING')").fetchone()[0],
        "failed": conn.execute("SELECT COUNT(*) FROM crawl_queue WHERE status IN ('FAILED','BLOCKED')").fetchone()[0],
    }
    total = max(1, counts["products"])
    completeness = round(counts["complete"] * 100 / total, 1)

    coverage_fields = {
        "Brand": "brand",
        "Model": "model",
        "Tire size": "tire_size",
        "Load index": "load_index",
        "Speed index": "speed_index",
        "Season": "season",
        "Manufacturer article": "manufacturer_article",
    }
    coverage: dict[str, float] = {}
    for label, field in coverage_fields.items():
        if field == "season":
            sql = "SELECT COUNT(*) FROM products WHERE active=1 AND season<>'' AND season<>'UNKNOWN'"
        else:
            sql = f"SELECT COUNT(*) FROM products WHERE active=1 AND {field}<>''"
        value = conn.execute(sql).fetchone()[0]
        coverage[label] = round(value * 100 / max(1, counts["active"]), 1)

    sellers = conn.execute(
        """
        SELECT seller_name, seller_id, COUNT(*) AS offers_count,
               ROUND(AVG(card_price),0) AS avg_price,
               MAX(last_checked_at) AS last_checked
        FROM offers WHERE active=1
        GROUP BY seller_key
        ORDER BY offers_count DESC, seller_name
        LIMIT 15
        """
    ).fetchall()

    runs = conn.execute(
        """
        SELECT * FROM runs ORDER BY started_at DESC LIMIT 12
        """
    ).fetchall()

    products = conn.execute(
        """
        SELECT p.article,p.title,p.brand,p.model,p.tire_size,p.load_index,p.speed_index,
               p.season,p.detail_status,p.identity_completeness_percent,p.canonical_url,
               p.last_detail_at,o.seller_name,o.card_price,o.regular_price,o.currency,o.last_checked_at
        FROM products p
        LEFT JOIN offers o ON o.article=p.article
          AND o.last_checked_at=(SELECT MAX(o2.last_checked_at) FROM offers o2 WHERE o2.article=p.article)
        WHERE p.active=1
        ORDER BY COALESCE(o.last_checked_at,p.last_seen_at) DESC
        LIMIT 100
        """
    ).fetchall()

    failures = conn.execute(
        """
        SELECT q.article,p.title,q.task_type,q.status,q.attempts,q.last_error,q.updated_at
        FROM crawl_queue q JOIN products p ON p.article=q.article
        WHERE q.status IN ('FAILED','BLOCKED','PENDING')
        ORDER BY CASE q.status WHEN 'BLOCKED' THEN 1 WHEN 'FAILED' THEN 2 ELSE 3 END,
                 q.priority DESC,q.updated_at DESC
        LIMIT 50
        """
    ).fetchall()

    price_changes = conn.execute(
        """
        WITH ranked AS (
          SELECT article,seller_key,card_price,collected_at,
                 ROW_NUMBER() OVER(PARTITION BY article,seller_key ORDER BY collected_at DESC,id DESC) rn
          FROM price_history
        ), pairs AS (
          SELECT a.article,a.seller_key,a.card_price current_price,b.card_price previous_price,a.collected_at
          FROM ranked a LEFT JOIN ranked b
            ON b.article=a.article AND b.seller_key=a.seller_key AND b.rn=2
          WHERE a.rn=1
        )
        SELECT p.article,p.title,o.seller_name,pairs.current_price,pairs.previous_price,
               pairs.current_price-COALESCE(pairs.previous_price,pairs.current_price) AS delta,
               pairs.collected_at,p.canonical_url
        FROM pairs JOIN products p ON p.article=pairs.article
        LEFT JOIN offers o ON o.article=pairs.article AND o.seller_key=pairs.seller_key
        WHERE pairs.previous_price IS NOT NULL AND pairs.current_price<>pairs.previous_price
        ORDER BY ABS(pairs.current_price-pairs.previous_price) DESC
        LIMIT 30
        """
    ).fetchall()

    seller_rows = "".join(
        f"<tr><td>{_esc(row['seller_name'] or 'Unknown')}</td><td>{_esc(row['seller_id'])}</td>"
        f"<td>{row['offers_count']}</td><td>{int(row['avg_price'] or 0):,} RUB</td>"
        f"<td>{_esc(row['last_checked'])}</td></tr>" for row in sellers
    ) or "<tr><td colspan='5' class='empty'>No offers yet</td></tr>"

    run_rows = "".join(
        f"<tr><td>{_esc(row['started_at'])}</td><td>{_esc(row['mode'])}</td>"
        f"<td><span class='badge {str(row['status']).lower()}'>{_esc(row['status'])}</span></td>"
        f"<td>{row['pages_loaded']}</td><td>{row['items_success']}/{row['items_total']}</td>"
        f"<td>{row['items_failed']}</td><td>{row['items_blocked']}</td>"
        f"<td>{float(row['duration_seconds'] or 0):.1f} sec</td></tr>" for row in runs
    ) or "<tr><td colspan='8' class='empty'>No runs yet</td></tr>"

    product_rows = "".join(
        f"<tr><td>{_esc(row['article'])}</td><td><a href='{_esc(row['canonical_url'])}' target='_blank'>"
        f"{_esc(row['title'])}</a><div class='muted'>{_esc(row['brand'])} · {_esc(row['model'])}</div></td>"
        f"<td>{_esc(row['tire_size'])}<div class='muted'>{_esc(str(row['load_index'] or '') + str(row['speed_index'] or ''))} · {_esc(row['season'])}</div></td>"
        f"<td>{int(row['card_price'] or 0):,} {_esc(row['currency'] or 'RUB')}<div class='muted'>regular {int(row['regular_price'] or 0):,}</div></td>"
        f"<td>{_esc(row['seller_name'])}</td><td><span class='badge complete'>{_esc(row['detail_status'])}</span></td>"
        f"<td>{float(row['identity_completeness_percent'] or 0):.0f}%</td><td>{_esc(row['last_checked_at'] or row['last_detail_at'])}</td></tr>"
        for row in products
    ) or "<tr><td colspan='8' class='empty'>No products yet</td></tr>"

    failure_rows = "".join(
        f"<tr><td>{_esc(row['article'])}</td><td>{_esc(row['title'])}</td><td>{_esc(row['task_type'])}</td>"
        f"<td><span class='badge {str(row['status']).lower()}'>{_esc(row['status'])}</span></td>"
        f"<td>{row['attempts']}</td><td class='error'>{_esc(row['last_error'])}</td><td>{_esc(row['updated_at'])}</td></tr>"
        for row in failures
    ) or "<tr><td colspan='7' class='empty'>No pending or failed tasks</td></tr>"

    change_rows = "".join(
        f"<tr><td><a href='{_esc(row['canonical_url'])}' target='_blank'>{_esc(row['title'])}</a></td>"
        f"<td>{_esc(row['seller_name'])}</td><td>{int(row['previous_price']):,}</td>"
        f"<td>{int(row['current_price']):,}</td><td class='{'up' if int(row['delta'])>0 else 'down'}'>{int(row['delta']):+,}</td>"
        f"<td>{_esc(row['collected_at'])}</td></tr>" for row in price_changes
    ) or "<tr><td colspan='6' class='empty'>Price changes will appear after the second refresh</td></tr>"

    coverage_rows = "".join(
        f"<div class='coverage-row'><div>{_esc(label)}</div><div class='track'><span style='width:{value}%'></span></div><strong>{_pct(value)}</strong></div>"
        for label, value in coverage.items()
    )

    payload = {
        "counts": counts,
        "coverage": coverage,
        "completeness": completeness,
    }

    page = f"""<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Ozon Collector Dashboard</title>
<style>
:root{{--bg:#f4f5f6;--panel:#fff;--ink:#12171d;--muted:#747d86;--line:#e4e7ea;--accent:#ff6b2c;--dark:#11161b;--ok:#14865a;--warn:#a86a00;--bad:#c63737}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Inter,Arial,sans-serif}}
header{{background:var(--dark);color:#fff;padding:30px 38px;border-bottom:4px solid var(--accent)}}header h1{{margin:0 0 5px;font-size:28px}}header p{{margin:0;color:#b7bec5}}
main{{max-width:1560px;margin:0 auto;padding:26px}}.cards{{display:grid;grid-template-columns:repeat(7,minmax(140px,1fr));gap:12px;margin-bottom:18px}}
.card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:15px;box-shadow:0 7px 22px rgba(10,20,30,.045)}}.card{{padding:16px}}.value{{font-size:27px;font-weight:800;margin-top:5px}}.label,.muted{{color:var(--muted);font-size:12px}}
.panel{{padding:20px;margin-bottom:18px}}h2{{font-size:18px;margin:0 0 15px}}.two{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}}a{{color:#135bba;text-decoration:none}}a:hover{{text-decoration:underline}}
.badge{{display:inline-block;border-radius:999px;padding:4px 8px;font-size:10px;font-weight:800}}.complete,.passed,.done{{background:#e5f5ee;color:var(--ok)}}.running,.pending{{background:#fff2d6;color:var(--warn)}}.failed,.blocked,.error{{background:#fde9e9;color:var(--bad)}}
.coverage-row{{display:grid;grid-template-columns:140px 1fr 55px;gap:10px;align-items:center;margin:11px 0}}.track{{height:9px;border-radius:999px;background:#edf0f2;overflow:hidden}}.track span{{display:block;height:100%;background:var(--accent)}}.empty{{color:var(--muted);text-align:center;padding:25px}}.error{{max-width:380px;word-break:break-word}}.up{{color:var(--bad);font-weight:700}}.down{{color:var(--ok);font-weight:700}}
.footer{{color:var(--muted);font-size:12px;padding:6px 2px 20px}}@media(max-width:1200px){{.cards{{grid-template-columns:repeat(3,1fr)}}.two{{grid-template-columns:1fr}}}}@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}main{{padding:14px}}header{{padding:24px 18px}}.panel{{overflow:auto}}}}
</style>
</head><body>
<header><h1>Ozon Collector 3.0</h1><p>Permanent product registry, price history and resumable collection</p></header>
<main>
<section class='cards'>
<div class='card'><div class='label'>Products</div><div class='value'>{counts['products']}</div></div>
<div class='card'><div class='label'>Active</div><div class='value'>{counts['active']}</div></div>
<div class='card'><div class='label'>Complete</div><div class='value'>{counts['complete']}</div><div class='muted'>{completeness}% coverage</div></div>
<div class='card'><div class='label'>Offers</div><div class='value'>{counts['offers']}</div></div>
<div class='card'><div class='label'>Price points</div><div class='value'>{counts['history']}</div></div>
<div class='card'><div class='label'>Pending</div><div class='value'>{counts['pending']}</div></div>
<div class='card'><div class='label'>Failed</div><div class='value'>{counts['failed']}</div></div>
</section>
<section class='two'><div class='panel'><h2>Data coverage</h2>{coverage_rows}</div><div class='panel'><h2>Sellers</h2><table><thead><tr><th>Seller</th><th>ID</th><th>Offers</th><th>Average price</th><th>Last check</th></tr></thead><tbody>{seller_rows}</tbody></table></div></section>
<section class='panel'><h2>Recent runs</h2><table><thead><tr><th>Started</th><th>Mode</th><th>Status</th><th>Pages</th><th>Success</th><th>Failed</th><th>Blocked</th><th>Duration</th></tr></thead><tbody>{run_rows}</tbody></table></section>
<section class='panel'><h2>Latest products</h2><table><thead><tr><th>Article</th><th>Product</th><th>Identity</th><th>Price</th><th>Seller</th><th>Status</th><th>Quality</th><th>Updated</th></tr></thead><tbody>{product_rows}</tbody></table></section>
<section class='two'><div class='panel'><h2>Price changes</h2><table><thead><tr><th>Product</th><th>Seller</th><th>Previous</th><th>Current</th><th>Delta</th><th>Updated</th></tr></thead><tbody>{change_rows}</tbody></table></div><div class='panel'><h2>Queue and failures</h2><table><thead><tr><th>Article</th><th>Product</th><th>Task</th><th>Status</th><th>Attempts</th><th>Error</th><th>Updated</th></tr></thead><tbody>{failure_rows}</tbody></table></div></section>
<div class='footer'>Database: {_esc(db_path)} · <script>document.write(new Date().toLocaleString())</script></div>
</main><script>window.OZON_COLLECTOR_SUMMARY={json.dumps(payload, ensure_ascii=False)};</script></body></html>"""
    output_path.write_text(page, encoding="utf-8")
    conn.close()
    return output_path
