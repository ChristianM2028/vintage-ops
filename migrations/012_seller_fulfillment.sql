-- Seller fulfillment is deliberately independent from checkout/payment state.
ALTER TABLE storefront_orders ADD COLUMN fulfillment_state TEXT NOT NULL DEFAULT 'not_applicable'
 CHECK(fulfillment_state IN ('not_applicable','unfulfilled','packing','shipped','delivered'));
ALTER TABLE storefront_orders ADD COLUMN refund_state TEXT NOT NULL DEFAULT 'none'
 CHECK(refund_state IN ('none','pending','refunded','manual_resolution'));
ALTER TABLE storefront_orders ADD COLUMN captured_amount INTEGER CHECK(captured_amount IS NULL OR captured_amount >= 0);
ALTER TABLE storefront_orders ADD COLUMN captured_currency TEXT;
ALTER TABLE storefront_orders ADD COLUMN stripe_refund_id TEXT;
ALTER TABLE storefront_orders ADD COLUMN tracking_carrier TEXT;
ALTER TABLE storefront_orders ADD COLUMN tracking_number TEXT;
ALTER TABLE storefront_orders ADD COLUMN tracking_url TEXT;
ALTER TABLE storefront_orders ADD COLUMN shipped_at TEXT;
ALTER TABLE storefront_orders ADD COLUMN delivered_at TEXT;
ALTER TABLE storefront_orders ADD COLUMN refunded_at TEXT;

-- Only completed historical sales enter the seller's fulfillment queue.
UPDATE storefront_orders SET fulfillment_state='unfulfilled'
 WHERE state='sold' AND payment_state='captured';

CREATE TABLE storefront_order_notes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES storefront_orders(id),
 note TEXT NOT NULL CHECK(length(trim(note)) > 0),
 actor TEXT,
 created_at TEXT NOT NULL
);

CREATE TABLE storefront_tracking_revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES storefront_orders(id),
 request_key TEXT NOT NULL UNIQUE,
 carrier TEXT,
 tracking_number TEXT,
 tracking_url TEXT,
 previous_carrier TEXT,
 previous_tracking_number TEXT,
 previous_tracking_url TEXT,
 actor TEXT,
 created_at TEXT NOT NULL,
 CHECK(carrier IS NOT NULL OR tracking_number IS NOT NULL OR tracking_url IS NOT NULL)
);

CREATE TABLE storefront_attention_cases (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES storefront_orders(id),
 kind TEXT NOT NULL,
 summary TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','acknowledged','resolved')),
 dedupe_key TEXT,
 actor TEXT,
 acknowledged_at TEXT,
 resolved_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX storefront_attention_case_dedupe
 ON storefront_attention_cases(order_id,dedupe_key) WHERE dedupe_key IS NOT NULL;

ALTER TABLE storefront_order_events ADD COLUMN actor TEXT;
ALTER TABLE storefront_order_events ADD COLUMN lifecycle TEXT;
ALTER TABLE storefront_order_events ADD COLUMN old_value TEXT;
ALTER TABLE storefront_order_events ADD COLUMN new_value TEXT;
ALTER TABLE storefront_order_events ADD COLUMN metadata_json TEXT CHECK(metadata_json IS NULL OR json_valid(metadata_json));
ALTER TABLE storefront_order_events ADD COLUMN dedupe_key TEXT;
CREATE UNIQUE INDEX storefront_order_event_dedupe
 ON storefront_order_events(order_id,dedupe_key) WHERE dedupe_key IS NOT NULL;

CREATE INDEX storefront_orders_fulfillment_filter
 ON storefront_orders(fulfillment_state,created_at DESC,id DESC);
CREATE INDEX storefront_orders_refund_filter
 ON storefront_orders(refund_state,created_at DESC,id DESC);
CREATE INDEX storefront_orders_dashboard_counts
 ON storefront_orders(fulfillment_state,refund_state,state);
CREATE INDEX storefront_order_notes_history
 ON storefront_order_notes(order_id,created_at,id);
CREATE INDEX storefront_tracking_history
 ON storefront_tracking_revisions(order_id,created_at,id);
CREATE INDEX storefront_attention_open
 ON storefront_attention_cases(state,updated_at,order_id) WHERE state!='resolved';
