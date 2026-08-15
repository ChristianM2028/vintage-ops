ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT 'Draft'
  CHECK (status IN ('Draft', 'Active', 'Sold', 'Delisted'));

CREATE TABLE item_photos (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
 filename TEXT NOT NULL UNIQUE,
 original_filename TEXT NOT NULL,
 display_order INTEGER NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(item_id, display_order)
);
