from __future__ import annotations
import sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'collectors'/'ozon'/'data'/'ozon_registry.db'
MARKET_URLS=[
'https://www.ozon.ru/category/shiny-8502/',
'https://www.ozon.ru/category/shiny-letnie-8506/',
'https://www.ozon.ru/category/shiny-zimnie-8803/?__rr=1',
'https://www.ozon.ru/category/vsesezonnye-shiny-37388/',
]
SCHEMA="""
CREATE TABLE IF NOT EXISTS market_search_jobs (client_article TEXT PRIMARY KEY,query_text TEXT NOT NULL DEFAULT '',query_url TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'NEW',attempts INTEGER NOT NULL DEFAULT 0,candidates_found INTEGER NOT NULL DEFAULT 0,exact_found INTEGER NOT NULL DEFAULT 0,comparable_found INTEGER NOT NULL DEFAULT 0,last_search_at TEXT,last_error TEXT NOT NULL DEFAULT '',last_run_id TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS market_search_candidates (client_article TEXT NOT NULL,candidate_article TEXT NOT NULL,query_text TEXT NOT NULL DEFAULT '',query_url TEXT NOT NULL DEFAULT '',catalog_rank INTEGER NOT NULL DEFAULT 0,match_level TEXT NOT NULL DEFAULT 'REJECTED',match_score REAL NOT NULL DEFAULT 0,match_method TEXT NOT NULL DEFAULT '',match_reason TEXT NOT NULL DEFAULT '',reasons_json TEXT NOT NULL DEFAULT '[]',active INTEGER NOT NULL DEFAULT 1,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,last_checked_at TEXT NOT NULL,last_run_id TEXT NOT NULL DEFAULT '',PRIMARY KEY(client_article,candidate_article));
CREATE INDEX IF NOT EXISTS idx_market_search_candidates_level ON market_search_candidates(client_article,active,match_level,match_score DESC);
"""
def main():
    urls_path=ROOT/'collectors'/'ozon'/'START_URLS.txt'
    existing=[]
    if urls_path.exists(): existing=[x.strip() for x in urls_path.read_text(encoding='utf-8-sig',errors='replace').splitlines() if x.strip() and not x.strip().startswith('#')]
    for url in MARKET_URLS:
        if url not in existing: existing.append(url)
    urls_path.write_text('\n'.join(existing)+'\n',encoding='utf-8')
    if DB.exists():
        conn=sqlite3.connect(DB); conn.executescript(SCHEMA)
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_sources'").fetchone():
            conn.execute("UPDATE catalog_sources SET source_type='MARKET_SEARCH' WHERE lower(source_url) LIKE '%/search/%'")
            conn.execute("UPDATE product_sources SET source_type='MARKET_SEARCH' WHERE lower(source_url) LIKE '%/search/%'")
        conn.commit();
        print('Ozon registry:',DB)
        print('Client products:',conn.execute("SELECT COUNT(DISTINCT article) FROM product_sources WHERE source_type='CLIENT_CATALOG'").fetchone()[0] if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_sources'").fetchone() else 0)
        conn.close()
    else: print('Ozon registry not found; tables will be created on first run.')
    print('Market categories:',len(MARKET_URLS))
    print('Migration 3.3.2: OK')
    return 0
if __name__=='__main__': raise SystemExit(main())
