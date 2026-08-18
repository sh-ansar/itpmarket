from __future__ import annotations

import os


def main() -> int:
    database_url = str(os.environ.get("DATABASE_URL") or "").strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL is missing or is not PostgreSQL.")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL runtime dependency is unavailable.") from exc
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT 1 FROM information_schema.tables
                   WHERE table_schema='app' AND table_name='tenants'"""
            )
            if cursor.fetchone() is None:
                raise RuntimeError("app.tenants is missing.")
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise RuntimeError("PostgreSQL SELECT probe failed.")
    print("connection and app.tenants are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
