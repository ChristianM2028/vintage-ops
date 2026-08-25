from .errors import EbayError

class MockEbayAdapter:
    """A deterministic local adapter. It has no HTTP or OAuth dependencies."""
    name = "ebay"
    def __init__(self, config, db): self.cfg=config; self.db=db; self.inspect_mode=None; self.withdraw_calls=0
    def list(self,item):
        if item.get("status") == "Sold": raise EbayError("conflict","A sold item cannot be listed")
        offer=item.get("ebay_offer_id") or f"MOCK-OFFER-{item['sku']}"
        listing=item.get("ebay_listing_id") or f"MOCK-LISTING-{item['sku']}"
        return self.db.update_marketplace(item["id"],ebay_sku=item.get("ebay_sku") or item["sku"],ebay_offer_id=offer,ebay_listing_id=listing,ebay_publication_state="published",status="Active",last_error=None)
    def delist(self,item):
        if item.get("ebay_publication_state") == "withdrawn": return item
        self.withdraw_calls+=1
        if self.inspect_mode=="lost_response":
            self.db.update_marketplace(item["id"],ebay_publication_state="withdrawn")
            raise EbayError("remote","Withdrawal outcome requires review")
        status="Sold" if item.get("status")=="Sold" else "Delisted"
        return self.db.update_marketplace(item["id"],ebay_publication_state="withdrawn",status=status,last_error=None)
    def update(self,item):
        if not item.get("ebay_offer_id"): raise EbayError("conflict","List the item before synchronizing edits")
        return self.db.update_marketplace(item["id"],ebay_sync_state="synchronized",last_error=None)
    def synchronize(self,item): return self.db.update_marketplace(item["id"],ebay_sync_state=item.get("ebay_publication_state") or "not_listed",last_error=None)
    def inspect_offer(self,item):
        mode=self.inspect_mode or item.get("ebay_publication_state")
        classification=mode if mode in ("published","withdrawn","missing","ambiguous") else "ambiguous"
        return {"classification":classification,"offer_id":item.get("ebay_offer_id"),
            "listing_id":item.get("ebay_listing_id"),"status":classification.upper() if classification not in ("missing","ambiguous") else None,
            **({"error_kind":"provider_error"} if classification=="ambiguous" else {})}
    listItem = list
    delistItem = delist
