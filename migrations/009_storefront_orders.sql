CREATE TABLE storefront_orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 public_id TEXT NOT NULL UNIQUE,
 item_id INTEGER NOT NULL REFERENCES items(id),
 price_snapshot TEXT NOT NULL,
 currency_snapshot TEXT NOT NULL,
 state TEXT NOT NULL,
 stripe_checkout_session_id TEXT UNIQUE,
 stripe_payment_intent_id TEXT UNIQUE,
 payment_state TEXT NOT NULL DEFAULT 'unpaid',
 customer_email TEXT,
 shipping_name TEXT,
 shipping_postal_code TEXT,
 reservation_id INTEGER,
 operation_error TEXT,
 conflict_details TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE item_reservations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_id INTEGER NOT NULL REFERENCES items(id),
 order_id INTEGER NOT NULL UNIQUE REFERENCES storefront_orders(id),
 state TEXT NOT NULL CHECK(state IN ('open','closed','released','expired')),
 expires_at TEXT NOT NULL,
 close_reason TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_open_reservation_per_item ON item_reservations(item_id) WHERE state='open';
CREATE INDEX reservations_expiry ON item_reservations(state,expires_at);
CREATE TABLE stripe_webhook_events (
 event_id TEXT PRIMARY KEY,
 event_type TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('processing','processed','failed')),
 attempts INTEGER NOT NULL DEFAULT 1,
 error TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE storefront_operations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES storefront_orders(id),
 operation_type TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','running','complete','failed','manual_resolution')),
 attempts INTEGER NOT NULL DEFAULT 0,
 idempotency_key TEXT NOT NULL UNIQUE,
 external_id TEXT,
 error TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(order_id, operation_type)
);
