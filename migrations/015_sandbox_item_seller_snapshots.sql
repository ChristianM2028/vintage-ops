-- Snapshot the sole usable Sandbox seller setup onto only unassociated legacy items.
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
), eligible AS (
 SELECT items.id FROM items, setup
 WHERE ebay_integration_mode='ebay_sandbox'
   AND nullif(trim(coalesce(ebay_offer_id,'')),'') IS NULL
   AND nullif(trim(coalesce(ebay_listing_id,'')),'') IS NULL
   AND nullif(trim(coalesce(ebay_publication_state,'')),'') IS NULL
)
UPDATE items
SET ebay_merchant_location_key=CASE WHEN nullif(trim(coalesce(ebay_merchant_location_key,'')),'') IS NULL THEN (SELECT location_key FROM setup) ELSE ebay_merchant_location_key END,
    ebay_fulfillment_policy_id=CASE WHEN nullif(trim(coalesce(ebay_fulfillment_policy_id,'')),'') IS NULL THEN (SELECT fulfillment_policy_id FROM setup) ELSE ebay_fulfillment_policy_id END,
    ebay_payment_policy_id=CASE WHEN nullif(trim(coalesce(ebay_payment_policy_id,'')),'') IS NULL THEN (SELECT payment_policy_id FROM setup) ELSE ebay_payment_policy_id END,
    ebay_return_policy_id=CASE WHEN nullif(trim(coalesce(ebay_return_policy_id,'')),'') IS NULL THEN (SELECT return_policy_id FROM setup) ELSE ebay_return_policy_id END,
    ebay_marketplace_id=CASE WHEN nullif(trim(coalesce(ebay_marketplace_id,'')),'') IS NULL THEN (SELECT marketplace_id FROM setup) ELSE ebay_marketplace_id END,
    last_error=CASE
      WHEN (last_error='classification_required: This item is missing its persisted eBay seller configuration'
        OR CASE WHEN json_valid(last_error) THEN
          json_extract(last_error,'$.code')='classification_required'
          AND json_extract(last_error,'$.message')='This item is missing its persisted eBay seller configuration'
          ELSE 0 END)
       AND nullif(trim(CASE WHEN nullif(trim(coalesce(ebay_merchant_location_key,'')),'') IS NULL THEN (SELECT location_key FROM setup) ELSE ebay_merchant_location_key END),'') IS NOT NULL
       AND nullif(trim(CASE WHEN nullif(trim(coalesce(ebay_fulfillment_policy_id,'')),'') IS NULL THEN (SELECT fulfillment_policy_id FROM setup) ELSE ebay_fulfillment_policy_id END),'') IS NOT NULL
       AND nullif(trim(CASE WHEN nullif(trim(coalesce(ebay_payment_policy_id,'')),'') IS NULL THEN (SELECT payment_policy_id FROM setup) ELSE ebay_payment_policy_id END),'') IS NOT NULL
       AND nullif(trim(CASE WHEN nullif(trim(coalesce(ebay_return_policy_id,'')),'') IS NULL THEN (SELECT return_policy_id FROM setup) ELSE ebay_return_policy_id END),'') IS NOT NULL
       AND nullif(trim(CASE WHEN nullif(trim(coalesce(ebay_marketplace_id,'')),'') IS NULL THEN (SELECT marketplace_id FROM setup) ELSE ebay_marketplace_id END),'') IS NOT NULL
      THEN NULL ELSE last_error END
WHERE id IN (SELECT id FROM eligible);
