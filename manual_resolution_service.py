"""Evidence-gated recovery for orders which normal orchestration cannot mutate."""
import json, secrets
from datetime import datetime, timezone, timedelta

from db import now
from integrations.ebay import EbayError
from integrations.stripe import StripeIntegrationError


class ManualResolutionError(ValueError): pass


def _json(value): return json.dumps(value,sort_keys=True,separators=(",",":"))


class ManualResolutionService:
    def __init__(self,db,stripe,ebay_resolver,config): self.db,self.stripe,self.ebay_resolver,self.cfg=db,stripe,ebay_resolver,config

    def _stripe_observation(self,order):
        try:
            value=self.stripe.retrieve_payment_intent(order["stripe_payment_intent_id"])
            if not isinstance(value,dict) or value.get("id")!=order["stripe_payment_intent_id"]: raise ValueError
            status=value.get("status"); method=value.get("capture_method")
            classification="requires_capture" if status=="requires_capture" and method=="manual" else status if status in ("succeeded","canceled") else "ambiguous"
            evidence={k:value.get(k) for k in ("id","status","capture_method","amount","amount_capturable","amount_received","currency")}
            evidence.update(classification=classification)
            return classification,evidence
        except Exception as exc:
            kind=exc.kind if isinstance(exc,StripeIntegrationError) else "malformed_result"
            return "ambiguous",{"classification":"ambiguous","id":order.get("stripe_payment_intent_id"),"error_kind":kind}

    def recheck(self,public_id,request_key):
        if not request_key: raise ManualResolutionError("A request token is required")
        token=secrets.token_urlsafe(24); stamp=now(); expiry=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
        with self.db.transaction(immediate=True) as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order or order["state"]!="manual_resolution": raise ManualResolutionError("Order is not in manual resolution")
            prior=c.execute("SELECT * FROM storefront_resolution_reviews WHERE order_id=? AND request_key=?",(order["id"],request_key)).fetchone()
            if prior and prior["state"] in ("complete","ambiguous"): return dict(prior)
            if prior and prior["lease_expires_at"] and prior["lease_expires_at"]>stamp: raise ManualResolutionError("A provider recheck is already active")
            if prior: c.execute("UPDATE storefront_resolution_reviews SET claim_token=?,lease_expires_at=?,updated_at=? WHERE id=?",(token,expiry,stamp,prior["id"])); review_id=prior["id"]
            else: review_id=c.execute("INSERT INTO storefront_resolution_reviews(order_id,request_key,provider_revision,state,claim_token,lease_expires_at,created_at,updated_at) VALUES(?,?,?,'checking',?,?,?,?)",(order["id"],request_key,order["provider_revision"],token,expiry,stamp,stamp)).lastrowid
            order=dict(order)
        stripe_class,stripe_evidence=self._stripe_observation(order)
        checked=now(); stripe_evidence["checked_at"]=checked
        with self.db.transaction(immediate=True) as c:
            changed=c.execute("UPDATE storefront_resolution_reviews SET stripe_classification=?,stripe_payment_intent_id=?,stripe_checked_at=?,stripe_evidence_json=?,updated_at=? WHERE id=? AND claim_token=?",(stripe_class,order.get("stripe_payment_intent_id"),checked,_json(stripe_evidence),checked,review_id,token)).rowcount
            if not changed: raise ManualResolutionError("Provider recheck was superseded")
            c.execute("UPDATE storefront_orders SET provider_revision=provider_revision+1,updated_at=? WHERE id=?",(checked,order["id"]))
            revision=c.execute("SELECT provider_revision FROM storefront_orders WHERE id=?",(order["id"],)).fetchone()[0]
            c.execute("UPDATE storefront_resolution_reviews SET provider_revision=? WHERE id=?",(revision,review_id))
        item=self.db.get_item(order["item_id"])
        try: ebay=self.ebay_resolver(item)[0].inspect_offer(item)
        except Exception: ebay={"classification":"ambiguous","offer_id":item.get("ebay_offer_id"),"listing_id":item.get("ebay_listing_id"),"error_kind":"provider_error"}
        ebay_class=ebay.get("classification") if ebay.get("classification") in ("published","withdrawn","missing") else "ambiguous"
        safe={k:ebay.get(k) for k in ("classification","offer_id","listing_id","status","request_id","correlation_id","error_kind")}; checked=now(); safe["checked_at"]=checked
        state="complete" if stripe_class!="ambiguous" and ebay_class!="ambiguous" else "ambiguous"
        with self.db.transaction(immediate=True) as c:
            current=c.execute("SELECT provider_revision FROM storefront_orders WHERE id=?",(order["id"],)).fetchone()[0]+1
            c.execute("UPDATE storefront_orders SET provider_revision=?,updated_at=? WHERE id=?",(current,checked,order["id"]))
            changed=c.execute("""UPDATE storefront_resolution_reviews SET ebay_classification=?,ebay_offer_id=?,ebay_listing_id=?,ebay_checked_at=?,ebay_evidence_json=?,state=?,provider_revision=?,claim_token=NULL,lease_expires_at=NULL,updated_at=? WHERE id=? AND claim_token=?""",(ebay_class,item.get("ebay_offer_id"),safe.get("listing_id"),checked,_json(safe),state,current,checked,review_id,token)).rowcount
            if not changed: raise ManualResolutionError("Provider recheck was superseded")
            if ebay_class in ("published","withdrawn"):
                c.execute("UPDATE items SET ebay_publication_state=?,ebay_listing_id=COALESCE(?,ebay_listing_id),updated_at=? WHERE id=?",(ebay_class,safe.get("listing_id"),checked,item["id"]))
            self.db.append_order_event(public_id,"manual_resolution_rechecked",actor="seller",metadata={"review_id":review_id,"stripe_classification":stripe_class,"ebay_classification":ebay_class,"payment_intent_id":order.get("stripe_payment_intent_id"),"offer_id":item.get("ebay_offer_id")},dedupe_key="resolution-review:"+str(review_id),con=c)
        self.db.create_attention_case(public_id,"manual_recovery","Fresh provider evidence is available; ambiguous evidence keeps the reservation held.","manual-recovery:"+public_id,"seller")
        return self.review(public_id,review_id)

    def review(self,public_id,review_id):
        with self.db.connect() as c:
            row=c.execute("SELECT r.* FROM storefront_resolution_reviews r JOIN storefront_orders o ON o.id=r.order_id WHERE o.public_id=? AND r.id=?",(public_id,review_id)).fetchone()
            return dict(row) if row else None

    def _claim_action(self,public_id,review_id,request_key,action):
        if not request_key: raise ManualResolutionError("A request token is required")
        token=secrets.token_urlsafe(24); stamp=now(); expiry=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
        with self.db.transaction(immediate=True) as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone(); review=c.execute("SELECT * FROM storefront_resolution_reviews WHERE id=?",(review_id,)).fetchone()
            if not order or order["state"]!="manual_resolution" or not review or review["order_id"]!=order["id"] or review["state"]!="complete" or review["provider_revision"]!=order["provider_revision"]: raise ManualResolutionError("Provider evidence is stale or incomplete")
            item=c.execute("SELECT * FROM items WHERE id=?",(order["item_id"],)).fetchone()
            if review["stripe_payment_intent_id"]!=order["stripe_payment_intent_id"] or review["ebay_offer_id"]!=item["ebay_offer_id"] or (review["ebay_listing_id"] or item["ebay_listing_id"])!=item["ebay_listing_id"]: raise ManualResolutionError("Provider identifiers changed")
            prior=c.execute("SELECT * FROM storefront_resolution_actions WHERE order_id=? AND request_key=?",(order["id"],request_key)).fetchone()
            if prior: return dict(order),dict(review),dict(item),dict(prior),None
            active=c.execute("SELECT 1 FROM storefront_resolution_actions WHERE order_id=? AND state IN ('pending','running') AND lease_expires_at>?",(order["id"],stamp)).fetchone()
            if active: raise ManualResolutionError("Another recovery action is active")
            aid=c.execute("INSERT INTO storefront_resolution_actions(order_id,review_id,action,request_key,state,claim_token,lease_expires_at,created_at,updated_at) VALUES(?,?,?,?, 'running',?,?,?,?)",(order["id"],review_id,action,request_key,token,expiry,stamp,stamp)).lastrowid
            return dict(order),dict(review),dict(item),{"id":aid,"state":"running"},token

    def _finish(self,public_id,action,token,state="complete",diagnostic=None):
        with self.db.transaction(immediate=True) as c:
            changed=c.execute("UPDATE storefront_resolution_actions SET state=?,diagnostic=?,claim_token=NULL,lease_expires_at=NULL,updated_at=? WHERE id=? AND state='running' AND claim_token=?",(state,diagnostic,now(),action["id"],token)).rowcount
            if not changed: raise ManualResolutionError("Recovery completion was superseded")
            c.execute("UPDATE storefront_orders SET provider_revision=provider_revision+1,updated_at=? WHERE public_id=?",(now(),public_id))

    def resume_purchase(self,public_id,review_id,request_key):
        order,review,item,action,token=self._claim_action(public_id,review_id,request_key,"resume_purchase")
        if token is None: return self.db.get_order(public_id)
        if review["stripe_classification"]!="requires_capture" or review["ebay_classification"] not in ("published","withdrawn"): self._finish(public_id,action,token,"rejected","unsupported_evidence"); raise ManualResolutionError("Provider evidence does not permit resume")
        adapter=self.ebay_resolver(item)[0]
        if review["ebay_classification"]=="published":
            observation=adapter.inspect_offer(item)
            if observation.get("classification")=="published":
                try: adapter.delist(item)
                except EbayError: pass
                item=self.db.get_item(item["id"]); observation=adapter.inspect_offer(item)
            if observation.get("classification")!="withdrawn": self._finish(public_id,action,token,"manual_resolution","withdrawal_unconfirmed"); return self.db.get_order(public_id)
        # Stripe is re-inspected before mutation, fencing external capture/cancel.
        stripe_class,_=self._stripe_observation(order)
        if stripe_class!="requires_capture": self._finish(public_id,action,token,"manual_resolution","stripe_state_changed"); return self.db.get_order(public_id)
        try: result=self.stripe.capture(order["stripe_payment_intent_id"],"capture:"+public_id)
        except StripeIntegrationError: self._finish(public_id,action,token,"manual_resolution","capture_ambiguous"); return self.db.get_order(public_id)
        if result.get("status")!="succeeded": self._finish(public_id,action,token,"manual_resolution","capture_ambiguous"); return self.db.get_order(public_id)
        if isinstance(result.get("amount_received"),int) and result.get("currency"): self.db.record_provider_capture(public_id,result["amount_received"],result["currency"])
        with self.db.transaction(immediate=True) as c:
            c.execute("UPDATE storefront_orders SET state='paid',payment_state='captured',provider_revision=provider_revision+1,updated_at=? WHERE id=? AND state='manual_resolution'",(now(),order["id"]))
        self._finish(public_id,action,token)
        return self.db.finalize_storefront_sale(public_id,source="seller")

    def cancel_authorization(self,public_id,review_id,request_key):
        order,review,item,action,token=self._claim_action(public_id,review_id,request_key,"cancel_authorization")
        if token is None: return self.db.get_order(public_id)
        if review["stripe_classification"]!="requires_capture" or order["payment_state"]=="captured": self._finish(public_id,action,token,"rejected","captured_or_unsupported"); raise ManualResolutionError("Captured payments cannot be cancelled")
        current,_=self._stripe_observation(order)
        if current=="succeeded": self._finish(public_id,action,token,"manual_resolution","captured"); raise ManualResolutionError("Captured payments cannot be cancelled")
        if current=="requires_capture":
            try: result=self.stripe.cancel(order["stripe_payment_intent_id"],"cancel:"+public_id)
            except StripeIntegrationError: self._finish(public_id,action,token,"manual_resolution","cancellation_ambiguous"); return self.db.get_order(public_id)
            current="canceled" if result.get("status")=="canceled" else "ambiguous"
        if current!="canceled": self._finish(public_id,action,token,"manual_resolution","cancellation_unverified"); return self.db.get_order(public_id)
        with self.db.transaction(immediate=True) as c:
            c.execute("UPDATE storefront_orders SET state='cancelled',payment_state='cancelled',provider_revision=provider_revision+1,updated_at=? WHERE id=?",(now(),order["id"]))
            c.execute("UPDATE item_reservations SET state='released',close_reason='verified_stripe_cancellation',updated_at=? WHERE order_id=? AND state='open'",(now(),order["id"]))
        self._finish(public_id,action,token); return self.db.get_order(public_id)

    def reconcile_captured(self,public_id,review_id,request_key):
        order,review,item,action,token=self._claim_action(public_id,review_id,request_key,"reconcile_captured")
        if token is None: return self.db.get_order(public_id)
        if review["stripe_classification"]!="succeeded" or review["ebay_classification"]!="withdrawn": self._finish(public_id,action,token,"rejected","captured_order_review"); raise ManualResolutionError("Captured order still requires review")
        evidence=json.loads(review["stripe_evidence_json"]); self.db.record_provider_capture(public_id,evidence.get("amount_received"),evidence.get("currency"))
        with self.db.transaction(immediate=True) as c: c.execute("UPDATE storefront_orders SET state='paid',payment_state='captured',updated_at=? WHERE id=?",(now(),order["id"]))
        self._finish(public_id,action,token); return self.db.finalize_storefront_sale(public_id,source="seller")

    def reconcile_canceled(self,public_id,review_id,request_key):
        order,review,item,action,token=self._claim_action(public_id,review_id,request_key,"reconcile_canceled")
        if token is None: return self.db.get_order(public_id)
        if review["stripe_classification"]!="canceled": self._finish(public_id,action,token,"rejected","cancellation_unverified"); raise ManualResolutionError("Cancellation is not verified")
        with self.db.transaction(immediate=True) as c:
            c.execute("UPDATE storefront_orders SET state='cancelled',payment_state='cancelled',provider_revision=provider_revision+1,updated_at=? WHERE id=?",(now(),order["id"]))
            c.execute("UPDATE item_reservations SET state='released',close_reason='verified_stripe_cancellation',updated_at=? WHERE order_id=? AND state='open'",(now(),order["id"]))
        self._finish(public_id,action,token); return self.db.get_order(public_id)

    def available_actions(self,public_id):
        with self.db.connect() as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order or order["state"]!="manual_resolution": return {"recheck":False}
            review=c.execute("SELECT * FROM storefront_resolution_reviews WHERE order_id=? ORDER BY created_at DESC,id DESC LIMIT 1",(order["id"],)).fetchone()
        result={"recheck":True,"review":dict(review) if review else None,"resume":False,"cancel":False,"reconcile_canceled":False,"reconcile_captured":False}
        if review and review["state"]=="complete" and review["provider_revision"]==order["provider_revision"]:
            result.update(resume=review["stripe_classification"]=="requires_capture" and review["ebay_classification"] in ("published","withdrawn"),cancel=review["stripe_classification"]=="requires_capture",reconcile_canceled=review["stripe_classification"]=="canceled",reconcile_captured=review["stripe_classification"]=="succeeded" and review["ebay_classification"]=="withdrawn")
        return result
