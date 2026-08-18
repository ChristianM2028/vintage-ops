ALTER TABLE items ADD COLUMN item_specifics TEXT NOT NULL DEFAULT '{}';
ALTER TABLE items ADD COLUMN ebay_sync_state TEXT;
ALTER TABLE items ADD COLUMN local_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN synced_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN publication_lock_token TEXT;
ALTER TABLE items ADD COLUMN publication_lock_expires_at TEXT;

ALTER TABLE item_photos ADD COLUMN remote_url TEXT;
ALTER TABLE item_photos ADD COLUMN source_fingerprint TEXT;
ALTER TABLE item_photos ADD COLUMN publication_error TEXT;

CREATE TABLE ebay_metadata_cache (
 cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE ebay_order_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, line_item_id TEXT NOT NULL,
 sku TEXT, listing_id TEXT, quantity INTEGER NOT NULL, payment_state TEXT, fulfillment_state TEXT,
 raw_payload TEXT NOT NULL, processed_at TEXT NOT NULL, UNIQUE(order_id,line_item_id)
);
CREATE TABLE ebay_operations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL REFERENCES items(id),
 operation TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
