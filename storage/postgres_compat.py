from __future__ import annotations

import os
import queue
import re
import sqlite3
import threading
from contextlib import contextmanager
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from storage.database_backend import DatabaseBackend, DatabaseSettings


# PostgreSQL catalog introspection is identical for every connection during one
# application process.  Reading information_schema for every HTTP request made
# the UI progressively feel much slower after the PostgreSQL migration.
_METADATA_CACHE: dict[
    tuple[str, str], tuple[dict[str, tuple[str, ...]], dict[str, str]]
] = {}
_METADATA_LOCK = threading.Lock()
_POOL_LOCK = threading.Lock()
class _PostgresPool:
    """A bounded idle cache plus a hard cap on checked-out connections."""

    def __init__(self, size: int) -> None:
        self.idle: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=size)
        self.slots = threading.BoundedSemaphore(value=size)


_CONNECTION_POOLS: dict[tuple[str, str], _PostgresPool] = {}
_POOL_SIZE = max(2, min(int(os.environ.get("ITP_POSTGRES_POOL_SIZE", "8")), 32))


def _postgres_pool(database_url: str, schema: str) -> _PostgresPool:
    key = (database_url, schema)
    with _POOL_LOCK:
        pool = _CONNECTION_POOLS.get(key)
        if pool is None:
            pool = _PostgresPool(_POOL_SIZE)
            _CONNECTION_POOLS[key] = pool
        return pool


class HybridRow(Mapping[str, Any]):
    """A psycopg row supporting both row[0] and row["column"]."""

    __slots__ = ("_columns", "_index", "_values")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._index = {name: offset for offset, name in enumerate(self._columns)}
        self._values = tuple(values)

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def __iter__(self) -> Iterator[Any]:
        # sqlite3.Row iterates over values, while dict(row) uses keys().
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._columns)


def _hybrid_row_factory(cursor: Any) -> Any:
    columns = tuple(str(column.name) for column in (cursor.description or ()))

    def make_row(values: Sequence[Any]) -> HybridRow:
        return HybridRow(columns, values)

    return make_row


def _schema_for_path(path: str | Path) -> str:
    normalized = str(path).replace("\\", "/").casefold()
    if "ozon_kz" in normalized or "ozon-kz" in normalized:
        return "ozon_kz"
    segments = [segment for segment in normalized.split("/") if segment]
    for index, segment in enumerate(segments):
        if (
            segment == "marketplaces"
            and index + 2 < len(segments)
            and segments[index + 2] == "ozon"
        ):
            return "ozon_ru"
    if "/collectors/ozon/" in normalized or normalized.endswith("ozon_registry.db"):
        return "ozon_ru"
    return "app"


def _qmarks_to_psycopg(query: str) -> str:
    result: list[str] = []
    quote = ""
    offset = 0
    while offset < len(query):
        character = query[offset]
        if quote:
            result.append(character)
            if character == quote:
                if offset + 1 < len(query) and query[offset + 1] == quote:
                    result.append(query[offset + 1])
                    offset += 1
                else:
                    quote = ""
        elif character in {"'", '"'}:
            quote = character
            result.append(character)
        elif character == "?":
            result.append("%s")
        else:
            result.append(character)
        offset += 1
    return "".join(result)


class EmptyCursor:
    rowcount = 0
    lastrowid = 0
    description = None

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []

    def __iter__(self) -> Iterator[Any]:
        return iter(())


