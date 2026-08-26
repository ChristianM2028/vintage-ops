-- Upgrade databases created before stripe_live was added to migration 011.
-- SQLite offers no ALTER CONSTRAINT, so update only the stored table DDL; the
-- schema cookie forces every connection to reparse and enforce the new CHECK.
PRAGMA writable_schema=ON;
UPDATE sqlite_master
SET sql=replace(sql,
  '''fake'',''stripe_test'',''legacy_unknown''',
  '''fake'',''stripe_test'',''stripe_live'',''legacy_unknown''')
WHERE type='table' AND name='storefront_orders'
  AND sql NOT LIKE '%''stripe_live''%';
PRAGMA writable_schema=OFF;
