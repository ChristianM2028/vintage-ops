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
        aspects=dict(item.get("item_specifics") or {})
        existing={k.casefold() for k in aspects}
        for k in ("brand","size","color"):
            if item.get(k) and k.casefold() not in existing: aspects[k.title()]=[item[k]]
        aspects={k:v if isinstance(v,list) else [v] for k,v in aspects.items() if v not in (None,"",[])}
        return {"availability":{"shipToLocationAvailability":{"quantity":item["quantity"]}},"condition":item["condition"],"product":{"title":item["title"],"description":item["description"],"imageUrls":photos,"aspects":aspects}}
    def _offer(self,item):
        return {"sku":item["ebay_sku"],"marketplaceId":self.cfg.marketplace_id,"format":"FIXED_PRICE","availableQuantity":item["quantity"],"categoryId":item.get("category") or self.cfg.category_id,"merchantLocationKey":self.cfg.location_key,"listingDescription":item["description"],"pricingSummary":{"price":{"value":str(item["price"]),"currency":item["currency"]}},"listingPolicies":{"fulfillmentPolicyId":self.cfg.fulfillment_policy_id,"paymentPolicyId":self.cfg.payment_policy_id,"returnPolicyId":self.cfg.return_policy_id}}
    def listItem(self,item):
        self._prereqs(item)
        lock=self.db.acquire_publication_lock(item["id"])
        if not lock: raise EbayError("conflict","Another publication is already in progress; wait and synchronize")
        sku=item.get("ebay_sku") or item["sku"]
        if not item.get("ebay_sku"): item=self.db.update_item(item["id"],ebay_sku=sku,ebay_publication_state="preparing",last_error=None)
        try:
            self.client.request("PUT",f"/sell/inventory/v1/inventory_item/{quote(sku,safe='')}",json=self._inventory(item),expected=(200,204))
            self.db.update_item(item["id"],ebay_publication_state="inventory_ready")
            if not item.get("ebay_offer_id"):
                # Recover first after a prior uncertain operation/process restart.
                current=self.db.get_item(item["id"])
                if current.get("ebay_publication_state") in ("creating_offer","failed","partial_failure"):
                    offers=self._matching_offers(sku)
                    if len(offers)>1: raise EbayError("manual_resolution","Multiple eBay offers match this SKU and marketplace; resolve duplicates in Seller Hub")
                    if offers: item=self.db.update_item(item["id"],ebay_offer_id=offers[0]["offerId"],ebay_publication_state="offer_created")
                if not item.get("ebay_offer_id"):
                    self.db.update_item(item["id"],ebay_publication_state="creating_offer")
                    try: result=self.client.request("POST","/sell/inventory/v1/offer",json=self._offer(item),expected=(201,))
                    except EbayError as e:
                        if e.kind=="remote":
                            offers=self._matching_offers(sku)
                            if len(offers)==1: result={"offerId":offers[0]["offerId"]}
                            elif len(offers)>1: raise EbayError("manual_resolution","Multiple matching offers were found after an ambiguous create")
                            else: raise
                        else: raise
                item=self.db.update_item(item["id"],ebay_offer_id=result["offerId"],ebay_publication_state="offer_created")
            try: result=self.client.request("POST",f"/sell/inventory/v1/offer/{quote(item['ebay_offer_id'],safe='')}/publish",expected=(200,))
            except EbayError as e:
                if e.kind=="remote":
                    try: remote=self.client.request("GET",f"/sell/inventory/v1/offer/{quote(item['ebay_offer_id'],safe='')}")
                    except (EbayError, IndexError): raise e
                    if remote.get("listing",{}).get("listingId"): result={"listingId":remote["listing"]["listingId"]}
                    else: raise
                else: raise
            return self.db.update_item(item["id"],ebay_listing_id=result["listingId"],ebay_publication_state="published",status="Active",last_error=None)
        except EbayError as e:
            state="partial_failure" if self.db.get_item(item["id"]).get("ebay_offer_id") else "failed"
            self.db.update_item(item["id"],ebay_publication_state=state,last_error=json.dumps(e.safe_dict()))
            if state=="partial_failure": e.kind="partial_publication"
            raise
        finally: self.db.release_publication_lock(item["id"],lock)
    def _matching_offers(self,sku):
        result=self.client.request("GET",f"/sell/inventory/v1/offer?sku={quote(sku,safe='')}&marketplace_id={quote(self.cfg.marketplace_id,safe='')}")
        return [o for o in result.get("offers",[]) if o.get("sku")==sku and o.get("marketplaceId",self.cfg.marketplace_id)==self.cfg.marketplace_id]
    def updateItem(self,item):
        if not item.get("ebay_sku") or not item.get("ebay_offer_id"): raise EbayError("conflict","Item has no persisted eBay SKU and offer ID; list it first")
        try:
            self.client.request("PUT",f"/sell/inventory/v1/inventory_item/{quote(item['ebay_sku'],safe='')}",json=self._inventory(item),expected=(200,204))
            self.client.request("PUT",f"/sell/inventory/v1/offer/{quote(item['ebay_offer_id'],safe='')}",json=self._offer(item),expected=(200,204))
            return self.db.update_item(item["id"],ebay_sync_state="synchronized",synced_revision=item.get("local_revision",0),last_error=None)
        except EbayError as e:
            self.db.update_item(item["id"],ebay_sync_state="failed",last_error=json.dumps(e.safe_dict()))
            if e.kind=="conflict": raise EbayError("relist_required","eBay does not permit this change while published; withdraw and relist",status=e.status,details=e.details,request_id=e.request_id,rlogid=e.rlogid)
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
    def delist(self,item): return self.delistItem(item)
    def update(self,item): return self.updateItem(item)
    def synchronize(self,item):
        observation=self.inspect_offer(item)
        state=observation["classification"]
        if state=="ambiguous":
            self.db.update_item(item["id"],ebay_sync_state="transient_error"); raise EbayError("remote","eBay state requires review")
        return self.db.update_item(item["id"],ebay_listing_id=observation.get("listing_id") or item.get("ebay_listing_id"),ebay_publication_state=state,ebay_sync_state="missing" if state=="missing" else "synchronized",last_error=None)
    def inspect_offer(self,item):
        """Inspect only the persisted offer identity and return a safe projection."""
        offer=item.get("ebay_offer_id")
        if not offer: return {"classification":"ambiguous","offer_id":None,"listing_id":None,"status":None,"error_kind":"missing_identifier"}
        try: remote=self.client.request("GET",f"/sell/inventory/v1/offer/{quote(offer,safe='')}")
        except EbayError as exc:
            return {"classification":"missing" if exc.status==404 else "ambiguous","offer_id":offer,
                "listing_id":item.get("ebay_listing_id"),"status":None,"error_kind":"not_found" if exc.status==404 else "provider_error",
                "request_id":getattr(exc,"request_id",None),"correlation_id":getattr(exc,"rlogid",None)}
        remote_offer=remote.get("offerId")
        status=str(remote.get("status") or "").upper(); listing=(remote.get("listing") or {}).get("listingId")
        classification=("ambiguous" if remote_offer and remote_offer!=offer else "published" if status in ("PUBLISHED","ACTIVE") else
            "withdrawn" if status in ("WITHDRAWN","ENDED") else "ambiguous")
        return {"classification":classification,"offer_id":offer,"listing_id":listing or item.get("ebay_listing_id"),"status":status or None}
    def reconcile_orders(self):
        data=self.client.request("GET","/sell/fulfillment/v1/order?filter=orderfulfillmentstatus:%7BNOT_STARTED,IN_PROGRESS%7D")
        counts={"processed":0,"duplicate":0,"unknown":0}
        for order in data.get("orders",[]):
            payment=(order.get("orderPaymentStatus") or "").upper()
            for line in order.get("lineItems",[]):
                sku=line.get("sku"); listing=(line.get("legacyItemId") or line.get("listingId")); qty=int(line.get("quantity",1))
                fresh=self.db.process_order_line(order["orderId"],line["lineItemId"],sku,listing,qty,payment,order.get("orderFulfillmentStatus"),order)
                counts["processed" if fresh else "duplicate"]+=1
                if fresh and not any((x.get("ebay_sku")==sku or x.get("ebay_listing_id")==listing) for x in self.db.inventory()): counts["unknown"]+=1
        return counts
