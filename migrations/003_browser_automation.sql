CREATE TABLE marketplace_accounts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 marketplace TEXT NOT NULL,
 account_key TEXT NOT NULL,
 encrypted_storage_state BLOB,
 session_expires_at TEXT,
 revoked_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(marketplace, account_key)
);

CREATE TABLE marketplace_listings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
 marketplace TEXT NOT NULL,
 remote_id TEXT,
 remote_url TEXT,
 lifecycle_state TEXT NOT NULL DEFAULT 'draft',
 last_successful_operation TEXT,
 safe_error_json TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(item_id, marketplace)
);

CREATE TABLE automation_runs (
 id TEXT PRIMARY KEY,
 item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
 marketplace TEXT NOT NULL,
 operation TEXT NOT NULL,
 state TEXT NOT NULL,
 checkpoint_field TEXT,
 idempotency_key TEXT NOT NULL UNIQUE,
 safe_error_json TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);

CREATE TABLE automation_field_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE CASCADE,
 field_key TEXT NOT NULL,
 attempt INTEGER NOT NULL,
 state TEXT NOT NULL,
 handler TEXT,
 resolution_confidence REAL,
 classification_confidence REAL,
 evidence_json TEXT NOT NULL DEFAULT '{}',
 safe_error_json TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(run_id, field_key, attempt)
);

CREATE TABLE automation_selector_cache (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 marketplace TEXT NOT NULL,
 form_identifier TEXT NOT NULL,
 field_key TEXT NOT NULL,
 route_family TEXT NOT NULL,
 locale TEXT NOT NULL DEFAULT '',
 adapter_version TEXT NOT NULL,
 generation INTEGER NOT NULL DEFAULT 1,
 locator_recipe_json TEXT NOT NULL,
 control_tag TEXT NOT NULL,
 control_role TEXT,
 control_type TEXT,
 accessible_name_hash TEXT,
 container_fingerprint TEXT,
 interaction_kind TEXT NOT NULL,
 resolution_confidence REAL NOT NULL,
 classification_confidence REAL NOT NULL,
 verification_success_count INTEGER NOT NULL DEFAULT 0,
 consecutive_failure_count INTEGER NOT NULL DEFAULT 0,
 first_success_at TEXT,
 last_success_at TEXT,
 page_build_fingerprint TEXT,
 quarantined_at TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(marketplace, form_identifier, field_key, route_family, locale, adapter_version, generation)
);

CREATE INDEX idx_automation_runs_pending ON automation_runs(state, created_at);
CREATE INDEX idx_field_attempts_run ON automation_field_attempts(run_id, field_key);
CREATE INDEX idx_selector_cache_lookup ON automation_selector_cache(marketplace, form_identifier, field_key, route_family, locale, adapter_version, quarantined_at);
