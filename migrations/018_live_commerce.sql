-- Immutable launch-pricing snapshots and read-only production eBay activation.
ALTER TABLE storefront_orders ADD COLUMN item_subtotal_amount INTEGER;
ALTER TABLE storefront_orders ADD COLUMN shipping_amount INTEGER;
ALTER TABLE storefront_orders ADD COLUMN tax_amount INTEGER;
ALTER TABLE storefront_orders ADD COLUMN final_total_amount INTEGER;
UPDATE storefront_orders SET item_subtotal_amount=CAST(ROUND(CAST(price_snapshot AS REAL)*100) AS INTEGER),
 shipping_amount=0,tax_amount=0,final_total_amount=CAST(ROUND(CAST(price_snapshot AS REAL)*100) AS INTEGER);
CREATE TRIGGER storefront_money_snapshots_immutable
BEFORE UPDATE OF item_subtotal_amount,shipping_amount,tax_amount,final_total_amount,currency_snapshot ON storefront_orders
WHEN OLD.item_subtotal_amount IS NOT NEW.item_subtotal_amount OR OLD.shipping_amount IS NOT NEW.shipping_amount
 OR OLD.tax_amount IS NOT NEW.tax_amount OR OLD.final_total_amount IS NOT NEW.final_total_amount
 OR OLD.currency_snapshot IS NOT NEW.currency_snapshot
BEGIN SELECT RAISE(ABORT,'order pricing snapshots are immutable'); END;
CREATE TABLE ebay_production_activations (
 activation_id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, marketplace TEXT NOT NULL,
 merchant_location_key TEXT NOT NULL, fulfillment_policy_id TEXT NOT NULL,
 payment_policy_id TEXT NOT NULL, return_policy_id TEXT NOT NULL, currency TEXT NOT NULL,
 token_identity TEXT NOT NULL, validated_at TEXT NOT NULL
);
