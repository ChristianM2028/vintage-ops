import json
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode
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

    @staticmethod
    def _safe_offer(offer):
        """Return only identifiers and state that are safe to show or persist."""
        listing=offer.get("listing") if isinstance(offer.get("listing"),dict) else {}
        return {"offer_id":offer.get("offerId"),"status":offer.get("status"),
                "listing_id":listing.get("listingId"),"sku":offer.get("sku"),
                "marketplace":offer.get("marketplaceId")}

    def inspect_recovery_offer(self,item):
        """Read seller-scoped offers and prove identity plus the full offer contract."""
        sku=item["sku"]; marketplace="EBAY_US"
        expected=self._offer(dict(item,ebay_sku=sku))
        try: result=self.client.request("GET",f"/sell/inventory/v1/offer?sku={quote(sku,safe='')}&marketplace_id={marketplace}")
        except EbayError:
            return {"classification":"ambiguous","sku":sku,"marketplace":marketplace,
                    "offers":[],"reason":"provider_inspection_error"}
        offers=result.get("offers") if isinstance(result,dict) else None
        if not isinstance(offers,list):
            return {"classification":"ambiguous","sku":sku,"marketplace":marketplace,
                    "offers":[],"reason":"invalid_offer_collection"}
        reports=[]; matches=[]
        for remote in offers:
            if not isinstance(remote,dict):
                reports.append({"offer_id":None,"status":None,"listing_id":None,"sku":None,
                                "marketplace":None,"reason":"malformed_offer"}); continue
            safe=self._safe_offer(remote); mismatches=[]
            if item.get("ebay_sku") not in (None,"",sku): mismatches.append("seller_snapshot_sku_mismatch")
            if self.cfg.marketplace_id!=marketplace: mismatches.append("seller_snapshot_marketplace_mismatch")
            for name,value in (("merchant_location_key",self.cfg.location_key),
                ("fulfillment_policy_id",self.cfg.fulfillment_policy_id),("payment_policy_id",self.cfg.payment_policy_id),
                ("return_policy_id",self.cfg.return_policy_id)):
                if not isinstance(value,str) or not value.strip(): mismatches.append(f"missing_seller_snapshot_{name}")
            comparisons=(("offer_id",remote.get("offerId"),None),("sku",remote.get("sku"),sku),
                ("marketplace",remote.get("marketplaceId"),marketplace),("format",remote.get("format"),"FIXED_PRICE"),
                ("available_quantity",remote.get("availableQuantity"),expected["availableQuantity"]),
                ("category_id",remote.get("categoryId"),expected["categoryId"]),
                ("merchant_location_key",remote.get("merchantLocationKey"),expected["merchantLocationKey"]),
                ("listing_description",remote.get("listingDescription"),expected["listingDescription"]))
            for name,actual,wanted in comparisons:
                if name=="offer_id":
                    if not actual: mismatches.append("missing_offer_id")
                elif str(actual) != str(wanted): mismatches.append(f"{name}_mismatch")
            policies=remote.get("listingPolicies") if isinstance(remote.get("listingPolicies"),dict) else {}
            for key in ("fulfillmentPolicyId","paymentPolicyId","returnPolicyId"):
                if policies.get(key)!=expected["listingPolicies"][key]: mismatches.append(f"{key}_mismatch")
            price=(remote.get("pricingSummary") or {}).get("price",{}) if isinstance(remote.get("pricingSummary"),dict) else {}
            if price.get("currency")!=expected["pricingSummary"]["price"]["currency"]: mismatches.append("price_currency_mismatch")
            try: price_matches=Decimal(str(price.get("value")))==Decimal(str(expected["pricingSummary"]["price"]["value"]))
            except (InvalidOperation,TypeError,ValueError): price_matches=False
            if not price_matches: mismatches.append("price_value_mismatch")
            status=str(remote.get("status") or "").upper(); listing=safe["listing_id"]
            if status=="UNPUBLISHED" and listing: mismatches.append("unpublished_offer_has_listing_id")
            elif status in ("PUBLISHED","ACTIVE") and not listing: mismatches.append("published_offer_missing_listing_id")
            elif status not in ("UNPUBLISHED","PUBLISHED","ACTIVE"): mismatches.append("unsupported_status")
            safe["reason"]="match" if not mismatches else ",".join(mismatches)
            reports.append(safe)
            if not mismatches: matches.append(remote)
        reason="exactly_one_match" if len(matches)==1 and len(offers)==1 else (
            "no_offers" if not offers else "multiple_offers" if len(offers)>1 else reports[0]["reason"])
        return {"classification":"recoverable" if len(matches)==1 and len(offers)==1 else "ambiguous",
                "sku":sku,"marketplace":marketplace,"offers":reports,"reason":reason,
                "match":matches[0] if len(matches)==1 and len(offers)==1 else None}

    def recover_offer(self,item):
        """Inspect first; only an exactly proven offer may be published or adopted."""
        lock=self.db.acquire_publication_lock(item["id"])
        if not lock: raise EbayError("conflict","Another publication recovery is already in progress; wait before inspecting again")
        try: return self._recover_offer_locked(self.db.get_item(item["id"]))
        finally: self.db.release_publication_lock(item["id"],lock)

    def _recover_offer_locked(self,item):
        report=self.inspect_recovery_offer(item); remote=report.pop("match",None)
        if not remote:
            error=EbayError("manual_resolution",f"Offer recovery for {report['sku']} on {report['marketplace']} is ambiguous: {report['reason']}",details=report["offers"])
            self.db.update_item(item["id"],last_error=json.dumps({**error.safe_dict(),"inspection":report},sort_keys=True))
            raise error
        offer_id=remote["offerId"]; status=str(remote["status"]).upper()
        if status=="UNPUBLISHED":
            item=self.db.update_item(item["id"],ebay_sku=report["sku"],ebay_offer_id=offer_id,
                                     ebay_publication_state="offer_created")
            try: published=self.client.request("POST",f"/sell/inventory/v1/offer/{quote(offer_id,safe='')}/publish",expected=(200,))
            except EbayError as error:
                self.db.update_item(item["id"],ebay_publication_state="partial_failure",last_error=json.dumps(error.safe_dict()))
                raise
            listing_id=published.get("listingId")
            if not listing_id: raise EbayError("manual_resolution","eBay published the inspected offer without returning a listing ID")
        else: listing_id=remote["listing"]["listingId"]
        return self.db.update_item(item["id"],ebay_sku=report["sku"],ebay_offer_id=offer_id,
            ebay_listing_id=listing_id,ebay_publication_state="published",status="Active",last_error=None)
    def listItem(self,item):
        if not getattr(self.cfg,"ebay_listing_enabled",True): raise EbayError("listing_disabled","New listings are temporarily disabled")
        if self.cfg.ebay_provider_mode=="production":
            from .production_validation import require_production_activation
            require_production_activation(self.cfg,self.db,self.cfg.expected_ebay_seller_id)
        self._prereqs(item)
        sku=item.get("ebay_sku") or item["sku"]
        marketplace=self.cfg.marketplace_id
        if item.get("ebay_sku") and item["ebay_sku"] != item["sku"]:
            raise EbayError("manual_resolution",f"Refusing to list {item['sku']}: persisted eBay SKU {item['ebay_sku']} does not match")
        if item.get("ebay_marketplace_id") and item["ebay_marketplace_id"] != marketplace:
            raise EbayError("manual_resolution",f"Refusing to list {sku}: item marketplace {item['ebay_marketplace_id']} does not match {marketplace}")
        lock=self.db.acquire_publication_lock(item["id"])
        if not lock: raise EbayError("conflict","Another publication is already in progress; wait and synchronize")
        if not item.get("ebay_sku"): item=self.db.update_item(item["id"],ebay_sku=sku,ebay_publication_state="preparing",last_error=None)
        try:
            self.client.request("PUT",f"/sell/inventory/v1/inventory_item/{quote(sku,safe='')}",json=self._inventory(item),expected=(200,204))
            self.db.update_item(item["id"],ebay_publication_state="inventory_ready")
            if not item.get("ebay_offer_id"):
                # Always inspect seller-scoped remote state before the one
                # non-idempotent create. This also repairs records whose prior
                # response was lost before their local state was persisted.
                offers=self._matching_offers(item)
                if len(offers)>1: raise self._offer_resolution_error(item,offers,"before create")
                if offers: item=self.db.update_item(item["id"],ebay_offer_id=offers[0]["offerId"],ebay_publication_state="offer_created")
                if not item.get("ebay_offer_id"):
                    self.db.update_item(item["id"],ebay_publication_state="creating_offer")
                    try: result=self.client.request("POST","/sell/inventory/v1/offer",json=self._offer(item),expected=(201,))
                    except EbayError as e:
                        if self._ambiguous_create_error(e):
                            offers=self._matching_offers(item)
                            if len(offers)==1: result={"offerId":offers[0]["offerId"]}
                            else: raise self._offer_resolution_error(item,offers,"after ambiguous create",e)
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
    def _matching_offers(self,item):
        sku=item["ebay_sku"]; marketplace=self.cfg.marketplace_id
        result=self.client.request("GET",f"/sell/inventory/v1/offer?sku={quote(sku,safe='')}&marketplace_id={quote(marketplace,safe='')}")
        offers=result.get("offers",[])
        if not isinstance(offers,list):
            raise EbayError("manual_resolution",f"Cannot safely inspect offers for SKU {sku} on {marketplace}: eBay returned an invalid offer collection")

        matches=[]; ambiguous=[]
        for offer in offers:
            if not isinstance(offer,dict):
                ambiguous.append(offer); continue
            remote_sku=offer.get("sku"); remote_marketplace=offer.get("marketplaceId")
            # getOffers is seller-scoped, but its query parameters are not an
            # identity guarantee. Never infer either identity when it is absent.
            if remote_sku is None or remote_marketplace is None:
                ambiguous.append(offer); continue
            if remote_sku != sku or remote_marketplace != marketplace:
                continue
            status=str(offer.get("status") or "").upper()
            listing_id=(offer.get("listing") or {}).get("listingId") if isinstance(offer.get("listing") or {},dict) else None
            offer_format=str(offer.get("format") or "FIXED_PRICE").upper()
            if (not offer.get("offerId") or status not in ("", "UNPUBLISHED") or
                    listing_id or offer_format != "FIXED_PRICE"):
                ambiguous.append(offer); continue
            # Category, location, and policy fields in getOffers may be omitted
            # or represented differently from createOffer. They are mutable
            # listing configuration, not seller-offer identity.
            matches.append(offer)
        if ambiguous:
            raise self._offer_resolution_error(item,matches + ambiguous,"during discovery")
        return matches
    @staticmethod
    def _ambiguous_create_error(error):
        if error.status is None or (error.status and error.status>=500): return True
        duplicate_text="offer entity already exists"
        for detail in error.details:
            text=" ".join(str(detail.get(key) or "") for key in ("message","longMessage")).casefold()
            if detail.get("errorId") == 25002 and duplicate_text in text:
                return True
        # Some client adapters do not expose structured details. Match eBay's
        # exact duplicate-offer wording, without assuming a particular 4xx.
        return duplicate_text in str(error).casefold()
    def _offer_resolution_error(self,item,offers,phase,original=None):
        sku=item["ebay_sku"]; marketplace=self.cfg.marketplace_id
        count=len(offers)
        message=(f"Cannot safely continue offer creation for SKU {sku} on {marketplace} {phase}: "
                 f"found {count} seller-scoped candidate or ambiguous offers; expected exactly one unambiguous unpublished offer. "
                 "Inspect this exact SKU and marketplace in Seller Hub; no create retry was attempted.")
        return EbayError("manual_resolution",message,status=getattr(original,"status",None),
                         request_id=getattr(original,"request_id",None),rlogid=getattr(original,"rlogid",None))
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
    def reconcile_orders(self,max_orders=None):
        if self.cfg.ebay_provider_mode=="production":
            from .production_validation import require_production_activation
            require_production_activation(self.cfg,self.db,self.cfg.expected_ebay_seller_id)
        maximum=min(max_orders if max_orders is not None else self.cfg.ebay_order_max_orders,self.cfg.ebay_order_max_orders)
        if not isinstance(maximum,int) or isinstance(maximum,bool) or maximum < 1: raise ValueError("max_orders must be positive")
        counts={"processed":0,"duplicate":0,"unknown":0,"pages_fetched":0}
        offset=0; seen_orders=0
        while seen_orders < maximum and counts["pages_fetched"] < self.cfg.ebay_order_max_pages:
            page_size=min(self.cfg.ebay_order_page_size,maximum-seen_orders)
            if hasattr(self.client,"order_page"):
                data=self.client.order_page(limit=page_size,offset=offset)
            else: # Preserve the small adapter contract used by existing fakes.
                query=urlencode({"filter":"orderfulfillmentstatus:{NOT_STARTED,IN_PROGRESS}","limit":page_size,"offset":offset})
                data=self.client.request("GET","/sell/fulfillment/v1/order?"+query)
            orders=data.get("orders",[])
            if not isinstance(orders,list): raise EbayError("remote","eBay returned an invalid orders page")
            counts["pages_fetched"]+=1
            if len(orders)>page_size: raise EbayError("remote","eBay exceeded the requested page size")
            for order in orders:
                if seen_orders>=maximum: break
                seen_orders+=1
                self._reconcile_order(order,counts)
            if len(orders)<page_size: break
            offset+=len(orders)
        if seen_orders < maximum and counts["pages_fetched"] >= self.cfg.ebay_order_max_pages and len(orders)==page_size:
            raise EbayError("remote","eBay reconciliation page limit reached before completion")
        return counts

    def _reconcile_order(self,order,counts):
            payment=(order.get("orderPaymentStatus") or "").upper()
            for line in order.get("lineItems",[]):
                sku=line.get("sku"); listing=(line.get("legacyItemId") or line.get("listingId")); qty=int(line.get("quantity",1))
                fresh=self.db.process_order_line(order["orderId"],line["lineItemId"],sku,listing,qty,payment,order.get("orderFulfillmentStatus"),order)
                counts["processed" if fresh else "duplicate"]+=1
                if fresh and not any((x.get("ebay_sku")==sku or x.get("ebay_listing_id")==listing) for x in self.db.inventory()): counts["unknown"]+=1
