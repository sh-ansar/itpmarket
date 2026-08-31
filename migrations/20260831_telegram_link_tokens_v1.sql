-- SPYON-AUTO-MIGRATION
-- Add one-time, hash-only Telegram account-link tokens.
BEGIN;
CREATE TABLE IF NOT EXISTS app.telegram_link_tokens (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES app.app_users(id) ON DELETE CASCADE,
    tenant_id BIGINT REFERENCES app.tenants(id) ON DELETE SET NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telegram_link_tokens_user
    ON app.telegram_link_tokens(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_telegram_link_tokens_expires
    ON app.telegram_link_tokens(expires_at);

INSERT INTO app.metadata(key,value)
VALUES('schema_telegram_link_tokens_v1',CURRENT_TIMESTAMP::text)
ON CONFLICT(key) DO NOTHING;

COMMIT;
