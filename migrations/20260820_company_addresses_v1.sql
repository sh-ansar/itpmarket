-- SPYON-AUTO-MIGRATION
BEGIN;

ALTER TABLE app.tenants
    ADD COLUMN IF NOT EXISTS legal_address text
        NOT NULL DEFAULT '';

ALTER TABLE app.tenants
    ADD COLUMN IF NOT EXISTS actual_address text
        NOT NULL DEFAULT '';

ALTER TABLE app.registration_requests
    ADD COLUMN IF NOT EXISTS legal_address text
        NOT NULL DEFAULT '';

ALTER TABLE app.registration_requests
    ADD COLUMN IF NOT EXISTS actual_address text
        NOT NULL DEFAULT '';

INSERT INTO app.metadata(key,value)
VALUES(
    'schema_company_addresses_v1',
    CURRENT_TIMESTAMP::text
)
ON CONFLICT(key) DO NOTHING;

COMMIT;
