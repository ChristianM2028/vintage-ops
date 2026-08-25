CREATE TABLE storefront_request_tokens (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES storefront_orders(id),
 action TEXT NOT NULL,
 token TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('succeeded','rejected')),
 created_at TEXT NOT NULL,
 UNIQUE(order_id,action,token)
);
CREATE INDEX storefront_request_tokens_order ON storefront_request_tokens(order_id,created_at);
ALTER TABLE storefront_tracking_revisions ADD COLUMN correction_reason TEXT;
