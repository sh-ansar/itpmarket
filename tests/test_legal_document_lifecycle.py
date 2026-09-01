from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from legal_document_service import LegalDocumentService
from schema import ensure_database


class LegalDocumentLifecycleTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.tmp = (
            tempfile.TemporaryDirectory(
                prefix="spyon_legal_"
            )
        )

        self.db_path = (
            Path(self.tmp.name)
            / "app.db"
        )

        ensure_database(
            self.db_path
        )

        conn = sqlite3.connect(
            self.db_path
        )

        stamp = (
            "2026-09-01T"
            "10:00:00+00:00"
        )

        self.user_id = int(
            conn.execute(
                """INSERT INTO app_users(
                     email,
                     display_name,
                     password_hash,
                     recovery_hash,
                     role,
                     is_active,
                     created_at,
                     updated_at
                   ) VALUES(
                     ?,?,?,?,?,?,?,?
                   )""",
                (
                    "legal@example.com",
                    "Legal User",
                    "x",
                    "x",
                    "admin",
                    1,
                    stamp,
                    stamp,
                ),
            ).lastrowid
        )

        self.tenant_id = int(
            conn.execute(
                """INSERT INTO tenants(
                     name,
                     slug,
                     registration_number,
                     status,
                     plan_code,
                     contact_email,
                     contact_phone,
                     created_at,
                     updated_at,
                     approved_at
                   ) VALUES(
                     ?,?,?,?,?,?,?,?,?,?
                   )""",
                (
                    "Legal Tenant",
                    "legal-tenant",
                    "LEGAL-1",
                    "approved",
                    "demo",
                    "tenant@example.com",
                    "+77000000000",
                    stamp,
                    stamp,
                    stamp,
                ),
            ).lastrowid
        )

        conn.execute(
            """INSERT INTO tenant_users(
                 tenant_id,
                 user_id,
                 tenant_role,
                 is_primary,
                 is_active,
                 created_at
               ) VALUES(
                 ?,?,'admin',1,1,?
               )""",
            (
                self.tenant_id,
                self.user_id,
                stamp,
            ),
        )

        conn.commit()
        conn.close()

        self.service = (
            LegalDocumentService(
                self.db_path
            )
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def draft(
        self,
        version: str,
        *,
        effective_at: str = "",
        title: str = "Новая оферта",
        number: str = "SPYON-OF-001",
    ) -> dict:
        return self.service.save_draft(
            {
                "type":
                    "offer",
                "number":
                    number,
                "version":
                    version,
                "title":
                    title,
                "effective_at":
                    effective_at,
                "body_text":
                    (
                        "Новая редакция."
                        "\n\n"
                        "Условия сервиса."
                    ),
                "acceptance_text":
                    (
                        "Я ознакомился "
                        "и принимаю новую редакцию."
                    ),
                "requires_acceptance":
                    True,
            },
            self.user_id,
        )

    def test_version_metadata_is_immutable(
        self,
    ) -> None:
        legacy = self.service.get_version(
            "offer",
            "1.0",
        )

        self.assertIsNotNone(
            legacy
        )

        legacy_number = (
            legacy["number"]
        )

        legacy_title = (
            legacy["title"]
        )

        draft = self.draft(
            "1.1",
            number="SPYON-OF-NEW",
            title="Оферта 1.1",
        )

        self.assertEqual(
            "draft",
            draft["status"],
        )

        old = self.service.get_version(
            "offer",
            "1.0",
        )

        self.assertEqual(
            legacy_number,
            old["number"],
        )

        self.assertEqual(
            legacy_title,
            old["title"],
        )

    def test_publish_snapshots_operator_and_is_immutable(
        self,
    ) -> None:
        draft = self.draft(
            "1.1"
        )

        published = (
            self.service.publish(
                int(
                    draft["id"]
                ),
                self.user_id,
            )
        )

        self.assertEqual(
            "published",
            published["status"],
        )

        self.assertTrue(
            published[
                "operator_snapshot"
            ]
        )

        original_snapshot = dict(
            published[
                "operator_snapshot"
            ]
        )

        conn = sqlite3.connect(
            self.db_path
        )

        conn.execute(
            """INSERT INTO platform_settings(
                 setting_key,
                 value_json,
                 updated_by,
                 updated_at
               ) VALUES(?,?,?,?)
               ON CONFLICT(setting_key)
               DO UPDATE SET
                 value_json=excluded.value_json,
                 updated_at=excluded.updated_at""",
            (
                "billing_supplier",
                json.dumps(
                    {
                        "name":
                            "ДРУГАЯ КОМПАНИЯ",
                        "registration_number":
                            "999999999999",
                    },
                    ensure_ascii=False,
                ),
                self.user_id,
                (
                    "2026-09-01T"
                    "11:00:00+00:00"
                ),
            ),
        )

        conn.commit()
        conn.close()

        after = self.service.get_version(
            "offer",
            "1.1",
        )

        self.assertEqual(
            original_snapshot,
            after[
                "operator_snapshot"
            ],
        )

        with self.assertRaisesRegex(
            ValueError,
            "только черновик",
        ):
            self.service.save_draft(
                {
                    "type":
                        "offer",
                    "number":
                        "BROKEN",
                    "version":
                        "1.1",
                    "title":
                        "BROKEN",
                    "body_text":
                        "BROKEN",
                    "acceptance_text":
                        "BROKEN",
                    "requires_acceptance":
                        True,
                },
                self.user_id,
                int(
                    published["id"]
                ),
            )

    def test_new_version_requires_acceptance_and_history_is_preserved(
        self,
    ) -> None:
        draft = self.draft(
            "1.1"
        )

        published = (
            self.service.publish(
                int(
                    draft["id"]
                ),
                self.user_id,
            )
        )

        required = (
            self.service.required_for_user(
                self.user_id,
                self.tenant_id,
            )
        )

        self.assertEqual(
            ["1.1"],
            [
                item["version"]
                for item in required
            ],
        )

        accepted = (
            self.service.accept(
                self.user_id,
                self.tenant_id,
                "offer",
                ip_address=
                    "127.0.0.1",
                user_agent=
                    "unit-test",
                locale=
                    "ru",
            )
        )

        self.assertEqual(
            published["sha256"],
            accepted["sha256"],
        )

        self.assertEqual(
            [],
            self.service.required_for_user(
                self.user_id,
                self.tenant_id,
            ),
        )

        # idempotent duplicate accept
        self.service.accept(
            self.user_id,
            self.tenant_id,
            "offer",
            ip_address=
                "127.0.0.1",
            user_agent=
                "unit-test",
            locale=
                "ru",
        )

        conn = sqlite3.connect(
            self.db_path
        )

        count = conn.execute(
            """SELECT COUNT(*)
               FROM legal_acceptances
               WHERE
                 user_id=?
                 AND document_type='offer'
                 AND document_version='1.1'""",
            (
                self.user_id,
            ),
        ).fetchone()[0]

        conn.close()

        self.assertEqual(
            1,
            count,
        )

        history = (
            self.service
            .accepted_documents_for_user(
                self.user_id,
                self.tenant_id,
            )
        )

        self.assertEqual(
            "1.1",
            history[0]["version"],
        )

    def test_acceptance_applies_to_all_user_tenants_without_duplicate(
        self,
    ) -> None:
        conn = sqlite3.connect(
            self.db_path
        )

        second_tenant_id = int(
            conn.execute(
                """INSERT INTO tenants(
                     name,
                     slug,
                     registration_number,
                     status,
                     plan_code,
                     contact_email,
                     contact_phone,
                     created_at,
                     updated_at,
                     approved_at
                   ) VALUES(
                     ?,?,?,?,?,?,?,?,?,?
                   )""",
                (
                    "Second Legal Tenant",
                    "second-legal-tenant",
                    "LEGAL-2",
                    "approved",
                    "demo",
                    "second@example.com",
                    "+77000000001",
                    "2026-09-01T10:00:00+00:00",
                    "2026-09-01T10:00:00+00:00",
                    "2026-09-01T10:00:00+00:00",
                ),
            ).lastrowid
        )

        conn.execute(
            """INSERT INTO tenant_users(
                 tenant_id,
                 user_id,
                 tenant_role,
                 is_primary,
                 is_active,
                 created_at
               ) VALUES(
                 ?,?,'admin',0,1,?
               )""",
            (
                second_tenant_id,
                self.user_id,
                "2026-09-01T10:00:00+00:00",
            ),
        )

        conn.commit()
        conn.close()

        draft = self.draft(
            "1.1"
        )
        published = self.service.publish(
            int(draft["id"]),
            self.user_id,
        )

        self.service.accept(
            self.user_id,
            self.tenant_id,
            "offer",
            ip_address="127.0.0.1",
            user_agent="unit-test",
            locale="ru",
        )

        self.assertEqual(
            [],
            self.service.required_for_user(
                self.user_id,
                second_tenant_id,
            ),
        )

        self.service.accept(
            self.user_id,
            second_tenant_id,
            "offer",
            ip_address="127.0.0.1",
            user_agent="unit-test",
            locale="ru",
        )

        conn = sqlite3.connect(
            self.db_path
        )
        rows = conn.execute(
            """SELECT
                 tenant_id,
                 document_version,
                 document_sha256
               FROM legal_acceptances
               WHERE
                 user_id=?
                 AND document_type='offer'
                 AND document_version='1.1'""",
            (self.user_id,),
        ).fetchall()
        conn.close()

        self.assertEqual(
            [(self.tenant_id, "1.1", published["sha256"])],
            rows,
        )

    def test_future_version_does_not_become_current_early(
        self,
    ) -> None:
        draft_11 = self.draft(
            "1.1"
        )

        self.service.publish(
            int(
                draft_11["id"]
            ),
            self.user_id,
        )

        future = (
            datetime.now(
                timezone.utc
            )
            + timedelta(
                days=10
            )
        ).isoformat(
            timespec="seconds"
        )

        draft_12 = self.draft(
            "1.2",
            effective_at=future,
        )

        self.service.publish(
            int(
                draft_12["id"]
            ),
            self.user_id,
        )

        current = (
            self.service
            .get_version(
                "offer"
            )
        )

        self.assertEqual(
            "1.1",
            current["version"],
        )


if __name__ == "__main__":
    unittest.main()
