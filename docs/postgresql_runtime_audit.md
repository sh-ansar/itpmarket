# PostgreSQL runtime SQLite audit

Audit date: 2026-08-30.

## Result

With `ITP_STORAGE_BACKEND=postgresql`, PostgreSQL is the application runtime
source of truth. `storage.database_backend.DatabaseSettings` rejects a missing
or invalid PostgreSQL URL before application startup, and
`storage.postgres_compat` is the sole runtime storage boundary used by the
application, services, workers, and collectors.

The production-like startup path must never fall back to
`data\unityre_kaspi.db`. The SQLite database is permitted only as a legacy
migration reader, an offline rollback/backup source, or an isolated test/tool
fixture.

## Allowed SQLite use

| Location | Purpose |
| --- | --- |
| `engine/postgres_migration.py`, `engine/postgres_bootstrap.py`, `migrate_spyon.py` | read-only legacy migration source |
| `engine/backup_database.py` | SQLite rollback/backup adapter |
| `schema.py` | SQLite bootstrap only when the explicit SQLite backend is selected |
| `storage/postgres_compat.py` | compatibility boundary; PostgreSQL connections never open the SQLite path |
| `tests/**`, `tools/**`, `.runtime/loadtest/**` | isolated fixtures, baselines, and load-test tooling |

## Runtime controls and verification

- PostgreSQL startup requires `ITP_STORAGE_BACKEND=postgresql` and a valid
  `DATABASE_URL`; `DatabaseSettings.assert_runtime_ready()` verifies the
  `app.tenants` table before the web application begins serving.
- `engine/postgres_initialize.py` provisions the canonical idempotent DDL on
  every initialize invocation, including already table-complete databases, so
  newly introduced columns, indexes, and baseline markers cannot be skipped.
  `--check` remains read-only.
- The canonical schema maps SQLite `REAL` values to PostgreSQL `DOUBLE
  PRECISION`.
- Historic baselines are adopted only after their concrete table/column/index
  contracts have been verified; `app.schema_migrations` records the checked-in
  SHA-256 digests.
- Migration verification treats only declared schema-owned `metadata` markers
  as allowed PostgreSQL-only rows. Unknown target metadata remains a failure.

For a local PostgreSQL smoke test, set an explicit local URL, run
`engine/postgres_initialize.py --check`, start the app on loopback, and verify
`/health` and `/ready`. Inspect logs for the configured backend and keep all
runtime databases, logs, credentials, forensic output, and load-test artifacts
ignored by Git.
