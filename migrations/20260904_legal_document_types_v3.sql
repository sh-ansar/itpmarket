-- SPYON-AUTO-MIGRATION
-- Add the two semantic document types used by the approved 04.09.2026 set.
-- Historical terms/cookies values remain valid for immutable audit evidence.
BEGIN;

ALTER TABLE app.legal_acceptances
    DROP CONSTRAINT IF EXISTS legal_acceptances_document_type_check;
ALTER TABLE app.legal_acceptances
    ADD CONSTRAINT legal_acceptances_document_type_check
    CHECK (document_type IN (
        'offer','tariff_policy','acceptable_use','personal_data_consent',
        'privacy','terms','cookies'
    ));

INSERT INTO app.metadata(key,value)
VALUES('schema_legal_document_types_v3',CURRENT_TIMESTAMP::text)
ON CONFLICT(key) DO NOTHING;

COMMIT;
