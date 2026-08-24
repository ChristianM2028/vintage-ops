-- NULL deliberately represents a legacy item which has not been classified.
ALTER TABLE items ADD COLUMN ebay_integration_mode TEXT
    CHECK (ebay_integration_mode IS NULL OR ebay_integration_mode IN ('local_mock','ebay_sandbox','ebay_production'));
ALTER TABLE items ADD COLUMN ebay_merchant_location_key TEXT;
ALTER TABLE items ADD COLUMN ebay_fulfillment_policy_id TEXT;
ALTER TABLE items ADD COLUMN ebay_payment_policy_id TEXT;
ALTER TABLE items ADD COLUMN ebay_return_policy_id TEXT;
ALTER TABLE items ADD COLUMN ebay_marketplace_id TEXT;
