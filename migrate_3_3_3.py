from pathlib import Path
import sqlite3
from schema import ensure_database
ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'unityre_kaspi.db'
def main():
    ensure_database(DB)
    conn=sqlite3.connect(DB)
    cols={row[1] for row in conn.execute('PRAGMA table_info(app_user_preferences)')}
    assert 'theme' in cols
    conn.execute("UPDATE app_user_preferences SET theme='system' WHERE theme IS NULL OR theme NOT IN ('system','light','dark')")
    conn.commit()
    users=conn.execute('SELECT COUNT(*) FROM app_user_preferences').fetchone()[0]
    conn.close()
    print('ITP Market Intelligence 3.3.3 migration: OK')
    print(f'User interface preferences: {users}')
    print('Theme preference column: OK')
    print('Product, price and operation tables were not changed.')
    return 0
if __name__=='__main__': raise SystemExit(main())
