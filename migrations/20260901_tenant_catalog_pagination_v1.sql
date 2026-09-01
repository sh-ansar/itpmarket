-- SPYON-AUTO-MIGRATION
-- Supports tenant-scoped ORDER BY/LIMIT product-list projection.
BEGIN;

CREATE INDEX IF NOT EXISTS idx_tenant_seller_products_page
ON app.tenant_seller_catalog_products(
    tenant_id,marketplace_code,active,title,source_product_code
);
CREATE INDEX IF NOT EXISTS idx_tenant_catalog_products_page
ON app.tenant_catalog_products(
    tenant_id,marketplace_code,active,title,source_product_code
);

INSERT INTO app.metadata(key,value)
VALUES('schema_tenant_catalog_pagination_v1',CURRENT_TIMESTAMP::text)
ON CONFLICT(key) DO NOTHING;

COMMIT;
