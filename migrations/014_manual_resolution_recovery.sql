ALTER TABLE storefront_orders ADD COLUMN provider_revision INTEGER NOT NULL DEFAULT 0;

CREATE TABLE storefront_resolution_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES storefront_orders(id),
 request_key TEXT NOT NULL,
 provider_revision INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('checking','complete','ambiguous')),
 stripe_classification TEXT CHECK(stripe_classification IN ('requires_capture','succeeded','canceled','ambiguous')),
 stripe_payment_intent_id TEXT,
 stripe_checked_at TEXT,
 stripe_evidence_json TEXT CHECK(stripe_evidence_json IS NULL OR json_valid(stripe_evidence_json)),
 ebay_classification TEXT CHECK(ebay_classification IN ('published','withdrawn','missing','ambiguous')),
 ebay_offer_id TEXT,
 ebay_listing_id TEXT,
 ebay_checked_at TEXT,
 ebay_evidence_json TEXT CHECK(ebay_evidence_json IS NULL OR json_valid(ebay_evidence_json)),
 claim_token TEXT, lease_expires_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(order_id,request_key)
);
CREATE INDEX storefront_resolution_reviews_order
 ON storefront_resolution_reviews(order_id,created_at DESC,id DESC);

CREATE TABLE storefront_resolution_actions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES storefront_orders(id),
 review_id INTEGER NOT NULL REFERENCES storefront_resolution_reviews(id),
 action TEXT NOT NULL CHECK(action IN ('resume_purchase','cancel_authorization','reconcile_canceled','reconcile_captured')),
 request_key TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','running','complete','manual_resolution','rejected')),
 claim_token TEXT, lease_expires_at TEXT, diagnostic TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(order_id,request_key)
);
CREATE INDEX storefront_resolution_actions_claims
 ON storefront_resolution_actions(order_id,state,lease_expires_at);

CREATE TABLE storefront_operation_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 operation_id INTEGER NOT NULL REFERENCES storefront_operations(id),
 attempt_number INTEGER NOT NULL,
 reason TEXT NOT NULL CHECK(reason IN ('normal','lease_recovery','manual_recovery','provider_reconciliation')),
 state TEXT NOT NULL CHECK(state IN ('running','complete','failed','manual_resolution','superseded')),
 claim_token TEXT, lease_expires_at TEXT, idempotency_key TEXT NOT NULL,
 result TEXT, diagnostic TEXT,
 review_id INTEGER REFERENCES storefront_resolution_reviews(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(operation_id,attempt_number)
);
INSERT INTO storefront_operation_attempts
 (operation_id,attempt_number,reason,state,claim_token,lease_expires_at,idempotency_key,result,diagnostic,created_at,updated_at)
 SELECT id,CASE WHEN attempts < 1 THEN 1 ELSE attempts END,'normal',
 CASE state WHEN 'pending' THEN 'running' ELSE state END,
 claim_token,lease_expires_at,idempotency_key,result,COALESCE(diagnostic,error),created_at,updated_at
 FROM storefront_operations;

ALTER TABLE stripe_webhook_events ADD COLUMN checkout_session_id TEXT;
ALTER TABLE stripe_webhook_events ADD COLUMN payment_intent_id TEXT;
ALTER TABLE stripe_webhook_events ADD COLUMN order_reference TEXT;
ALTER TABLE stripe_webhook_events ADD COLUMN correlation_state TEXT
 CHECK(correlation_state IN ('irrelevant','matched','unmatched_retryable','unmatched_attention'));
ALTER TABLE stripe_webhook_events ADD COLUMN matched_order_id INTEGER REFERENCES storefront_orders(id);
ALTER TABLE stripe_webhook_events ADD COLUMN next_attempt_at TEXT;
ALTER TABLE stripe_webhook_events ADD COLUMN last_attempt_at TEXT;
CREATE INDEX stripe_webhook_retryable ON stripe_webhook_events(correlation_state,next_attempt_at);
CREATE INDEX stripe_webhook_session ON stripe_webhook_events(checkout_session_id) WHERE checkout_session_id IS NOT NULL;
CREATE INDEX stripe_webhook_intent ON stripe_webhook_events(payment_intent_id) WHERE payment_intent_id IS NOT NULL;

INSERT OR IGNORE INTO storefront_attention_cases
 (order_id,kind,summary,state,dedupe_key,actor,created_at,updated_at)
 SELECT id,'manual_recovery','Fresh provider inspection is required before this order can be resolved.',
 'open','manual-recovery:'||public_id,'migration',datetime('now'),datetime('now')
 FROM storefront_orders WHERE state='manual_resolution';
