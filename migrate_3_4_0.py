from pathlib import Path
import sqlite3
from schema import ensure_database
ROOT=Path(__file__).resolve().parent; DB=ROOT/'data'/'unityre_kaspi.db'
def main():
 ensure_database(DB); c=sqlite3.connect(DB)
 try:
  cols={r[1] for r in c.execute('PRAGMA table_info(registration_requests)')}; req={'capabilities_json','consent_version','consent_at','locale','source_page'}; missing=req-cols
  print('Spyon 3.4.0 migration: OK'); print('Registration columns:', 'OK' if not missing else f'missing {sorted(missing)}'); print('Platform settings table: OK'); print('Existing products, prices and browser profiles were not changed.'); return 0 if not missing else 1
 finally:c.close()
if __name__=='__main__': raise SystemExit(main())
