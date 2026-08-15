import json
from urllib.parse import quote
from .errors import EbayError

class EbayService:
    name = "ebay"
    def __init__(self, config, db, client): self.cfg=config; self.db=db; self.client=client
    def _prereqs(self,item):
        missing=[name for name,value in {"merchant location key":self.cfg.location_key,"fulfillment policy ID":self.cfg.fulfillment_policy_id,"payment policy ID":self.cfg.payment_policy_id,"return policy ID":self.cfg.return_policy_id,"leaf category ID":item.get("category") or self.cfg.category_id}.items() if not value]
        if missing: raise EbayError("missing_prerequisite","Configure "+", ".join(missing)+" before listing")
        photos=json.loads(item["photo_urls"]) if isinstance(item["photo_urls"],str) else item["photo_urls"]
        if not photos or any(not p.startswith("https://") for p in photos): raise EbayError("invalid_item","At least one public HTTPS photo URL is required")
    def _inventory(self,item):
        photos=json.loads(item["photo_urls"]) if isinstance(item["photo_urls"],str) else item["photo_urls"]
        aspects={k:[item[k]] for k in ("brand","size","color") if item.get(k)}
        return {"availability":{"shipToLocationAvailability":{"quantity":item["quantity"]}},"condition":item["condition"],"product":{"title":item["title"],"description":item["description"],"imageUrls":photos,"aspects":aspects}}
    def _offer(self,item):
        return {"sku":item["ebay_sku"],"marketplaceId":self.cfg.marketplace_id,"format":"FIXED_PRICE","availableQuantity":item["quantity"],"categoryId":item.get("category") or self.cfg.category_id,"merchantLocationKey":self.cfg.location_key,"listingDescription":item["description"],"pricingSummary":{"price":{"value":str(item["price"]),"currency":item["currency"]}},"listingPolicies":{"fulfillmentPolicyId":self.cfg.fulfillment_policy_id,"paymentPolicyId":self.cfg.payment_policy_id,"returnPolicyId":self.cfg.return_policy_id}}
    def listItem(self,item):
        self._prereqs(item); sku=item.get("ebay_sku") or item["sku"]
        if not item.get("ebay_sku"): item=self.db.update_item(item["id"],ebay_sku=sku,ebay_publication_state="preparing",last_error=None)
        try:
            self.client.request("PUT",f"/sell/inventory/v1/inventory_item/{quote(sku,safe='')}",json=self._inventory(item),expected=(200,204))
            self.db.update_item(item["id"],ebay_publication_state="inventory_ready")
            if not item.get("ebay_offer_id"):
                result=self.client.request("POST","/sell/inventory/v1/offer",json=self._offer(item),expected=(201,))
                item=self.db.update_item(item["id"],ebay_offer_id=result["offerId"],ebay_publication_state="offer_created")
            result=self.client.request("POST",f"/sell/inventory/v1/offer/{quote(item['ebay_offer_id'],safe='')}/publish",expected=(200,))
            return self.db.update_item(item["id"],ebay_listing_id=result["listingId"],ebay_publication_state="published",status="Active",last_error=None)
        except EbayError as e:
            state="partial_failure" if self.db.get_item(item["id"]).get("ebay_offer_id") else "failed"
            self.db.update_item(item["id"],ebay_publication_state=state,last_error=json.dumps(e.safe_dict()))
            if state=="partial_failure": e.kind="partial_publication"
            raise
    def updateItem(self,item):
        if not item.get("ebay_sku") or not item.get("ebay_offer_id"): raise EbayError("conflict","Item has no persisted eBay SKU and offer ID; list it first")
        try:
            self.client.request("PUT",f"/sell/inventory/v1/inventory_item/{quote(item['ebay_sku'],safe='')}",json=self._inventory(item),expected=(200,204))
            self.client.request("PUT",f"/sell/inventory/v1/offer/{quote(item['ebay_offer_id'],safe='')}",json=self._offer(item),expected=(200,204))
            return self.db.update_item(item["id"],last_error=None)
        except EbayError as e:
            if e.kind=="conflict": raise EbayError("relist_required","eBay does not permit this change while published; withdraw and relist",status=e.status,details=e.details,request_id=e.request_id)
            raise
    def delistItem(self,item):
        if item.get("ebay_publication_state")=="withdrawn": return item
        if not item.get("ebay_offer_id"): raise EbayError("withdrawal_failure","No eBay offer ID is available to withdraw")
        try: self.client.request("POST",f"/sell/inventory/v1/offer/{quote(item['ebay_offer_id'],safe='')}/withdraw",expected=(200,204))
        except EbayError as e:
            self.db.update_item(item["id"],last_error=json.dumps(e.safe_dict())); e.kind="withdrawal_failure"; raise
        return self.db.update_item(item["id"],ebay_publication_state="withdrawn",status="Delisted",last_error=None)
    # Explicit adapter interface; camelCase remains for compatibility with the
    # original Sandbox lifecycle and callers.
    def list(self,item): return self.listItem(item)
    def update(self,item): return self.updateItem(item)
    def delist(self,item): return self.delistItem(item)
