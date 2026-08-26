-- Marks customer data lifecycle completion without deleting financial records.
ALTER TABLE storefront_orders ADD COLUMN anonymized_at TEXT;
CREATE INDEX storefront_orders_privacy_expiry ON storefront_orders(created_at,id) WHERE anonymized_at IS NULL;
