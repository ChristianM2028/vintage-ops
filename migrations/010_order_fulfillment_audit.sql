ALTER TABLE storefront_orders ADD COLUMN shipping_line1 TEXT;
ALTER TABLE storefront_orders ADD COLUMN shipping_line2 TEXT;
ALTER TABLE storefront_orders ADD COLUMN shipping_city TEXT;
ALTER TABLE storefront_orders ADD COLUMN shipping_state TEXT;
ALTER TABLE storefront_orders ADD COLUMN shipping_country TEXT;
ALTER TABLE storefront_orders ADD COLUMN shipping_phone TEXT;

CREATE TABLE storefront_order_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES storefront_orders(id),
 from_state TEXT,
 to_state TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('shopper','seller','stripe_webhook','ebay_reconciliation','maintenance')),
 category TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX storefront_order_events_order ON storefront_order_events(order_id,created_at,id);
