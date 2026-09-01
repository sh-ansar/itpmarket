-- SPYON-AUTO-MIGRATION
-- Expand the accepted lifecycle type set.  No document text or version is seeded.
BEGIN;

ALTER TABLE app.legal_acceptances
    DROP CONSTRAINT IF EXISTS legal_acceptances_document_type_check;
ALTER TABLE app.legal_acceptances
    ADD CONSTRAINT legal_acceptances_document_type_check
    CHECK (document_type IN (
        'offer','terms','privacy','cookies','personal_data_consent'
    ));

INSERT INTO app.metadata(key,value)
VALUES('schema_legal_document_types_v2',CURRENT_TIMESTAMP::text)
ON CONFLICT(key) DO NOTHING;

COMMIT;
