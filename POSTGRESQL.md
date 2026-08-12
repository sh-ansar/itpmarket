# PostgreSQL

Spyon uses SQLite by default. To initialize or start it on PostgreSQL, set the
connection in the process environment; credentials are never stored in the
repository.

For the local Windows installation, run `SETUP_POSTGRES.bat` once. It creates
an application-owned cluster on `127.0.0.1:55433`, generates a random SCRAM
password under ignored `.runtime`, migrates and verifies the current data.
After that, `START.bat` starts and selects PostgreSQL automatically.

```powershell
$env:ITP_STORAGE_BACKEND = "postgresql"
$env:DATABASE_URL = "postgresql://spyon:password@127.0.0.1:5432/spyon"
.\start.bat
```

On the first start, `engine/postgres_initialize.py` creates the isolated
schemas `app`, `ozon_ru`, and `ozon_kz`, copies the existing SQLite data,
installs indexes and foreign keys, and verifies row digests. Later starts only
check the schema and never overwrite live PostgreSQL data.

Manual commands:

```powershell
python engine/postgres_bootstrap.py plan
python engine/postgres_initialize.py --check
python engine/postgres_bootstrap.py verify --database-url $env:DATABASE_URL
```

Keep the SQLite files until the first PostgreSQL acceptance test and backup
have completed. PostgreSQL mode is shared by the web application, scheduler,
reports, and marketplace collectors.
