"""Database-backed lifecycle for immutable legal-document versions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Any

from markupsafe import Markup

from billing_service import (
    BILLING_SUPPLIER_SETTING_KEY,
    OPERATOR_LEGAL_FIELDS,
    OPERATOR_LEGAL_PROFILE,
)
from legal_documents import LEGAL_DOCUMENTS
from storage.postgres_compat import (
    PostgresConnection,
    configure_connection,
    connect_database,
)


LEGAL_TYPES = {"offer", "privacy"}


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
    )


def parse_bool(
    value: Any,
    default: bool = True,
) -> bool:
    if isinstance(value, bool):
        return value

    if value is None or value == "":
        return default

    return (
        str(value)
        .strip()
        .casefold()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )


def normalize_effective_at(
    value: Any,
) -> str:
    raw = str(
        value or ""
    ).strip()

    if not raw:
        return ""

    try:
        if len(raw) == 10:
            parsed = datetime.fromisoformat(
                raw + "T00:00:00+00:00"
            )
        else:
            parsed = datetime.fromisoformat(
                raw.replace(
                    "Z",
                    "+00:00",
                )
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            parsed = parsed.astimezone(
                timezone.utc
            )

    except ValueError as exc:
        raise ValueError(
            "Укажите корректную дату вступления в силу."
        ) from exc

    return parsed.isoformat(
        timespec="seconds"
    )


class LegalDocumentService:
    """
    Stores drafts and immutable published versions.

    legal_documents identifies a logical document type.
    Every historical/legal field belongs to legal_document_versions.
    """

    def __init__(
        self,
        db_path: Path,
    ) -> None:
        self.db_path = Path(
            db_path
        )

        self.ensure_schema()

    def _connect(
        self,
    ) -> Any:
        return configure_connection(
            connect_database(
                self.db_path,
                timeout=30,
            ),
            foreign_keys=True,
            busy_timeout=30000,
        )

    @staticmethod
    def _sqlite_columns(
        conn: Any,
        table: str,
    ) -> set[str]:
        return {
            str(row[1])
            for row in conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }

    @staticmethod
    def _audit(
        conn: Any,
        actor_user_id: int | None,
        action: str,
        entity_id: str,
        *,
        tenant_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO platform_audit_log(
                   actor_user_id,
                   action,
                   tenant_id,
                   entity_type,
                   entity_id,
                   details_json,
                   created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                (
                    int(actor_user_id)
                    if actor_user_id is not None
                    else None
                ),
                str(action),
                (
                    int(tenant_id)
                    if tenant_id is not None
                    else None
                ),
                "legal_document",
                str(entity_id),
                json.dumps(
                    details or {},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now_iso(),
            ),
        )

    @staticmethod
    def _operator_snapshot(
        conn: Any,
    ) -> dict[str, str]:
        result = {
            key: str(
                OPERATOR_LEGAL_PROFILE.get(
                    key
                )
                or ""
            )
            for key in OPERATOR_LEGAL_FIELDS
        }

        row = conn.execute(
            """SELECT value_json
               FROM platform_settings
               WHERE setting_key=?""",
            (
                BILLING_SUPPLIER_SETTING_KEY,
            ),
        ).fetchone()

        if not row:
            return result

        try:
            stored = json.loads(
                str(
                    row["value_json"]
                    or "{}"
                )
            )
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            stored = {}

        if isinstance(
            stored,
            dict,
        ):
            for key in OPERATOR_LEGAL_FIELDS:
                if key in stored:
                    result[key] = str(
                        stored.get(key)
                        or ""
                    )

        return result

    @staticmethod
    def _hash_payload(
        *,
        document_type: str,
        number: str,
        version: str,
        title: str,
        effective_at: str,
        body_text: str,
        acceptance_text: str,
        requires_acceptance: bool,
        operator_snapshot: dict[str, Any],
    ) -> str:
        payload = {
            "document_type":
                document_type,
            "number":
                number,
            "version":
                version,
            "title":
                title,
            "effective_at":
                effective_at,
            "body_text":
                body_text,
            "acceptance_text":
                acceptance_text,
            "requires_acceptance":
                bool(
                    requires_acceptance
                ),
            "operator_snapshot":
                operator_snapshot,
        }

        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        return sha256(
            canonical.encode(
                "utf-8"
            )
        ).hexdigest()

    def ensure_schema(
        self,
    ) -> None:
        conn = self._connect()

        try:
            if isinstance(
                conn,
                PostgresConnection,
            ):
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS legal_documents(
                       id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                       document_type TEXT NOT NULL UNIQUE,
                       document_number TEXT NOT NULL,
                       title TEXT NOT NULL,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
                )

                conn.execute(
                    """CREATE TABLE IF NOT EXISTS legal_document_versions(
                       id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                       document_id BIGINT NOT NULL
                           REFERENCES legal_documents(id)
                           ON DELETE CASCADE,
                       version TEXT NOT NULL,
                       document_number TEXT NOT NULL DEFAULT '',
                       title TEXT NOT NULL DEFAULT '',
                       effective_at TEXT NOT NULL DEFAULT '',
                       status TEXT NOT NULL DEFAULT 'draft',
                       body_text TEXT NOT NULL DEFAULT '',
                       acceptance_text TEXT NOT NULL DEFAULT '',
                       operator_snapshot_json TEXT NOT NULL DEFAULT '{}',
                       content_sha256 TEXT NOT NULL,
                       requires_acceptance INTEGER NOT NULL DEFAULT 1,
                       created_by BIGINT
                           REFERENCES app_users(id)
                           ON DELETE SET NULL,
                       created_at TEXT NOT NULL,
                       published_by BIGINT
                           REFERENCES app_users(id)
                           ON DELETE SET NULL,
                       published_at TEXT,
                       archived_at TEXT,
                       UNIQUE(document_id,version)
                   )"""
                )

                for (
                    column,
                    ddl,
                ) in (
                    (
                        "document_number",
                        "TEXT NOT NULL DEFAULT ''",
                    ),
                    (
                        "title",
                        "TEXT NOT NULL DEFAULT ''",
                    ),
                    (
                        "effective_at",
                        "TEXT NOT NULL DEFAULT ''",
                    ),
                    (
                        "operator_snapshot_json",
                        "TEXT NOT NULL DEFAULT '{}'",
                    ),
                ):
                    conn.execute(
                        "ALTER TABLE "
                        "legal_document_versions "
                        f"ADD COLUMN IF NOT EXISTS "
                        f"{column} {ddl}"
                    )

                conn.execute(
                    """ALTER TABLE legal_acceptances
                       ADD COLUMN IF NOT EXISTS
                       legal_document_version_id BIGINT"""
                )

            else:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS legal_documents(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       document_type TEXT NOT NULL UNIQUE,
                       document_number TEXT NOT NULL,
                       title TEXT NOT NULL,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
                )

                conn.execute(
                    """CREATE TABLE IF NOT EXISTS legal_document_versions(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       document_id INTEGER NOT NULL,
                       version TEXT NOT NULL,
                       document_number TEXT NOT NULL DEFAULT '',
                       title TEXT NOT NULL DEFAULT '',
                       effective_at TEXT NOT NULL DEFAULT '',
                       status TEXT NOT NULL DEFAULT 'draft',
                       body_text TEXT NOT NULL DEFAULT '',
                       acceptance_text TEXT NOT NULL DEFAULT '',
                       operator_snapshot_json TEXT NOT NULL DEFAULT '{}',
                       content_sha256 TEXT NOT NULL,
                       requires_acceptance INTEGER NOT NULL DEFAULT 1,
                       created_by INTEGER,
                       created_at TEXT NOT NULL,
                       published_by INTEGER,
                       published_at TEXT,
                       archived_at TEXT,
                       UNIQUE(document_id,version),
                       FOREIGN KEY(document_id)
                           REFERENCES legal_documents(id)
                           ON DELETE CASCADE
                   )"""
                )

                columns = (
                    self._sqlite_columns(
                        conn,
                        "legal_document_versions",
                    )
                )

                for (
                    column,
                    ddl,
                ) in (
                    (
                        "document_number",
                        "TEXT NOT NULL DEFAULT ''",
                    ),
                    (
                        "title",
                        "TEXT NOT NULL DEFAULT ''",
                    ),
                    (
                        "effective_at",
                        "TEXT NOT NULL DEFAULT ''",
                    ),
                    (
                        "operator_snapshot_json",
                        "TEXT NOT NULL DEFAULT '{}'",
                    ),
                ):
                    if column not in columns:
                        conn.execute(
                            "ALTER TABLE "
                            "legal_document_versions "
                            f"ADD COLUMN "
                            f"{column} {ddl}"
                        )

                acceptance_columns = (
                    self._sqlite_columns(
                        conn,
                        "legal_acceptances",
                    )
                )

                if (
                    "legal_document_version_id"
                    not in acceptance_columns
                ):
                    conn.execute(
                        """ALTER TABLE legal_acceptances
                           ADD COLUMN
                           legal_document_version_id INTEGER"""
                    )

            conn.execute(
                """UPDATE legal_document_versions
                   SET document_number=COALESCE(
                         NULLIF(document_number,''),
                         (
                           SELECT d.document_number
                           FROM legal_documents d
                           WHERE d.id=
                             legal_document_versions.document_id
                         ),
                         ''
                       ),
                       title=COALESCE(
                         NULLIF(title,''),
                         (
                           SELECT d.title
                           FROM legal_documents d
                           WHERE d.id=
                             legal_document_versions.document_id
                         ),
                         ''
                       )
                   WHERE document_number=''
                      OR title=''"""
            )

            conn.execute(
                """CREATE INDEX IF NOT EXISTS
                   idx_legal_versions_current
                   ON legal_document_versions(
                     document_id,
                     status,
                     effective_at DESC,
                     published_at DESC,
                     id DESC
                   )"""
            )

            self._seed_legacy_versions(
                conn
            )

            self._link_legacy_acceptances(
                conn
            )

            conn.commit()

        finally:
            conn.close()

    def _seed_legacy_versions(
        self,
        conn: Any,
    ) -> None:
        """
        Import immutable current static definitions.

        Existing v1.0 SHA remains exactly the static-file SHA so
        already-recorded acceptances stay valid.
        """
        stamp = now_iso()

        for definition in (
            LEGAL_DOCUMENTS
            .current_documents()
        ):
            parent = conn.execute(
                """SELECT id
                   FROM legal_documents
                   WHERE document_type=?""",
                (
                    definition.document_type,
                ),
            ).fetchone()

            if parent is None:
                cursor = conn.execute(
                    """INSERT INTO legal_documents(
                         document_type,
                         document_number,
                         title,
                         created_at,
                         updated_at
                       ) VALUES(?,?,?,?,?)""",
                    (
                        definition.document_type,
                        definition.number,
                        definition.title,
                        stamp,
                        stamp,
                    ),
                )

                document_id = int(
                    cursor.lastrowid
                )

            else:
                document_id = int(
                    parent["id"]
                )

            existing = conn.execute(
                """SELECT id
                   FROM legal_document_versions
                   WHERE document_id=?
                     AND version=?""",
                (
                    document_id,
                    definition.version,
                ),
            ).fetchone()

            if existing is not None:
                conn.execute(
                    """UPDATE legal_document_versions
                       SET document_number=
                             CASE
                               WHEN document_number=''
                               THEN ?
                               ELSE document_number
                             END,
                           title=
                             CASE
                               WHEN title=''
                               THEN ?
                               ELSE title
                             END,
                           effective_at=
                             CASE
                               WHEN effective_at=''
                               THEN COALESCE(
                                 published_at,
                                 ?
                               )
                               ELSE effective_at
                             END
                       WHERE id=?""",
                    (
                        definition.number,
                        definition.title,
                        stamp,
                        int(
                            existing["id"]
                        ),
                    ),
                )

                continue

            body = "\n\n".join(
                block.get(
                    "text",
                    "",
                )
                for block
                in LEGAL_DOCUMENTS.blocks(
                    definition
                )
                if block.get(
                    "text"
                )
            )

            conn.execute(
                """INSERT INTO legal_document_versions(
                     document_id,
                     version,
                     document_number,
                     title,
                     effective_at,
                     status,
                     body_text,
                     acceptance_text,
                     operator_snapshot_json,
                     content_sha256,
                     requires_acceptance,
                     created_at,
                     published_at
                   ) VALUES(
                     ?,?,?,?,?, 'published',
                     ?,?,'{}',?,1,?,?
                   )""",
                (
                    document_id,
                    definition.version,
                    definition.number,
                    definition.title,
                    stamp,
                    body,
                    definition.acceptance_text,
                    LEGAL_DOCUMENTS.sha256(
                        definition
                    ),
                    stamp,
                    stamp,
                ),
            )

    def _link_legacy_acceptances(
        self,
        conn: Any,
    ) -> None:
        """
        Attach old evidence rows to matching immutable DB versions.
        No version/hash is rewritten.
        """
        conn.execute(
            """UPDATE legal_acceptances
               SET legal_document_version_id=(
                   SELECT v.id
                   FROM legal_document_versions v
                   JOIN legal_documents d
                     ON d.id=v.document_id
                   WHERE
                     d.document_type=
                       legal_acceptances.document_type
                     AND
                     v.version=
                       legal_acceptances.document_version
                     AND
                     v.content_sha256=
                       legal_acceptances.document_sha256
                   ORDER BY v.id DESC
                   LIMIT 1
               )
               WHERE legal_document_version_id IS NULL"""
        )

    @staticmethod
    def _metadata(
        row: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            operator_snapshot = json.loads(
                str(
                    row.get(
                        "operator_snapshot_json"
                    )
                    or "{}"
                )
            )
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            operator_snapshot = {}

        if not isinstance(
            operator_snapshot,
            dict,
        ):
            operator_snapshot = {}

        document_type = str(
            row["document_type"]
        )

        version = str(
            row["version"]
        )

        definition = (
            LEGAL_DOCUMENTS.get(
                document_type,
                version,
            )
        )

        pdf_available = bool(
            definition is not None
            and definition.pdf_path.is_file()
        )

        return {
            "id":
                int(
                    row["id"]
                ),
            "type":
                document_type,
            "number":
                str(
                    row.get(
                        "document_number"
                    )
                    or ""
                ),
            "title":
                str(
                    row.get(
                        "title"
                    )
                    or ""
                ),
            "version":
                version,
            "status":
                str(
                    row["status"]
                ),
            "sha256":
                str(
                    row["content_sha256"]
                ),
            "requires_acceptance":
                bool(
                    row["requires_acceptance"]
                ),
            "effective_at":
                row.get(
                    "effective_at"
                ),
            "published_at":
                row.get(
                    "published_at"
                ),
            "created_at":
                row.get(
                    "created_at"
                ),
            "accepted_count":
                int(
                    row.get(
                        "accepted_count"
                    )
                    or 0
                ),
            "operator_snapshot":
                operator_snapshot,
            "legacy_static":
                bool(
                    definition is not None
                    and version == "1.0"
                ),
            "pdf_available":
                pdf_available,
        }

    def _version(
        self,
        conn: Any,
        document_type: str,
        version: str | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = [
            str(
                document_type
                or ""
            )
            .strip()
            .casefold()
        ]

        where = (
            "d.document_type=?"
        )

        if version is None:
            where += (
                " AND v.status='published'"
                " AND ("
                "v.effective_at=''"
                " OR v.effective_at IS NULL"
                " OR v.effective_at<=?"
                ")"
            )

            params.append(
                now_iso()
            )

            order = (
                "ORDER BY "
                "CASE "
                "WHEN v.effective_at='' "
                "OR v.effective_at IS NULL "
                "THEN v.published_at "
                "ELSE v.effective_at "
                "END DESC,"
                "v.published_at DESC,"
                "v.id DESC "
                "LIMIT 1"
            )

        else:
            where += (
                " AND v.version=?"
            )

            params.append(
                str(
                    version
                ).strip()
            )

            order = "LIMIT 1"

        row = conn.execute(
            """SELECT
                   v.*,
                   d.document_type,
                   (
                     SELECT COUNT(
                       DISTINCT a.user_id
                     )
                     FROM legal_acceptances a
                     WHERE
                       a.legal_document_version_id=v.id
                   ) AS accepted_count
               FROM legal_document_versions v
               JOIN legal_documents d
                 ON d.id=v.document_id
               WHERE """
            + where
            + " "
            + order,
            params,
        ).fetchone()

        return (
            dict(row)
            if row
            else None
        )

    def get_version(
        self,
        document_type: str,
        version: str | None = None,
    ) -> dict[str, Any] | None:
        conn = self._connect()

        try:
            row = self._version(
                conn,
                document_type,
                version,
            )

            if not row:
                return None

            return {
                **self._metadata(
                    row
                ),
                "body_text":
                    str(
                        row["body_text"]
                    ),
                "acceptance_text":
                    str(
                        row[
                            "acceptance_text"
                        ]
                    ),
            }

        finally:
            conn.close()

    def current_documents(
        self,
    ) -> list[dict[str, Any]]:
        result: list[
            dict[str, Any]
        ] = []

        for document_type in (
            "offer",
            "privacy",
        ):
            item = self.get_version(
                document_type
            )

            if item:
                result.append(
                    item
                )

        return result

    def acceptance_records(
        self,
        *,
        ip_address: str,
        user_agent: str,
        locale: str,
        source: str = "registration",
    ) -> list[dict[str, Any]]:
        language = (
            str(
                locale
                or "ru"
            )
            .strip()
            .casefold()
        )

        if language not in {
            "ru",
            "kk",
            "en",
        }:
            language = "ru"

        stamp = now_iso()

        return [
            {
                "legal_document_version_id":
                    item["id"],
                "document_type":
                    item["type"],
                "document_number":
                    item["number"],
                "document_version":
                    item["version"],
                "document_sha256":
                    item["sha256"],
                "accepted_at":
                    stamp,
                "ip_address":
                    str(
                        ip_address
                        or ""
                    )[:128],
                "user_agent":
                    str(
                        user_agent
                        or ""
                    )[:1024],
                "locale":
                    language,
                "acceptance_text":
                    item[
                        "acceptance_text"
                    ],
                "source":
                    source,
            }
            for item
            in self.current_documents()
            if item[
                "requires_acceptance"
            ]
        ]

    def accepted_documents_for_user(
        self,
        user_id: int,
        tenant_id: int | None,
    ) -> list[dict[str, Any]]:
        """
        Return actual acceptance history, not only current documents.
        """
        conn = self._connect()

        try:
            rows = conn.execute(
                """SELECT
                     a.legal_document_version_id,
                     a.document_type,
                     a.document_number,
                     a.document_version,
                     a.document_sha256,
                     a.accepted_at,
                     a.acceptance_text,
                     a.locale,
                     a.source,
                     v.title,
                     v.effective_at,
                     v.published_at
                   FROM legal_acceptances a
                   LEFT JOIN legal_document_versions v
                     ON v.id=
                       a.legal_document_version_id
                   WHERE a.user_id=?
                   ORDER BY
                     a.accepted_at DESC,
                     a.id DESC""",
                (
                    int(
                        user_id
                    ),
                ),
            ).fetchall()

            result: list[
                dict[str, Any]
            ] = []

            for raw in rows:
                row = dict(
                    raw
                )

                definition = (
                    LEGAL_DOCUMENTS.get(
                        str(
                            row[
                                "document_type"
                            ]
                        ),
                        str(
                            row[
                                "document_version"
                            ]
                        ),
                    )
                )

                title = str(
                    row.get(
                        "title"
                    )
                    or ""
                )

                if (
                    not title
                    and definition
                    is not None
                ):
                    title = (
                        definition.title
                    )

                result.append(
                    {
                        "id":
                            int(
                                row.get(
                                    "legal_document_version_id"
                                )
                                or 0
                            ),
                        "type":
                            str(
                                row[
                                    "document_type"
                                ]
                            ),
                        "number":
                            str(
                                row[
                                    "document_number"
                                ]
                            ),
                        "title":
                            (
                                title
                                or str(
                                    row[
                                        "document_type"
                                    ]
                                )
                            ),
                        "version":
                            str(
                                row[
                                    "document_version"
                                ]
                            ),
                        "sha256":
                            str(
                                row[
                                    "document_sha256"
                                ]
                            ),
                        "accepted_at":
                            row[
                                "accepted_at"
                            ],
                        "acceptance_text":
                            str(
                                row[
                                    "acceptance_text"
                                ]
                            ),
                        "locale":
                            str(
                                row[
                                    "locale"
                                ]
                            ),
                        "source":
                            str(
                                row[
                                    "source"
                                ]
                            ),
                        "effective_at":
                            row.get(
                                "effective_at"
                            ),
                        "published_at":
                            row.get(
                                "published_at"
                            ),
                        "pdf_available":
                            bool(
                                definition
                                is not None
                                and
                                definition
                                .pdf_path
                                .is_file()
                            ),
                        "tenant_id":
                            tenant_id,
                    }
                )

            return result

        finally:
            conn.close()

    def required_for_user(
        self,
        user_id: int,
        tenant_id: int | None,
    ) -> list[dict[str, Any]]:
        if tenant_id is None:
            return []

        conn = self._connect()

        try:
            result: list[
                dict[str, Any]
            ] = []

            for document in (
                self.current_documents()
            ):
                if not document[
                    "requires_acceptance"
                ]:
                    continue

                accepted = conn.execute(
                    """SELECT id
                       FROM legal_acceptances
                       WHERE
                         user_id=?
                         AND tenant_id=?
                         AND document_type=?
                         AND document_version=?
                         AND document_sha256=?
                       LIMIT 1""",
                    (
                        int(
                            user_id
                        ),
                        int(
                            tenant_id
                        ),
                        document["type"],
                        document["version"],
                        document["sha256"],
                    ),
                ).fetchone()

                # v1.0 predates managed lifecycle on old
                # installations. Never unexpectedly lock a
                # historic user just because old evidence is
                # unavailable.
                if (
                    not accepted
                    and document["version"]
                    == "1.0"
                    and document["type"]
                    in LEGAL_TYPES
                ):
                    continue

                if not accepted:
                    result.append(
                        document
                    )

            return result

        finally:
            conn.close()

    def accept(
        self,
        user_id: int,
        tenant_id: int,
        document_type: str,
        *,
        ip_address: str,
        user_agent: str,
        locale: str,
    ) -> dict[str, Any]:
        document = self.get_version(
            document_type
        )

        if (
            not document
            or not document[
                "requires_acceptance"
            ]
        ):
            raise ValueError(
                "Документ недоступен для принятия."
            )

        conn = self._connect()

        try:
            membership = conn.execute(
                """SELECT 1
                   FROM tenant_users
                   WHERE
                     tenant_id=?
                     AND user_id=?
                     AND is_active=1
                   LIMIT 1""",
                (
                    int(
                        tenant_id
                    ),
                    int(
                        user_id
                    ),
                ),
            ).fetchone()

            if not membership:
                raise ValueError(
                    "Пользователь не состоит "
                    "в указанной компании."
                )

            stamp = now_iso()

            conn.execute(
                """INSERT INTO legal_acceptances(
                     legal_document_version_id,
                     user_id,
                     tenant_id,
                     document_type,
                     document_number,
                     document_version,
                     document_sha256,
                     accepted_at,
                     ip_address,
                     user_agent,
                     locale,
                     acceptance_text,
                     source,
                     created_at
                   ) VALUES(
                     ?,?,?,?,?,?,?,?,?,?,?,?,?,?
                   )
                   ON CONFLICT(
                     user_id,
                     document_type,
                     document_version
                   ) DO NOTHING""",
                (
                    document["id"],
                    int(
                        user_id
                    ),
                    int(
                        tenant_id
                    ),
                    document["type"],
                    document["number"],
                    document["version"],
                    document["sha256"],
                    stamp,
                    str(
                        ip_address
                        or ""
                    )[:128],
                    str(
                        user_agent
                        or ""
                    )[:1024],
                    str(
                        locale
                        or "ru"
                    )[:16],
                    document[
                        "acceptance_text"
                    ],
                    "reaccept",
                    stamp,
                ),
            )

            self._audit(
                conn,
                int(
                    user_id
                ),
                "legal_document_accepted",
                str(
                    document["id"]
                ),
                tenant_id=int(
                    tenant_id
                ),
                details={
                    "type":
                        document[
                            "type"
                        ],
                    "number":
                        document[
                            "number"
                        ],
                    "version":
                        document[
                            "version"
                        ],
                    "sha256":
                        document[
                            "sha256"
                        ],
                },
            )

            conn.commit()

            return document

        finally:
            conn.close()

    def list_versions(
        self,
    ) -> list[dict[str, Any]]:
        conn = self._connect()

        try:
            rows = conn.execute(
                """SELECT
                     v.*,
                     d.document_type,
                     (
                       SELECT COUNT(
                         DISTINCT a.user_id
                       )
                       FROM legal_acceptances a
                       WHERE
                         a.legal_document_version_id=
                           v.id
                     ) AS accepted_count
                   FROM legal_document_versions v
                   JOIN legal_documents d
                     ON d.id=v.document_id
                   ORDER BY
                     d.document_type,
                     CASE
                       WHEN v.status='draft'
                       THEN 0
                       ELSE 1
                     END,
                     COALESCE(
                       NULLIF(
                         v.effective_at,
                         ''
                       ),
                       v.created_at
                     ) DESC,
                     v.id DESC"""
            ).fetchall()

            return [
                {
                    **self._metadata(
                        dict(
                            row
                        )
                    ),
                    "body_text":
                        str(
                            row[
                                "body_text"
                            ]
                        ),
                    "acceptance_text":
                        str(
                            row[
                                "acceptance_text"
                            ]
                        ),
                }
                for row in rows
            ]

        finally:
            conn.close()

    def acceptance_audit(
        self,
        version_id: int | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._connect()

        try:
            where = (
                "WHERE "
                "a.legal_document_version_id=?"
                if version_id
                else ""
            )

            rows = conn.execute(
                """SELECT
                     a.accepted_at,
                     a.document_type,
                     a.document_number,
                     a.document_version,
                     a.document_sha256,
                     a.ip_address,
                     a.locale,
                     a.source,
                     u.display_name,
                     u.email,
                     t.name AS tenant_name
                   FROM legal_acceptances a
                   JOIN app_users u
                     ON u.id=a.user_id
                   JOIN tenants t
                     ON t.id=a.tenant_id """
                + where
                + """ ORDER BY
                        a.accepted_at DESC
                      LIMIT 500""",
                (
                    [
                        int(
                            version_id
                        )
                    ]
                    if version_id
                    else []
                ),
            ).fetchall()

            return [
                dict(
                    row
                )
                for row in rows
            ]

        finally:
            conn.close()

    def save_draft(
        self,
        payload: dict[str, Any],
        actor_user_id: int,
        version_id: int | None = None,
    ) -> dict[str, Any]:
        document_type = (
            str(
                payload.get(
                    "type"
                )
                or ""
            )
            .strip()
            .casefold()
        )

        version = str(
            payload.get(
                "version"
            )
            or ""
        ).strip()

        number = str(
            payload.get(
                "number"
            )
            or ""
        ).strip()

        title = str(
            payload.get(
                "title"
            )
            or ""
        ).strip()

        body = str(
            payload.get(
                "body_text"
            )
            or ""
        ).strip()

        acceptance = str(
            payload.get(
                "acceptance_text"
            )
            or ""
        ).strip()

        effective_at = (
            normalize_effective_at(
                payload.get(
                    "effective_at"
                )
            )
        )

        requires_acceptance = (
            parse_bool(
                payload.get(
                    "requires_acceptance"
                ),
                default=True,
            )
        )

        if (
            document_type
            not in LEGAL_TYPES
            or not version
            or not number
            or not title
            or not body
            or not acceptance
        ):
            raise ValueError(
                "Заполните тип, номер, версию, "
                "название, текст и формулировку "
                "принятия."
            )

        conn = self._connect()

        try:
            stamp = now_iso()

            if version_id:
                old = conn.execute(
                    """SELECT
                         v.*,
                         d.document_type
                       FROM legal_document_versions v
                       JOIN legal_documents d
                         ON d.id=v.document_id
                       WHERE v.id=?""",
                    (
                        int(
                            version_id
                        ),
                    ),
                ).fetchone()

                if (
                    not old
                    or str(
                        old["status"]
                    )
                    != "draft"
                ):
                    raise ValueError(
                        "Редактировать можно "
                        "только черновик."
                    )

                if (
                    str(
                        old[
                            "document_type"
                        ]
                    )
                    != document_type
                ):
                    raise ValueError(
                        "Тип существующего "
                        "документа менять нельзя."
                    )

                duplicate = conn.execute(
                    """SELECT id
                       FROM legal_document_versions
                       WHERE
                         document_id=?
                         AND version=?
                         AND id<>?
                       LIMIT 1""",
                    (
                        int(
                            old[
                                "document_id"
                            ]
                        ),
                        version,
                        int(
                            version_id
                        ),
                    ),
                ).fetchone()

                if duplicate:
                    raise ValueError(
                        "Такая версия документа "
                        "уже существует."
                    )

                digest = (
                    self._hash_payload(
                        document_type=
                            document_type,
                        number=
                            number,
                        version=
                            version,
                        title=
                            title,
                        effective_at=
                            effective_at,
                        body_text=
                            body,
                        acceptance_text=
                            acceptance,
                        requires_acceptance=
                            requires_acceptance,
                        operator_snapshot={},
                    )
                )

                conn.execute(
                    """UPDATE legal_document_versions
                       SET
                         version=?,
                         document_number=?,
                         title=?,
                         effective_at=?,
                         body_text=?,
                         acceptance_text=?,
                         operator_snapshot_json='{}',
                         content_sha256=?,
                         requires_acceptance=?
                       WHERE id=?""",
                    (
                        version,
                        number,
                        title,
                        effective_at,
                        body,
                        acceptance,
                        digest,
                        (
                            1
                            if
                            requires_acceptance
                            else 0
                        ),
                        int(
                            version_id
                        ),
                    ),
                )

                entity_id = int(
                    version_id
                )

                action = (
                    "legal_document_"
                    "draft_updated"
                )

            else:
                parent = conn.execute(
                    """SELECT id
                       FROM legal_documents
                       WHERE document_type=?""",
                    (
                        document_type,
                    ),
                ).fetchone()

                if parent is None:
                    cursor = conn.execute(
                        """INSERT INTO legal_documents(
                             document_type,
                             document_number,
                             title,
                             created_at,
                             updated_at
                           ) VALUES(?,?,?,?,?)""",
                        (
                            document_type,
                            number,
                            title,
                            stamp,
                            stamp,
                        ),
                    )

                    document_id = int(
                        cursor.lastrowid
                    )

                else:
                    document_id = int(
                        parent["id"]
                    )

                duplicate = conn.execute(
                    """SELECT id
                       FROM legal_document_versions
                       WHERE
                         document_id=?
                         AND version=?
                       LIMIT 1""",
                    (
                        document_id,
                        version,
                    ),
                ).fetchone()

                if duplicate:
                    raise ValueError(
                        "Такая версия документа "
                        "уже существует."
                    )

                digest = (
                    self._hash_payload(
                        document_type=
                            document_type,
                        number=
                            number,
                        version=
                            version,
                        title=
                            title,
                        effective_at=
                            effective_at,
                        body_text=
                            body,
                        acceptance_text=
                            acceptance,
                        requires_acceptance=
                            requires_acceptance,
                        operator_snapshot={},
                    )
                )

                cursor = conn.execute(
                    """INSERT INTO
                       legal_document_versions(
                         document_id,
                         version,
                         document_number,
                         title,
                         effective_at,
                         status,
                         body_text,
                         acceptance_text,
                         operator_snapshot_json,
                         content_sha256,
                         requires_acceptance,
                         created_by,
                         created_at
                       ) VALUES(
                         ?,?,?,?,?,
                         'draft',
                         ?,?,'{}',?,?,?,?
                       )""",
                    (
                        document_id,
                        version,
                        number,
                        title,
                        effective_at,
                        body,
                        acceptance,
                        digest,
                        (
                            1
                            if
                            requires_acceptance
                            else 0
                        ),
                        int(
                            actor_user_id
                        ),
                        stamp,
                    ),
                )

                entity_id = int(
                    cursor.lastrowid
                )

                action = (
                    "legal_document_"
                    "draft_created"
                )

            self._audit(
                conn,
                int(
                    actor_user_id
                ),
                action,
                str(
                    entity_id
                ),
                details={
                    "type":
                        document_type,
                    "number":
                        number,
                    "version":
                        version,
                    "effective_at":
                        effective_at,
                    "requires_acceptance":
                        requires_acceptance,
                },
            )

            conn.commit()

        finally:
            conn.close()

        return (
            self.get_version(
                document_type,
                version,
            )
            or {}
        )

    def publish(
        self,
        version_id: int,
        actor_user_id: int,
    ) -> dict[str, Any]:
        conn = self._connect()

        try:
            row = conn.execute(
                """SELECT
                     v.*,
                     d.document_type
                   FROM legal_document_versions v
                   JOIN legal_documents d
                     ON d.id=v.document_id
                   WHERE v.id=?""",
                (
                    int(
                        version_id
                    ),
                ),
            ).fetchone()

            if (
                not row
                or str(
                    row["status"]
                )
                != "draft"
            ):
                raise ValueError(
                    "Опубликовать можно "
                    "только существующий черновик."
                )

            stamp = now_iso()

            effective_at = (
                str(
                    row[
                        "effective_at"
                    ]
                    or ""
                )
                or stamp
            )

            operator_snapshot = (
                self._operator_snapshot(
                    conn
                )
                if str(
                    row[
                        "document_type"
                    ]
                )
                == "offer"
                else {}
            )

            digest = (
                self._hash_payload(
                    document_type=
                        str(
                            row[
                                "document_type"
                            ]
                        ),
                    number=
                        str(
                            row[
                                "document_number"
                            ]
                        ),
                    version=
                        str(
                            row[
                                "version"
                            ]
                        ),
                    title=
                        str(
                            row[
                                "title"
                            ]
                        ),
                    effective_at=
                        effective_at,
                    body_text=
                        str(
                            row[
                                "body_text"
                            ]
                        ),
                    acceptance_text=
                        str(
                            row[
                                "acceptance_text"
                            ]
                        ),
                    requires_acceptance=
                        bool(
                            row[
                                "requires_acceptance"
                            ]
                        ),
                    operator_snapshot=
                        operator_snapshot,
                )
            )

            conn.execute(
                """UPDATE legal_document_versions
                   SET
                     status='published',
                     effective_at=?,
                     operator_snapshot_json=?,
                     content_sha256=?,
                     published_by=?,
                     published_at=?,
                     archived_at=NULL
                   WHERE id=?""",
                (
                    effective_at,
                    json.dumps(
                        operator_snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    digest,
                    int(
                        actor_user_id
                    ),
                    stamp,
                    int(
                        version_id
                    ),
                ),
            )

            # IMPORTANT:
            # previous published versions stay immutable
            # and addressable. "Current" is selected by
            # effective_at/published_at, not by destructive
            # status rewriting.

            self._audit(
                conn,
                int(
                    actor_user_id
                ),
                "legal_document_published",
                str(
                    version_id
                ),
                details={
                    "type":
                        str(
                            row[
                                "document_type"
                            ]
                        ),
                    "number":
                        str(
                            row[
                                "document_number"
                            ]
                        ),
                    "version":
                        str(
                            row[
                                "version"
                            ]
                        ),
                    "effective_at":
                        effective_at,
                    "sha256":
                        digest,
                },
            )

            conn.commit()

        finally:
            conn.close()

        result = self.get_version(
            str(
                row[
                    "document_type"
                ]
            ),
            str(
                row[
                    "version"
                ]
            ),
        )

        if result is None:
            raise RuntimeError(
                "Опубликованная версия "
                "не найдена."
            )

        return result

    @staticmethod
    def html(
        document: dict[str, Any],
    ) -> Markup:
        blocks = [
            (
                "<p>"
                + escape(
                    part.strip()
                )
                + "</p>"
            )
            for part
            in str(
                document.get(
                    "body_text"
                )
                or ""
            ).split(
                "\n\n"
            )
            if part.strip()
        ]

        operator = document.get(
            "operator_snapshot"
        )

        if (
            document.get(
                "type"
            )
            == "offer"
            and isinstance(
                operator,
                dict,
            )
            and operator
        ):
            fields = (
                (
                    "Наименование",
                    "name",
                ),
                (
                    "БИН",
                    "registration_number",
                ),
                (
                    "Юридический адрес",
                    "legal_address",
                ),
                (
                    "IBAN",
                    "iban",
                ),
                (
                    "Банк",
                    "bank_name",
                ),
                (
                    "БИК",
                    "bic",
                ),
                (
                    "КБе",
                    "kbe",
                ),
            )

            rows = "".join(
                "<tr>"
                f"<th>{escape(label)}</th>"
                "<td>"
                + escape(
                    str(
                        operator.get(
                            key
                        )
                        or "—"
                    )
                )
                + "</td>"
                "</tr>"
                for (
                    label,
                    key,
                )
                in fields
            )

            blocks.append(
                '<section class="legal-operator-details">'
                "<h2>Реквизиты оператора</h2>"
                "<table><tbody>"
                + rows
                + "</tbody></table>"
                "</section>"
            )

        return Markup(
            "\n".join(
                blocks
            )
        )
