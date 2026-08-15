from .errors import EbayError

class MockEbayAdapter:
    """A deterministic local adapter. It has no HTTP or OAuth dependencies."""
    name = "ebay"
    def __init__(self, config, db): self.cfg=config; self.db=db
    def list(self,item):
        if item.get("status") == "Sold": raise EbayError("conflict","A sold item cannot be listed")
        offer=item.get("ebay_offer_id") or f"MOCK-OFFER-{item['sku']}"
        listing=item.get("ebay_listing_id") or f"MOCK-LISTING-{item['sku']}"
        return self.db.update_marketplace(item["id"],ebay_sku=item.get("ebay_sku") or item["sku"],ebay_offer_id=offer,ebay_listing_id=listing,ebay_publication_state="published",status="Active",last_error=None)
    def delist(self,item):
        if item.get("ebay_publication_state") == "withdrawn": return item
        status="Sold" if item.get("status")=="Sold" else "Delisted"
        return self.db.update_marketplace(item["id"],ebay_publication_state="withdrawn",status=status,last_error=None)
    listItem = list
    delistItem = delist
