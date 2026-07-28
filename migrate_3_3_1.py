from __future__ import annotations
from pathlib import Path
from urllib.parse import urlsplit
from schema import ensure_database

ROOT=Path(__file__).resolve().parent
DB_PATH=ROOT/'data'/'unityre_kaspi.db'
URLS_PATH=ROOT/'collectors'/'ozon'/'START_URLS.txt'
REQUIRED_MARKET_URLS=[
    'https://www.ozon.ru/category/shiny-8502/',
    'https://www.ozon.ru/category/shiny-letnie-8506/',
    'https://www.ozon.ru/category/shiny-zimnie-8803/?__rr=1',
    'https://www.ozon.ru/category/vsesezonnye-shiny-37388/',
]

def key(url:str)->str:
    p=urlsplit(url.strip())
    return (p.netloc.casefold()+p.path.rstrip('/').casefold())

def main()->int:
    ensure_database(DB_PATH)
    existing=[]
    if URLS_PATH.exists():
        existing=[line.strip() for line in URLS_PATH.read_text(encoding='utf-8-sig',errors='replace').splitlines() if line.strip() and not line.strip().startswith('#')]
    merged=[]; seen=set()
    for url in [*existing,*REQUIRED_MARKET_URLS]:
        k=key(url)
        if not k or k in seen: continue
        seen.add(k); merged.append(url)
    URLS_PATH.parent.mkdir(parents=True,exist_ok=True)
    URLS_PATH.write_text('\n'.join(merged)+'\n',encoding='utf-8')
    print('Spyon 3.3.1 migration: OK')
    print(f'Ozon sources: {len(merged)}')
    print('Marketplace permissions initialized for existing users.')
    print('Products, prices and history were not changed.')
    return 0

if __name__=='__main__': raise SystemExit(main())
