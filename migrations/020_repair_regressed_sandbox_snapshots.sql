-- Repair items affected by the seller-create regression. The persisted mode is
-- authoritative: remote identifiers are neither inspected nor changed.
WITH setup AS (
 SELECT trim(merchant_location_key) AS location_key,
        trim(fulfillment_policy_id) AS fulfillment_policy_id,
        trim(payment_policy_id) AS payment_policy_id,
        trim(return_policy_id) AS return_policy_id,
        trim(marketplace) AS marketplace_id
 FROM ebay_seller_setup
 WHERE (SELECT count(*) FROM ebay_seller_setup) = 1
   AND nullif(trim(merchant_location_key),'') IS NOT NULL
   AND nullif(trim(fulfillment_policy_id),'') IS NOT NULL
   AND nullif(trim(payment_policy_id),'') IS NOT NULL
   AND nullif(trim(return_policy_id),'') IS NOT NULL
   AND nullif(trim(marketplace),'') IS NOT NULL
), affected AS (
 SELECT items.id FROM items, setup
 WHERE ebay_integration_mode='ebay_sandbox'
   AND (last_error='classification_required: This item is missing its persisted eBay seller configuration'
     OR (json_valid(last_error)
       AND json_extract(last_error,'$.code')='classification_required'
       AND json_extract(last_error,'$.message')='This item is missing its persisted eBay seller configuration'))
)
UPDATE items
SET ebay_merchant_location_key=(SELECT location_key FROM setup),
    ebay_fulfillment_policy_id=(SELECT fulfillment_policy_id FROM setup),
    ebay_payment_policy_id=(SELECT payment_policy_id FROM setup),
    ebay_return_policy_id=(SELECT return_policy_id FROM setup),
    ebay_marketplace_id=(SELECT marketplace_id FROM setup),
    last_error=NULL
WHERE id IN (SELECT id FROM affected);
