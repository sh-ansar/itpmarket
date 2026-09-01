-- SPYON-AUTO-MIGRATION
-- Add the subscription invoice tax model to newly issued add-on invoices.
-- Historical rows are intentionally neither recalculated nor updated.
BEGIN;

ALTER TABLE app.tenant_addon_invoices
    ADD COLUMN IF NOT EXISTS subtotal_amount double precision NOT NULL DEFAULT 0;
ALTER TABLE app.tenant_addon_invoices
    ADD COLUMN IF NOT EXISTS vat_rate double precision NOT NULL DEFAULT 0;
ALTER TABLE app.tenant_addon_invoices
    ADD COLUMN IF NOT EXISTS vat_amount double precision NOT NULL DEFAULT 0;
ALTER TABLE app.tenant_addon_invoices
    ADD COLUMN IF NOT EXISTS total_amount double precision NOT NULL DEFAULT 0;

INSERT INTO app.metadata(key,value)
VALUES('schema_addon_billing_v2',CURRENT_TIMESTAMP::text)
ON CONFLICT(key) DO NOTHING;

COMMIT;