class PostgresCursor:
    def __init__(self, connection: "PostgresConnection", raw_cursor: Any | None = None) -> None:
        self.connection = connection
        self.raw = raw_cursor or connection.raw.cursor(row_factory=_hybrid_row_factory)
        self.lastrowid = 0

    @property
    def rowcount(self) -> int:
        return int(self.raw.rowcount)

    @property
    def description(self) -> Any:
        return self.raw.description

    def execute(
        self,
        query: str,
        parameters: Sequence[Any] | Mapping[str, Any] | None = None,
    ) -> "PostgresCursor":
        translated, table = self.connection.translate(query)
        identity = self.connection._identity_keys.get(table)
        capture_identity = bool(
            identity
            and re.match(r"^\s*INSERT\b", translated, re.I)
            and not re.search(r"\bRETURNING\b", translated, re.I)
        )
        if capture_identity:
            translated = translated.rstrip().rstrip(";") + f' RETURNING "{identity}"'
        self.raw.execute(translated, parameters or ())
        if capture_identity:
            row = self.raw.fetchone()
            self.lastrowid = int(row[0]) if row else 0
        else:
            self.lastrowid = 0
        return self

    def executemany(self, query: str, parameters: Sequence[Sequence[Any]]) -> "PostgresCursor":
        translated, _ = self.connection.translate(query)
        self.raw.executemany(translated, parameters)
        self.lastrowid = 0
        return self

    def fetchone(self) -> Any:
        return self.raw.fetchone()

    def fetchall(self) -> list[Any]:
        return self.raw.fetchall()

    def close(self) -> None:
        self.raw.close()

    def __iter__(self) -> Iterator[Any]:
        return iter(self.raw)


