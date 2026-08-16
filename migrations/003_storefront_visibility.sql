ALTER TABLE items ADD COLUMN storefront_visible INTEGER NOT NULL DEFAULT 0
  CHECK (storefront_visible IN (0, 1));
