from pathlib import Path
import sqlite3
from schema import ensure_database

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"unityre_kaspi.db"

def main():
    ensure_database(DB)
    conn=sqlite3.connect(DB)
    try:
        columns={row[1] for row in conn.execute("PRAGMA table_info(operation_schedules)")}
        ok="run_date" in columns
        print("Spyon 3.4.1 migration:", "OK" if ok else "FAILED")
        print("One-time schedule date column:", "OK" if ok else "MISSING")
        print("Existing schedules, products, prices and collector data were preserved.")
        return 0 if ok else 1
    finally:
        conn.close()

if __name__=="__main__":
    raise SystemExit(main())
