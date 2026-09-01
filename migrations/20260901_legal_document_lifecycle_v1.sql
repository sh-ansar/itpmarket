-- SPYON-AUTO-MIGRATION
-- Immutable versioned legal-document lifecycle.
BEGIN;

CREATE TABLE IF NOT EXISTS app.legal_documents (
    id BIGSERIAL PRIMARY KEY,
    document_type TEXT NOT NULL UNIQUE,
    document_number TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app.legal_document_versions (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL
        REFERENCES app.legal_documents(id)
        ON DELETE CASCADE,
    version TEXT NOT NULL,
    document_number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    effective_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (
            status IN (
                'draft',
                'published',
                'archived'
            )
        ),
    body_text TEXT NOT NULL DEFAULT '',
    acceptance_text TEXT NOT NULL DEFAULT '',
    operator_snapshot_json TEXT NOT NULL DEFAULT '{}',
    content_sha256 TEXT NOT NULL,
    requires_acceptance INTEGER NOT NULL DEFAULT 1,
    created_by BIGINT
        REFERENCES app.app_users(id)
        ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    published_by BIGINT
        REFERENCES app.app_users(id)
        ON DELETE SET NULL,
    published_at TEXT,
    archived_at TEXT,
    UNIQUE(document_id, version)
);

ALTER TABLE app.legal_document_versions
ADD COLUMN IF NOT EXISTS
document_number TEXT NOT NULL DEFAULT '';

ALTER TABLE app.legal_document_versions
ADD COLUMN IF NOT EXISTS
title TEXT NOT NULL DEFAULT '';

ALTER TABLE app.legal_document_versions
ADD COLUMN IF NOT EXISTS
effective_at TEXT NOT NULL DEFAULT '';

ALTER TABLE app.legal_document_versions
ADD COLUMN IF NOT EXISTS
operator_snapshot_json TEXT NOT NULL DEFAULT '{}';

UPDATE app.legal_document_versions v
SET
    document_number = CASE
        WHEN COALESCE(
            v.document_number,
            ''
        ) = ''
        THEN d.document_number
        ELSE v.document_number
    END,
    title = CASE
        WHEN COALESCE(
            v.title,
            ''
        ) = ''
        THEN d.title
        ELSE v.title
    END
FROM app.legal_documents d
WHERE
    d.id = v.document_id
    AND (
        COALESCE(
            v.document_number,
            ''
        ) = ''
        OR COALESCE(
            v.title,
            ''
        ) = ''
    );

CREATE INDEX IF NOT EXISTS
idx_legal_document_versions_current
ON app.legal_document_versions(
    document_id,
    status,
    effective_at DESC,
    published_at DESC,
    id DESC
);

ALTER TABLE app.legal_acceptances
ADD COLUMN IF NOT EXISTS
legal_document_version_id BIGINT;

COMMIT;
