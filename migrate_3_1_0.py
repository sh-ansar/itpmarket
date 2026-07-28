from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from schema import ensure_database

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "unityre_kaspi.db"
BACKUPS = ROOT / "backups"


def main() -> int:
    if not DB.exists():
        print(f"Database not found: {DB}")
        return 1
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUPS / f"pre_3_1_0_{stamp}.db"
    shutil.copy2(DB, backup)
    ensure_database(DB)

    conn = sqlite3.connect(DB)
    try:
        catalog = conn.execute("SELECT COUNT(*) FROM catalog_products").fetchone()[0]
        legacy = conn.execute(
            "SELECT COUNT(*) FROM market_candidates WHERE candidate_product_code<>source_product_code"
        ).fetchone()[0]
        exact = conn.execute(
            "SELECT COUNT(*) FROM market_seller_offers WHERE source_product_code=candidate_product_code"
        ).fetchone()[0]
        scans = conn.execute("SELECT COUNT(*) FROM exact_offer_scans").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    print("Spyon 3.1.0 migration completed.")
    print(f"Backup: {backup}")
    print(f"Database integrity: {integrity}")
    print(f"Catalog products: {catalog}")
    print(f"Archived old candidates: {legacy}")
    print(f"Existing exact seller offers: {exact}")
    print(f"Exact-offer scan states: {scans}")
    print("Old candidates were not deleted and are excluded from current analytics.")
    return 0 if integrity == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
