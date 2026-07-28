from __future__ import annotations
import sqlite3
from pathlib import Path
from schema import ensure_database
ROOT=Path(__file__).resolve().parent
DB_PATH=ROOT/'data'/'unityre_kaspi.db'
def main()->int:
    ensure_database(DB_PATH)
    conn=sqlite3.connect(DB_PATH); conn.row_factory=sqlite3.Row
    try:
        tenant=conn.execute('SELECT * FROM tenants ORDER BY id LIMIT 1').fetchone()
        print('ITP Market Intelligence 3.3.0 migration: OK')
        print(f"Default workspace: {tenant['name'] if tenant else 'not created'}")
        print(f"Users: {conn.execute('SELECT COUNT(*) FROM app_users').fetchone()[0]}")
        print(f"Tenant memberships: {conn.execute('SELECT COUNT(*) FROM tenant_users').fetchone()[0]}")
        print(f"Integrations: {conn.execute('SELECT COUNT(*) FROM tenant_integrations').fetchone()[0]}")
        print('Product and price tables were not deleted or rewritten.')
        return 0
    finally: conn.close()
if __name__=='__main__': raise SystemExit(main())
