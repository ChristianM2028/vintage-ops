CREATE TABLE IF NOT EXISTS items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '', price TEXT NOT NULL, currency TEXT NOT NULL,
 quantity INTEGER NOT NULL DEFAULT 1, condition TEXT NOT NULL, brand TEXT, size TEXT,
 color TEXT, category TEXT NOT NULL, photo_urls TEXT NOT NULL DEFAULT '[]',
 ebay_sku TEXT UNIQUE, ebay_offer_id TEXT, ebay_listing_id TEXT,
 ebay_publication_state TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_states (
 state_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS ebay_tokens (
 id INTEGER PRIMARY KEY CHECK(id=1), refresh_token_encrypted BLOB NOT NULL,
 access_token_encrypted BLOB, access_expires_at TEXT, refresh_expires_at TEXT,
 scopes TEXT NOT NULL, updated_at TEXT NOT NULL
);
