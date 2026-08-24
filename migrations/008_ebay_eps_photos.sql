ALTER TABLE item_photos ADD COLUMN ebay_image_id TEXT;
ALTER TABLE item_photos ADD COLUMN ebay_eps_url TEXT;
ALTER TABLE item_photos ADD COLUMN ebay_source_fingerprint TEXT;
ALTER TABLE item_photos ADD COLUMN ebay_upload_error TEXT;
ALTER TABLE items ADD COLUMN photo_set_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN ebay_synced_photo_revision INTEGER NOT NULL DEFAULT 0;
