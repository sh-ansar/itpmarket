from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class DatabaseBackend(str, Enum):
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


@dataclass(frozen=True)
class DatabaseSettings:
    backend: DatabaseBackend
    database_url: str
    production: bool

    @classmethod
    def from_environment(cls) -> "DatabaseSettings":
        raw_backend = str(os.environ.get("ITP_STORAGE_BACKEND") or "").casefold()
        environment = str(os.environ.get("ITP_ENV") or "").casefold()
        production = environment == "production"
        if not raw_backend and environment in {"production", "staging", "preproduction"}:
            raise RuntimeError(
                "Для production-like runtime явно укажите ITP_STORAGE_BACKEND=postgresql."
            )
        raw_backend = raw_backend or "sqlite"
        if raw_backend not in {item.value for item in DatabaseBackend}:
            raise RuntimeError("ITP_STORAGE_BACKEND должен быть sqlite или postgresql.")
        backend = DatabaseBackend(raw_backend)
        database_url = str(os.environ.get("DATABASE_URL") or "").strip()
        if backend is DatabaseBackend.POSTGRESQL:
            parsed = urlparse(database_url)
            if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
                raise RuntimeError("Для PostgreSQL требуется корректный DATABASE_URL.")
        return cls(backend=backend, database_url=database_url, production=production)

    def assert_runtime_ready(self) -> None:
        if self.backend is DatabaseBackend.POSTGRESQL:
            try:
                import psycopg
            except ImportError as exc:
                raise RuntimeError(
                    "Для PostgreSQL установите зависимости из requirements-postgres.txt."
                ) from exc
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema='app' AND table_name='tenants'"
                    )
                    if cursor.fetchone() is None:
                        raise RuntimeError(
                            "PostgreSQL не инициализирован. Запустите "
                            "python engine/postgres_bootstrap.py apply --apply."
                        )
