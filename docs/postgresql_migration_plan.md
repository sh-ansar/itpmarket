# PostgreSQL migration plan

This is an analysis and rollout plan only. It does not authorize a production
migration or a change of the active backend.

## Existing support

`storage/postgres_compat.py` provides a SQLite-shaped connection/cursor API on
top of psycopg, including mapping-and-index row access, placeholder conversion,
transaction handling, bounded per `(DATABASE_URL, schema)` pools, and common
SQLite upsert translation. `engine/postgres_bootstrap.py` and the PostgreSQL
migration helpers initialize and check schemas; the existing compatibility,
migration, and Windows-runtime tests exercise those paths.

The current default pool is `ITP_POSTGRES_POOL_SIZE=8` (bounded from 2 to 32).
For `max_parallel_tasks=6`, retain 8 initially: six collector workers plus the
web/scheduler path can make progress without unbounded connections. Measure
checked-out connections during the 5/10/20-company rehearsal before increasing
it; pool exhaustion must remain a visible timeout, not an implicit retry loop.

## SQLite-specific work still to audit

The compatibility layer deliberately rejects `PRAGMA` on PostgreSQL. SQLite
usage remains in application and collector paths, including `journal_mode`,
`synchronous`, `foreign_keys`, `busy_timeout`, `integrity_check`, and schema
inspection through `table_info`, `index_list`, `index_info`, and
`foreign_key_list`. These occur notably in catalog/offer engines, Ozon registry
and Ozon.kz storage, Halyk/Forte connectors, snapshot/backup tooling, and the
SQLite-to-PostgreSQL migration reader.

`sqlite3.Row` assumptions remain in collector and engine code that assigns
`conn.row_factory = sqlite3.Row`. The compatibility adapter emulates ordinary
mapping/index access, but code relying on concrete row type, SQLite connection
methods, or cursor `lastrowid` needs a PostgreSQL-specific test.

Review all SQL that uses SQLite-only forms before changing a collector backend:
`INSERT OR REPLACE`, `INSERT OR IGNORE`, `AUTOINCREMENT`, `datetime('now')`,
`COLLATE NOCASE`, `json_extract`, and SQLite conflict-target inference. The
adapter already translates several upsert and time forms; each remaining query
needs a primary-source PostgreSQL integration test. Standalone collector
registries (especially Ozon/Ozon.kz) must be treated separately from the app
database rather than silently pointed at PostgreSQL.

## Validation and rollout

1. Provision local PostgreSQL with a non-production database and set the
   backend only in that local environment.
2. Restore/migrate the sanitized production snapshot, record row counts and
   digests, then run `engine/postgres_initialize.py --check`.
3. Run the entire Python suite plus the PostgreSQL compatibility, migration and
   Windows-runtime suites against that database.
4. Run deterministic queue/load tests for 5, 10, and 20 companies (30/60/120
   operations), preserving `max_parallel=6` and the independent Ozon.ru and
   Ozon.kz browser resources.
5. Repeat the same checks in staging, including collector-specific storage and
   tenant/seller isolation. Only then request a separate production-change
   approval.
6. Production cutover, if approved, is a scheduled maintenance change: freeze
   writes, take and verify a PostgreSQL custom-format backup plus the SQLite
   source snapshot, migrate, run schema/readiness checks, and only then restart.

## Backup and rollback

Before migration use the documented `engine/backup_database.py` flow and verify
that `pg_restore --list` can read the resulting dump. Keep the pre-cutover
SQLite database and its checksum untouched. If validation fails, stop the new
application writers, restore the verified PostgreSQL backup (or revert the
backend configuration before any new writes), and return to the checked source
database through an approved rollback. Do not copy database files by hand and
do not use destructive cleanup as rollback.

## Scale-out prerequisites

The JSON task-state queue is protected for the current single-host process
model. Horizontal workers require a transactional shared task store with lease
ownership/expiry, idempotency keys, distributed resource locks, durable event
notifications, and a database-backed audit/history retention policy. Keep
collector commands idempotent and tenant/seller-scoped so a leased task can be
safely retried after worker loss.
