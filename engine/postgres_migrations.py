from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

AUTO_MARKER = "SPYON-AUTO-MIGRATION"

ADVISORY_LOCK_KEY = 724_910_826_202_608_20

BASELINE_MARKERS = {
    "20260818_multi_seller_v1.sql":
        "schema_multi_seller_v1_backfilled",
    "20260818_inventory_matching_v1.sql":
        "schema_inventory_matching_v1",
    "20260818_telegram_notifications_v1.sql":
        "schema_telegram_notifications_v1",
    "20260819_email_auth_notifications_v1.sql":
        "schema_email_auth_notifications_v1",
}

FORBIDDEN_AUTO_SQL = re.compile(
    r"\b("
    r"DROP|"
    r"TRUNCATE|"
    r"DELETE|"
    r"VACUUM|"
    r"REINDEX"
    r")\b",
    re.IGNORECASE,
)

DOLLAR_TAG_RE = re.compile(
    r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$"
)


class MigrationError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def database_url() -> str:
    value = str(
        os.environ.get("DATABASE_URL") or ""
    ).strip()

    if not value.startswith(
        ("postgresql://", "postgres://")
    ):
        raise MigrationError(
            "DATABASE_URL must point to PostgreSQL."
        )

    return value


def migration_files(root: Path) -> list[Path]:
    folder = root / "migrations"

    if not folder.is_dir():
        return []

    return sorted(
        path
        for path in folder.glob("*.sql")
        if re.fullmatch(
            r"\d{8}_[A-Za-z0-9_.-]+\.sql",
            path.name,
        )
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def split_sql(text: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []

    index = 0
    length = len(text)

    single_quote = False
    double_quote = False
    line_comment = False
    block_comment = False
    dollar_tag: str | None = None

    while index < length:
        char = text[index]
        next_char = (
            text[index + 1]
            if index + 1 < length
            else ""
        )

        if line_comment:
            if char == "\n":
                line_comment = False
                buffer.append("\n")
            index += 1
            continue

        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                buffer.append(" ")
                continue

            index += 1
            continue

        if dollar_tag is not None:
            if text.startswith(dollar_tag, index):
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue

            buffer.append(char)
            index += 1
            continue

        if single_quote:
            buffer.append(char)

            if char == "'":
                if next_char == "'":
                    buffer.append(next_char)
                    index += 2
                    continue

                single_quote = False

            index += 1
            continue

        if double_quote:
            buffer.append(char)

            if char == '"':
                if next_char == '"':
                    buffer.append(next_char)
                    index += 2
                    continue

                double_quote = False

            index += 1
            continue

        if char == "-" and next_char == "-":
            line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue

        if char == "'":
            single_quote = True
            buffer.append(char)
            index += 1
            continue

        if char == '"':
            double_quote = True
            buffer.append(char)
            index += 1
            continue

        if char == "$":
            match = DOLLAR_TAG_RE.match(
                text,
                index,
            )

            if match:
                dollar_tag = match.group(0)
                buffer.append(dollar_tag)
                index = match.end()
                continue

        if char == ";":
            statement = "".join(
                buffer
            ).strip()

            if statement:
                statements.append(statement)

            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    tail = "".join(buffer).strip()

    if tail:
        statements.append(tail)

    return statements


def validate_pending_migration(
    path: Path,
    text: str,
) -> list[str]:
    if AUTO_MARKER not in text:
        raise MigrationError(
            f"{path.name}: missing "
            f"{AUTO_MARKER} marker"
        )

    statements = split_sql(text)

    if len(statements) < 3:
        raise MigrationError(
            f"{path.name}: migration is incomplete"
        )

    if statements[0].strip().upper() != "BEGIN":
        raise MigrationError(
            f"{path.name}: must start with BEGIN"
        )

    if statements[-1].strip().upper() != "COMMIT":
        raise MigrationError(
            f"{path.name}: must end with COMMIT"
        )

    body = statements[1:-1]

    for statement in body:
        if FORBIDDEN_AUTO_SQL.search(statement):
            raise MigrationError(
                f"{path.name}: destructive SQL "
                "requires manual deployment"
            )

        if re.search(
            r"\bCONCURRENTLY\b",
            statement,
            re.IGNORECASE,
        ):
            raise MigrationError(
                f"{path.name}: CONCURRENTLY is not "
                "allowed in automatic migrations"
            )

        if statement.strip().upper() in {
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
        }:
            raise MigrationError(
                f"{path.name}: nested transaction "
                "control is not allowed"
            )

    return body


def _tracking_exists(conn: Any) -> bool:
    row = conn.execute(
        """
        SELECT
            to_regclass(
                'app.schema_migrations'
            ) IS NOT NULL
        """
    ).fetchone()

    return bool(row and row[0])


def _load_tracking(
    conn: Any,
) -> dict[str, str]:
    if not _tracking_exists(conn):
        return {}

    rows = conn.execute(
        """
        SELECT migration_name,sha256
        FROM app.schema_migrations
        """
    ).fetchall()

    return {
        str(row[0]): str(row[1])
        for row in rows
    }


def _load_metadata_markers(
    conn: Any,
) -> dict[str, str]:
    values = tuple(
        BASELINE_MARKERS.values()
    )

    if not values:
        return {}

    rows = conn.execute(
        """
        SELECT key,value
        FROM app.metadata
        WHERE key = ANY(%s)
        """,
        (list(values),),
    ).fetchall()

    return {
        str(row[0]): str(row[1] or "")
        for row in rows
    }


def migration_status(
    root: Path,
    db_url: str,
) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise MigrationError(
            "Install requirements-postgres.txt."
        ) from exc

    files = migration_files(root)

    with psycopg.connect(
        db_url,
        connect_timeout=10,
        autocommit=True,
    ) as conn:
        tracked = _load_tracking(conn)
        markers = _load_metadata_markers(conn)

    applied: list[str] = []
    baseline_untracked: list[str] = []
    pending: list[str] = []
    changed: list[str] = []
    blocked: list[dict[str, str]] = []

    for path in files:
        digest = file_sha256(path)
        recorded = tracked.get(path.name)

        if recorded is not None:
            if recorded != digest:
                changed.append(path.name)
            else:
                applied.append(path.name)

            continue

        marker = BASELINE_MARKERS.get(
            path.name
        )

        if marker and marker in markers:
            baseline_untracked.append(
                path.name
            )
            continue

        text = path.read_text(
            encoding="utf-8-sig"
        )

        try:
            validate_pending_migration(
                path,
                text,
            )
        except MigrationError as exc:
            blocked.append({
                "name": path.name,
                "reason": str(exc),
            })
            continue

        pending.append(path.name)

    return {
        "ok": not changed and not blocked,
        "migration_count": len(files),
        "applied": applied,
        "baseline_untracked":
            baseline_untracked,
        "pending": pending,
        "changed": changed,
        "blocked": blocked,
        "applied_count": len(applied),
        "baseline_untracked_count":
            len(baseline_untracked),
        "pending_count": len(pending),
        "changed_count": len(changed),
        "blocked_count": len(blocked),
    }


def _ensure_tracking_table(
    conn: Any,
) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS
        app.schema_migrations (
            migration_name text PRIMARY KEY,
            sha256 text NOT NULL,
            applied_at text NOT NULL
        )
        """
    )


def _record_existing_baselines(
    conn: Any,
    root: Path,
) -> None:
    markers = _load_metadata_markers(conn)

    for path in migration_files(root):
        marker = BASELINE_MARKERS.get(
            path.name
        )

        if not marker:
            continue

        applied_at = markers.get(marker)

        if applied_at is None:
            continue

        conn.execute(
            """
            INSERT INTO app.schema_migrations(
                migration_name,
                sha256,
                applied_at
            )
            VALUES(%s,%s,%s)
            ON CONFLICT(migration_name)
            DO NOTHING
            """,
            (
                path.name,
                file_sha256(path),
                applied_at or now_iso(),
            ),
        )


def apply_migrations(
    root: Path,
    db_url: str,
) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise MigrationError(
            "Install requirements-postgres.txt."
        ) from exc

    files = migration_files(root)

    with psycopg.connect(
        db_url,
        connect_timeout=10,
        autocommit=True,
    ) as conn:
        conn.execute(
            "SELECT pg_advisory_lock(%s)",
            (ADVISORY_LOCK_KEY,),
        )

        try:
            _ensure_tracking_table(conn)

            _record_existing_baselines(
                conn,
                root,
            )

            tracked = _load_tracking(conn)

            for path in files:
                digest = file_sha256(path)
                recorded = tracked.get(
                    path.name
                )

                if recorded is not None:
                    if recorded != digest:
                        raise MigrationError(
                            f"{path.name}: applied "
                            "migration checksum changed"
                        )

                    continue

                text = path.read_text(
                    encoding="utf-8-sig"
                )

                body = validate_pending_migration(
                    path,
                    text,
                )

                with conn.transaction():
                    conn.execute(
                        """
                        SET LOCAL
                        lock_timeout = '15s'
                        """
                    )

                    conn.execute(
                        """
                        SET LOCAL
                        statement_timeout = '5min'
                        """
                    )

                    for statement in body:
                        conn.execute(statement)

                    conn.execute(
                        """
                        INSERT INTO
                        app.schema_migrations(
                            migration_name,
                            sha256,
                            applied_at
                        )
                        VALUES(%s,%s,%s)
                        """,
                        (
                            path.name,
                            digest,
                            now_iso(),
                        ),
                    )

                tracked[path.name] = digest

        finally:
            conn.execute(
                "SELECT pg_advisory_unlock(%s)",
                (ADVISORY_LOCK_KEY,),
            )

    result = migration_status(
        root,
        db_url,
    )

    if (
        result["pending_count"]
        or result["changed_count"]
        or result["blocked_count"]
        or result[
            "baseline_untracked_count"
        ]
    ):
        raise MigrationError(
            "Migration state is not clean "
            "after apply."
        )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Spyon append-only PostgreSQL "
            "migration runner."
        )
    )

    parser.add_argument(
        "command",
        choices=("status", "apply"),
    )

    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__)
        .resolve()
        .parents[1],
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args()

    root = args.root.resolve()
    db_url = database_url()

    try:
        if args.command == "status":
            result = migration_status(
                root,
                db_url,
            )
        else:
            result = apply_migrations(
                root,
                db_url,
            )

    except Exception as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(
                f"MIGRATION ERROR: {exc}"
            )

        return 2

    if args.json:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
            )
        )
    else:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )

    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
