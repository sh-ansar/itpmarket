from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.database_backend import DatabaseBackend, DatabaseSettings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Безопасная резервная копия SQLite")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _postgres_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    roots = sorted(
        Path("C:/Program Files/PostgreSQL").glob(f"*/bin/{name}.exe"),
        reverse=True,
    )
    if roots:
        return str(roots[0])
    raise RuntimeError(f"Не найден {name}. Установите клиентские инструменты PostgreSQL.")


def _backup_postgres(output: Path, database_url: str) -> Path:
    parsed = urlparse(database_url)
    database = unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not database:
        raise RuntimeError("Некорректный DATABASE_URL для резервной копии.")
    target = output / f"spyon_postgresql_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dump"
    environment = os.environ.copy()
    if parsed.password:
        environment["PGPASSWORD"] = unquote(parsed.password)
    command = [
        _postgres_binary("pg_dump"), "--format=custom", "--no-owner", "--no-acl",
        "--host", parsed.hostname, "--port", str(parsed.port or 5432),
        "--username", unquote(parsed.username or "postgres"), "--dbname", database,
        "--schema", "app", "--schema", "ozon_ru", "--schema", "ozon_kz",
        "--file", str(target),
    ]
    print("[Резервная копия] 1/2 PostgreSQL pg_dump")
    subprocess.run(command, env=environment, check=True)
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("pg_dump не создал резервную копию.")
    print("[Резервная копия] 2/2 Проверка архива")
    subprocess.run(
        [_postgres_binary("pg_restore"), "--list", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )
    print(f"[Резервная копия] Готово: {target}")
    return target


def main(args: argparse.Namespace) -> int:
    source = Path(args.db)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    settings = DatabaseSettings.from_environment()
    if settings.backend is DatabaseBackend.POSTGRESQL:
        _backup_postgres(output, settings.database_url)
        return 0
    if not source.exists():
        print("[Резервная копия] База данных пока не создана.")
        return 1
    target = output / f"unityre_kaspi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(source, timeout=60)
    dst = sqlite3.connect(target)
    try:
        print("[Резервная копия] 1/2 Чтение базы")
        src.backup(dst)
        print("[Резервная копия] 2/2 Проверка")
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check: {result}")
        dst.commit()
    finally:
        dst.close()
        src.close()
    print(f"[Резервная копия] Готово: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
