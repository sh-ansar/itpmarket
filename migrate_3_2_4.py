from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db"


def lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        value.strip()
        for value in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if value.strip() and not value.strip().startswith("#")
    ]


def normalized(value: object) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", str(value or "").casefold())


def source_type(url: str) -> str:
    return "CLIENT_CATALOG" if "/seller/" in url.casefold() else "MARKET_CATEGORY"


def main() -> int:
    if not DB_PATH.exists():
        print("Ozon registry not found. Migration will be applied automatically after the first discovery.")
        return 0

    expected = normalized((lines(ROOT / "collectors" / "ozon" / "EXPECTED_SELLER.txt") or [""])[0])
    seller_ids: set[str] = set()
    for url in lines(ROOT / "collectors" / "ozon" / "START_URLS.txt"):
        match = re.search(r"/seller/[^/?]*-(\d+)", url, flags=re.I)
        if match:
            seller_ids.add(match.group(1))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS catalog_sources (
            source_url TEXT PRIMARY KEY,
            source_type TEXT NOT NULL DEFAULT 'MARKET_CATEGORY',
            label TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS product_sources (
            article TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'MARKET_CATEGORY',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_run_id TEXT NOT NULL DEFAULT '',
            page_no INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(article,source_url)
        );
        CREATE INDEX IF NOT EXISTS idx_product_sources_type
            ON product_sources(source_type,article);
        """
    )
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        for row in conn.execute(
            "SELECT article,discovery_url,first_seen_at,last_seen_at FROM products"
        ).fetchall():
            url = str(row["discovery_url"] or "").strip()
            if not url:
                continue
            kind = source_type(url)
            first_seen = str(row["first_seen_at"] or now)
            last_seen = str(row["last_seen_at"] or first_seen)
            conn.execute(
                """
                INSERT INTO catalog_sources(source_url,source_type,label,first_seen_at,last_seen_at,active)
                VALUES(?,?,?,?,?,1)
                ON CONFLICT(source_url) DO UPDATE SET
                    source_type=excluded.source_type,last_seen_at=excluded.last_seen_at,active=1
                """,
                (url,kind,"",first_seen,last_seen),
            )
            conn.execute(
                """
                INSERT INTO product_sources(
                    article,source_url,source_type,first_seen_at,last_seen_at,last_run_id,page_no
                ) VALUES(?,?,?,?,?,'',0)
                ON CONFLICT(article,source_url) DO UPDATE SET
                    source_type=excluded.source_type,last_seen_at=excluded.last_seen_at
                """,
                (str(row["article"]),url,kind,first_seen,last_seen),
            )

        for row in conn.execute(
            "SELECT article,seller_id,seller_name,seller_url,first_seen_at,last_seen_at FROM offers WHERE active=1"
        ).fetchall():
            own = (
                (str(row["seller_id"] or "").strip() in seller_ids)
                or (expected and normalized(row["seller_name"]) == expected)
            )
            if not own:
                continue
            url = str(row["seller_url"] or "").strip() or f"seller://{row['seller_id'] or expected}"
            first_seen = str(row["first_seen_at"] or now)
            last_seen = str(row["last_seen_at"] or first_seen)
            conn.execute(
                """
                INSERT INTO catalog_sources(source_url,source_type,label,first_seen_at,last_seen_at,active)
                VALUES(?,'CLIENT_CATALOG','',?,?,1)
                ON CONFLICT(source_url) DO UPDATE SET
                    source_type='CLIENT_CATALOG',last_seen_at=excluded.last_seen_at,active=1
                """,
                (url,first_seen,last_seen),
            )
            conn.execute(
                """
                INSERT INTO product_sources(
                    article,source_url,source_type,first_seen_at,last_seen_at,last_run_id,page_no
                ) VALUES(?,?,'CLIENT_CATALOG',?,?, '',0)
                ON CONFLICT(article,source_url) DO UPDATE SET
                    source_type='CLIENT_CATALOG',last_seen_at=excluded.last_seen_at
                """,
                (str(row["article"]),url,first_seen,last_seen),
            )

    client_count = conn.execute(
        "SELECT COUNT(DISTINCT article) FROM product_sources WHERE source_type='CLIENT_CATALOG'"
    ).fetchone()[0]
    market_count = conn.execute(
        "SELECT COUNT(DISTINCT article) FROM product_sources WHERE source_type='MARKET_CATEGORY'"
    ).fetchone()[0]
    conn.close()

    print("Ozon source migration: OK")
    print(f"Client catalogue: {client_count}")
    print(f"Market listings: {market_count}")
    print("No products or price history were deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
