-- SPYON-AUTO-MIGRATION
BEGIN;

ALTER TABLE app.tenant_subscriptions
    ADD COLUMN IF NOT EXISTS billing_anchor_day integer;

INSERT INTO app.metadata(key, value)
VALUES(
    'schema_billing_calendar_anchor_v1',
    CURRENT_TIMESTAMP::text
)
ON CONFLICT(key) DO NOTHING;

COMMIT;
