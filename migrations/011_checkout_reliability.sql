-- Forward-only reliability metadata. Existing Stripe rows cannot be identified
-- from their identifier prefixes, so they deliberately remain legacy_unknown.
ALTER TABLE storefront_orders ADD COLUMN stripe_provider_identity TEXT NOT NULL DEFAULT 'legacy_unknown'
 CHECK(stripe_provider_identity IN ('fake','stripe_test','stripe_live','legacy_unknown'));

ALTER TABLE storefront_operations ADD COLUMN claim_token TEXT;
ALTER TABLE storefront_operations ADD COLUMN lease_expires_at TEXT;
ALTER TABLE storefront_operations ADD COLUMN result TEXT;
ALTER TABLE storefront_operations ADD COLUMN diagnostic TEXT;

ALTER TABLE stripe_webhook_events ADD COLUMN claim_token TEXT;
ALTER TABLE stripe_webhook_events ADD COLUMN lease_expires_at TEXT;
CREATE INDEX storefront_operations_claims
 ON storefront_operations(order_id,operation_type,state,lease_expires_at);
