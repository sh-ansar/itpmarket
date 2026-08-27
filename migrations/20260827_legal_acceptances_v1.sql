-- SPYON-AUTO-MIGRATION
-- Immutable document-version evidence for electronic registration acceptance.
BEGIN;

CREATE TABLE IF NOT EXISTS app.legal_acceptances (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES app.app_users(id),
    tenant_id BIGINT NOT NULL REFERENCES app.tenants(id),
    document_type TEXT NOT NULL CHECK (document_type IN ('offer', 'privacy')),
    document_number TEXT NOT NULL,
    document_version TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    locale TEXT NOT NULL DEFAULT 'ru',
    acceptance_text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'registration',
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_legal_acceptances_user_document_version
        UNIQUE (user_id, document_type, document_version)
);

CREATE INDEX IF NOT EXISTS idx_legal_acceptances_tenant
    ON app.legal_acceptances (tenant_id, accepted_at DESC);

COMMIT;
