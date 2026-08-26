-- Immutable launch-pricing snapshots and read-only production eBay activation.
ALTER TABLE storefront_orders ADD COLUMN item_subtotal_amount INTEGER;
ALTER TABLE storefront_orders ADD COLUMN shipping_amount INTEGER;
ALTER TABLE storefront_orders ADD COLUMN tax_amount INTEGER;
ALTER TABLE storefront_orders ADD COLUMN final_total_amount INTEGER;
-- Database.migrate performs the versioned backfill with Decimal, rejecting
-- malformed/over-precise legacy values, then installs the immutable trigger.
CREATE TABLE ebay_production_activations (
 activation_id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, marketplace TEXT NOT NULL,
 merchant_location_key TEXT NOT NULL, fulfillment_policy_id TEXT NOT NULL,
 payment_policy_id TEXT NOT NULL, return_policy_id TEXT NOT NULL, currency TEXT NOT NULL,
 token_identity TEXT NOT NULL, validated_at TEXT NOT NULL
);
