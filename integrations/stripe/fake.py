import hashlib, hmac, json
from .errors import StripeIntegrationError

class FakeStripeAdapter:
    identity = "fake"
    def __init__(self, config): self.cfg=config; self.sessions={}; self.captures=[]; self.cancellations=[]; self.calls=[]; self.payment_intents={}; self.refunds={}; self.refund_keys={}; self.refund_mode="succeeded"; self.refund_calls=[]
    def create_checkout(self, order, item, success_url, cancel_url):
        self.calls.append({"order":order["public_id"],"amount":order["price_snapshot"],"currency":order["currency_snapshot"],"quantity":1,"name":item["title"]})
        sid="cs_test_"+order["public_id"][:16]; pi="pi_test_"+order["public_id"][:16]
        result={"id":sid,"payment_intent":pi,"url":f"/shop/order/{order['public_id']}"}; self.sessions[sid]=result
        amount=int(round(float(order["price_snapshot"])*100))
        self.payment_intents[pi]={"id":pi,"status":"requires_capture","capture_method":"manual",
            "amount":amount,"amount_capturable":amount,"amount_received":0,"currency":order["currency_snapshot"].upper()}
        return result
    def retrieve_session(self,session_id):
        session=self.sessions.get(session_id)
        if session is None:
            raise StripeIntegrationError("session_not_found","Checkout session was not found",ambiguous=False)
        return session
    def capture(self,payment_intent_id,key):
        intent=self.payment_intents.get(payment_intent_id)
        if not intent:
            # The development fake is process-local; preserve the historical
            # restart path without inventing provider-confirmed money.
            intent=self.payment_intents.setdefault(payment_intent_id,{"id":payment_intent_id,"status":"requires_capture","capture_method":"manual","amount":None,"amount_capturable":None,"amount_received":None,"currency":None})
        if key not in [x[1] for x in self.captures]:
            self.captures.append((payment_intent_id,key)); intent.update(status="succeeded",amount_received=intent.get("amount"),amount_capturable=0)
        return dict(intent)
    def cancel(self,payment_intent_id,key):
        intent=self.payment_intents.get(payment_intent_id)
        if not intent: intent=self.payment_intents.setdefault(payment_intent_id,{"id":payment_intent_id,"status":"requires_capture","capture_method":"manual","amount":None,"amount_capturable":None,"amount_received":None,"currency":None})
        if key not in [x[1] for x in self.cancellations]:
            self.cancellations.append((payment_intent_id,key)); intent.update(status="canceled",amount_capturable=0)
        return dict(intent)
    def retrieve_payment_intent(self,payment_intent_id):
        intent=self.payment_intents.get(payment_intent_id)
        if not intent: raise StripeIntegrationError("not_found","Payment authorization requires review",ambiguous=True)
        return {k:intent.get(k) for k in ("id","status","capture_method","amount","amount_capturable","amount_received","currency")}
    def create_full_refund(self,payment_intent_id,amount,currency,public_id):
        key=f"refund:{public_id}:full:v1"
        if key in self.refund_keys: return dict(self.refunds[self.refund_keys[key]])
        self.refund_calls.append((payment_intent_id,amount,currency,key))
        if self.refund_mode=="failed": raise StripeIntegrationError("refund_rejected","Stripe rejected the refund",ambiguous=False)
        rid="re_test_"+public_id[:16]
        result={"id":rid,"payment_intent":payment_intent_id,"amount":amount,"currency":currency.upper(),
                "status":"pending" if self.refund_mode in ("pending","ambiguous") else "succeeded"}
        self.refunds[rid]=result; self.refund_keys[key]=rid
        if self.refund_mode=="ambiguous": raise StripeIntegrationError("refund_ambiguous","Refund outcome requires reconciliation",ambiguous=True,provider_id=rid)
        return dict(result)
    def retrieve_refund(self,refund_id):
        if self.refund_mode=="ambiguous": raise StripeIntegrationError("refund_ambiguous","Refund retrieval requires review",ambiguous=True,provider_id=refund_id)
        if refund_id not in self.refunds: raise StripeIntegrationError("refund_rejected","Refund was not found",ambiguous=False)
        result=dict(self.refunds[refund_id])
        if self.refund_mode in ("succeeded","pending","failed"): result["status"]=self.refund_mode
        return result
    def verify_webhook(self,payload,signature):
        expected=hmac.new(self.cfg.stripe_webhook_secret.encode(),payload,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature or ""): raise StripeIntegrationError("invalid_signature","Invalid webhook signature")
        return json.loads(payload)
