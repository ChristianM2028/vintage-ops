CREATE TABLE ebay_seller_setup (
 id INTEGER PRIMARY KEY CHECK (id = 1),
 seller_id TEXT,
 fulfillment_policy_id TEXT NOT NULL,
 payment_policy_id TEXT NOT NULL,
 return_policy_id TEXT NOT NULL,
 merchant_location_key TEXT NOT NULL,
 marketplace TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