class PostgresConnection:
    def __init__(self, database_url: str, schema: str, timeout: float = 30) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Для PostgreSQL установите requirements-postgres.txt") from exc
        self.schema = schema
        self._pool = _postgres_pool(database_url, schema)
        self._closed = False
        self.raw = None
        acquired = self._pool.slots.acquire(timeout=max(0.0, float(timeout)))
        if not acquired:
            raise TimeoutError(
                f"PostgreSQL connection pool exhausted after {float(timeout):g}s."
            )
        try:
            while self.raw is None:
                try:
                    candidate = self._pool.idle.get_nowait()
                except queue.Empty:
                    candidate = None
                if candidate is None:
                    self.raw = psycopg.connect(database_url, row_factory=_hybrid_row_factory)
                    self.raw.execute(f'SET search_path TO "{schema}"')
                    # Persist the session setting before any read-only caller can
                    # return the connection with a rollback.
                    self.raw.commit()
                    break
                if not bool(getattr(candidate, "closed", True)):
                    try:
                        candidate.rollback()
                        self.raw = candidate
                    except Exception:
                        try:
                            candidate.close()
                        except Exception:
                            pass
        except Exception:
            self._pool.slots.release()
            raise
        self._primary_keys: dict[str, tuple[str, ...]] = {}
        self._identity_keys: dict[str, str] = {}
        cache_key = (database_url, schema)
        try:
            with _METADATA_LOCK:
                cached = _METADATA_CACHE.get(cache_key)
            if cached is None:
                self._load_metadata()
                with _METADATA_LOCK:
                    _METADATA_CACHE[cache_key] = (
                        dict(self._primary_keys), dict(self._identity_keys)
                    )
            else:
                self._primary_keys = dict(cached[0])
                self._identity_keys = dict(cached[1])
        except Exception:
            try:
                self.raw.close()
            finally:
                self._closed = True
                self._pool.slots.release()
            raise

    @property
    def row_factory(self) -> Any:
        return _hybrid_row_factory

    @row_factory.setter
    def row_factory(self, _value: Any) -> None:
        pass

    def _load_metadata(self) -> None:
        with self.raw.cursor() as cursor:
            cursor.execute(
                """
                SELECT tc.table_name,kcu.column_name,kcu.ordinal_position
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_schema=kcu.constraint_schema
                 AND tc.constraint_name=kcu.constraint_name
                WHERE tc.constraint_schema=%s AND tc.constraint_type='PRIMARY KEY'
                ORDER BY tc.table_name,kcu.ordinal_position
                """,
                (self.schema,),
            )
            grouped: dict[str, list[str]] = {}
            for table, column, _ in cursor.fetchall():
                grouped.setdefault(str(table), []).append(str(column))
            self._primary_keys = {key: tuple(value) for key, value in grouped.items()}
            cursor.execute(
                """SELECT table_name,column_name FROM information_schema.columns
                   WHERE table_schema=%s AND is_identity='YES'""",
                (self.schema,),
            )
            self._identity_keys = {str(table): str(column) for table, column in cursor.fetchall()}
        self.raw.commit()

    @staticmethod
    def _table_name(query: str) -> str:
        match = re.search(
            r"\bINSERT\s+(?:OR\s+(?:IGNORE|REPLACE)\s+)?INTO\s+\"?([A-Za-z_][A-Za-z0-9_]*)",
            query,
            re.I,
        )
        return str(match.group(1)) if match else ""

    def _replace_upsert(self, query: str, table: str) -> str:
        query = re.sub(
            r"\bINSERT\s+OR\s+REPLACE\s+INTO\b",
            "INSERT INTO",
            query,
            count=1,
            flags=re.I,
        )
        match = re.search(
            r"\bINSERT\s+INTO\s+\"?[A-Za-z_][A-Za-z0-9_]*\"?\s*(?:\((.*?)\))?\s*VALUES\s*\(",
            query,
            re.I | re.S,
        )
        if not match:
            raise RuntimeError(f"Не удалось преобразовать SQLite upsert для {table}.")
        columns_raw = match.group(1)
        if columns_raw:
            columns = [value.strip().strip('"') for value in columns_raw.split(",")]
        else:
            with self.raw.cursor() as cursor:
                cursor.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position""",
                    (self.schema, table),
                )
                columns = [str(row[0]) for row in cursor.fetchall()]
            insertion = "(" + ",".join(f'"{column}"' for column in columns) + ") "
            query = re.sub(
                r"(\bINSERT\s+INTO\s+\"?[A-Za-z_][A-Za-z0-9_]*\"?\s*)VALUES",
                r"\1" + insertion + "VALUES",
                query,
                count=1,
                flags=re.I,
            )
        primary = self._primary_keys.get(table, ())
        if not primary:
            return query.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        updates = [column for column in columns if column not in primary]
        conflict = ",".join(f'"{column}"' for column in primary)
        if not updates:
            return query.rstrip().rstrip(";") + f" ON CONFLICT ({conflict}) DO NOTHING"
        assignment = ",".join(f'"{column}"=EXCLUDED."{column}"' for column in updates)
        return query.rstrip().rstrip(";") + f" ON CONFLICT ({conflict}) DO UPDATE SET {assignment}"

    def translate(self, query: str) -> tuple[str, str]:
        value = str(query).strip()
        if not value:
            return value, ""
        if re.match(r"^BEGIN\s+IMMEDIATE\b", value, re.I):
            return "BEGIN", ""
        if re.match(r"^PRAGMA\b", value, re.I):
            return "SELECT 1 WHERE FALSE", ""
        if re.search(r"\bFROM\s+sqlite_master\b", value, re.I):
            if re.search(r"\bname\s*=\s*\?", value, re.I):
                value = (
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=current_schema() AND table_name=?"
                )
            else:
                value = (
                    "SELECT table_name AS name FROM information_schema.tables "
                    "WHERE table_schema=current_schema()"
                )
        table = self._table_name(value)
        if re.search(r"\bINSERT\s+OR\s+REPLACE\b", value, re.I):
            value = self._replace_upsert(value, table)
        elif re.search(r"\bINSERT\s+OR\s+IGNORE\b", value, re.I):
            value = re.sub(
                r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
                "INSERT INTO",
                value,
                count=1,
                flags=re.I,
            )
            value = value.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        value = re.sub(
            r"([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\?\s+COLLATE\s+NOCASE",
            r"LOWER(\1)=LOWER(?)",
            value,
            flags=re.I,
        )
        value = re.sub(r"\s+COLLATE\s+NOCASE\b", "", value, flags=re.I)
        value = re.sub(
            r"datetime\(\s*'now'\s*,\s*'localtime'\s*\)",
            "CURRENT_TIMESTAMP",
            value,
            flags=re.I,
        )
        value = re.sub(
            r"datetime\(\s*'now'\s*\)",
            "TO_CHAR(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')",
            value,
            flags=re.I,
        )
        value = re.sub(
            r"datetime\(\s*\?\s*\)",
            "CAST(NULLIF(?, '') AS timestamp)",
            value,
            flags=re.I,
        )
        value = re.sub(
            r"datetime\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
            r"CAST(NULLIF(\1, '') AS timestamp)",
            value,
            flags=re.I,
        )
        value = re.sub(
            r"json_extract\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*'\$\[0\]'\s*\)",
            r"(CAST(\1 AS jsonb)->>0)",
            value,
            flags=re.I,
        )
        value = re.sub(r"\bMAX\(([^()]*,[^()]*)\)", r"GREATEST(\1)", value, flags=re.I)
        value = re.sub(r"\bMIN\(([^()]*,[^()]*)\)", r"LEAST(\1)", value, flags=re.I)
        return _qmarks_to_psycopg(value), table

    def cursor(self) -> PostgresCursor:
        return PostgresCursor(self)

    def execute(
        self,
        query: str,
        parameters: Sequence[Any] | Mapping[str, Any] | None = None,
    ) -> PostgresCursor | EmptyCursor:
        if re.match(r"^\s*PRAGMA\b", str(query), re.I):
            return EmptyCursor()
        return PostgresCursor(self).execute(query, parameters)

    def executemany(self, query: str, parameters: Sequence[Sequence[Any]]) -> PostgresCursor:
        return PostgresCursor(self).executemany(query, parameters)

    def executescript(self, _script: str) -> EmptyCursor:
        # Schemas are versioned by engine/postgres_bootstrap.py.
        return EmptyCursor()

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self.raw.rollback()
            except Exception:
                try:
                    self.raw.close()
                except Exception:
                    pass
                return
            try:
                self._pool.idle.put_nowait(self.raw)
            except queue.Full:
                self.raw.close()
        finally:
            self._pool.slots.release()

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def connect_database(
    path: str | Path,
    timeout: float = 30,
    *,
    uri: bool = False,
    schema: str | None = None,
) -> sqlite3.Connection | PostgresConnection:
    settings = DatabaseSettings.from_environment()
    if settings.backend is DatabaseBackend.SQLITE:
        connection = sqlite3.connect(path, timeout=timeout, uri=uri)
        connection.row_factory = sqlite3.Row
        return connection
    return PostgresConnection(
        settings.database_url, schema or _schema_for_path(path), timeout=timeout
    )


def is_postgres_connection(connection: Any) -> bool:
    return isinstance(connection, PostgresConnection)


def configure_connection(
    connection: sqlite3.Connection | PostgresConnection,
    *,
    foreign_keys: bool = False,
    busy_timeout: int | None = None,
    journal_mode: str | None = None,
    synchronous: str | None = None,
) -> sqlite3.Connection | PostgresConnection:
    """Apply SQLite transport tuning only at the storage boundary.

    PostgreSQL receives none of SQLite's PRAGMA statements; server-side
    durability and FK enforcement are configured in the database itself.
    """
    if is_postgres_connection(connection):
        return connection
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys=ON")
    if busy_timeout is not None:
        connection.execute(f"PRAGMA busy_timeout={max(0, int(busy_timeout))}")
    if journal_mode:
        connection.execute(f"PRAGMA journal_mode={str(journal_mode).upper()}")
    if synchronous:
        connection.execute(f"PRAGMA synchronous={str(synchronous).upper()}")
    return connection


@contextmanager
def transaction(
    connection: sqlite3.Connection | PostgresConnection,
    *,
    immediate: bool = False,
) -> Iterator[sqlite3.Connection | PostgresConnection]:
    """Run a portable transaction; PostgreSQL never sees BEGIN IMMEDIATE."""
    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def table_exists(
    connection: sqlite3.Connection | PostgresConnection, table: str
) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (str(table),)
    ).fetchone()
    return row is not None


def database_error_types() -> tuple[type[BaseException], ...]:
    try:
        import psycopg

        return (sqlite3.Error, psycopg.Error)
    except ImportError:
        return (sqlite3.Error,)


def integrity_error_types() -> tuple[type[BaseException], ...]:
    try:
        import psycopg

        return (sqlite3.IntegrityError, psycopg.IntegrityError)
    except ImportError:
        return (sqlite3.IntegrityError,)
